"""x402-Middleware aktiv (kein Bypass): 402-Verhalten + Routen-Abgrenzung.

Nutzt einen Mock-Facilitator, damit keine echten Zahlungen/Netzwerkzugriffe
noetig sind (Konvention aus den bestehenden Services).
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import dev_settings
from .mock_facilitator import app as facilitator_app


@pytest.fixture(scope="module")
def facilitator_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(facilitator_app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def paid_client(tmp_path, facilitator_url):
    settings = dev_settings(
        tmp_path,
        X402_BYPASS_FOR_LOCAL_DEVELOPMENT=False,
        PAY_TO_ADDRESS="0xE72b85A97A6e19413D8b80633787Eda6d6237A77",
        NETWORK="eip155:8453",
        PRICE="$0.50",
        FACILITATOR_URL=facilitator_url,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def test_unpaid_post_returns_402_v1_body(paid_client):
    resp = paid_client.post(
        "/v1/signature-requests",
        json={"documentName": "x"},
        headers={"Idempotency-Key": "k"},
    )
    assert resp.status_code == 402
    body = resp.json()
    assert body["x402Version"] == 1
    accepts = body["accepts"][0]
    assert accepts["scheme"] == "exact"
    assert accepts["network"] == "base"
    assert accepts["maxAmountRequired"] == "500000"  # $0.50 in atomaren USDC-Einheiten
    assert accepts["payTo"] == "0xE72b85A97A6e19413D8b80633787Eda6d6237A77"
    assert accepts["asset"] == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC auf Base
    assert accepts["resource"].endswith("/v1/signature-requests")
    assert "outputSchema" in accepts


def test_get_probe_also_402(paid_client):
    resp = paid_client.get("/v1/signature-requests")
    assert resp.status_code == 402


def test_status_routes_not_paywalled(paid_client):
    # Detail-Routen duerfen NICHT hinter der Paywall liegen (Bearer-Auth statt 402)
    resp = paid_client.get("/v1/signature-requests/sigreq_abc123")
    assert resp.status_code == 401
    resp = paid_client.get("/v1/signature-requests/sigreq_abc123/document")
    assert resp.status_code == 401


def test_free_endpoints_stay_free(paid_client):
    assert paid_client.get("/health").status_code == 200
    assert paid_client.get("/").status_code == 200
    assert paid_client.get("/v1/schema").status_code == 200
    assert paid_client.get("/agent-tool.json").status_code == 200
