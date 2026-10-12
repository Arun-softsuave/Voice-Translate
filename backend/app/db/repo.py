"""Queries shared by the history and admin pages."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Call, TranscriptLine, User


def normalise_email(email: str) -> str:
    return email.strip().lower()


def user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalise_email(email)))


def utc(value: datetime | None) -> datetime | None:
    """A time from a query string, as the naive UTC the database holds."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    """UTC, marked as such, so the browser shows it in local time."""
    return value.isoformat(timespec="seconds") + "Z" if value else None


def user_public(user: User, *, calls: int = 0, last_call_at: datetime | None = None) -> dict:
    return {
        "id": user.id, "name": user.name, "email": user.email,
        "role": user.role, "status": user.status,
        "created_at": iso(user.created_at), "approved_at": iso(user.approved_at),
        "calls": calls, "last_call_at": iso(last_call_at),
    }


def _call_public(call: Call, user: User, lines: int) -> dict:
    return {
        "id": call.id,
        "user": {"id": user.id, "name": user.name, "email": user.email},
        "phone_number": call.phone_number,
        "source_lang": call.source_lang, "target_lang": call.target_lang,
        "status": call.status, "end_reason": call.end_reason,
        "started_at": iso(call.started_at), "answered_at": iso(call.answered_at),
        "ended_at": iso(call.ended_at), "duration_s": call.duration_s,
        "model_usd": round(call.model_usd or 0.0, 4),
        "lines": lines,
    }


def _line_counts():
    return (select(TranscriptLine.call_id, func.count().label("n"))
            .group_by(TranscriptLine.call_id).subquery())


def list_calls(db: Session, *, user_id: int | None = None, q: str = "",
               date_from: datetime | None = None, date_to: datetime | None = None,
               limit: int = 50, offset: int = 0) -> dict:
    """Newest first. `user_id` limits to one person's calls; `q` searches the
    caller's name or email and the number dialled."""
    counts = _line_counts()
    query = (select(Call, User, func.coalesce(counts.c.n, 0))
             .join(User, User.id == Call.user_id)
             .outerjoin(counts, counts.c.call_id == Call.id))
    if user_id is not None:
        query = query.where(Call.user_id == user_id)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(User.name).like(like),
                                func.lower(User.email).like(like),
                                Call.phone_number.like(like)))
    if date_from is not None:
        query = query.where(Call.started_at >= date_from)
    if date_to is not None:
        query = query.where(Call.started_at < date_to)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(Call.started_at.desc(), Call.id.desc())
                      .limit(limit).offset(offset)).all()
    return {"total": total, "items": [_call_public(c, u, n) for c, u, n in rows]}


def call_detail(db: Session, call_id: int, *, user_id: int | None = None) -> dict | None:
    """One call and everything said on it; None if missing or not `user_id`'s."""
    row = db.execute(select(Call, User).join(User, User.id == Call.user_id)
                     .where(Call.id == call_id)).first()
    if row is None or (user_id is not None and row[0].user_id != user_id):
        return None
    call, user = row
    lines = db.scalars(select(TranscriptLine).where(TranscriptLine.call_id == call.id)
                       .order_by(TranscriptLine.t_offset_s, TranscriptLine.line_no)).all()
    detail = _call_public(call, user, len(lines))
    detail["transcript"] = [
        {"id": l.line_no, "owner": l.owner, "kind": l.kind, "lang": l.lang,
         "text": l.text, "t": l.t_offset_s}
        for l in lines
    ]
    return detail
