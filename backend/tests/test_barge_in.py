"""The two latency fixes from the demo-call logs.

1. Barge-in (cutting the translation someone hears because they talk over
   it) used to fire on 60 ms of sound: 52 cuts and ~28 s of lost translation
   in a 4-minute call, mostly on coughs, line noise and the translation's own
   echo. It now needs sustained speech, and louder speech while a translation
   is playing.
2. Near-silent filler from the model used to be queued at Twilio, so real
   speech waited behind it (~0.35 s a sentence). Filler is now left out when
   audio is already queued, except for short pauses inside speech.
"""

import time
from dataclasses import replace

import numpy as np
import pytest

from app.config import get_settings
from app.models.session import ParticipantId
from app.services import session_service
from app.services.session_service import SessionRegistry, playback_backlog_s
from app.utils import audio
from app.utils.vad import (BARGE_IN_FRAMES, BARGE_IN_PLAYING_THRESHOLD,
                           BARGE_IN_RELEASE_FRAMES, BargeInDetector)
from app.websocket import media_stream

A, B = ParticipantId.A, ParticipantId.B
SPEECH, ECHO, QUIET = 2500.0, 1000.0, 50.0


def run(detector, levels, playing=False):
    return [i for i, lv in enumerate(levels) if detector.feed(lv, playing=playing)]


# ------------------------------------------------------------- barge-in
def test_a_cough_does_not_cut_the_translation():
    d = BargeInDetector()
    fired = run(d, [SPEECH] * 5 + [QUIET] * 20)          # 100 ms of sound
    assert fired == []
    assert d.ignored == 1


def test_the_old_trigger_length_is_no_longer_enough():
    """60 ms (the old onset) is far short of the new threshold."""
    d = BargeInDetector()
    assert run(d, [SPEECH] * 3 + [QUIET] * 20) == []


def test_sustained_speech_cuts_once():
    d = BargeInDetector()
    fired = run(d, [SPEECH] * 60)
    assert fired == [BARGE_IN_FRAMES - 1]                 # once, at ~360 ms
    assert d.fired == 1


def test_gaps_between_syllables_do_not_reset_the_count():
    """Real speech dips below the threshold between syllables."""
    syllables = ([SPEECH] * 4 + [QUIET] * 3) * 6          # never 18 in a row
    d = BargeInDetector()
    assert len(run(d, syllables)) == 1


def test_a_long_pause_starts_a_new_burst():
    d = BargeInDetector()
    burst = [SPEECH] * BARGE_IN_FRAMES + [QUIET] * BARGE_IN_RELEASE_FRAMES
    assert len(run(d, burst * 2)) == 2


def test_echo_level_sound_cannot_cut_a_playing_translation():
    """What the microphone hears while the translation plays is mostly echo."""
    d = BargeInDetector()
    assert run(d, [ECHO] * 100, playing=True) == []


def test_clearly_direct_speech_still_interrupts_a_playing_translation():
    d = BargeInDetector()
    assert len(run(d, [BARGE_IN_PLAYING_THRESHOLD + 500] * 30, playing=True)) == 1


def test_the_same_level_does_interrupt_when_nothing_is_playing():
    d = BargeInDetector()
    assert len(run(d, [ECHO] * 30, playing=False)) == 1


# ------------------------------------------------------------- playout clock
class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


def wired():
    s = SessionRegistry().create(source_language="en", target_language="ta")
    for p, sid in ((s.a, "MZ_A"), (s.b, "MZ_B")):
        p.ws, p.stream_sid = FakeWS(), sid
    return s


def chunk(ms: int, loud: bool) -> str:
    n = 8 * ms
    if not loud:
        return audio.b64_encode(audio.ulaw_silence(ms))
    t = np.arange(n) / 8000
    pcm = (9000 * np.sin(2 * np.pi * 300 * t)).astype("<i2").tobytes()
    return audio.b64_encode(audio.pcm16_to_ulaw(pcm))


@pytest.mark.asyncio
async def test_routed_audio_advances_the_playout_clock():
    s = wired()
    await session_service.route_audio(s, A, chunk(500, True))
    await session_service.route_audio(s, A, chunk(500, True))
    assert playback_backlog_s(s.b) == pytest.approx(1.0, abs=0.05)
    assert playback_backlog_s(s.a) == 0.0


@pytest.mark.asyncio
async def test_clearing_empties_the_playout_clock():
    s = wired()
    await session_service.route_audio(s, A, chunk(800, True))
    await session_service.clear_playback(s, B)
    assert playback_backlog_s(s.b) == 0.0


def test_backlog_drains_in_real_time():
    s = wired()
    now = time.monotonic()
    s.b.playout_until = now + 0.4
    assert playback_backlog_s(s.b, now + 0.1) == pytest.approx(0.3)
    assert playback_backlog_s(s.b, now + 1.0) == 0.0


# ------------------------------------------------------------- filler
class Recorder:
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
def deliver(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(media_stream, "translator_class", lambda st: rec)
    s = wired()

    async def build():
        settings = replace(get_settings(), openai_api_key="sk-test")
        await media_stream._open_translator(settings, s, A)
        return rec.kwargs["on_audio"]

    return s, build


def media_sent(ws):
    return [m for m in ws.sent if m["event"] == "media"]


@pytest.mark.asyncio
async def test_filler_is_not_queued_behind_a_backlog(deliver):
    s, build = deliver
    on_audio = await build()
    s.b.playout_until = time.monotonic() + 1.0          # 1 s already queued
    s.b.last_speech_out_at = None

    await on_audio(chunk(100, loud=False))

    assert media_sent(s.b.ws) == []
    assert s.b.silence_skipped_ms == pytest.approx(100)


@pytest.mark.asyncio
async def test_filler_is_kept_when_nothing_is_queued(deliver):
    """With an empty queue, silence costs nothing and keeps the line alive."""
    s, build = deliver
    on_audio = await build()

    await on_audio(chunk(100, loud=False))

    assert len(media_sent(s.b.ws)) == 1


@pytest.mark.asyncio
async def test_a_pause_right_after_speech_keeps_its_length(deliver):
    """Dropping it would make phrases run together."""
    s, build = deliver
    on_audio = await build()
    await on_audio(chunk(300, loud=True))               # speech -> backlog
    assert playback_backlog_s(s.b) > media_stream.SILENCE_BACKLOG_S

    await on_audio(chunk(100, loud=False))              # pause within speech

    assert len(media_sent(s.b.ws)) == 2
    assert s.b.silence_skipped_ms == 0


@pytest.mark.asyncio
async def test_speech_is_never_skipped(deliver):
    s, build = deliver
    on_audio = await build()
    s.b.playout_until = time.monotonic() + 5.0

    await on_audio(chunk(100, loud=True))

    assert len(media_sent(s.b.ws)) == 1


@pytest.mark.asyncio
async def test_long_silence_after_speech_is_trimmed_once_queued(deliver, monkeypatch):
    s, build = deliver
    on_audio = await build()
    s.b.last_speech_out_at = time.monotonic() - (media_stream.PAUSE_KEEP_S + 0.5)
    s.b.playout_until = time.monotonic() + 1.0

    await on_audio(chunk(100, loud=False))

    assert media_sent(s.b.ws) == []
