from __future__ import annotations

import io
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def make_pdf(pages: int = 2) -> bytes:
    """Minimal-PDF ohne externe Abhaengigkeiten."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(pages):
        c.drawString(72, 720, f"Test page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def dev_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        APP_ENV="test",
        X402_BYPASS_FOR_LOCAL_DEVELOPMENT=True,
        SIGNATURE_PROVIDER="fake",
        FAKE_AUTOSIGN_SECONDS=0,
        DATABASE_PATH=str(tmp_path / "test.db"),
        BASE_URL="http://testserver",
        DOCUMENSO_WEBHOOK_SECRET="hook-secret",
    )
    base.update(overrides)
    settings = Settings()
    for key, value in base.items():
        setattr(settings, key, value)
    return settings


@pytest.fixture
def client(tmp_path):
    app = create_app(dev_settings(tmp_path))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pdf_bytes():
    return make_pdf()
