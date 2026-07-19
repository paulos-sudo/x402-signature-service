"""x402-Signature-Service — Agent-Native Microservice (B2A).

POST /v1/signature-requests            -> Signaturanfrage (x402-geschuetzt, USDC auf Base)
GET  /v1/signature-requests/{id}       -> Status (Bearer-Token)
GET  /v1/signature-requests/{id}/document -> signiertes PDF (Bearer-Token)
POST /webhooks/documenso               -> Provider-Webhook (X-Documenso-Secret)
GET  / , /health , /v1/schema , /agent-tool.json -> frei

⚠️ Disclaimer: This MVP facilitates simple electronic signatures through the
configured signature provider. Users are responsible for determining whether
this signature type is appropriate for their document, jurisdiction, and use case.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from datetime import timedelta
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError
from sqlmodel import select

from . import discovery
from .config import Settings, enforce_production_safety, load_settings
from .db import (
    STATUS_COMPLETED,
    STATUS_EXPIRED,
    TERMINAL_STATUSES,
    ProviderEvent,
    SignatureRequest,
    iso,
    make_engine,
    session_for,
    utcnow,
)
from .logging_setup import setup_logging
from .models import SignatureRequestCreate, SignatureRequestPublic, SignerPublic
from .pdf_utils import InvalidPdfError, validate_pdf
from .providers import ProviderError, build_provider
from .security import (
    DocumentFetchError,
    constant_time_equals,
    fetch_document_safely,
    generate_access_token,
    hash_token,
    mask_email,
    sha256_hex,
)

logger = logging.getLogger("signature-service")

DISCLAIMER = (
    "This MVP facilitates simple electronic signatures through the configured "
    "signature provider. Users are responsible for determining whether this "
    "signature type is appropriate for their document, jurisdiction, and use case."
)

# Documenso-Webhook-Events -> normalisierter Status
_WEBHOOK_STATUS_MAP = {
    "DOCUMENT_OPENED": "viewed",
    "DOCUMENT_VIEWED": "viewed",
    "DOCUMENT_SIGNED": "completed",
    "DOCUMENT_COMPLETED": "completed",
    "DOCUMENT_REJECTED": "declined",
    "DOCUMENT_CANCELLED": "declined",
}


def _error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "error": message})


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    enforce_production_safety(settings)
    setup_logging()

    engine = make_engine(settings.DATABASE_PATH)
    provider = build_provider(settings)

    resource_url = f"{settings.BASE_URL}/v1/signature-requests"

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        # Periodischer Retention-Task (Datensparsamkeit)
        from .cleanup import purge_old_requests

        async def _purger():
            while True:
                try:
                    n = purge_old_requests(engine, settings.PURGE_AFTER_DAYS)
                    if n:
                        logger.info("retention purge removed %d request(s)", n)
                except Exception:
                    logger.exception("retention purge failed")
                await asyncio.sleep(24 * 3600)

        task = asyncio.create_task(_purger())
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    app = FastAPI(
        title="x402-signature-service",
        version=settings.VERSION,
        description=discovery.DESCRIPTION + "\n\n**Disclaimer:** " + DISCLAIMER,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.provider = provider

    # ------------------------------------------------------------------
    # x402 Payment-Middleware (nur POST /v1/signature-requests + GET-Probe)
    # ------------------------------------------------------------------
    if not settings.X402_BYPASS_FOR_LOCAL_DEVELOPMENT:
        _install_payment_middleware(app, settings, resource_url)
    else:
        logger.warning("x402 paywall DISABLED (X402_BYPASS_FOR_LOCAL_DEVELOPMENT=true)")

    # ------------------------------------------------------------------
    # Kern-Endpunkte
    # ------------------------------------------------------------------

    @app.post("/v1/signature-requests", status_code=201)
    async def create_signature_request(
        request: Request,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ):
        if not idempotency_key or not (1 <= len(idempotency_key) <= 200):
            return _error(400, "Idempotency-Key header is required (1-200 characters)")

        raw = await request.body()
        try:
            body = SignatureRequestCreate.model_validate_json(raw)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"status": "error", "error": "validation failed", "detail": json.loads(exc.json(include_url=False))},
            )

        payload_hash = sha256_hex(
            json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        )

        # Idempotenz: gleicher Key + gleicher Payload -> gespeicherte Ressource
        # (ohne accessToken — der wird nur genau einmal ausgegeben).
        with session_for(engine) as session:
            existing = session.exec(
                select(SignatureRequest).where(SignatureRequest.idempotency_key == idempotency_key)
            ).first()
            if existing is not None:
                if existing.normalized_payload_hash != payload_hash:
                    return _error(409, "Idempotency-Key was already used with a different payload")
                return JSONResponse(
                    status_code=200,
                    content=_public(existing, settings, access_token=None).model_dump(exclude_none=True),
                )

        # Dokument beschaffen (Base64 oder via SSRF-geschuetztem Download)
        try:
            if body.documentBase64 is not None:
                pdf = body.decoded_document(settings.MAX_PDF_BYTES)
            else:
                pdf = await fetch_document_safely(str(body.documentUrl), settings.MAX_PDF_BYTES)
        except DocumentFetchError as exc:
            return _error(exc.status_code, str(exc))
        except ValueError as exc:
            return _error(400, str(exc))

        try:
            last_page = validate_pdf(pdf)
        except InvalidPdfError as exc:
            return _error(400, str(exc))

        # Provider-Flow: hochladen, Feld platzieren, senden
        try:
            provider_request_id = await provider.create_and_send(
                pdf=pdf,
                document_name=body.documentName,
                signer_name=body.signer.name,
                signer_email=body.signer.email,
                message=body.message,
                last_page=last_page,
            )
        except ProviderError as exc:
            logger.error("provider create_and_send failed: %s", exc)
            return _error(502, "Signature provider is unavailable, please retry")

        token = generate_access_token()
        now = utcnow()
        record = SignatureRequest(
            id="sigreq_" + secrets.token_hex(8),
            provider=provider.name,
            provider_request_id=provider_request_id,
            document_name=body.documentName,
            original_document_hash=sha256_hex(pdf),
            signer_name=body.signer.name,
            signer_email=body.signer.email,
            message=body.message,
            external_reference=body.externalReference,
            status="sent",
            access_token_hash=hash_token(token),
            idempotency_key=idempotency_key,
            normalized_payload_hash=payload_hash,
            created_at=now,
            expires_at=now + timedelta(days=body.expiresInDays),
            # None -> der erste Status-Poll synchronisiert immer live beim Provider
            last_synced_at=None,
        )
        with session_for(engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)

        logger.info(
            "signature request created id=%s signer=%s provider=%s",
            record.id,
            mask_email(record.signer_email),
            provider.name,
        )
        return JSONResponse(
            status_code=201,
            content=_public(record, settings, access_token=token).model_dump(exclude_none=True),
        )

    async def _authorized_record(record_id: str, authorization: Optional[str]):
        """Bearer-Auth: Token hashen + zeitsicher vergleichen. Liefert Record oder Response."""
        if not authorization or not authorization.startswith("Bearer "):
            return None, _error(401, "Authorization: Bearer <access-token> header is required")
        token = authorization[len("Bearer ") :].strip()
        with session_for(engine) as session:
            record = session.get(SignatureRequest, record_id)
        if record is None:
            return None, _error(404, "Signature request not found")
        if not constant_time_equals(hash_token(token), record.access_token_hash):
            return None, _error(401, "Invalid access token")
        return record, None

    async def _sync_status(record: SignatureRequest) -> SignatureRequest:
        """Live-Status beim Provider abfragen (15s-Cache), Ablauf pruefen."""
        now = utcnow()
        if record.status in TERMINAL_STATUSES:
            return record

        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            from datetime import timezone as _tz

            expires_at = expires_at.replace(tzinfo=_tz.utc)
        if now > expires_at:
            record.status = STATUS_EXPIRED
        else:
            last = record.last_synced_at
            if last is not None and last.tzinfo is None:
                from datetime import timezone as _tz

                last = last.replace(tzinfo=_tz.utc)
            if last is None or (now - last).total_seconds() >= settings.STATUS_CACHE_SECONDS:
                try:
                    ps = await provider.get_status(record.provider_request_id)
                    record.status = ps.status
                    if ps.completed_at is not None:
                        record.completed_at = ps.completed_at
                    record.last_synced_at = now
                except ProviderError:
                    logger.warning("status sync failed for %s (keeping cached status)", record.id)

        with session_for(engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    @app.get("/v1/signature-requests/{record_id}")
    async def get_signature_request(record_id: str, authorization: Optional[str] = Header(default=None)):
        record, err = await _authorized_record(record_id, authorization)
        if err:
            return err
        record = await _sync_status(record)
        return JSONResponse(_public(record, settings, access_token=None).model_dump(exclude_none=True))

    @app.get("/v1/signature-requests/{record_id}/document")
    async def download_signed_document(record_id: str, authorization: Optional[str] = Header(default=None)):
        record, err = await _authorized_record(record_id, authorization)
        if err:
            return err
        record = await _sync_status(record)
        if record.status != STATUS_COMPLETED:
            return _error(409, f"Document is not completed yet (status: {record.status})")
        try:
            pdf = await provider.download_completed(record.provider_request_id)
        except ProviderError as exc:
            return _error(exc.status_code, str(exc))
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="signed-document.pdf"'},
        )

    # ------------------------------------------------------------------
    # Documenso-Webhook (keine Zahlung; Auth via X-Documenso-Secret)
    # ------------------------------------------------------------------

    @app.post("/webhooks/documenso")
    async def documenso_webhook(request: Request):
        secret = settings.DOCUMENSO_WEBHOOK_SECRET
        if not secret:
            return _error(503, "Webhook secret is not configured")
        provided = request.headers.get("X-Documenso-Secret", "")
        if not constant_time_equals(provided, secret):
            return _error(401, "Invalid webhook secret")

        raw = await request.body()
        payload_hash = sha256_hex(raw)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return _error(400, "Invalid JSON payload")

        event_type = str(payload.get("event") or payload.get("type") or "").upper()
        doc = payload.get("payload") or payload.get("data") or {}
        provider_doc_id = str(doc.get("id") or doc.get("documentId") or "")

        with session_for(engine) as session:
            # Idempotenz: identische Payload nur einmal verarbeiten
            dup = session.exec(
                select(ProviderEvent).where(
                    ProviderEvent.provider == "documenso",
                    ProviderEvent.payload_hash == payload_hash,
                )
            ).first()
            if dup is not None:
                return {"status": "ok", "duplicate": True}

            record = None
            if provider_doc_id:
                record = session.exec(
                    select(SignatureRequest).where(
                        SignatureRequest.provider_request_id == provider_doc_id
                    )
                ).first()

            event = ProviderEvent(
                provider="documenso",
                provider_event_id=str(payload.get("webhookId") or "") or None,
                signature_request_id=record.id if record else None,
                event_type=event_type or "UNKNOWN",
                payload_hash=payload_hash,
                processed_at=utcnow(),
            )
            session.add(event)

            new_status = _WEBHOOK_STATUS_MAP.get(event_type)
            if record is not None and new_status is not None:
                # Terminal-Status nie zurueckdrehen
                if record.status not in TERMINAL_STATUSES or new_status == STATUS_COMPLETED:
                    record.status = new_status
                    if new_status == STATUS_COMPLETED and record.completed_at is None:
                        record.completed_at = utcnow()
                    session.add(record)
            session.commit()

        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Freie Endpunkte: Health, Schema, Tool-Definition, Landing
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "x402-signature-service", "version": settings.VERSION}

    @app.get("/v1/schema")
    async def schema() -> dict:
        """Frei abrufbares Schema — erleichtert Agenten die Integration ohne Zahlung."""
        return {
            "service": settings.SERVICE_NAME,
            "endpoint": resource_url,
            "method": "POST",
            "price": settings.PRICE,
            "network": settings.NETWORK,
            "payment_protocol": "x402",
            "description": discovery.DESCRIPTION,
            "disclaimer": DISCLAIMER,
            "tool_name": discovery.TOOL_NAME,
            "input_schema": discovery.INPUT_SCHEMA,
            "input_example": discovery.EXAMPLE_INPUT,
            "output_schema": discovery.OUTPUT_SCHEMA,
            "output_example": discovery.EXAMPLE_OUTPUT,
        }

    @app.get("/agent-tool.json")
    async def agent_tool() -> dict:
        """LLM-Tool-Definition (JSON-Schema) fuer Tool-Calling-Agenten."""
        return {
            "name": discovery.TOOL_NAME,
            "description": discovery.DESCRIPTION,
            "input_schema": discovery.INPUT_SCHEMA,
        }

    @app.get("/", response_class=HTMLResponse)
    async def landing() -> HTMLResponse:
        return HTMLResponse(_landing_html(settings))

    return app


# ---------------------------------------------------------------------------
# x402-Middleware-Aufbau (Konvention aus E-Invoice-Gen / Legal-Engine)
# ---------------------------------------------------------------------------


def _install_payment_middleware(app: FastAPI, settings: Settings, resource_url: str) -> None:
    from x402 import x402ResourceServer
    from x402.extensions.bazaar import (
        OutputConfig,
        bazaar_resource_server_extension,
        declare_discovery_extension,
    )
    from x402.http import HTTPFacilitatorClient
    from x402.http.middleware.fastapi import payment_middleware
    from x402.http.types import HTTPResponseBody, PaymentOption, RouteConfig
    from x402.mechanisms.evm.exact.register import register_exact_evm_server

    class CdpCompatFacilitatorClient(HTTPFacilitatorClient):
        """CDP-Facilitator akzeptiert (Stand 07/2026) nur das x402-v1-Wire-Format.
        Konvertiert v2-Payloads vor verify/settle nach v1."""

        _V1_NAMES = {"eip155:8453": "base", "eip155:84532": "base-sepolia"}

        def _build_request_body(self, version, payload_dict, requirements_dict):
            try:
                if version == 2 and requirements_dict.get("network") in self._V1_NAMES:
                    net = self._V1_NAMES[requirements_dict["network"]]
                    accepted = payload_dict.get("accepted") or {}
                    resource = payload_dict.get("resource") or {}
                    v1_payload = {
                        "x402Version": 1,
                        "scheme": accepted.get("scheme") or requirements_dict.get("scheme", "exact"),
                        "network": net,
                        "payload": payload_dict.get("payload"),
                    }
                    v1_requirements = {
                        "scheme": requirements_dict.get("scheme", "exact"),
                        "network": net,
                        "maxAmountRequired": requirements_dict.get("amount"),
                        "resource": resource.get("url") or resource_url,
                        "description": (resource.get("description") or "x402 resource")[:500],
                        "mimeType": resource.get("mimeType") or "application/json",
                        "payTo": requirements_dict.get("payTo"),
                        "maxTimeoutSeconds": requirements_dict.get("maxTimeoutSeconds") or 120,
                        "asset": requirements_dict.get("asset"),
                        "extra": requirements_dict.get("extra"),
                    }
                    return super()._build_request_body(1, v1_payload, v1_requirements)
            except Exception:  # noqa: BLE001 — im Zweifel Original-Format senden
                pass
            return super()._build_request_body(version, payload_dict, requirements_dict)

    def _facilitator_client() -> HTTPFacilitatorClient:
        config: dict = {"url": settings.FACILITATOR_URL}
        if settings.CDP_API_KEY_ID and settings.CDP_API_KEY_SECRET:
            # Coinbase CDP-Facilitator (noetig fuer automatisches Bazaar-Listing).
            from cdp.auth.utils.jwt import JwtOptions, generate_jwt  # type: ignore

            def _make_headers(path: str, method: str = "POST") -> dict[str, str]:
                token = generate_jwt(
                    JwtOptions(
                        api_key_id=settings.CDP_API_KEY_ID,
                        api_key_secret=settings.CDP_API_KEY_SECRET,
                        request_method=method,
                        request_host="api.cdp.coinbase.com",
                        request_path=path,
                    )
                )
                return {"Authorization": f"Bearer {token}"}

            config["create_headers"] = lambda: {
                "verify": _make_headers("/platform/v2/x402/verify"),
                "settle": _make_headers("/platform/v2/x402/settle"),
                "supported": _make_headers("/platform/v2/x402/supported", "GET"),
            }
            return CdpCompatFacilitatorClient(config)
        elif settings.FACILITATOR_AUTH_HEADERS:
            static = settings.FACILITATOR_AUTH_HEADERS
            config["create_headers"] = lambda: {
                "verify": static,
                "settle": static,
                "supported": static,
            }
        return HTTPFacilitatorClient(config)

    x402_server = x402ResourceServer(_facilitator_client())
    register_exact_evm_server(x402_server)
    x402_server.register_extension(bazaar_resource_server_extension)

    _V1_NETWORK_NAMES = {"eip155:8453": "base", "eip155:84532": "base-sepolia"}
    _USDC_BY_NETWORK = {
        "eip155:8453": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "eip155:84532": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
    }

    def _price_atomic_usdc(price: str) -> str:
        from decimal import Decimal

        return str(int(Decimal(price.lstrip("$").strip()) * 1_000_000))

    def _v1_unpaid_body(_ctx) -> HTTPResponseBody:
        return HTTPResponseBody(
            content_type="application/json",
            body={
                "x402Version": 1,
                "error": "X-PAYMENT header is required",
                "accepts": [
                    {
                        "scheme": "exact",
                        "network": _V1_NETWORK_NAMES.get(settings.NETWORK, settings.NETWORK),
                        "maxAmountRequired": _price_atomic_usdc(settings.PRICE),
                        "resource": resource_url,
                        "description": discovery.DESCRIPTION,
                        "mimeType": "application/json",
                        "payTo": settings.PAY_TO_ADDRESS,
                        "maxTimeoutSeconds": 120,
                        "asset": _USDC_BY_NETWORK.get(settings.NETWORK, ""),
                        "extra": {"name": "USD Coin", "version": "2"},
                        "outputSchema": {
                            "input": {
                                "type": "http",
                                "method": "POST",
                                "bodyType": "json",
                                "bodyFields": discovery.INPUT_SCHEMA,
                            },
                            "output": discovery.OUTPUT_SCHEMA,
                        },
                    }
                ],
            },
        )

    def _discovery_extension() -> dict:
        ext = declare_discovery_extension(
            input=discovery.EXAMPLE_INPUT,
            input_schema=discovery.INPUT_SCHEMA,
            body_type="json",
            output=OutputConfig(example=discovery.EXAMPLE_OUTPUT, schema=discovery.OUTPUT_SCHEMA),
        )
        # Das Schema der Bazaar-Discovery verlangt "method" bereits zur Startzeit
        ext["bazaar"]["info"]["input"]["method"] = "POST"
        return ext

    def _route_config() -> RouteConfig:
        return RouteConfig(
            accepts=PaymentOption(
                scheme="exact",
                pay_to=settings.PAY_TO_ADDRESS,
                price=settings.PRICE,
                network=settings.NETWORK,
                max_timeout_seconds=120,
            ),
            resource=resource_url,
            description=discovery.DESCRIPTION,
            mime_type="application/json",
            service_name=settings.SERVICE_NAME[:32],
            tags=["signature", "e-signature", "pdf", "documents", "contracts", "workflow"],
            unpaid_response_body=_v1_unpaid_body,
            extensions=_discovery_extension(),
        )

    routes = {
        # Eigentlicher Service-Endpunkt
        "POST /v1/signature-requests": _route_config(),
        # GET-Probe (x402scan-Registrierung u.a. validieren per GET) -> gleiche 402-Antwort.
        # Wichtig: exakter Pfad — GET /v1/signature-requests/{id} bleibt frei (Bearer-Auth).
        "GET /v1/signature-requests": _route_config(),
    }

    app.middleware("http")(payment_middleware(routes, x402_server))


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _public(record: SignatureRequest, settings: Settings, access_token: Optional[str]) -> SignatureRequestPublic:
    return SignatureRequestPublic(
        id=record.id,
        status=record.status,
        documentName=record.document_name,
        signer=SignerPublic(name=record.signer_name, email=mask_email(record.signer_email)),
        createdAt=iso(record.created_at),
        expiresAt=iso(record.expires_at),
        completedAt=iso(record.completed_at),
        externalReference=record.external_reference,
        statusUrl=f"{settings.BASE_URL}/v1/signature-requests/{record.id}",
        accessToken=access_token,
    )


def _landing_html(settings: Settings) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>x402-signature-service — e-signatures for AI agents</title>
<meta name="description" content="Pay-per-call API (x402, {settings.PRICE} USDC on Base): send one PDF to one email recipient for simple electronic signature, track status, download the signed PDF.">
<style>
 body{{font-family:system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.6;color:#1a1a1a}}
 code,pre{{background:#f4f4f4;border-radius:6px}} pre{{padding:14px;overflow-x:auto;font-size:13px}}
 code{{padding:2px 5px}} h1{{font-size:1.7rem}} .badge{{display:inline-block;background:#0052ff;color:#fff;border-radius:20px;padding:3px 12px;font-size:13px;margin-right:6px}}
 .warn{{background:#fff7e0;border:1px solid #e6c200;border-radius:8px;padding:12px 16px;font-size:14px}}
 a{{color:#0052ff}}
</style></head><body>
<h1>x402-signature-service</h1>
<p><span class="badge">x402 · {settings.PRICE} USDC</span><span class="badge">Base</span><span class="badge">e-signature</span></p>
<p>Agent-native API that closes a critical capability gap: an AI agent sends a
<strong>PDF to a human for signature</strong>, tracks progress, and downloads the signed document —
fully autonomously, paid per call via <a href="https://x402.org">x402</a>.</p>
<div class="warn"><strong>⚠️ Disclaimer:</strong> {DISCLAIMER}</div>
<h2>Usage</h2>
<pre>POST {settings.BASE_URL}/v1/signature-requests   # 402 + payment requirements (x402)
# pay {settings.PRICE} USDC on Base, retry with payment header
# -&gt; 201 {{ "id": "sigreq_…", "accessToken": "sec_…", "statusUrl": "…" }}

GET  {settings.BASE_URL}/v1/signature-requests/&lt;id&gt;            # Authorization: Bearer sec_…
GET  {settings.BASE_URL}/v1/signature-requests/&lt;id&gt;/document   # signed PDF once completed</pre>
<p>Free machine-readable schema &amp; examples: <a href="/v1/schema">/v1/schema</a> ·
LLM tool definition: <a href="/agent-tool.json">/agent-tool.json</a> ·
OpenAPI: <a href="/docs">/docs</a></p>
<h2>Why agents use this</h2>
<ul>
<li><strong>Human-in-the-loop, agent-operated:</strong> the signer gets a normal e-mail + browser signing page (Documenso); the agent keeps full control via API.</li>
<li><strong>Secure by design:</strong> SSRF-guarded document fetching, one-time bearer tokens (only hashes stored), masked e-mail addresses in logs.</li>
<li><strong>Private:</strong> PDFs are processed in memory and never written to logs; completed requests are purged automatically.</li>
</ul>
</body></html>"""


app = create_app()
