# x402-signature-service

**Agent-native e-signature API, paid per call with USDC via [x402](https://x402.org).**

Send one PDF to one email recipient for simple electronic signature. The service
emails the signer, tracks completion, and returns the completed signed PDF —
giving AI agents the missing capability to get human sign-off on contracts,
NDAs and agreements, fully autonomously.

> ## ⚠️ Disclaimer
> **This MVP facilitates simple electronic signatures through the configured
> signature provider. Users are responsible for determining whether this
> signature type is appropriate for their document, jurisdiction, and use case.**
>
> Simple electronic signatures (SES) only — no qualified (QES) or advanced (AES)
> signatures, no identity verification, no legal advice. Signatures are executed
> by [Documenso](https://documenso.com).

## How it works

```
Agent                        Service                       Human signer
  │ POST /v1/signature-requests │                              │
  │──── 402 Payment Required ───│                              │
  │─ pay $0.50 USDC (Base) ────►│                              │
  │◄─ 201 {id, accessToken} ────│──── signature email ────────►│
  │                             │                              │ signs in browser
  │ GET statusUrl (Bearer) ────►│◄──── webhook: completed ─────│
  │◄─ {status: "completed"} ────│                              │
  │ GET statusUrl/document ────►│                              │
  │◄──────── signed PDF ────────│                              │
```

## API

| Method & Path | Payment | Auth | Purpose |
|---|---|---|---|
| `GET /health` | free | — | Healthcheck |
| `GET /` | free | — | Landing page |
| `GET /v1/schema` | free | — | Machine-readable schema & examples |
| `GET /agent-tool.json` | free | — | LLM tool definition (`send_document_for_signature`) |
| `POST /v1/signature-requests` | **x402, $0.50 USDC** | `Idempotency-Key` header | Create & send a signature request |
| `GET /v1/signature-requests/{id}` | free | `Authorization: Bearer <accessToken>` | Poll status (`sent → viewed → completed/declined/expired`) |
| `GET /v1/signature-requests/{id}/document` | free | Bearer token | Download the signed PDF (only when `completed`) |
| `POST /webhooks/documenso` | free | `X-Documenso-Secret` | Provider status webhook |

### Create request — body

```json
{
  "documentName": "Service Agreement",
  "documentUrl": "https://example.com/agreement.pdf",
  "signer": { "name": "Anna Müller", "email": "anna@example.com" },
  "message": "Please review and sign this agreement.",
  "expiresInDays": 14,
  "externalReference": "customer-contract-4821"
}
```

Exactly **one** of `documentUrl` (https only, max 5 MB) or `documentBase64`
(max 5 MB decoded) must be provided. Exactly one signer. `expiresInDays`: 1–30.

The `201` response contains a **one-time `accessToken`** (`sec_…`). Only its
SHA-256 hash is stored server-side — save the token, it is never shown again.

## Security & privacy

* **SSRF guard** for `documentUrl`: https-only, DNS resolved *before* connect,
  private/loopback/link-local/reserved IPs rejected, connection pinned to the
  verified IP (DNS-rebinding protection), max 3 redirects (each re-verified),
  hard 5 MB streaming byte limit.
* **Data minimization**: PDFs are processed in memory and never logged; signer
  emails are masked in logs (`a***@example.com`); access tokens stored as
  SHA-256 hashes only; completed/expired requests are purged after
  `PURGE_AFTER_DAYS` (default 30) — automatically and via `python -m app.cleanup`.
* **Production safety anchor**: with `APP_ENV=production`, the app refuses to
  start (`sys.exit(1)`) if `SIGNATURE_PROVIDER=fake` or
  `X402_BYPASS_FOR_LOCAL_DEVELOPMENT=true` is set.
* Webhooks are authenticated via constant-time comparison of the
  `X-Documenso-Secret` header (Documenso's verification scheme).

## Local development (no payments, no Documenso)

```bash
cp .env.example .env
pip install -r requirements-dev.txt

SIGNATURE_PROVIDER=fake \
X402_BYPASS_FOR_LOCAL_DEVELOPMENT=true \
uvicorn app.main:app --reload

pytest            # 15 tests: validation, SSRF, auth, idempotency, webhook, 402 routing
```

Full paid-flow E2E against a mock facilitator (no blockchain needed):

```bash
uvicorn tests.mock_facilitator:app --port 8402 &
SIGNATURE_PROVIDER=fake FAKE_AUTOSIGN_SECONDS=0 \
  FACILITATOR_URL=http://127.0.0.1:8402 uvicorn app.main:app --port 8000 &
python tests/e2e_paid_flow.py
```

## Deployment (Render)

1. Push this repo to GitHub, then **Render → New + → Blueprint** (uses `render.yaml`).
2. After the first deploy, set `BASE_URL` to the public URL and redeploy.
3. Set `DOCUMENSO_API_TOKEN` (+ create a webhook in Documenso pointing to
   `<BASE_URL>/webhooks/documenso` with `DOCUMENSO_WEBHOOK_SECRET`).
4. Verify: `GET /health` → 200, `GET /v1/signature-requests` → 402 with payment
   requirements.
5. Register on [x402scan](https://www.x402scan.com/resources/register) with the
   resource URL `<BASE_URL>/v1/signature-requests`. For automatic
   [x402 Bazaar](https://docs.cdp.coinbase.com/x402/bazaar) indexing, switch
   `FACILITATOR_URL` to the CDP facilitator and set `CDP_API_KEY_ID/SECRET`.

## Configuration

See [.env.example](.env.example) for the full, commented list
(payment, facilitator, provider, limits, retention).

## Stack

Python 3.12 · FastAPI · Pydantic v2 · SQLModel/SQLite · httpx · pypdf ·
x402 SDK ≥ 2.15 · Documenso API v1 · structured JSON logging.
