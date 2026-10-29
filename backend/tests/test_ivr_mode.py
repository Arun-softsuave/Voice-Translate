"""IVR mode: reading the far side instead of hearing it.

A recorded menu is spoken straight through. Translating it into speech means
option three is still being read aloud fifteen seconds after the menu asked
for a key — by which time the menu has given up. The captions carry the same
words immediately, so in IVR mode we stop playing the translation and let the
caller read it.

What must NOT change: the captions themselves, and the caller's own voice
still reaching the far end.
"""

import json
import time
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models.session import ParticipantId
from app.services.session_service import SessionRegistry, registry
from app.utils import audio
from app.websocket import media_stream

from tests.test_call_states import _start, fake_translation, wait_for  # noqa: F401

A, B = ParticipantId.A, ParticipantId.B


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def wired():
    s = SessionRegistry().create(source_language="en", target_language="es")
    for p, sid in ((s.a, "MZ_A"), (s.b, "MZ_B")):
        p.ws, p.stream_sid = FakeWS(), sid
    return s


def speech(ms: int = 300) -> str:
    t = np.arange(8 * ms) / 8000
    pcm = (9000 * np.sin(2 * np.pi * 300 * t)).astype("<i2").tobytes()
    return audio.b64_encode(audio.pcm16_to_ulaw(pcm))


def media_sent(ws):
    return [m for m in ws.sent if m["event"] == "media"]


class Recorder:
    """Stands in for the translator, capturing the callbacks wired to it."""

    def __init__(self):
        self.kwargs = None

    def __call__(self, settings, **kwargs):
        self.kwargs = kwargs
        return self

    @property
    def __name__(self):
        return "Recorder"

    async def connect(self):
        return None


@pytest.fixture
def live_call(signed_in, fake_translation):  # noqa: F811
    """A signed-in client and a real session both legs can stream into."""
    client, user_id = signed_in
    session = registry.create(source_language="en", target_language="es",
                              phone_number="+34910930511", user_id=user_id)
    with client:
        yield client, session
    registry.remove(session.session_id)


@pytest.fixture
def translator(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(media_stream, "translator_class", lambda st: rec)
    s = wired()

    async def build(speaker):
        """The real callbacks for one leg's translator."""
        settings = replace(get_settings(), openai_api_key="sk-test")
        await media_stream._open_translator(settings, s, speaker)
        return rec.kwargs

    return s, build


@pytest.mark.asyncio
async def test_the_far_sides_voice_is_not_played_in_ivr_mode(translator):
    s, build = translator
    s.ivr_mode = True
    on_audio = (await build(B))["on_audio"]

    await on_audio(speech())

    assert media_sent(s.a.ws) == [], "the menu should be read, not heard"
    assert s.a.suppressed_ms > 0


@pytest.mark.asyncio
async def test_the_caller_can_still_be_heard_in_ivr_mode(translator):
    """Their voice must keep reaching the menu, or they cannot answer it."""
    s, build = translator
    s.ivr_mode = True
    on_audio = (await build(A))["on_audio"]

    await on_audio(speech())

    assert len(media_sent(s.b.ws)) == 1


@pytest.mark.asyncio
async def test_captions_are_unaffected_by_ivr_mode(translator):
    s, build = translator
    s.ivr_mode = True
    callbacks = await build(B)

    await callbacks["on_transcript"]("in", "Para atención al cliente, pulse uno.")
    await callbacks["on_transcript"]("out", "For customer service, press one.")

    assert [line.text for line in s.captions.lines()] == [
        "Para atención al cliente, pulse uno.",
        "For customer service, press one.",
    ]


@pytest.mark.asyncio
async def test_ordinary_calls_are_untouched(translator):
    s, build = translator
    on_audio = (await build(B))["on_audio"]

    await on_audio(speech())

    assert len(media_sent(s.a.ws)) == 1
    assert s.a.suppressed_ms == 0


@pytest.mark.asyncio
async def test_speech_is_not_played_over_a_keypad_tone(translator):
    """The tone must survive, or the keypress silently does nothing."""
    s, build = translator
    on_audio = (await build(A))["on_audio"]
    s.b.quiet_until = time.monotonic() + 1.0

    await on_audio(speech())
    assert media_sent(s.b.ws) == []

    s.b.quiet_until = time.monotonic() - 0.01       # the tone has finished
    await on_audio(speech())
    assert len(media_sent(s.b.ws)) == 1


@pytest.mark.asyncio
async def test_a_fresh_call_is_never_held_quiet():
    """A wrong default here would silence every call in the system."""
    s = wired()
    assert s.a.quiet_until == 0.0
    assert s.b.quiet_until == 0.0
    assert s.ivr_mode is False
    assert not media_stream._suppressed(s, A, s.a, time.monotonic())


# --- the barge-in half ------------------------------------------------------
# A machine does not stop talking when we start. Left alone, its voice counts
# as interrupting us and throws away what we are saying into it: 1.75 s was
# lost that way on the Spanish menu call of 2026-10-15. These drive the real
# websocket endpoint, because the point is what the stream loop does, not
# what a re-stated condition returns.

def _clears_of_b(caplog):
    """How many times something queued for the far leg was thrown away."""
    return [r for r in caplog.records
            if r.getMessage() == "playback_cleared"
            and getattr(r, "participant", None) == "B"]


def _run_menu(client, session, loud_frames=60, hold_quiet=False):
    """Play a long, loud monologue down leg B, as a recorded menu does."""
    noise = speech(20)
    with client.websocket_connect("/ws/media-stream") as ws_a:
        ws_a.send_text(json.dumps(_start(session.session_id, "A", "MZ_A")))
        with client.websocket_connect("/ws/media-stream") as ws_b:
            ws_b.send_text(json.dumps(_start(session.session_id, "B", "MZ_B")))
            assert wait_for(lambda: session.b.connected and session.a.connected)
            # Something of ours is playing to the menu, for it to cut.
            session.b.playout_until = time.monotonic() + 5.0
            if hold_quiet:
                session.b.quiet_until = time.monotonic() + 5.0
            for n in range(loud_frames):
                ws_b.send_text(json.dumps(
                    {"event": "media", "streamSid": "MZ_B",
                     "media": {"timestamp": str(n * 20), "payload": noise}}))
            assert wait_for(lambda: session.b.frames_in == loud_frames)
            ws_b.send_text(json.dumps({"event": "stop", "streamSid": "MZ_B"}))
        ws_a.send_text(json.dumps({"event": "stop", "streamSid": "MZ_A"}))


def test_a_machine_talking_over_us_cuts_us_off_normally(live_call, caplog):
    """The behaviour being fixed, so the test below cannot pass vacuously."""
    client, session = live_call
    with caplog.at_level("INFO"):
        _run_menu(client, session)
    assert _clears_of_b(caplog), "expected the menu's voice to cut us off"


def test_a_machine_cannot_cut_the_caller_off_in_ivr_mode(live_call, caplog):
    client, session = live_call
    session.ivr_mode = True
    with caplog.at_level("INFO"):
        _run_menu(client, session)
    assert not _clears_of_b(caplog), "our words into the menu were thrown away"


def test_a_keypad_tone_is_not_cut_off_by_the_menu(live_call, caplog):
    """Otherwise the keypress silently does nothing and the call stalls."""
    client, session = live_call
    with caplog.at_level("INFO"):
        _run_menu(client, session, hold_quiet=True)
    assert not _clears_of_b(caplog)
