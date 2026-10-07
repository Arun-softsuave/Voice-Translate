"""Session registry and the single audio routing rule.

Design doc §8/§9. THE rule, stated once and implemented once:

    Audio received on leg X is played only into the peer of X.

`route_audio` is the only function in the codebase that writes call audio to a
Twilio stream. Nothing else may do it. That is what makes cross-talk between
concurrent calls structurally impossible rather than merely unlikely.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Iterable

from app.models.session import (
    CallStatus,
    Participant,
    ParticipantId,
    TranslationSession,
)
from app.utils import audio
from app.websocket import twilio_protocol as proto

log = logging.getLogger(__name__)

# How long an ended call stays readable, so the browser can learn *why* it
# ended (busy, no answer, ...) after its own leg has already been hung up.
ENDED_TTL_S = 120.0


class SessionRegistry:
    """In-process session store.

    Deliberately not Redis (design doc §9): a single process holds the live
    WebSockets, so distributing the state buys nothing until we also solve
    sticky routing (§17).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, TranslationSession] = {}
        self._ended: dict[str, TranslationSession] = {}

    # --- lifecycle --------------------------------------------------------
    def create(
        self,
        *,
        source_language: str,
        target_language: str,
        participant_b_kind: str = "pstn",
        phone_number: str | None = None,
    ) -> TranslationSession:
        session_id = "sess_" + secrets.token_urlsafe(16)
        session = TranslationSession(
            session_id=session_id,
            a=Participant(
                participant_id=ParticipantId.A,
                language=source_language,
                kind="browser",
            ),
            b=Participant(
                participant_id=ParticipantId.B,
                language=target_language,
                kind=participant_b_kind,
                phone_number=phone_number,
            ),
        )
        self._sessions[session_id] = session
        self._purge_ended()
        log.info(
            "session_created",
            extra={
                "session_id": session_id,
                "a_lang": source_language,
                "b_lang": target_language,
                "b_kind": participant_b_kind,
            },
        )
        return session

    def get(self, session_id: str) -> TranslationSession | None:
        return self._sessions.get(session_id)

    def all(self) -> Iterable[TranslationSession]:
        return list(self._sessions.values())

    def remove(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session:
            session.captions.close()
            _discard_prewarmed(session)
            log.info("session_removed", extra={"session_id": session_id,
                                                **session.snapshot()["participants"]})

    def end(self, session: TranslationSession, reason: str) -> None:
        """Finish a call but keep it readable for ENDED_TTL_S.

        Live lookups (`get`, used by TwiML, media streams and captions) stop
        finding it immediately; only `get_ended` still does, for the status
        endpoint the browser polls.
        """
        session.status = CallStatus.ENDED
        if session.end_reason is None:
            session.end_reason = reason
        session.ended_at = time.monotonic()
        self.remove(session.session_id)
        self._ended[session.session_id] = session
        self._purge_ended()

    def get_ended(self, session_id: str) -> TranslationSession | None:
        self._purge_ended()
        return self._ended.get(session_id)

    def _purge_ended(self) -> None:
        cutoff = time.monotonic() - ENDED_TTL_S
        for sid in [k for k, s in self._ended.items() if (s.ended_at or 0) < cutoff]:
            self._ended.pop(sid, None)

    def find_by_call_sid(self, call_sid: str) -> tuple[TranslationSession, ParticipantId] | None:
        for session in self._sessions.values():
            for participant in (session.a, session.b):
                if participant.call_sid == call_sid:
                    return session, participant.participant_id
        return None

    # --- stream binding ---------------------------------------------------
    def bind_stream(
        self,
        session: TranslationSession,
        pid: ParticipantId,
        *,
        stream_sid: str,
        call_sid: str | None,
        ws,
        echo_mode: bool = False,
    ) -> Participant:
        participant = session.participant(pid)
        if participant.connected:
            raise ValueError(
                f"participant {pid.value} of {session.session_id} already bound"
            )
        participant.stream_sid = stream_sid
        participant.ws = ws
        participant.bound_at = time.monotonic()
        if call_sid:
            participant.call_sid = call_sid
        # In echo mode there is only ever one leg, and it is the whole call.
        if session.both_connected or echo_mode:
            session.status = CallStatus.CONNECTED
        log.info(
            "stream_bound",
            extra={
                "session_id": session.session_id,
                "participant": pid.value,
                "stream_sid": stream_sid,
                "status": session.status.value,
            },
        )
        return participant

    def unbind_stream(self, session: TranslationSession, pid: ParticipantId) -> None:
        participant = session.participant(pid)
        participant.ws = None
        participant.stream_sid = None
        if session.status not in (CallStatus.ENDED, CallStatus.ERROR):
            session.status = CallStatus.ENDING
        log.info(
            "stream_unbound",
            extra={"session_id": session.session_id, "participant": pid.value},
        )


def _discard_prewarmed(session: TranslationSession) -> None:
    """Close translators opened for a leg whose stream never arrived.

    A declined or unanswered call ends before Twilio streams the phone leg, so
    a translator prepared for it would otherwise stay connected to the model.
    """
    tasks, session.translator_tasks = session.translator_tasks, {}
    for task in tasks.values():
        if not task.done():
            task.cancel()
            continue
        if task.cancelled() or task.exception() is not None:
            continue
        translator = task.result()
        if translator is not None:
            try:
                asyncio.get_running_loop().create_task(translator.close())
            except RuntimeError:            # no loop: nothing left to close with
                pass


# --- the routing rule -------------------------------------------------------
async def route_audio(
    session: TranslationSession,
    source: ParticipantId,
    payload_b64: str,
    *,
    echo_mode: bool = False,
) -> bool:
    """Play audio produced by `source` into the correct leg.

    Normal operation: into the PEER of `source`, never back into `source`.
    Echo mode (milestone 2 only): back into `source`, to prove that
    bidirectional injection works before a second leg exists.
    """
    destination = (
        session.participant(source) if echo_mode else session.peer_of(source)
    )

    if not destination.connected:
        destination.dropped_no_peer += 1
        return False

    sent = await proto.send(
        destination.ws, proto.build_media(destination.stream_sid, payload_b64)
    )
    if sent:
        destination.frames_out += 1
        destination.queued_ms += audio.duration_ms(audio.b64_decode(payload_b64))
    return sent


async def clear_playback(session: TranslationSession, target: ParticipantId) -> float:
    """Barge-in: drop everything buffered for `target` (design doc §8.2).

    Returns how many milliseconds of queued audio were thrown away.
    """
    participant = session.participant(target)
    if not participant.connected:
        return 0.0
    await proto.send(participant.ws, proto.build_clear(participant.stream_sid))
    dropped = participant.queued_ms - participant.played_ms
    participant.queued_ms = participant.played_ms
    log.info(
        "playback_cleared",
        extra={
            "session_id": session.session_id,
            "participant": target.value,
            "dropped_ms": round(max(dropped, 0.0)),
        },
    )
    return max(dropped, 0.0)


async def send_mark(
    session: TranslationSession, target: ParticipantId, name: str
) -> None:
    participant = session.participant(target)
    if participant.connected:
        await proto.send(participant.ws, proto.build_mark(participant.stream_sid, name))


registry = SessionRegistry()
