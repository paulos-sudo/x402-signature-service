"""Kernflows mit Fake-Provider + x402-Bypass (lokaler Entwicklungsmodus)."""

from __future__ import annotations

import base64
import json

from .conftest import make_pdf


def _create_body(pdf: bytes, **overrides) -> dict:
    body = {
        "documentName": "Service Agreement",
        "documentBase64": base64.b64encode(pdf).decode(),
        "signer": {"name": "Anna Müller", "email": "anna@example.com"},
        "message": "Please review and sign this agreement.",
        "expiresInDays": 14,
        "externalReference": "customer-contract-4821",
    }
    body.update(overrides)
    return body


def _headers(key: str = "idem-1") -> dict:
    return {"Idempotency-Key": key}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "ok", "service": "x402-signature-service", "version": "1.0.0"}


def test_landing_and_schema_and_tool(client):
    assert "Disclaimer" in client.get("/").text
    schema = client.get("/v1/schema").json()
    assert schema["payment_protocol"] == "x402"
    assert "disclaimer" in schema
    tool = client.get("/agent-tool.json").json()
    assert tool["name"] == "send_document_for_signature"
    assert "oneOf" in tool["input_schema"]


def test_full_flow_create_status_download(client, pdf_bytes):
    resp = client.post(
        "/v1/signature-requests", json=_create_body(pdf_bytes), headers=_headers()
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["id"].startswith("sigreq_")
    assert data["status"] == "sent"
    assert data["signer"]["email"] == "a***@example.com"
    assert data["accessToken"].startswith("sec_")
    token = data["accessToken"]

    # Status ohne Token -> 401
    assert client.get(f"/v1/signature-requests/{data['id']}").status_code == 401
    # Falscher Token -> 401
    assert (
        client.get(
            f"/v1/signature-requests/{data['id']}",
            headers={"Authorization": "Bearer sec_wrong"},
        ).status_code
        == 401
    )
    # Unbekannte ID -> 404
    assert (
        client.get(
            "/v1/signature-requests/sigreq_doesnotexist",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 404
    )

    # FAKE_AUTOSIGN_SECONDS=0 -> beim ersten Poll bereits completed
    resp = client.get(
        f"/v1/signature-requests/{data['id']}", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    status = resp.json()
    assert status["status"] == "completed"
    assert status.get("accessToken") is None  # Token nie erneut ausgeben
    assert status["completedAt"]

    # Download
    resp = client.get(
        f"/v1/signature-requests/{data['id']}/document",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")


def test_download_requires_completed(client, tmp_path, pdf_bytes):
    # Eigenes App-Setup mit langsamem Fake-Provider
    from fastapi.testclient import TestClient

    from app.main import create_app

    from .conftest import dev_settings

    app = create_app(dev_settings(tmp_path / "slow", FAKE_AUTOSIGN_SECONDS=3600))
    with TestClient(app) as slow:
        resp = slow.post(
            "/v1/signature-requests", json=_create_body(pdf_bytes), headers=_headers()
        )
        assert resp.status_code == 201
        data = resp.json()
        resp = slow.get(
            f"/v1/signature-requests/{data['id']}/document",
            headers={"Authorization": f"Bearer {data['accessToken']}"},
        )
        assert resp.status_code == 409


def test_idempotency(client, pdf_bytes):
    body = _create_body(pdf_bytes)
    first = client.post("/v1/signature-requests", json=body, headers=_headers("key-A"))
    assert first.status_code == 201

    # Gleicher Key + gleicher Payload -> 200, gleiche Ressource, KEIN Token
    replay = client.post("/v1/signature-requests", json=body, headers=_headers("key-A"))
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert "accessToken" not in replay.json()

    # Gleicher Key + anderer Payload -> 409
    other = _create_body(pdf_bytes, documentName="Other Doc")
    conflict = client.post("/v1/signature-requests", json=other, headers=_headers("key-A"))
    assert conflict.status_code == 409

    # Fehlender Key -> 400
    assert client.post("/v1/signature-requests", json=body).status_code == 400


def test_validation_rules(client, pdf_bytes):
    b64 = base64.b64encode(pdf_bytes).decode()

    # Beide Quellen -> 422
    both = _create_body(pdf_bytes, documentUrl="https://example.com/x.pdf")
    assert client.post("/v1/signature-requests", json=both, headers=_headers("v1")).status_code == 422

    # Keine Quelle -> 422
    none = _create_body(pdf_bytes)
    none.pop("documentBase64")
    assert client.post("/v1/signature-requests", json=none, headers=_headers("v2")).status_code == 422

    # Ungueltige E-Mail -> 422
    bad_mail = _create_body(pdf_bytes)
    bad_mail["signer"]["email"] = "not-an-email"
    assert client.post("/v1/signature-requests", json=bad_mail, headers=_headers("v3")).status_code == 422

    # documentName zu lang -> 422
    long_name = _create_body(pdf_bytes, documentName="x" * 151)
    assert client.post("/v1/signature-requests", json=long_name, headers=_headers("v4")).status_code == 422

    # expiresInDays ausserhalb 1..30 -> 422
    for bad in (0, 31):
        b = _create_body(pdf_bytes, expiresInDays=bad)
        assert client.post("/v1/signature-requests", json=b, headers=_headers(f"v5-{bad}")).status_code == 422

    # Signer-Name zu lang -> 422
    bad_signer = _create_body(pdf_bytes)
    bad_signer["signer"]["name"] = "x" * 121
    assert client.post("/v1/signature-requests", json=bad_signer, headers=_headers("v6")).status_code == 422

    # Kein PDF -> 400
    not_pdf = _create_body(pdf_bytes, documentBase64=base64.b64encode(b"hello world").decode())
    assert client.post("/v1/signature-requests", json=not_pdf, headers=_headers("v7")).status_code == 400

    # Kaputtes Base64 -> 400
    bad_b64 = _create_body(pdf_bytes, documentBase64="%%%not-base64%%%")
    assert client.post("/v1/signature-requests", json=bad_b64, headers=_headers("v8")).status_code == 400

    # Mehrseitiges PDF ok (Signaturfeld letzte Seite)
    ok = _create_body(make_pdf(pages=5))
    assert client.post("/v1/signature-requests", json=ok, headers=_headers("v9")).status_code == 201


def test_pdf_size_limit(client):
    big = b"%PDF-" + b"0" * (5 * 1024 * 1024 + 100)
    body = _create_body(big, documentBase64=base64.b64encode(big).decode())
    resp = client.post("/v1/signature-requests", json=body, headers=_headers("big"))
    assert resp.status_code == 400
    assert "size limit" in resp.json()["error"]


def test_ssrf_guard(client):
    # http:// -> abgelehnt (422 durch Pydantic-Validator)
    body = {
        "documentName": "Doc",
        "documentUrl": "http://example.com/x.pdf",
        "signer": {"name": "A", "email": "a@example.com"},
    }
    assert client.post("/v1/signature-requests", json=body, headers=_headers("s1")).status_code == 422

    # localhost / private IPs -> 400
    for url in (
        "https://127.0.0.1/doc.pdf",
        "https://localhost/doc.pdf",
        "https://192.168.1.10/doc.pdf",
        "https://10.0.0.5/doc.pdf",
        "https://169.254.169.254/latest/meta-data",
    ):
        body["documentUrl"] = url
        resp = client.post("/v1/signature-requests", json=body, headers=_headers(f"s-{url}"))
        assert resp.status_code == 400, url
        assert "documentUrl" in resp.json()["error"]


def test_webhook(client, pdf_bytes):
    created = client.post(
        "/v1/signature-requests", json=_create_body(pdf_bytes), headers=_headers("wh")
    ).json()

    # Provider-Request-ID aus der DB holen (ueber den Status-Endpoint nicht exponiert)
    from sqlmodel import select

    from app.db import SignatureRequest, session_for

    engine = client.app.state.engine
    with session_for(engine) as session:
        record = session.exec(
            select(SignatureRequest).where(SignatureRequest.id == created["id"])
        ).first()
        provider_id = record.provider_request_id

    payload = {
        "event": "DOCUMENT_COMPLETED",
        "payload": {"id": provider_id, "status": "COMPLETED"},
        "createdAt": "2026-07-19T12:00:00Z",
    }

    # Ohne/mit falschem Secret -> 401
    assert client.post("/webhooks/documenso", json=payload).status_code == 401
    assert (
        client.post(
            "/webhooks/documenso", json=payload, headers={"X-Documenso-Secret": "wrong"}
        ).status_code
        == 401
    )

    # Korrektes Secret -> Status-Update
    resp = client.post(
        "/webhooks/documenso", json=payload, headers={"X-Documenso-Secret": "hook-secret"}
    )
    assert resp.status_code == 200

    with session_for(engine) as session:
        record = session.exec(
            select(SignatureRequest).where(SignatureRequest.id == created["id"])
        ).first()
        assert record.status == "completed"

    # Idempotenz: identische Payload -> duplicate
    resp = client.post(
        "/webhooks/documenso", json=payload, headers={"X-Documenso-Secret": "hook-secret"}
    )
    assert resp.json().get("duplicate") is True


def test_production_safety_guard(tmp_path):
    import pytest

    from app.config import Settings, enforce_production_safety

    s = Settings()
    s.APP_ENV = "production"
    s.X402_BYPASS_FOR_LOCAL_DEVELOPMENT = True
    s.SIGNATURE_PROVIDER = "documenso"
    with pytest.raises(SystemExit):
        enforce_production_safety(s)

    s2 = Settings()
    s2.APP_ENV = "production"
    s2.X402_BYPASS_FOR_LOCAL_DEVELOPMENT = False
    s2.SIGNATURE_PROVIDER = "fake"
    with pytest.raises(SystemExit):
        enforce_production_safety(s2)

    s3 = Settings()
    s3.APP_ENV = "production"
    s3.X402_BYPASS_FOR_LOCAL_DEVELOPMENT = False
    s3.SIGNATURE_PROVIDER = "documenso"
    enforce_production_safety(s3)  # darf NICHT exiten


def test_retention_purge(tmp_path, client, pdf_bytes):
    from datetime import timedelta

    from sqlmodel import select

    from app.cleanup import purge_old_requests
    from app.db import SignatureRequest, session_for, utcnow

    created = client.post(
        "/v1/signature-requests", json=_create_body(pdf_bytes), headers=_headers("purge")
    ).json()
    engine = client.app.state.engine

    # Frisch -> wird nicht geloescht
    assert purge_old_requests(engine, purge_after_days=30) == 0

    # Alt + terminal -> wird geloescht
    with session_for(engine) as session:
        record = session.exec(
            select(SignatureRequest).where(SignatureRequest.id == created["id"])
        ).first()
        record.status = "completed"
        record.created_at = utcnow() - timedelta(days=60)
        session.add(record)
        session.commit()
    assert purge_old_requests(engine, purge_after_days=30) == 1
