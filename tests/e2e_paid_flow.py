"""End-to-End-Test des kompletten x402-Zahlungsflusses.

Nutzt einen echten Signer (Wegwerf-Key) + Mock-Facilitator:
402 -> Payment-Payload signieren -> Retry -> 201 -> Status-Poll -> Download.

Start (2 Terminals oder wie in tests/run_e2e.sh):
  1. uvicorn tests.mock_facilitator:app --port 8402
  2. SIGNATURE_PROVIDER=fake FAKE_AUTOSIGN_SECONDS=0 \
     FACILITATOR_URL=http://127.0.0.1:8402 APP_ENV=development \
     uvicorn app.main:app --port 8000
  3. python tests/e2e_paid_flow.py
"""

import asyncio
import base64
import io
import json
import time

from eth_account import Account

from x402 import x402Client
from x402.http.clients.httpx import x402HttpxClient
from x402.mechanisms.evm.exact.register import register_exact_evm_client

BASE_URL = "http://127.0.0.1:8000"


def _make_pdf() -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "E2E Service Agreement")
    c.showPage()
    c.save()
    return buf.getvalue()


async def main() -> None:
    account = Account.create()
    print("payer (throwaway):", account.address)

    payload = {
        "documentName": "E2E Service Agreement",
        "documentBase64": base64.b64encode(_make_pdf()).decode(),
        "signer": {"name": "Anna Müller", "email": "anna@example.com"},
        "message": "Please review and sign.",
        "expiresInDays": 7,
    }

    client = x402Client()
    register_exact_evm_client(client, account)

    t0 = time.time()
    async with x402HttpxClient(client, base_url=BASE_URL) as http:
        resp = await http.post(
            "/v1/signature-requests",
            json=payload,
            headers={"Idempotency-Key": "e2e-1"},
            timeout=60,
        )
        body = await resp.aread()
        print("HTTP", resp.status_code, f"in {time.time() - t0:.2f}s")
        assert resp.status_code == 201, body[:500]
        data = json.loads(body)
        print("created:", data["id"], "| status:", data["status"], "| signer:", data["signer"])
        token = data["accessToken"]
        assert token.startswith("sec_")

        settle_hdr = resp.headers.get("PAYMENT-RESPONSE") or resp.headers.get("X-PAYMENT-RESPONSE")
        if settle_hdr:
            settle = json.loads(base64.b64decode(settle_hdr))
            print("settlement:", {k: settle.get(k) for k in ("success", "transaction", "network", "payer")})

    # Status + Download laufen OHNE Zahlung, nur mit Bearer-Token
    import httpx

    async with httpx.AsyncClient(base_url=BASE_URL) as plain:
        resp = await plain.get(
            f"/v1/signature-requests/{data['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        status = resp.json()
        print("status:", status["status"])
        assert status["status"] == "completed"

        resp = await plain.get(
            f"/v1/signature-requests/{data['id']}/document",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/pdf")
        assert resp.content.startswith(b"%PDF-")
        print("signed PDF:", len(resp.content), "bytes")

    print("E2E PAID FLOW OK")


asyncio.run(main())
