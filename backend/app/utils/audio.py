"""G.711 µ-law helpers.

Design doc §7: the primary path needs NO conversion — Twilio sends µ-law 8 kHz
and the OpenAI Realtime API accepts `audio/pcmu`, so audio passes through
byte-for-byte. These helpers exist for the PCM16 fallback path, for tests, and
for silence generation.

Implemented with lookup tables rather than the stdlib `audioop` module, which
was removed in Python 3.13.
"""

from __future__ import annotations

import base64

_BIAS = 0x84
_CLIP = 32635
ULAW_SILENCE = 0xFF          # µ-law encoding of PCM 0
SAMPLE_RATE = 8000
BYTES_PER_MS = SAMPLE_RATE // 1000   # 8 µ-law bytes == 1 ms of audio


def _build_decode_table() -> list[int]:
    table = []
    for byte in range(256):
        u = ~byte & 0xFF
        magnitude = ((u & 0x0F) << 3) + _BIAS
        magnitude <<= (u & 0x70) >> 4
        table.append(_BIAS - magnitude if u & 0x80 else magnitude - _BIAS)
    return table


_DECODE = _build_decode_table()

# Segment lookup from the reference G.711 implementation: 2,2,4,8,16,32,64,128
_EXP_LUT = (
    [0] * 2 + [1] * 2 + [2] * 4 + [3] * 8 + [4] * 16 + [5] * 32 + [6] * 64
    + [7] * 128
)
assert len(_EXP_LUT) == 256


def _encode_sample(pcm: int) -> int:
    sign = 0x80 if pcm < 0 else 0x00
    if pcm < 0:
        pcm = -pcm
    if pcm > _CLIP:
        pcm = _CLIP
    pcm += _BIAS
    exponent = _EXP_LUT[(pcm >> 7) & 0xFF]
    mantissa = (pcm >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def ulaw_to_pcm16(payload: bytes) -> bytes:
    """µ-law bytes -> little-endian signed 16-bit PCM."""
    out = bytearray(len(payload) * 2)
    for i, byte in enumerate(payload):
        out[2 * i:2 * i + 2] = (_DECODE[byte] & 0xFFFF).to_bytes(2, "little")
    return bytes(out)


def pcm16_to_ulaw(payload: bytes) -> bytes:
    """Little-endian signed 16-bit PCM -> µ-law bytes."""
    if len(payload) % 2:
        raise ValueError("PCM16 payload must contain an even number of bytes")
    out = bytearray(len(payload) // 2)
    for i in range(0, len(payload), 2):
        sample = int.from_bytes(payload[i:i + 2], "little", signed=True)
        out[i // 2] = _encode_sample(sample)
    return bytes(out)


def ulaw_silence(duration_ms: int) -> bytes:
    return bytes([ULAW_SILENCE]) * (duration_ms * BYTES_PER_MS)


def duration_ms(ulaw_payload: bytes) -> float:
    """How many milliseconds of audio a µ-law payload represents."""
    return len(ulaw_payload) / BYTES_PER_MS


def b64_decode(payload: str) -> bytes:
    return base64.b64decode(payload)


def b64_encode(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")
