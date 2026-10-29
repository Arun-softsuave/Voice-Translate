"""Tables: who may use the app, who is signed in, and what was said on calls.

Times are naive UTC datetimes. The schema itself is created and changed only
by the Alembic migrations in backend/alembic/; these classes must match them.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# users.role
USER = "user"
ADMIN = "admin"

# users.status
PENDING = "pending"        # asked for access, waiting for an admin
ACTIVE = "active"          # may sign in
REJECTED = "rejected"      # an admin said no; may ask again
DISABLED = "disabled"      # was active, turned off by an admin

# calls.status
IN_PROGRESS = "in_progress"
ENDED = "ended"
FAILED = "failed"          # never placed: Twilio refused the call


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default=USER)
    status: Mapped[str] = mapped_column(String(16), default=PENDING)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None)


class AuthSession(Base):
    """A signed-in browser. The cookie holds a token; only its hash is kept."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         index=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (Index("ix_calls_user_started", "user_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    phone_number: Mapped[str | None] = mapped_column(String(32), default=None)
    source_lang: Mapped[str] = mapped_column(String(16))
    target_lang: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=IN_PROGRESS)
    end_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    # What picked up, when Twilio was asked: human | machine_end_beep | ...
    answered_by: Mapped[str | None] = mapped_column(String(24), default=None)
    started_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    answered_at: Mapped[datetime | None] = mapped_column(default=None)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    duration_s: Mapped[float | None] = mapped_column(Float, default=None)
    model_usd: Mapped[float] = mapped_column(Float, default=0.0)


class TranscriptLine(Base):
    __tablename__ = "transcript_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"),
                                         index=True)
    line_no: Mapped[int]
    owner: Mapped[str] = mapped_column(String(1))       # A (caller) | B (callee)
    kind: Mapped[str] = mapped_column(String(8))        # heard | spoke
    lang: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    t_offset_s: Mapped[float] = mapped_column(Float)
