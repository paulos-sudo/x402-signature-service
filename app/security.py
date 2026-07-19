"""Sicherheits-Bausteine: Access-Tokens, E-Mail-Maskierung, SSRF-Schutzwall.

Der SSRF-Schutz beim Laden von `documentUrl` erfuellt folgende Garantien:
  * nur https://
  * DNS wird VOR dem Connect aufgeloest; private/loopback/link-local/reservierte
    IPs werden abgelehnt (RFC 1918, 127.0.0.0/8, ::1, 169.254.0.0/16, ...)
  * die Verbindung wird auf die geprueften IPs gepinnt (Schutz vor DNS-Rebinding):
    connect zur IP, TLS-SNI + Host-Header tragen den Original-Hostnamen,
    Zertifikatspruefung laeuft gegen den Hostnamen
  * maximal 3 Redirects, jedes Ziel wird erneut komplett geprueft
  * harter Byte-Limit-Stream (Abbruch > MAX_PDF_BYTES)
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import socket
from urllib.parse import urljoin, urlparse

import anyio
import httpx

TOKEN_PREFIX = "sec_"
MAX_REDIRECTS = 3


class DocumentFetchError(Exception):
    """Fehler beim sicheren Laden eines Dokuments (wird als HTTP 400 gemappt)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Tokens & Hashing
# ---------------------------------------------------------------------------


def generate_access_token() -> str:
    """Krypto-Token, mind. 32 Bytes Entropie. Wird nur EINMAL im Klartext ausgegeben."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_email(email: str) -> str:
    """'anna@example.com' -> 'a***@example.com' (fuer Logs & Responses)."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return (local[:1] or "*") + "***@" + domain


# ---------------------------------------------------------------------------
# SSRF-Schutzwall
# ---------------------------------------------------------------------------


def _is_forbidden_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_and_check(host: str, port: int) -> str:
    """DNS-Aufloesung VOR dem Connect. Liefert eine geprüfte IP oder wirft."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise DocumentFetchError(f"documentUrl: DNS resolution failed for {host!r}") from exc
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise DocumentFetchError(f"documentUrl: no IP addresses for {host!r}")
    for ip in ips:
        if _is_forbidden_ip(ip):
            raise DocumentFetchError(
                "documentUrl: host resolves to a private, loopback or otherwise "
                "forbidden IP range — refusing to fetch"
            )
    return ips[0]


async def fetch_document_safely(url: str, max_bytes: int) -> bytes:
    """Laedt ein Dokument unter Einhaltung des SSRF-Schutzwalls (s. Modul-Doc)."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        parsed = urlparse(current)
        if parsed.scheme != "https":
            raise DocumentFetchError("documentUrl: only https:// URLs are allowed")
        host = parsed.hostname
        if not host:
            raise DocumentFetchError("documentUrl: invalid URL (no host)")
        port = parsed.port or 443

        ip = await anyio.to_thread.run_sync(_resolve_and_check, host, port)

        # IP-Pinning: wir verbinden zur gepruefte IP; SNI & Hostname-Verifikation
        # laufen ueber `sni_hostname`, der Host-Header traegt den Originalnamen.
        netloc = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
        pinned_url = parsed._replace(netloc=netloc).geturl()
        host_header = host if port == 443 else f"{host}:{port}"

        async with httpx.AsyncClient(follow_redirects=False, timeout=30.0) as client:
            try:
                async with client.stream(
                    "GET",
                    pinned_url,
                    headers={"Host": host_header, "User-Agent": "x402-signature-service/1.0"},
                    extensions={"sni_hostname": host},
                ) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise DocumentFetchError("documentUrl: redirect without Location header")
                        current = urljoin(current, location)
                        continue  # naechster Hop wird erneut komplett geprueft
                    if resp.status_code != 200:
                        raise DocumentFetchError(
                            f"documentUrl: upstream returned HTTP {resp.status_code}"
                        )
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            raise DocumentFetchError(
                                f"documentUrl: document exceeds the {max_bytes // (1024 * 1024)} MB limit"
                            )
                    return bytes(buf)
            except httpx.HTTPError as exc:
                raise DocumentFetchError(f"documentUrl: fetch failed ({exc.__class__.__name__})") from exc

    raise DocumentFetchError(f"documentUrl: more than {MAX_REDIRECTS} redirects")
