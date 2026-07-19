"""Lokaler Mock-Facilitator fuer Tests ohne Blockchain (Konvention der
bestehenden Services). Simuliert /supported, /verify und /settle."""

from fastapi import FastAPI, Request

app = FastAPI()


@app.get("/supported")
async def supported():
    return {
        "kinds": [
            {"x402Version": 2, "scheme": "exact", "network": "eip155:8453"},
            {"x402Version": 2, "scheme": "exact", "network": "eip155:84532"},
        ],
        "extensions": ["bazaar"],
        "signers": {},
    }


@app.post("/verify")
async def verify(request: Request):
    return {"isValid": True, "payer": "0xAgentAgentAgentAgentAgentAgentAgentAgent"}


@app.post("/settle")
async def settle(request: Request):
    return {
        "success": True,
        "payer": "0xAgentAgentAgentAgentAgentAgentAgentAgent",
        "transaction": "0x" + "ab" * 32,
        "network": "eip155:8453",
        "amount": "500000",
    }
