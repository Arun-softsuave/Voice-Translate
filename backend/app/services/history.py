"""Saving calls and what was said on them.

Every function here is called from the live call path, so none may raise:
a database problem is logged as `history_write_failed` and the call goes on.
Only sessions with an owner (`user_id`) are saved. What was said is never
logged, only stored.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable

from sqlalchemy import update

from app.db.engine import session_scope
from app.db.models import ENDED, FAILED, IN_PROGRESS, Call, TranscriptLine, utcnow
from app.services.captions import HEARD, PRESSED, Line

if TYPE_CHECKING:  # pragma: no cover
    from app.models.session import TranslationSession

log = logging.getLogger(__name__)


def _failed(step: str, session: "TranslationSession") -> None:
    log.exception("history_write_failed",
                  extra={"step": step, "session_id": session.session_id})


def _wall(session: "TranslationSession", monotonic: float | None) -> datetime | None:
    if monotonic is None:
        return None
    return session.started_wall + timedelta(seconds=monotonic - session.created_at)


def record_start(session: "TranslationSession") -> None:
    if session.user_id is None:
        return
    try:
        with session_scope() as db:
            call = Call(
                session_id=session.session_id, user_id=session.user_id,
                phone_number=session.b.phone_number,
                source_lang=session.a.language, target_lang=session.b.language,
                status=IN_PROGRESS, started_at=session.started_wall,
            )
            db.add(call)
            db.flush()
            session.call_id = call.id
    except Exception:  # noqa: BLE001
        _failed("start", session)


def line_sink(session: "TranslationSession") -> Callable[[Line], None]:
    """Saves each finished caption line of `session`."""

    def save(line: Line) -> None:
        text = line.text.strip()
        if session.call_id is None or not text:
            return
        owner = session.participant(line.owner)
        if line.kind == PRESSED:
            lang = ""                       # keypad digits are in no language
        elif line.kind == HEARD:
            lang = owner.language
        else:
            # A translator speaks the other person's language (see view()).
            lang = session.peer_of(line.owner).language
        try:
            with session_scope() as db:
                db.add(TranscriptLine(
                    call_id=session.call_id, line_no=line.id,
                    owner=line.owner.value, kind=line.kind, lang=lang,
                    text=text, t_offset_s=line.t,
                ))
        except Exception:  # noqa: BLE001
            _failed("line", session)

    return save


def record_end(session: "TranslationSession") -> None:
    if session.call_id is None:
        return
    b = session.b
    answered = _wall(session, b.answered_at if b.answered_at is not None else b.bound_at)
    ended = _wall(session, session.ended_at) or utcnow()
    try:
        with session_scope() as db:
            db.execute(update(Call).where(Call.id == session.call_id).values(
                status=ENDED, end_reason=session.end_reason,
                answered_at=answered, ended_at=ended,
                duration_s=round((ended - answered).total_seconds(), 1) if answered else None,
            ))
    except Exception:  # noqa: BLE001
        _failed("end", session)


def record_failed(session: "TranslationSession", reason: str) -> None:
    """The call was never placed (Twilio refused it)."""
    if session.call_id is None:
        return
    try:
        with session_scope() as db:
            db.execute(update(Call).where(Call.id == session.call_id).values(
                status=FAILED, end_reason=reason[:32], ended_at=utcnow()))
    except Exception:  # noqa: BLE001
        _failed("failed", session)


def record_answered_by(session: "TranslationSession", verdict: str | None) -> None:
    """A person or a machine picked up, so the history can say which."""
    if session.call_id is None or not verdict:
        return
    try:
        with session_scope() as db:
            db.execute(update(Call).where(Call.id == session.call_id).values(
                answered_by=verdict[:24]))
    except Exception:  # noqa: BLE001
        _failed("answered_by", session)


def add_model_cost(session: "TranslationSession", usd: float) -> None:
    """Each leg's translator reports its own cost when it closes."""
    if session.call_id is None or not usd:
        return
    try:
        with session_scope() as db:
            db.execute(update(Call).where(Call.id == session.call_id)
                       .values(model_usd=Call.model_usd + round(usd, 6)))
    except Exception:  # noqa: BLE001
        _failed("cost", session)


def close_orphans() -> int:
    """Calls still open from before a restart can never finish now."""
    with session_scope() as db:
        result = db.execute(
            update(Call).where(Call.ended_at.is_(None)).values(
                status=ENDED, end_reason="server_restart", ended_at=utcnow()))
        return result.rowcount or 0
