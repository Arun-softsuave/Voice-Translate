"""Twilio Media Streams WebSocket endpoint.

Design doc §11. One connection per call leg. The connection identifies itself
in the `start` message via the custom parameters we put in the TwiML, which is
why no guessing by phone number or arrival order is ever needed.

Audio path (design doc §7):

    Twilio media frame
      -> OpenAI Realtime session for THIS participant's language
      -> translated audio
      -> session_service.route_audio -> the OPPOSITE leg

With no OpenAI key configured the translator is skipped and the original audio
is forwarded, which is what makes the echo test work before phase 7 is set up.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.models.session import CallStatus, ParticipantId
from app.services import session_service
from app.services.realtime_service import RealtimeTranslator, language_name
from app.services.session_service import registry
from app.websocket import twilio_protocol as proto

log = logging.getLogger(__name__)

MARK_EVERY_FRAMES = 25          # ~500 ms at 20 ms/frame


async def media_stream_endpoint(ws: WebSocket) -> None:
    settings = get_settings()
    await ws.accept()

    session = None
    pid: ParticipantId | None = None
    translator: RealtimeTranslator | None = None
    frames_since_mark = 0
    opened_at = time.monotonic()

    try:
        while True:
            raw = await ws.receive_text()
            message = json.loads(raw)
            event = message.get("event")

            # --- handshake ------------------------------------------------
            if event == proto.EVENT_CONNECTED:
                log.info("stream_connected", extra={"protocol": message.get("protocol")})
                continue

            if event == proto.EVENT_START:
                start = message.get("start", {})
                params = start.get("customParameters", {}) or {}
                session_id = params.get("session_id")
                participant_raw = params.get("participant")

                session = registry.get(session_id) if session_id else None
                if session is None or participant_raw not in ("A", "B"):
                    # §13: unknown session ids are rejected, not guessed at.
                    log.warning(
                        "stream_rejected",
                        extra={"reason": "unknown_session", "session_id": session_id},
                    )
                    await ws.close(code=1008)
                    return

                pid = ParticipantId(participant_raw)
                try:
                    registry.bind_stream(
                        session,
                        pid,
                        stream_sid=start.get("streamSid") or message.get("streamSid"),
                        call_sid=start.get("callSid"),
                        ws=ws,
                    )
                except ValueError:
                    log.warning(
                        "stream_rejected",
                        extra={"reason": "already_bound",
                               "session_id": session.session_id,
                               "participant": pid.value},
                    )
                    await ws.close(code=1008)
                    return

                translator = await _open_translator(settings, session, pid)
                continue

            # everything below requires a bound stream
            if session is None or pid is None:
                log.warning("stream_message_before_start", extra={"event": event})
                continue

            # --- audio ----------------------------------------------------
            if event == proto.EVENT_MEDIA:
                participant = session.participant(pid)
                participant.frames_in += 1
                payload = message.get("media", {}).get("payload")
                if not payload:
                    continue

                if session.status is CallStatus.CONNECTED:
                    session.status = CallStatus.TRANSLATING

                if translator is not None:
                    # Translated audio comes back asynchronously and is routed
                    # by the callback set up in _open_translator.
                    await translator.append_audio(payload)
                else:
                    await session_service.route_audio(
                        session, pid, payload, echo_mode=settings.echo_mode
                    )
                    frames_since_mark += 1
                    if frames_since_mark >= MARK_EVERY_FRAMES:
                        frames_since_mark = 0
                        target = pid if settings.echo_mode else pid.peer
                        await session_service.send_mark(
                            session, target, f"chk-{participant.frames_in}"
                        )
                continue

            # --- playback accounting (needed for barge-in truncation) ------
            if event == proto.EVENT_MARK:
                name = message.get("mark", {}).get("name", "")
                participant = session.participant(pid)
                participant.played_ms = min(participant.queued_ms,
                                            participant.played_ms + MARK_EVERY_FRAMES * 20)
                log.debug(
                    "mark_ack",
                    extra={"session_id": session.session_id,
                           "participant": pid.value, "mark": name},
                )
                continue

            if event == proto.EVENT_DTMF:
                log.info(
                    "dtmf",
                    extra={"session_id": session.session_id, "participant": pid.value},
                )
                continue

            if event == proto.EVENT_STOP:
                log.info(
                    "stream_stop",
                    extra={"session_id": session.session_id, "participant": pid.value},
                )
                break

            log.debug("stream_unknown_event", extra={"event": event})

    except WebSocketDisconnect:
        log.info(
            "stream_disconnected",
            extra={
                "session_id": session.session_id if session else None,
                "participant": pid.value if pid else None,
            },
        )
    except Exception:
        log.exception(
            "stream_error",
            extra={"session_id": session.session_id if session else None},
        )
    finally:
        if translator is not None:
            await translator.close()
        if session and pid:
            participant = session.participant(pid)
            log.info(
                "stream_closed",
                extra={
                    "session_id": session.session_id,
                    "participant": pid.value,
                    "duration_s": round(time.monotonic() - opened_at, 1),
                    "frames_in": participant.frames_in,
                    "frames_out": participant.frames_out,
                    "dropped_no_peer": participant.dropped_no_peer,
                    "audio_s_in": round(participant.frames_in * 20 / 1000, 1),
                },
            )
            registry.unbind_stream(session, pid)


async def _open_translator(settings, session, pid: ParticipantId):
    """Start the OpenAI session that translates THIS participant's speech.

    Its output is routed to the opposite participant — or back to the sender in
    echo mode, which is how one browser can test the whole pipeline alone.
    """
    if not settings.translation_enabled:
        log.info(
            "translation_disabled",
            extra={"session_id": session.session_id, "participant": pid.value,
                   "reason": "no OPENAI_API_KEY"},
        )
        return None

    # The language pair is ALWAYS speaker -> counterpart. Echo mode changes
    # only where the translated audio is played, never what it is translated
    # into; conflating the two makes the session translate a language into
    # itself, so everything comes back in the speaker's own language.
    speaker = session.participant(pid)
    counterpart = session.peer_of(pid)

    async def deliver(audio_b64: str) -> None:
        await session_service.route_audio(
            session, pid, audio_b64, echo_mode=settings.echo_mode
        )

    async def on_speech_started() -> None:
        """The speaker interrupted whatever they were being played."""
        await session_service.clear_playback(session, pid)

    translator = RealtimeTranslator(
        settings,
        source_language=language_name(speaker.language),
        target_language=language_name(counterpart.language),
        on_audio=deliver,
        on_speech_started=on_speech_started,
        label=f"{session.session_id[:12]}/{pid.value}",
    )

    try:
        await translator.connect()
    except Exception as exc:  # noqa: BLE001
        log.error(
            "realtime_connect_failed",
            extra={"session_id": session.session_id, "participant": pid.value,
                   "reason": type(exc).__name__, "detail": str(exc)[:200]},
        )
        return None

    return translator
