"""Voice activity detection tests (design doc §8.2).

Barge-in depends entirely on this, so the behaviour that matters is:
silence never fires, a click never fires, real speech fires once and only
once, and a natural pause mid-sentence does not end the turn.
"""

import math

import numpy as np
import pytest

from app.utils import audio
from app.utils.vad import SpeechDetector, frame_rms

FRAME_MS = 20
RATE = 8000


def loud_frame(amplitude: int = 6000, hz: float = 300.0) -> bytes:
    n = RATE * FRAME_MS // 1000
    t = np.arange(n) / RATE
    pcm = (amplitude * np.sin(2 * math.pi * hz * t)).astype("<i2").tobytes()
    return audio.pcm16_to_ulaw(pcm)


def quiet_frame() -> bytes:
    return audio.ulaw_silence(FRAME_MS)


def feed(detector: SpeechDetector, frames) -> int:
    """Returns how many times speech-start fired."""
    return sum(1 for f in frames if detector.feed(f))


# ------------------------------------------------------------------ energy
def test_rms_separates_speech_from_silence():
    assert frame_rms(quiet_frame()) < 50
    assert frame_rms(loud_frame()) > 3000


def test_rms_of_empty_frame_is_zero():
    assert frame_rms(b"") == 0.0


# ------------------------------------------------------------------ onset
def test_silence_never_triggers():
    d = SpeechDetector()
    assert feed(d, [quiet_frame()] * 200) == 0
    assert d.speaking is False


def test_speech_triggers_once_after_the_onset_window():
    d = SpeechDetector(onset_frames=3)
    fired = [d.feed(loud_frame()) for _ in range(10)]

    assert fired[:3] == [False, False, True], "should fire on the 3rd loud frame"
    assert sum(fired) == 1, "must be an edge, not a level"
    assert d.speaking is True


def test_a_single_click_does_not_trigger():
    """One loud frame among silence is a click, not speech."""
    d = SpeechDetector(onset_frames=3)
    frames = [quiet_frame(), loud_frame(), quiet_frame(), quiet_frame()]
    assert feed(d, frames) == 0
    assert d.speaking is False


def test_quiet_speech_below_threshold_does_not_trigger():
    d = SpeechDetector(threshold=700)
    assert feed(d, [loud_frame(amplitude=200)] * 20) == 0


# --------------------------------------------------------------- hangover
def test_a_short_pause_does_not_end_the_turn():
    """Gaps between words must not re-fire speech-start on every syllable."""
    d = SpeechDetector(onset_frames=3, hangover_frames=25)
    fired = feed(d, [loud_frame()] * 5)
    assert fired == 1

    # 10 frames of silence = 200 ms, a normal inter-word gap
    fired += feed(d, [quiet_frame()] * 10)
    assert d.speaking is True, "turn ended too early"

    fired += feed(d, [loud_frame()] * 10)
    assert fired == 1, "re-fired mid-sentence"


def test_a_long_pause_ends_the_turn_and_the_next_utterance_fires_again():
    d = SpeechDetector(onset_frames=3, hangover_frames=10)
    assert feed(d, [loud_frame()] * 5) == 1

    feed(d, [quiet_frame()] * 15)          # past the hangover
    assert d.speaking is False

    assert feed(d, [loud_frame()] * 5) == 1, "a new utterance should fire again"


# ----------------------------------------------------------------- state
def test_reset_clears_state():
    d = SpeechDetector()
    feed(d, [loud_frame()] * 10)
    assert d.speaking is True

    d.reset()
    assert d.speaking is False
    assert feed(d, [loud_frame()] * 2) == 0, "onset counter should have restarted"


def test_detectors_are_independent():
    """One leg speaking must not mark the other leg as speaking."""
    a, b = SpeechDetector(), SpeechDetector()
    feed(a, [loud_frame()] * 10)

    assert a.speaking is True
    assert b.speaking is False


@pytest.mark.parametrize("amplitude,expected", [(100, False), (6000, True)])
def test_threshold_boundaries(amplitude, expected):
    d = SpeechDetector(onset_frames=2)
    feed(d, [loud_frame(amplitude=amplitude)] * 6)
    assert d.speaking is expected
