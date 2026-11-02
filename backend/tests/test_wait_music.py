"""Waiting music: what fills the silence, and what must never fill it.

A translated call is silent for about three seconds after someone speaks.
Without a cue the person who answered reads that as a dead line - production
calls have ended with "Hello? Am I audible?" and a hang-up.

The music starts when their stream opens and stops the instant the first
translated voice reaches them. After that the call is silent between turns,
as it always was. Their own speaking does NOT stop it: "hello?" into a silent
line is exactly the person it exists to reassure, and that "hello?" still
reaches the caller.

Two earlier attempts are worth remembering, because the tests here are what
stop them coming back.

A spoken `<Say>` in front of `<Connect><Stream>` broke every call: the stream
could not open until the sentence finished, so setup went from 1.1s to 5-9s,
the caller's voice was discarded throughout, and hanging up during it meant
the stream was never reached at all. Five of seven production calls produced
nothing. `test_twiml_is_untouched_by_the_music` guards against that.

Music in every mid-call gap was built and then removed again: on a live line
it never once played, because the conditions for "a real silence" are almost
never all true at the same time on a phone call. The music is deliberately a
one-off now, which is also what makes it simple enough to reason about.
"""

import asyncio

import pytest

from app.config import get_settings
from app.models.session import CallStatus, ParticipantId
from app.services import session_service, twilio_service, wait_music
from app.services.session_service import GAP_S, registry
from app.utils import audio

A, B = ParticipantId.A, ParticipantId.B


# --- the guard -------------------------------------------------------------

@pytest.mark.parametrize("participant", [A, B])
def test_twiml_is_untouched_by_the_music(participant):
    """No verb may precede <Connect>, or the stream waits for it to finish."""
    xml = twilio_service.build_stream_twiml(
        get_settings(), "sess_guard", participant)
    assert "<Say" not in xml
    assert "<Play" not in xml
    assert "<Pause" not in xml
    assert xml.split("<Response>")[1].startswith("<Connect>")


# --- the loop itself -------------------------------------------------------

def test_the_music_exists_and_is_long_enough_to_cover_a_wait():
    frames = wait_music.frames()
    assert frames, "no music generated - run scripts/make_hold_music.py"
    seconds = len(frames) * wait_music.FRAME_MS / 1000
    assert seconds >= 3, f"{seconds:.1f}s is shorter than a translation wait"


def test_every_frame_is_the_size_twilio_expects():
    for frame in wait_music.frames()[:50]:
        assert len(audio.b64_decode(frame)) == wait_music.FRAME_BYTES


def test_a_missing_file_disables_the_music_instead_of_failing(monkeypatch):
    """A missing clip must never be able to break a call."""
    monkeypatch.setattr(wait_music, "_frames", None)
    monkeypatch.setattr(wait_music, "MUSIC_FILE",
                        wait_music.MUSIC_FILE.with_name("absent.ulaw"))
    assert wait_music.frames() == []
    assert wait_music.available() is False


# --- scaffolding -----------------------------------------------------------

class FakeSocket:
    """Collects what was sent, and tells clears apart from media."""

    def __init__(self):
        self.media, self.clears = [], 0

    async def send_text(self, text: str) -> None:
        if '"clear"' in text:
            self.clears += 1
        else:
            self.media.append(text)

    async def send_json(self, payload) -> None:
        if payload.get("event") == "clear":
            self.clears += 1
        else:
            self.media.append(payload)


@pytest.fixture
async def call():
    """A connected call with both legs streaming."""
    session = registry.create(source_language="ta", target_language="en",
                              phone_number="+919876543210")
    sockets = {}
    for pid in (A, B):
        sockets[pid] = FakeSocket()
        registry.bind_stream(session, pid, stream_sid=f"MZ{pid.value}",
                             call_sid=f"CA{pid.value}", ws=sockets[pid],
                             echo_mode=False)
    session.status = CallStatus.CONNECTED
    yield session, sockets
    for pid in (A, B):
        await session_service.stop_wait_music(session, pid)
    registry.remove(session.session_id)


async def settle():
    """Let the music task get a turn."""
    for _ in range(8):
        await asyncio.sleep(0)


def speech_frame() -> str:
    """One frame of translated audio, as base64 the way a translator sends it."""
    return audio.b64_encode(b"\xff" * wait_music.FRAME_BYTES)


# --- playing ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_music_reaches_the_person_who_answered(call):
    session, sockets = call
    assert await session_service.start_wait_music(session, B, opening=True)
    await settle()
    assert sockets[B].media, "they heard nothing"


@pytest.mark.asyncio
async def test_the_caller_never_hears_it(call):
    """It goes to the phone only; the caller is watching the screen."""
    session, sockets = call
    await session_service.start_wait_music(session, B)
    await settle()
    assert sockets[A].media == []


# --- stopping --------------------------------------------------------------

@pytest.mark.asyncio
async def test_their_own_speaking_does_not_stop_it(call):
    """The agreed rule, and the whole point of the music.

    Their speech travels B -> A, so it reaches the caller as usual; nothing
    about it touches what is playing on B.
    """
    session, sockets = call
    await session_service.start_wait_music(session, B)
    await settle()

    before = len(sockets[A].media)
    for _ in range(5):
        await session_service.route_audio(session, B, speech_frame())
    await settle()

    assert session.participant(B).music_task is not None, \
        "their speaking stopped the music"
    assert len(sockets[A].media) > before, "their hello was swallowed"


@pytest.mark.asyncio
async def test_our_translated_voice_stops_it(call):
    session, sockets = call
    await session_service.start_wait_music(session, B)
    await settle()
    before = sockets[B].clears

    await session_service.route_audio(session, A, speech_frame())
    await settle()

    assert session.participant(B).music_task is None
    assert sockets[B].clears > before, \
        "queued music was not dropped, so the translation plays behind it"


@pytest.mark.asyncio
async def test_the_opening_gets_exactly_one_turn(call):
    """Later silences are gap music's job, not the opening's."""
    session, _ = call
    await session_service.start_wait_music(session, B, opening=True)
    await settle()
    await session_service.route_audio(session, A, speech_frame())
    await settle()

    assert await session_service.start_wait_music(
        session, B, opening=True) is False


# --- gap music -------------------------------------------------------------

def quiet_for(session, seconds, *, queued=0.0):
    """A call where both people last heard a word `seconds` ago.

    `queued` is how much audio sits in Twilio's buffer - normally filler the
    model streams to keep the line warm, which must not count as something
    to listen to.
    """
    now = asyncio.get_event_loop().time()
    for p in (session.a, session.b):
        p.speaking = False
        p.opening_music_done = True
        p.last_speech_out_at = now - seconds
        p.spoke_at = now - seconds
        p.playout_until = now + queued
    return now


@pytest.mark.asyncio
async def test_a_real_silence_gets_music_on_both_sides(call):
    session, sockets = call
    now = quiet_for(session, GAP_S + 0.5)
    await session_service.tick_gap_music(session, now)
    await settle()
    assert session.a.music_task is not None, "the caller heard nothing"
    assert session.b.music_task is not None, "the phone heard nothing"
    assert sockets[A].media and sockets[B].media


@pytest.mark.asyncio
async def test_queued_filler_does_not_block_the_music(call):
    """The bug that stopped gap music ever playing on a live call.

    The model streams near-silent filler between phrases and `_skip_filler`
    keeps a cushion of it queued on purpose, so the playback queue is never
    empty. Judging silence by "nothing queued" meant the music could never
    start - `music_s` was 0.0 on every production call.
    """
    session, _ = call
    now = quiet_for(session, GAP_S + 0.5, queued=1.5)   # buffer full of filler
    await session_service.tick_gap_music(session, now)
    await settle()
    assert session.b.music_task is not None,         "queued filler was mistaken for something to listen to"


@pytest.mark.asyncio
async def test_filler_does_not_stop_the_music(call):
    """Only real speech ends it; filler would cut it off instantly."""
    session, _ = call
    now = quiet_for(session, GAP_S + 0.5)
    await session_service.tick_gap_music(session, now)
    await settle()

    await session_service.route_audio(session, A, speech_frame(), is_speech=False)
    await settle()
    assert session.b.music_task is not None, "filler stopped the music"

    await session_service.route_audio(session, A, speech_frame())
    await settle()
    assert session.b.music_task is None, "real speech should have stopped it"


@pytest.mark.asyncio
async def test_a_short_pause_is_left_alone(call):
    """Someone drawing breath is not a gap; filling it would flicker."""
    session, _ = call
    now = quiet_for(session, GAP_S / 2)
    await session_service.tick_gap_music(session, now)
    assert session.a.music_task is None and session.b.music_task is None


@pytest.mark.asyncio
async def test_their_speaking_stops_their_own_gap_music_only(call):
    """The other person is still waiting on a translation."""
    session, _ = call
    now = quiet_for(session, GAP_S + 0.5)
    await session_service.tick_gap_music(session, now)
    await settle()

    await session_service.stop_gap_music(session, B)
    assert session.b.music_task is None
    assert session.a.music_task is not None,         "the far side's speaking silenced the caller's music"


@pytest.mark.asyncio
async def test_gap_music_may_play_again_later(call):
    """Unlike the opening, gaps recur - so the music must be allowed to."""
    session, _ = call
    now = quiet_for(session, GAP_S + 0.5)
    await session_service.tick_gap_music(session, now)
    await settle()
    await session_service.stop_gap_music(session, B)

    quiet_for(session, GAP_S + 0.5)
    await session_service.tick_gap_music(session, now)
    await settle()
    assert session.b.music_task is not None


@pytest.mark.asyncio
async def test_no_gap_music_before_they_have_heard_anything(call):
    """The opening music covers the start; this must not pre-empt it."""
    session, _ = call
    now = quiet_for(session, GAP_S + 0.5)
    session.a.last_speech_out_at = session.b.last_speech_out_at = None
    await session_service.tick_gap_music(session, now)
    assert session.a.music_task is None and session.b.music_task is None


# --- teardown --------------------------------------------------------------

@pytest.mark.asyncio
async def test_stopping_twice_is_harmless(call):
    session, _ = call
    await session_service.start_wait_music(session, B)
    await settle()
    await session_service.stop_wait_music(session, B)
    assert await session_service.stop_wait_music(session, B) == 0.0


@pytest.mark.asyncio
async def test_a_leg_going_away_cancels_the_music(call):
    session, _ = call
    await session_service.start_wait_music(session, B)
    await settle()
    registry.unbind_stream(session, B)
    assert session.participant(B).music_task is None


@pytest.mark.asyncio
async def test_nothing_plays_into_a_leg_that_is_not_connected(call):
    session, _ = call
    registry.unbind_stream(session, B)
    assert await session_service.start_wait_music(session, B) is False


# --- the endpoint actually starting it -------------------------------------

def test_the_phone_leg_binds_and_starts_the_music_for_real(monkeypatch):
    """Drives the real WebSocket endpoint with the music switched on.

    The rest of this file calls `start_wait_music` directly, which is why a
    call that no longer matched its signature reached production: every other
    test runs with WAIT_MUSIC off, so the one line that starts the music was
    never executed. A live call then crashed the phone leg the instant it
    bound, with no audio and no transcript.
    """
    import json
    import time

    from fastapi.testclient import TestClient

    from app.main import app
    from app.websocket import media_stream

    monkeypatch.setattr(media_stream, "get_settings",
                        lambda: get_settings().__class__(
                            **{**get_settings().__dict__, "wait_music": True}))

    session = registry.create(source_language="ta", target_language="hi",
                              participant_b_kind="browser")
    start = lambda p, ssid: {                                    # noqa: E731
        "event": "start", "sequenceNumber": "1", "streamSid": ssid,
        "start": {"streamSid": ssid, "accountSid": "AC" + "0" * 32,
                  "callSid": "CA" + p * 32, "tracks": ["inbound"],
                  "mediaFormat": {"encoding": "audio/x-mulaw",
                                  "sampleRate": 8000, "channels": 1},
                  "customParameters": {"session_id": session.session_id,
                                       "participant": p}}}
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/media-stream") as ws_a:
                ws_a.send_text(json.dumps({"event": "connected",
                                           "protocol": "Call"}))
                ws_a.send_text(json.dumps(start("A", "MZ_A")))
                with client.websocket_connect("/ws/media-stream") as ws_b:
                    ws_b.send_text(json.dumps({"event": "connected",
                                               "protocol": "Call"}))
                    ws_b.send_text(json.dumps(start("B", "MZ_B")))

                    # Checked on the session, not by reading the socket: a
                    # leg that died would simply never answer, and the test
                    # would hang rather than fail.
                    for _ in range(50):
                        if session.b.music_task is not None:
                            break
                        time.sleep(0.02)

                    assert session.b.connected,                         "the phone leg died as soon as it bound"
                    assert session.b.music_task is not None,                         "the stream bound but the music never started"
    finally:
        registry.remove(session.session_id)


def test_a_broken_music_start_cannot_kill_the_call(monkeypatch):
    """The music is a nicety and must never cost a call.

    A signature mismatch in the start call once crashed the phone leg as soon
    as it bound: no audio, no transcript, nothing. Whatever goes wrong in
    here, the call carries on without music.
    """
    import json
    import time

    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import session_service as svc
    from app.websocket import media_stream

    monkeypatch.setattr(media_stream, "get_settings",
                        lambda: get_settings().__class__(
                            **{**get_settings().__dict__, "wait_music": True}))

    async def boom(*_args, **_kwargs):
        raise RuntimeError("music is broken")

    monkeypatch.setattr(media_stream.session_service, "start_wait_music", boom)

    session = registry.create(source_language="ta", target_language="hi",
                              participant_b_kind="browser")
    start = lambda p, ssid: {                                    # noqa: E731
        "event": "start", "sequenceNumber": "1", "streamSid": ssid,
        "start": {"streamSid": ssid, "accountSid": "AC" + "0" * 32,
                  "callSid": "CA" + p * 32, "tracks": ["inbound"],
                  "mediaFormat": {"encoding": "audio/x-mulaw",
                                  "sampleRate": 8000, "channels": 1},
                  "customParameters": {"session_id": session.session_id,
                                       "participant": p}}}
    try:
        with TestClient(app) as client:
            with client.websocket_connect("/ws/media-stream") as ws_a:
                ws_a.send_text(json.dumps({"event": "connected",
                                           "protocol": "Call"}))
                ws_a.send_text(json.dumps(start("A", "MZ_A")))
                with client.websocket_connect("/ws/media-stream") as ws_b:
                    ws_b.send_text(json.dumps({"event": "connected",
                                               "protocol": "Call"}))
                    ws_b.send_text(json.dumps(start("B", "MZ_B")))
                    time.sleep(0.4)
                    assert session.b.connected, \
                        "a broken music start took the call down with it"
    finally:
        registry.remove(session.session_id)
