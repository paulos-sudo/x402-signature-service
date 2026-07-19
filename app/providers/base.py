"""Provider-Abstraktion: Documenso (Produktion) & Fake (lokale Entwicklung)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ProviderStatus:
    status: str  # normalisiert: sent | viewed | completed | declined | expired | error
    completed_at: Optional[datetime] = None


class SignatureProvider(Protocol):
    name: str

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
        """Laedt das PDF hoch, platziert das Signaturfeld (unten rechts, letzte
        Seite), sendet die Anfrage an den Unterzeichner und liefert die
        Provider-Request-ID zurueck."""
        ...

    async def get_status(self, provider_request_id: str) -> ProviderStatus: ...

    async def download_completed(self, provider_request_id: str) -> bytes:
        """Laedt das signierte PDF in den RAM (keine permanente URL)."""
        ...
