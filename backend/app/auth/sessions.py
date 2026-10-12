"""Signed-in browsers.

The cookie carries a random token; the database keeps only its SHA-256, so a
copy of the database cannot be used to sign in. A session ends after
IDLE without a request, or ABSOLUTE after sign-in, whichever comes first.
Deleting the rows signs a user out everywhere at once.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, User, utcnow

COOKIE = "vt_session"
IDLE = timedelta(hours=12)
ABSOLUTE = timedelta(days=7)
# last_seen_at is written at most this often; the call screen polls every 1.5 s.
TOUCH_EVERY = timedelta(minutes=1)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(db: Session, user: User, *, ip: str | None, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(AuthSession(
        user_id=user.id, token_sha256=_digest(token), created_at=now,
        last_seen_at=now, expires_at=now + ABSOLUTE,
        ip=(ip or "")[:64] or None, user_agent=(user_agent or "")[:255] or None,
    ))
    return token


def lookup(db: Session, token: str | None) -> User | None:
    """The user behind a cookie, or None if it is unknown or has expired."""
    if not token or len(token) > 128:
        return None
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_sha256 == _digest(token))
    ).first()
    if row is None:
        return None
    session, user = row
    now = utcnow()
    if now >= session.expires_at or now - session.last_seen_at >= IDLE:
        db.delete(session)
        return None
    if now - session.last_seen_at >= TOUCH_EVERY:
        session.last_seen_at = now
    return user


def revoke(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_sha256 == _digest(token)))


def revoke_all(db: Session, user_id: int) -> None:
    db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))
