"""Documenso-Provider (API v1, stabil dokumentiert).

Flow:
  1. POST   /api/v1/documents                -> documentId + S3-Upload-URL + recipientId
  2. PUT    <uploadUrl>                      -> PDF-Bytes hochladen
  3. POST   /api/v1/documents/{id}/fields    -> SIGNATURE-Feld unten rechts, letzte Seite
  4. POST   /api/v1/documents/{id}/send      -> E-Mail an den Unterzeichner
  5. GET    /api/v1/documents/{id}           -> Status
  6. GET    /api/v1/documents/{id}/download  -> tempo. downloadUrl -> PDF in den RAM

Auth: `Authorization: <api_token>` (Documenso-Token, Prefix `api_`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from ..pdf_utils import SIGNATURE_FIELD_POSITION
from .base import ProviderError, ProviderStatus

logger = logging.getLogger("signature-service.documenso")

# Documenso-Status -> normalisierter Service-Status
_STATUS_MAP = {
    "DRAFT": "sent",
    "PENDING": "sent",
    "COMPLETED": "completed",
    "REJECTED": "declined",
    "CANCELLED": "declined",
}


class DocumensoProvider:
    name = "documenso"

    def __init__(self, base_url: str, api_token: str, timeout: float = 30.0):
        if not api_token:
            logger.warning(
                "DocumensoProvider ohne DOCUMENSO_API_TOKEN initialisiert — "
                "Requests werden fehlschlagen, bis das Token gesetzt ist."
            )
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": self.api_token},
            timeout=self.timeout,
        )

    @staticmethod
    def _raise_for(resp: httpx.Response, action: str) -> None:
        if resp.status_code >= 400:
            # Niemals Dokumentinhalte loggen — nur Statuscode & Aktion.
            logger.error("Documenso %s failed: HTTP %s", action, resp.status_code)
            raise ProviderError(f"Signature provider error during {action}")

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
        async with self._client() as client:
            # 1. Dokument anlegen
            resp = await client.post(
                "/api/v1/documents",
                json={
                    "title": document_name,
                    "recipients": [
                        {"name": signer_name, "email": signer_email, "role": "SIGNER"}
                    ],
                    "meta": {
                        "subject": f"Signature requested: {document_name}",
                        "message": message or "Please review and sign this document.",
                    },
                },
            )
            self._raise_for(resp, "document creation")
            data = resp.json()
            document_id = data.get("documentId") or data.get("id")
            upload_url = data.get("uploadUrl")
            recipients = data.get("recipients") or []
            if not document_id or not upload_url or not recipients:
                raise ProviderError("Unexpected response from signature provider")
            recipient_id = recipients[0].get("recipientId") or recipients[0].get("id")

            # 2. PDF hochladen (separater, vor-signierter Upload-Endpunkt)
            async with httpx.AsyncClient(timeout=self.timeout) as upload_client:
                up = await upload_client.put(
                    upload_url, content=pdf, headers={"Content-Type": "application/pdf"}
                )
                self._raise_for(up, "document upload")

            # 3. Signaturfeld unten rechts auf der letzten Seite
            resp = await client.post(
                f"/api/v1/documents/{document_id}/fields",
                json={
                    "recipientId": recipient_id,
                    "type": "SIGNATURE",
                    "pageNumber": last_page,
                    "pageX": SIGNATURE_FIELD_POSITION["page_x"],
                    "pageY": SIGNATURE_FIELD_POSITION["page_y"],
                    "pageWidth": SIGNATURE_FIELD_POSITION["page_width"],
                    "pageHeight": SIGNATURE_FIELD_POSITION["page_height"],
                },
            )
            self._raise_for(resp, "field placement")

            # 4. Senden (E-Mail-Versand an den Unterzeichner)
            resp = await client.post(
                f"/api/v1/documents/{document_id}/send",
                json={"sendEmail": True},
            )
            self._raise_for(resp, "document send")

        return str(document_id)

    async def get_status(self, provider_request_id: str) -> ProviderStatus:
        async with self._client() as client:
            resp = await client.get(f"/api/v1/documents/{provider_request_id}")
            self._raise_for(resp, "status check")
            data = resp.json()

        raw = (data.get("status") or "").upper()
        status = _STATUS_MAP.get(raw, "sent")
        completed_at: Optional[datetime] = None
        if status == "completed":
            stamp = data.get("completedAt")
            if stamp:
                try:
                    completed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                except ValueError:
                    completed_at = datetime.now(timezone.utc)
            else:
                completed_at = datetime.now(timezone.utc)
        return ProviderStatus(status=status, completed_at=completed_at)

    async def download_completed(self, provider_request_id: str) -> bytes:
        async with self._client() as client:
            resp = await client.get(f"/api/v1/documents/{provider_request_id}/download")
            self._raise_for(resp, "download link")
            download_url = resp.json().get("downloadUrl")
            if not download_url:
                raise ProviderError("Signature provider returned no download URL")

        # PDF direkt in den RAM streamen — keine permanente oeffentliche URL.
        async with httpx.AsyncClient(timeout=60.0) as dl:
            resp = await dl.get(download_url)
            self._raise_for(resp, "download")
            return resp.content
