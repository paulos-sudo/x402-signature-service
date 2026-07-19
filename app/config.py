"""Zentrale Konfiguration via Umgebungsvariablen.

Alle zahlungs- und providerrelevanten Einstellungen sind env-basiert, damit
derselbe Container fuer Testnet (Base Sepolia) und Mainnet (Base) sowie fuer
Fake- und Documenso-Provider genutzt werden kann.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: str = "false") -> bool:
    return _env(name, default).lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    # --- Umgebung ---
    APP_ENV: str = field(default_factory=lambda: _env("APP_ENV", "development"))

    # --- Zahlung / x402 ---
    # Base-Wallet-Adresse, auf der USDC ankommt (PFLICHT fuer Produktion)
    PAY_TO_ADDRESS: str = field(
        default_factory=lambda: _env("PAY_TO_ADDRESS", "0x0000000000000000000000000000000000000000")
    )
    # CAIP-2 Netzwerk: Base Mainnet = eip155:8453, Base Sepolia = eip155:84532
    NETWORK: str = field(default_factory=lambda: _env("X402_NETWORK", "eip155:8453"))
    # Preis pro Signaturanfrage (Money-String, USDC).
    # SIGNATURE_REQUEST_PRICE_USD hat Vorrang (PRD), X402_PRICE ist der
    # oekosystemweite Fallback der anderen Services.
    PRICE: str = field(
        default_factory=lambda: _env("SIGNATURE_REQUEST_PRICE_USD") or _env("X402_PRICE", "$0.50")
    )
    # Facilitator: verifiziert & settelt Zahlungen.
    #  - Base Sepolia (Test):  https://x402.org/facilitator
    #  - Base Mainnet (frei):  https://facilitator.payai.network
    #  - CDP (Bazaar-Listing): https://api.cdp.coinbase.com/platform/v2/x402
    FACILITATOR_URL: str = field(
        default_factory=lambda: _env("FACILITATOR_URL", "https://facilitator.payai.network")
    )
    FACILITATOR_AUTH_HEADERS: dict = field(
        default_factory=lambda: json.loads(_env("FACILITATOR_AUTH_HEADERS", "{}") or "{}")
    )
    # Optional: CDP-API-Keys -> aktiviert den Coinbase-Facilitator inkl. Bazaar-Indexierung
    CDP_API_KEY_ID: str = field(default_factory=lambda: _env("CDP_API_KEY_ID"))
    CDP_API_KEY_SECRET: str = field(default_factory=lambda: _env("CDP_API_KEY_SECRET"))
    # Lokaler Entwicklungsmodus: deaktiviert die Bezahlschranke komplett.
    X402_BYPASS_FOR_LOCAL_DEVELOPMENT: bool = field(
        default_factory=lambda: _env_bool("X402_BYPASS_FOR_LOCAL_DEVELOPMENT")
    )

    # --- Service ---
    BASE_URL: str = field(default_factory=lambda: _env("BASE_URL", "http://localhost:8000"))
    SERVICE_NAME: str = field(
        default_factory=lambda: _env("SERVICE_NAME", "x402-signature-service")
    )  # <= 32 Zeichen (Bazaar-Limit)
    VERSION: str = "1.0.0"

    # --- Datenbank ---
    DATABASE_PATH: str = field(
        default_factory=lambda: _env("DATABASE_PATH", "./data/signature-service.db")
    )

    # --- Signatur-Provider ---
    # "documenso" (Produktion) oder "fake" (lokale Entwicklung/Tests)
    SIGNATURE_PROVIDER: str = field(default_factory=lambda: _env("SIGNATURE_PROVIDER", "documenso"))
    DOCUMENSO_BASE_URL: str = field(
        default_factory=lambda: _env("DOCUMENSO_BASE_URL", "https://app.documenso.com")
    )
    DOCUMENSO_API_TOKEN: str = field(default_factory=lambda: _env("DOCUMENSO_API_TOKEN"))
    DOCUMENSO_WEBHOOK_SECRET: str = field(default_factory=lambda: _env("DOCUMENSO_WEBHOOK_SECRET"))
    # Fake-Provider: Sekunden bis zur simulierten Unterschrift
    FAKE_AUTOSIGN_SECONDS: int = field(
        default_factory=lambda: int(_env("FAKE_AUTOSIGN_SECONDS", "5"))
    )

    # --- Limits & Verhalten ---
    MAX_PDF_BYTES: int = field(
        default_factory=lambda: int(_env("MAX_PDF_BYTES", str(5 * 1024 * 1024)))
    )
    STATUS_CACHE_SECONDS: int = field(
        default_factory=lambda: int(_env("STATUS_CACHE_SECONDS", "15"))
    )
    # Datensparsamkeit: abgeschlossene/abgelaufene Requests nach N Tagen hart loeschen
    PURGE_AFTER_DAYS: int = field(default_factory=lambda: int(_env("PURGE_AFTER_DAYS", "30")))


def enforce_production_safety(settings: Settings) -> None:
    """Sicherheitsanker: Produktion darf niemals mit Dev-Bypass oder
    Fake-Provider starten. Beendet den Prozess sofort (sys.exit(1))."""
    if settings.APP_ENV != "production":
        return
    problems = []
    if settings.X402_BYPASS_FOR_LOCAL_DEVELOPMENT:
        problems.append("X402_BYPASS_FOR_LOCAL_DEVELOPMENT=true")
    if settings.SIGNATURE_PROVIDER == "fake":
        problems.append("SIGNATURE_PROVIDER=fake")
    if problems:
        sys.stderr.write(
            "FATAL: APP_ENV=production ist mit unsicheren Dev-Flags kombiniert: "
            + ", ".join(problems)
            + " — Start verweigert.\n"
        )
        sys.exit(1)


def load_settings() -> Settings:
    return Settings()
