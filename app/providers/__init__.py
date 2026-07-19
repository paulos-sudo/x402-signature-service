from __future__ import annotations

from ..config import Settings
from .base import ProviderError, ProviderStatus, SignatureProvider


def build_provider(settings: Settings) -> SignatureProvider:
    if settings.SIGNATURE_PROVIDER == "fake":
        from .fake import FakeProvider

        return FakeProvider(autosign_seconds=settings.FAKE_AUTOSIGN_SECONDS)
    if settings.SIGNATURE_PROVIDER == "documenso":
        from .documenso import DocumensoProvider

        return DocumensoProvider(
            base_url=settings.DOCUMENSO_BASE_URL,
            api_token=settings.DOCUMENSO_API_TOKEN,
        )
    raise ValueError(f"Unknown SIGNATURE_PROVIDER: {settings.SIGNATURE_PROVIDER!r}")


__all__ = ["SignatureProvider", "ProviderError", "ProviderStatus", "build_provider"]
