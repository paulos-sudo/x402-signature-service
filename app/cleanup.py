"""Retention/Datensparsamkeit: alte Requests hart aus SQLite loeschen.

Loescht Requests, deren Status terminal ist (completed/declined/expired/error)
ODER die abgelaufen sind, sobald sie aelter als PURGE_AFTER_DAYS sind.

Nutzung als Skript:  python -m app.cleanup
(läuft zusaetzlich automatisch alle 24h im Server-Prozess)
"""

from __future__ import annotations

from datetime import timedelta

from sqlmodel import delete, select

from .db import TERMINAL_STATUSES, ProviderEvent, SignatureRequest, session_for, utcnow


def purge_old_requests(engine, purge_after_days: int) -> int:
    cutoff = utcnow() - timedelta(days=purge_after_days)
    removed = 0
    with session_for(engine) as session:
        rows = session.exec(select(SignatureRequest)).all()
        for row in rows:
            created = row.created_at
            expires = row.expires_at
            if created.tzinfo is None:
                from datetime import timezone

                created = created.replace(tzinfo=timezone.utc)
                expires = expires.replace(tzinfo=timezone.utc) if expires.tzinfo is None else expires
            is_old = created < cutoff
            is_terminal = row.status in TERMINAL_STATUSES
            is_expired = expires < utcnow()
            if is_old and (is_terminal or is_expired):
                session.exec(
                    delete(ProviderEvent).where(ProviderEvent.signature_request_id == row.id)
                )
                session.delete(row)
                removed += 1
        session.commit()
    return removed


if __name__ == "__main__":
    from .config import load_settings
    from .db import make_engine

    settings = load_settings()
    engine = make_engine(settings.DATABASE_PATH)
    n = purge_old_requests(engine, settings.PURGE_AFTER_DAYS)
    print(f"purged {n} request(s)")
