"""Sample-rate conversion between Twilio and the translation endpoint.

Twilio speaks G.711 µ-law at 8 kHz. The `/v1/realtime/translations` endpoint
speaks 16-bit PCM at 24 kHz and will not negotiate — both `audio.input.format`
and `audio.output.format` are rejected outright. So this module exists solely
to bridge those two.

Three things here are easy to get wrong and miserable to debug later:

1. **Anti-aliasing.** Going 24k -> 8k by taking every third sample folds all
   energy above 4 kHz back into the audible band as distortion. It presents as
   "the audio sounds a bit rough", which sends you hunting in the wrong place.
   The low-pass has to run *before* the decimation.

2. **Filter state.** Audio arrives in chunks. A filter with no memory produces
   a discontinuity at every chunk boundary — with 20 ms frames that is an
   audible 50 Hz buzz. The filter tail carries across calls, which is why these
   are classes and not functions.

3. **Decimation phase.** Chunk lengths are not multiples of 3, so "take every
   third sample" has to remember where it was. Restarting at zero each chunk
   both drops and duplicates samples, drifting the playback clock.

8000 and 24000 divide exactly, so this is integer 3x conversion with no
fractional interpolation anywhere.
"""

from __future__ import annotations

import numpy as np

from app.utils import audio

TWILIO_RATE = 8000
OPENAI_RATE = 24000
RATIO = OPENAI_RATE // TWILIO_RATE          # exactly 3

# Below the 4 kHz Nyquist of the 8 kHz side, and near the top of the telephone
# band, so nothing worth keeping is lost.
CUTOFF_HZ = 3400
NUM_TAPS = 48


def _design_lowpass(cutoff_hz: int, rate: int, taps: int) -> np.ndarray:
    """Windowed-sinc FIR, computed once at import. scipy is not needed."""
    n = np.arange(taps) - (taps - 1) / 2.0
    kernel = np.sinc(2.0 * cutoff_hz / rate * n) * np.hamming(taps)
    return (kernel / kernel.sum()).astype(np.float32)


_LOWPASS = _design_lowpass(CUTOFF_HZ, OPENAI_RATE, NUM_TAPS)
GROUP_DELAY = (NUM_TAPS - 1) // 2           # samples, at 24 kHz


class _Filter:
    """Stateful FIR. Keeps the tail so chunks join without a discontinuity."""

    def __init__(self, kernel: np.ndarray) -> None:
        self._kernel = kernel
        self._tail = np.zeros(len(kernel) - 1, dtype=np.float32)

    def __call__(self, samples: np.ndarray) -> np.ndarray:
        padded = np.concatenate((self._tail, samples))
        self._tail = padded[len(padded) - (len(self._kernel) - 1):].copy()
        return np.convolve(padded, self._kernel, mode="valid").astype(np.float32)

    def reset(self) -> None:
        self._tail.fill(0.0)


def _to_float(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<i2").astype(np.float32)


def _to_pcm16(samples: np.ndarray) -> bytes:
    return np.clip(np.rint(samples), -32768, 32767).astype("<i2").tobytes()


class TwilioToOpenAI:
    """µ-law 8 kHz -> PCM16 24 kHz. One instance per inbound stream."""

    def __init__(self) -> None:
        self._filter = _Filter(_LOWPASS)

    def __call__(self, ulaw: bytes) -> bytes:
        if not ulaw:
            return b""
        pcm8k = _to_float(audio.ulaw_to_pcm16(ulaw))

        # Zero-stuff to 24 kHz, then low-pass away the spectral images the
        # stuffing creates. The RATIO gain restores the level the filter loses.
        stuffed = np.zeros(len(pcm8k) * RATIO, dtype=np.float32)
        stuffed[::RATIO] = pcm8k * RATIO

        return _to_pcm16(self._filter(stuffed))

    def reset(self) -> None:
        self._filter.reset()


class OpenAIToTwilio:
    """PCM16 24 kHz -> µ-law 8 kHz. One instance per outbound stream."""

    def __init__(self) -> None:
        self._filter = _Filter(_LOWPASS)
        self._odd = b""          # a trailing half-sample, held for next chunk
        self._offset = 0         # index of the next sample to keep

    def __call__(self, pcm24k: bytes) -> bytes:
        if not pcm24k:
            return b""

        data = self._odd + pcm24k
        if len(data) % 2:
            data, self._odd = data[:-1], data[-1:]
        else:
            self._odd = b""
        if not data:
            return b""

        # Low-pass first, decimate second. The other order is what aliases.
        filtered = self._filter(_to_float(data))

        indices = np.arange(self._offset, len(filtered), RATIO)
        decimated = filtered[indices]
        # Where the next chunk should start, so the 3x phase never slips.
        self._offset = (self._offset - len(filtered)) % RATIO

        return audio.pcm16_to_ulaw(_to_pcm16(decimated))

    def reset(self) -> None:
        self._filter.reset()
        self._odd = b""
        self._offset = 0


def ulaw_ms(ulaw: bytes) -> float:
    """Duration of a µ-law payload, in milliseconds."""
    return len(ulaw) / audio.BYTES_PER_MS


def pcm24k_ms(pcm: bytes) -> float:
    """Duration of a 24 kHz PCM16 payload, in milliseconds."""
    return len(pcm) / 2 / (OPENAI_RATE / 1000)
