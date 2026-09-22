"""µ-law codec tests (design doc §26, 'Audio')."""

import struct

import pytest

from app.utils import audio


def test_silence_round_trip():
    pcm = audio.ulaw_to_pcm16(bytes([audio.ULAW_SILENCE]) * 10)
    assert len(pcm) == 20
    samples = struct.unpack("<10h", pcm)
    assert all(abs(s) <= 8 for s in samples), samples


def test_round_trip_is_stable():
    """µ-law is lossy, but encode(decode(x)) must be idempotent."""
    original = bytes(range(256))
    once = audio.pcm16_to_ulaw(audio.ulaw_to_pcm16(original))
    twice = audio.pcm16_to_ulaw(audio.ulaw_to_pcm16(once))
    assert once == twice


def test_round_trip_preserves_amplitude_within_tolerance():
    for value in (-30000, -8000, -100, 0, 100, 8000, 30000):
        pcm = struct.pack("<h", value)
        decoded = struct.unpack("<h", audio.ulaw_to_pcm16(audio.pcm16_to_ulaw(pcm)))[0]
        tolerance = max(64, abs(value) * 0.10)
        assert abs(decoded - value) <= tolerance, (value, decoded)


def test_sign_is_preserved():
    for value in (-20000, -500, 500, 20000):
        pcm = struct.pack("<h", value)
        decoded = struct.unpack("<h", audio.ulaw_to_pcm16(audio.pcm16_to_ulaw(pcm)))[0]
        assert (decoded < 0) == (value < 0), (value, decoded)


def test_duration_matches_twilio_frame_size():
    """Twilio sends 20 ms frames == 160 µ-law bytes at 8 kHz."""
    assert audio.duration_ms(b"\xff" * 160) == 20.0
    assert len(audio.ulaw_silence(20)) == 160


def test_pcm16_rejects_odd_length():
    with pytest.raises(ValueError):
        audio.pcm16_to_ulaw(b"\x00\x01\x02")


def test_base64_helpers_round_trip():
    payload = bytes(range(64))
    assert audio.b64_decode(audio.b64_encode(payload)) == payload
