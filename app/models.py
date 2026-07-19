"""Pydantic-v2-Modelle (Strikter Modus) fuer Requests & Responses."""

from __future__ import annotations

import base64
import binascii
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class Signer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120, description="Full name of the signer")
    email: EmailStr = Field(description="Email address the signature request is sent to")


class SignatureRequestCreate(BaseModel):
    """Request-Body fuer POST /v1/signature-requests.

    Exakt EINES der Felder documentUrl / documentBase64 muss gesetzt sein.
    """

    model_config = ConfigDict(extra="forbid")

    documentName: str = Field(min_length=1, max_length=150)
    documentUrl: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="HTTPS URL of the PDF to be signed (max 5 MB)",
    )
    documentBase64: Optional[str] = Field(
        default=None,
        description="Base64-encoded PDF (max 5 MB after decoding)",
    )
    signer: Signer
    message: Optional[str] = Field(default=None, max_length=2000)
    expiresInDays: int = Field(default=14, ge=1, le=30)
    externalReference: Optional[str] = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _exactly_one_document_source(self) -> "SignatureRequestCreate":
        if bool(self.documentUrl) == bool(self.documentBase64):
            raise ValueError(
                "Exactly one of 'documentUrl' or 'documentBase64' must be provided"
            )
        if self.documentUrl and not self.documentUrl.lower().startswith("https://"):
            raise ValueError("documentUrl must be an https:// URL")
        return self

    def decoded_document(self, max_bytes: int) -> Optional[bytes]:
        """Dekodiert documentBase64 (falls gesetzt) mit hartem Groessenlimit."""
        if self.documentBase64 is None:
            return None
        # Grobe Vorab-Pruefung: Base64 blaeht ~4/3 auf.
        if len(self.documentBase64) > (max_bytes * 4) // 3 + 16:
            raise ValueError("documentBase64 exceeds the size limit")
        try:
            data = base64.b64decode(self.documentBase64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("documentBase64 is not valid base64") from exc
        if len(data) > max_bytes:
            raise ValueError("documentBase64 exceeds the size limit after decoding")
        return data


class SignerPublic(BaseModel):
    name: str
    email: str  # maskiert, z.B. a***@example.com


class SignatureRequestPublic(BaseModel):
    id: str
    status: str
    documentName: str
    signer: SignerPublic
    createdAt: str
    expiresAt: str
    completedAt: Optional[str] = None
    externalReference: Optional[str] = None
    statusUrl: str
    accessToken: Optional[str] = Field(
        default=None,
        description="Plain-text bearer token — returned exactly once at creation",
    )
