"""Virtueller Signatur-Provider fuer lokale Entwicklung & Tests.

Simuliert den kompletten Documenso-Flow im Speicher:
  sent -> viewed -> completed (nach `autosign_seconds`).

Der Download liefert das Original-PDF mit einem angehaengten, minimalen
"[signed]"-Marker-Kommentar (rein technisch, keine echte Signatur).
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from .base import ProviderError, ProviderStatus


class FakeProvider:
    name = "fake"

    def __init__(self, autosign_seconds: int = 5):
        self.autosign_seconds = autosign_seconds
        self._store: dict[str, dict] = {}

    async def create_and_send(
        self,
        *,
        pdf: bytes,
        document_name: str,
        signer_name: str,
        signer_email: str,
        message: Optional[str],
        last_page: int,
    ) -> str:
        provider_id = "fake_" + secrets.token_hex(8)
        self._store[provider_id] = {
            "pdf": pdf,
            "created": time.monotonic(),
            "document_name": document_name,
            "declined": False,
        }
        return provider_id

    def _entry(self, provider_request_id: str) -> dict:
        entry = self._store.get(provider_request_id)
        if entry is None:
            raise ProviderError("Unknown provider request id", status_code=404)
        return entry

    async def get_status(self, provider_request_id: str) -> ProviderStatus:
        entry = self._entry(provider_request_id)
        if entry["declined"]:
            return ProviderStatus(status="declined")
        elapsed = time.monotonic() - entry["created"]
        if elapsed >= self.autosign_seconds:
            entry.setdefault("completed_at", datetime.now(timezone.utc))
            return ProviderStatus(status="completed", completed_at=entry["completed_at"])
        if elapsed >= self.autosign_seconds / 2:
            return ProviderStatus(status="viewed")
        return ProviderStatus(status="sent")

    async def download_completed(self, provider_request_id: str) -> bytes:
        entry = self._entry(provider_request_id)
        status = await self.get_status(provider_request_id)
        if status.status != "completed":
            raise ProviderError("Document is not completed yet", status_code=409)
        return entry["pdf"] + b"\n% [signed by fake provider - development only]\n"

    # Test-Helfer
    def decline(self, provider_request_id: str) -> None:
        self._entry(provider_request_id)["declined"] = True
