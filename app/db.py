"""SQLite-Persistenz via SQLModel (zustandsbehaftete Statusverfolgung)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Normalisierte Status-Werte
STATUS_SENT = "sent"
STATUS_VIEWED = "viewed"
STATUS_COMPLETED = "completed"
STATUS_DECLINED = "declined"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"

TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_DECLINED, STATUS_EXPIRED, STATUS_ERROR}


class SignatureRequest(SQLModel, table=True):
    __tablename__ = "signature_requests"

    id: str = Field(primary_key=True)
    provider: str = Field(default="documenso")
    provider_request_id: Optional[str] = Field(default=None, index=True)
    document_name: str
    original_document_hash: str  # SHA-256 des Original-PDFs
    signer_name: str
    signer_email: str
    message: Optional[str] = None
    external_reference: Optional[str] = None
    status: str = Field(default=STATUS_SENT, index=True)
    access_token_hash: str  # SHA-256 des Bearer-Tokens (nie der Klartext!)
    idempotency_key: str = Field(index=True, unique=True)
    normalized_payload_hash: str
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None
    expires_at: datetime
    last_synced_at: Optional[datetime] = None


class ProviderEvent(SQLModel, table=True):
    __tablename__ = "provider_events"
    __table_args__ = (UniqueConstraint("provider", "payload_hash", name="uq_provider_payload"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str
    provider_event_id: Optional[str] = None
    signature_request_id: Optional[str] = Field(default=None, foreign_key="signature_requests.id")
    event_type: str
    received_at: datetime = Field(default_factory=utcnow)
    processed_at: Optional[datetime] = None
    payload_hash: str  # SHA-256 des Webhook-Bodys (Idempotenz)


def make_engine(database_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(database_path)) or ".", exist_ok=True)
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def session_for(engine) -> Session:
    return Session(engine)
