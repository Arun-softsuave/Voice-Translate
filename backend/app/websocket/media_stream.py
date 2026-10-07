"""Twilio Media Streams WebSocket endpoint.

Design doc §11. One connection per call leg. The connection identifies itself
in the `start` message via the custom parameters we put in the TwiML, which is
why no guessing by phone number or arrival order is ever needed.

Audio path (design doc §7):

    Twilio media frame
      -> translation session (OpenAI or Gemini) for THIS participant's language
      -> translated audio
      -> session_service.route_audio -> the OPPOSITE leg

With no API key for the chosen backend the translator is skipped and the original
audio is forwarded, which is what makes the echo test work before phase 7 is set up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.models.session import CallStatus, ParticipantId
from app.services import session_service
from app.services.captions import HEARD, SPOKE
from app.services.gemini_service import GeminiTranslateSession
from app.services.latency import MARK_PREFIX, SPEECH_RMS
from app.services.realtime_service import RealtimeTranslator
from app.services.session_service import registry
from app.services.translate_service import TranslateSession
from app.services.translator import Translator
from app.utils.audio import b64_decode
from app.utils.vad import SpeechDetector, frame_rms
from app.websocket import twilio_protocol as proto

log = logging.getLogger(__name__)

MARK_EVERY_FRAMES = 25          # ~500 ms at 20 ms/frame
FRAME_S = 0.02                  # one Twilio media frame


async def media_stream_endpoint(ws: WebSocket) -> None:
    settings = get_settings()
    await ws.accept()

    session = None
    pid: ParticipantId | None = None
    translator: Translator | None = None
    frames_since_mark = 0
    frames_before_answer = 0
    opened_at = time.monotonic()

    # Barge-in lives here rather than in the translator: the translate endpoint
    # emits no speech-started event and offers no way to cancel a response, so
    # detecting interruption ourselves is the only way both backends can behave
    # the same (design doc §8.2).
    speech = SpeechDetector()

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
                        echo_mode=settings.echo_mode,
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

                translator = await _adopt_or_open_translator(settings, session, pid)
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

                now = time.monotonic()
                participant.inbound.frame(message["media"].get("timestamp"), now)

                # Nothing is translated until both people are on the call.
                # Speech while the phone rings would be billed, captioned and
                # then thrown away, since there is nobody to play it to.
                if not (settings.echo_mode or session.peer_of(pid).connected):
                    frames_before_answer += 1
                    continue

                tracker = session.latency[pid]

                # This speaker starting up interrupts whatever they were being
                # played, so drop it rather than talking over them.
                was_speaking = speech.speaking
                if speech.feed(b64_decode(payload)):
                    log.debug("barge_in",
                              extra={"session_id": session.session_id,
                                     "participant": pid.value})
                    # The detector decides a few frames in; date the start back.
                    tracker.speech_started(now - speech.onset_frames * FRAME_S)
                    dropped = await session_service.clear_playback(session, pid)
                    if dropped:
                        # Whose translation was cut: the peer's, or our own in
                        # echo mode.
                        source = pid if settings.echo_mode else pid.peer
                        session.latency[source].playback_cleared(dropped)
                elif was_speaking and not speech.speaking:
                    tracker.speech_stopped(now - speech.hangover_frames * FRAME_S)

                for record in tracker.finalize_due(now):
                    _log_turn(session, pid, record)

                if translator is not None:
                    # Translated audio comes back asynchronously and is routed
                    # by the callback set up in _open_translator.
                    await translator.append_audio(payload)
                else:
                    await session_service.route_audio(
                        session, pid, payload, echo_mode=settings.echo_mode
                    )

                # Marks track what has actually been played, which is what
                # makes clear_playback's accounting meaningful. They belong on
                # both paths — sending them only on the pass-through path left
                # played_ms stuck at zero for every translated call.
                frames_since_mark += 1
                if frames_since_mark >= MARK_EVERY_FRAMES:
                    frames_since_mark = 0
                    target = pid if settings.echo_mode else pid.peer
                    name = f"chk-{participant.frames_in}"
                    session.participant(target).marks.sent(name, now)
                    await session_service.send_mark(session, target, name)
                continue

            # --- playback accounting (needed for barge-in truncation) ------
            if event == proto.EVENT_MARK:
                name = message.get("mark", {}).get("name", "")
                participant = session.participant(pid)
                now = time.monotonic()
                if name.startswith(MARK_PREFIX):
                    # A latency probe: the translation it follows has played.
                    # It must not touch played_ms, which assumes one chk- mark
                    # per 500 ms of audio.
                    speaker = _mark_speaker(name)
                    if speaker is not None:
                        session.latency[speaker].played(name, now)
                    continue
                participant.marks.acked(name, now)
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
            _log_latency_summary(session, pid, translator)
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
                    "frames_before_answer": frames_before_answer,
                    "audio_s_in": round(participant.frames_in * 20 / 1000, 1),
                },
            )
            registry.unbind_stream(session, pid)


def translator_class(settings):
    """Pick the translation backend.

    Kept as a function so tests can patch the choice rather than a class name,
    and so switching backends is one env var rather than a code change.
    """
    return {
        "translate": TranslateSession,
        "realtime": RealtimeTranslator,
        "gemini": GeminiTranslateSession,
    }[settings.translation_backend]


async def _open_translator(settings, session, pid: ParticipantId):
    """Start the session that translates THIS participant's speech.

    Its output is routed to the opposite participant — or back to the sender in
    echo mode, which is how one browser can test the whole pipeline alone.
    """
    if not settings.translation_enabled:
        log.info(
            "translation_disabled",
            extra={"session_id": session.session_id, "participant": pid.value,
                   "reason": "no API key for backend "
                             f"{settings.translation_backend!r}"},
        )
        return None

    # The language pair is ALWAYS speaker -> counterpart. Echo mode changes
    # only where the translated audio is played, never what it is translated
    # into; conflating the two makes the session translate a language into
    # itself, so everything comes back in the speaker's own language.
    speaker = session.participant(pid)
    counterpart = session.peer_of(pid)

    tracker = session.latency[pid]
    listener = pid if settings.echo_mode else pid.peer

    async def deliver(audio_b64: str) -> None:
        await session_service.route_audio(
            session, pid, audio_b64, echo_mode=settings.echo_mode
        )
        # Latency: when translated *speech* (not the near-silent filler the
        # model also sends) reaches us, plus a mark so Twilio tells us when it
        # has actually been played.
        is_speech = frame_rms(b64_decode(audio_b64)) > SPEECH_RMS
        mark = tracker.audio_out(time.monotonic(), is_speech)
        if mark is not None:
            await session_service.send_mark(session, listener, mark)

    async def caption(direction: str, text: str) -> None:
        # "in" is what this participant said, "out" is the translation.
        session.captions.add(pid, HEARD if direction == "in" else SPOKE, text)
        if direction == "in":
            tracker.heard(time.monotonic())

    backend = translator_class(settings)
    translator = backend(
        settings,
        # ISO codes, not display names: the translate endpoint needs codes, and
        # the realtime prompt resolves them to names itself.
        source_language=speaker.language,
        target_language=counterpart.language,
        on_audio=deliver,
        on_transcript=caption,
        label=f"{session.session_id[:12]}/{pid.value}",
    )

    try:
        await translator.connect()
    except Exception as exc:  # noqa: BLE001
        log.error(
            "translator_connect_failed",
            extra={"session_id": session.session_id, "participant": pid.value,
                   "backend": backend.__name__, "model": settings.translation_model,
                   "reason": type(exc).__name__, "detail": str(exc)[:200]},
        )
        return None

    log.info(
        "translator_started",
        extra={"session_id": session.session_id, "participant": pid.value,
               "backend": backend.__name__, "model": settings.translation_model,
               "direction": f"{speaker.language}->{counterpart.language}"},
    )

    speaker.ready_at = time.monotonic()
    return translator


def prewarm_translator(settings, session, pid: ParticipantId) -> None:
    """Start opening this leg's translator before its media stream exists.

    Called when Twilio fetches the leg's TwiML. Twilio then takes ~0.8 s to
    open the stream; connecting the model in that window means the first
    sentence no longer waits for it. The stream handler adopts the result.
    """
    if not settings.translation_enabled or pid in session.translator_tasks:
        return
    session.translator_tasks[pid] = asyncio.create_task(
        _open_translator(settings, session, pid)
    )


async def _adopt_or_open_translator(settings, session, pid: ParticipantId):
    task = session.translator_tasks.pop(pid, None)
    translator = None
    if task is not None:
        try:
            translator = await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            translator = None
    prewarmed = translator is not None
    if translator is None:
        # Not prewarmed, or the early attempt failed: one more try now.
        translator = await _open_translator(settings, session, pid)

    # How long this leg waited, after its stream arrived, for a translator.
    # Audio arriving meanwhile queues in the socket and delays the first
    # sentence; with prewarming this is ~0.
    speaker = session.participant(pid)
    waited = None
    if speaker.bound_at is not None and speaker.ready_at is not None:
        waited = max(0, round((speaker.ready_at - speaker.bound_at) * 1000))
    log.info(
        "latency_setup",
        extra={"session_id": session.session_id, "participant": pid.value,
               "prewarmed": prewarmed,
               "answered_to_twiml_ms": _between(speaker.answered_at, speaker.twiml_at),
               "twiml_to_stream_ms": _between(speaker.twiml_at, speaker.bound_at),
               "stream_to_ready_ms": waited},
    )
    return translator


# --- latency logging ----------------------------------------------------------
def _between(a: float | None, b: float | None) -> int | None:
    return round((b - a) * 1000) if a is not None and b is not None else None


def _mark_speaker(name: str) -> ParticipantId | None:
    """`lat:<speaker>:<turn>:<seq>` -> the speaker whose translation it follows."""
    try:
        return ParticipantId(name.split(":")[1])
    except (IndexError, ValueError):
        return None


def _direction(session, pid: ParticipantId) -> str:
    return f"{session.participant(pid).language}->{session.peer_of(pid).language}"


def _log_turn(session, pid: ParticipantId, record: dict) -> None:
    """One sentence: how long each stage took. Timings only, never words."""
    log.info(
        "latency_turn",
        extra={"session_id": session.session_id, "speaker": pid.value,
               "direction": _direction(session, pid), **record},
    )


def _log_latency_summary(session, pid: ParticipantId, translator) -> None:
    """End of this leg: every sentence's figures, then the call-level ones."""
    tracker = session.latency[pid]
    for record in tracker.flush(time.monotonic()):
        _log_turn(session, pid, record)
    participant = session.participant(pid)
    log.info(
        "latency_summary",
        extra={"session_id": session.session_id, "speaker": pid.value,
               "direction": _direction(session, pid),
               **tracker.summary(),
               # Round trip server <-> Twilio on this leg; half is one way.
               "twilio_rtt_ms": participant.marks.rtt_ms,
               "model_rtt_ms": getattr(translator, "rtt_ms", None),
               **participant.inbound.summary()},
    )
