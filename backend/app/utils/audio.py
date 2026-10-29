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
import math
from functools import lru_cache

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


# --- DTMF -------------------------------------------------------------------
# Keypad tones, so the caller can answer a far-end menu ("press one for sales").
# Twilio's Media Streams carry no outbound DTMF signal — only audio — so the
# tone is synthesised here and played into the leg like any other sound.
#
# Levels follow Bellcore: each tone a little under half of full scale, with the
# high one ~2 dB louder than the low one ("twist"), because the high group loses
# more on a real line. Their sum peaks at 69% of full scale, which leaves enough
# headroom that µ-law never clips — clipping is what makes a far-end detector
# reject a tone that otherwise looks correct.

_DTMF_ROWS = (697, 770, 852, 941)
_DTMF_COLS = (1209, 1336, 1477, 1633)
_DTMF_KEYS = "123A456B789C*0#D"

#: Which two frequencies each key is made of (ITU-T Q.23).
DTMF_PAIRS: dict[str, tuple[int, int]] = {
    key: (_DTMF_ROWS[i // 4], _DTMF_COLS[i % 4])
    for i, key in enumerate(_DTMF_KEYS)
}

DTMF_LOW_AMPLITUDE = 10_000     # ≈ -7.1 dBm0
DTMF_HIGH_AMPLITUDE = 12_600    # ≈ -5.1 dBm0, i.e. 2 dB of twist
DTMF_TONE_MS = 160              # well above the 40 ms a detector needs
DTMF_GAP_MS = 80                # silence after each tone, so digits stay distinct
DTMF_RAMP_MS = 5                # fade in and out, or the edges click and splatter


@lru_cache(maxsize=64)
def ulaw_dtmf(digit: str, duration_ms: int = DTMF_TONE_MS) -> bytes:
    """One keypad tone as µ-law 8 kHz audio.

    The two sine waves are generated across the whole tone in one pass. Building
    it frame by frame would restart each wave's phase every 20 ms, and those
    discontinuities are exactly what a far-end detector rejects.
    """
    key = digit.upper()
    if key not in DTMF_PAIRS:
        raise ValueError(f"{digit!r} is not a keypad digit")
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")

    low, high = DTMF_PAIRS[key]
    total = duration_ms * BYTES_PER_MS
    ramp = min(DTMF_RAMP_MS * BYTES_PER_MS, total // 2)
    step_low = 2 * math.pi * low / SAMPLE_RATE
    step_high = 2 * math.pi * high / SAMPLE_RATE

    pcm = bytearray(total * 2)
    for n in range(total):
        sample = (DTMF_LOW_AMPLITUDE * math.sin(step_low * n)
                  + DTMF_HIGH_AMPLITUDE * math.sin(step_high * n))
        if ramp:
            if n < ramp:
                sample *= 0.5 * (1 - math.cos(math.pi * n / ramp))
            elif n >= total - ramp:
                sample *= 0.5 * (1 - math.cos(math.pi * (total - 1 - n) / ramp))
        pcm[2 * n:2 * n + 2] = int(round(sample)).to_bytes(2, "little", signed=True)

    return pcm16_to_ulaw(bytes(pcm))


def ulaw_dtmf_burst(digits: str, *, tone_ms: int = DTMF_TONE_MS,
                    gap_ms: int = DTMF_GAP_MS) -> list[bytes]:
    """One µ-law blob per key of `digits`, each a tone followed by silence.

    Returned separately rather than joined so the caller sends one message per
    key: if the socket dies mid-burst, whole digits have arrived rather than
    half of one.
    """
    gap = ulaw_silence(gap_ms) if gap_ms else b""
    return [ulaw_dtmf(digit, tone_ms) + gap for digit in digits]
