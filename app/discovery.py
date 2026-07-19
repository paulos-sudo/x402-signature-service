"""Discovery-Metadaten fuer x402 Bazaar & x402scan.

Qualitaet dieser Metadaten bestimmt das Ranking im Bazaar-Index:
semantische Beschreibung, sauberes inputSchema/outputSchema und
realistische Beispiele.
"""

DESCRIPTION = (
    "Send one PDF to one email recipient for simple electronic signature. "
    "The service emails the signer, tracks completion, and returns the completed "
    "signed PDF. Give an AI agent the missing capability to get human sign-off: "
    "submit a contract, NDA or agreement by URL or base64, the recipient signs "
    "in the browser (powered by Documenso), and the agent polls a status URL "
    "with a one-time bearer token until the signed document is ready for "
    "download. Simple electronic signatures (SES) only — no QES/AES, no "
    "identity verification, no legal advice. Documents are processed in memory; "
    "signer emails are masked in logs."
)

TOOL_NAME = "send_document_for_signature"

EXAMPLE_INPUT = {
    "documentName": "Service Agreement",
    "documentUrl": "https://example.com/agreement.pdf",
    "signer": {"name": "Anna Müller", "email": "anna@example.com"},
    "message": "Please review and sign this agreement.",
    "expiresInDays": 14,
    "externalReference": "customer-contract-4821",
}

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "documentName": {
            "type": "string",
            "minLength": 1,
            "maxLength": 150,
            "description": "Human-readable document title shown to the signer",
        },
        "documentUrl": {
            "type": "string",
            "description": "HTTPS URL of the PDF to sign (max 5 MB). Provide either documentUrl OR documentBase64.",
        },
        "documentBase64": {
            "type": "string",
            "description": "Base64-encoded PDF (max 5 MB decoded). Provide either documentUrl OR documentBase64.",
        },
        "signer": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "email": {"type": "string", "format": "email"},
            },
            "required": ["name", "email"],
        },
        "message": {
            "type": "string",
            "maxLength": 2000,
            "description": "Optional message included in the signature request email",
        },
        "expiresInDays": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "default": 14,
            "description": "Days until the signature request expires",
        },
        "externalReference": {
            "type": "string",
            "maxLength": 200,
            "description": "Optional caller-side reference id, echoed back in responses",
        },
    },
    "required": ["documentName", "signer"],
    "oneOf": [
        {"required": ["documentUrl"], "not": {"required": ["documentBase64"]}},
        {"required": ["documentBase64"], "not": {"required": ["documentUrl"]}},
    ],
}

EXAMPLE_OUTPUT = {
    "id": "sigreq_1f4a9c2b7d3e8a01",
    "status": "sent",
    "documentName": "Service Agreement",
    "signer": {"name": "Anna Müller", "email": "a***@example.com"},
    "createdAt": "2026-07-19T15:00:00Z",
    "expiresAt": "2026-08-02T15:00:00Z",
    "statusUrl": "https://your-api.example.com/v1/signature-requests/sigreq_1f4a9c2b7d3e8a01",
    "accessToken": "sec_… (plain-text bearer token, shown exactly once)",
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["sent", "viewed", "completed", "declined", "expired", "error"],
        },
        "documentName": {"type": "string"},
        "signer": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string", "description": "masked, e.g. a***@example.com"},
            },
        },
        "createdAt": {"type": "string"},
        "expiresAt": {"type": "string"},
        "statusUrl": {
            "type": "string",
            "description": "Poll this URL with 'Authorization: Bearer <accessToken>' to track progress; append /document once completed to download the signed PDF.",
        },
        "accessToken": {
            "type": "string",
            "description": "One-time plain-text bearer token for statusUrl and document download. Store it — it is never shown again.",
        },
    },
    "required": ["id", "status", "statusUrl", "accessToken"],
}
