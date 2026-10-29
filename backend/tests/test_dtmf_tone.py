"""Keypad tones: is what we generate really DTMF?

The one test that matters is `test_every_key_decodes_back_to_itself`. It runs
our tone through an independent Goertzel decoder — the same algorithm a real
telephone exchange uses — and checks the digit comes back out. Everything else
guards a specific way a homegrown DTMF generator goes wrong while still looking
plausible: the wrong frequency pairing, no twist between the two tones,
clipping, or square edges.

None of this can prove a far-end carrier accepts the tone; only a real call can.
It does prove the signal we put on the line is the one the spec asks for.
"""

import math

import numpy as np
import pytest

from app.utils import audio
from app.utils.audio import (
    DTMF_GAP_MS,
    DTMF_HIGH_AMPLITUDE,
    DTMF_LOW_AMPLITUDE,
    DTMF_PAIRS,
    DTMF_TONE_MS,
    SAMPLE_RATE,
    ULAW_SILENCE,
    ulaw_dtmf,
    ulaw_dtmf_burst,
)

KEYS = list(DTMF_PAIRS)
ROWS = (697, 770, 852, 941)
COLS = (1209, 1336, 1477, 1633)
DIGIT_OF = {pair: key for key, pair in DTMF_PAIRS.items()}


def samples(ulaw: bytes) -> np.ndarray:
    return np.frombuffer(audio.ulaw_to_pcm16(ulaw), dtype="<i2").astype(np.float64)


def goertzel(pcm: np.ndarray, hz: float) -> float:
    """Energy at one frequency. Deliberately a second implementation."""
    k = int(0.5 + len(pcm) * hz / SAMPLE_RATE)
    w = 2 * math.pi * k / len(pcm)
    coeff = 2 * math.cos(w)
    s1 = s2 = 0.0
    for x in pcm:
        s0 = x + coeff * s1 - s2
        s2, s1 = s1, s0
    return s1 * s1 + s2 * s2 - coeff * s1 * s2


def decode(ulaw: bytes) -> str:
    """Which key this tone sounds like, by strongest row and column."""
    pcm = samples(ulaw)
    row = max(ROWS, key=lambda hz: goertzel(pcm, hz))
    col = max(COLS, key=lambda hz: goertzel(pcm, hz))
    return DIGIT_OF[(row, col)]


def peak_hz_pair(ulaw: bytes) -> tuple[float, float]:
    """The two loudest frequencies present, by FFT."""
    pcm = samples(ulaw)
    spectrum = np.abs(np.fft.rfft(pcm * np.hanning(len(pcm))))
    freqs = np.fft.rfftfreq(len(pcm), 1 / SAMPLE_RATE)
    # Peaks are a few bins wide; take the two tallest that are far apart.
    order = np.argsort(spectrum)[::-1]
    first = freqs[order[0]]
    second = next(freqs[i] for i in order if abs(freqs[i] - first) > 100)
    return tuple(sorted((float(first), float(second))))


@pytest.mark.parametrize("key", KEYS)
def test_every_key_decodes_back_to_itself(key):
    assert decode(ulaw_dtmf(key)) == key


@pytest.mark.parametrize("key", KEYS)
def test_the_frequencies_are_within_spec(key):
    low, high = DTMF_PAIRS[key]
    got_low, got_high = peak_hz_pair(ulaw_dtmf(key))
    # The spec allows 1.5%; a detector rejects anything beyond it.
    assert abs(got_low - low) / low < 0.015
    assert abs(got_high - high) / high < 0.015


def test_the_high_tone_carries_the_expected_twist():
    pcm = samples(ulaw_dtmf("5"))
    low, high = DTMF_PAIRS["5"]
    twist_db = 10 * math.log10(goertzel(pcm, high) / goertzel(pcm, low))
    assert 1.0 < twist_db < 3.5          # nominal 2 dB, permitted 0-4


def test_nothing_clips():
    # Clipping breeds intermodulation products, which detectors reject.
    peak = np.abs(samples(ulaw_dtmf("9"))).max()
    assert peak < 0.8 * 32767
    assert peak == pytest.approx(DTMF_LOW_AMPLITUDE + DTMF_HIGH_AMPLITUDE, rel=0.08)


def test_no_strong_energy_outside_the_two_tones():
    pcm = samples(ulaw_dtmf("1"))
    low, high = DTMF_PAIRS["1"]
    wanted = min(goertzel(pcm, low), goertzel(pcm, high))
    for stray in (2 * low, high - low, high + low):
        if stray < SAMPLE_RATE / 2:
            assert 10 * math.log10(wanted / goertzel(pcm, stray)) > 25


def test_the_edges_are_faded_not_cut():
    pcm = samples(ulaw_dtmf("7"))
    assert abs(pcm[0]) < 0.02 * 32767
    assert abs(pcm[-1]) < 0.02 * 32767

    # Both edges are quieter than the body. (Per-millisecond RMS is not
    # monotonic even under a clean fade, because the two tones beat against
    # each other, so compare the whole ramp against the steady part.)
    ramp = 5 * 8
    loudness = lambda block: float(np.sqrt(np.mean(block ** 2)))
    middle = loudness(pcm[len(pcm) // 2 - ramp:len(pcm) // 2 + ramp])
    assert loudness(pcm[:ramp]) < 0.7 * middle
    assert loudness(pcm[-ramp:]) < 0.7 * middle


def test_a_tone_is_exactly_as_long_as_asked():
    assert audio.duration_ms(ulaw_dtmf("3")) == DTMF_TONE_MS
    assert audio.duration_ms(ulaw_dtmf("3", 250)) == 250


def test_a_burst_is_one_blob_per_key():
    blobs = ulaw_dtmf_burst("*123#")
    assert len(blobs) == 5
    for blob in blobs:
        assert audio.duration_ms(blob) == DTMF_TONE_MS + DTMF_GAP_MS


def test_the_gap_after_each_tone_is_silent():
    blob = ulaw_dtmf_burst("8")[0]
    assert set(blob[DTMF_TONE_MS * 8:]) == {ULAW_SILENCE}


def test_a_whole_burst_decodes_to_the_string_sent():
    digits = "0924*#"
    blobs = ulaw_dtmf_burst(digits)
    assert "".join(decode(blob[:DTMF_TONE_MS * 8]) for blob in blobs) == digits


def test_lower_case_letter_keys_are_accepted():
    assert ulaw_dtmf("a") == ulaw_dtmf("A")


@pytest.mark.parametrize("bad", ["", "E", "12", " ", "-"])
def test_anything_that_is_not_a_key_is_refused(bad):
    with pytest.raises(ValueError):
        ulaw_dtmf(bad)


def test_the_amplitudes_are_the_documented_ones():
    # Guards the levels against a well-meaning "make it louder" edit.
    assert DTMF_HIGH_AMPLITUDE > DTMF_LOW_AMPLITUDE
    assert DTMF_LOW_AMPLITUDE + DTMF_HIGH_AMPLITUDE < 0.75 * 32767
