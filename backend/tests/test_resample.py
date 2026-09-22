"""Sample-rate conversion tests.

Two of these matter far more than the rest:

* `test_no_discontinuity_across_chunk_boundaries` catches a stateless filter,
  which produces an audible buzz at the chunk rate.
* `test_high_tone_is_attenuated_not_aliased` catches decimating without a
  low-pass, which folds high frequencies back into the voice band as
  distortion.

Both sound like "the audio is a bit rough" in a live call, which is the worst
possible way to find out about them.
"""

import math

import numpy as np
import pytest

from app.utils import audio, resample
from app.utils.resample import OPENAI_RATE, RATIO, TWILIO_RATE, OpenAIToTwilio, TwilioToOpenAI


def tone(hz: float, ms: int, rate: int, amplitude: int = 12000) -> bytes:
    n = int(rate * ms / 1000)
    t = np.arange(n) / rate
    samples = (amplitude * np.sin(2 * math.pi * hz * t)).astype("<i2")
    return samples.tobytes()


def pcm_to_array(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype="<i2").astype(np.float64)


def peak_hz(raw: bytes, rate: int) -> float:
    """Dominant frequency, via FFT."""
    samples = pcm_to_array(raw)
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / rate)[np.argmax(spectrum)])


def rms(raw: bytes) -> float:
    samples = pcm_to_array(raw)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


# --------------------------------------------------------------- basic shape
def test_ratio_is_exact():
    assert OPENAI_RATE / TWILIO_RATE == 3.0
    assert RATIO == 3


def test_upsample_triples_the_sample_count():
    ulaw = audio.ulaw_silence(20)                 # 160 bytes = 160 samples
    out = TwilioToOpenAI()(ulaw)
    assert len(out) // 2 == len(ulaw) * RATIO     # 480 samples of PCM16


def test_downsample_thirds_the_sample_count():
    pcm = tone(440, 100, OPENAI_RATE)             # 2400 samples
    out = OpenAIToTwilio()(pcm)
    assert len(out) == pytest.approx(2400 // RATIO, abs=2)


def test_duration_is_preserved_both_ways():
    ulaw = audio.ulaw_silence(200)
    pcm = TwilioToOpenAI()(ulaw)
    assert resample.pcm24k_ms(pcm) == pytest.approx(200, abs=1)
    back = OpenAIToTwilio()(pcm)
    assert resample.ulaw_ms(back) == pytest.approx(200, abs=2)


def test_empty_input_is_handled():
    assert TwilioToOpenAI()(b"") == b""
    assert OpenAIToTwilio()(b"") == b""


# ------------------------------------------------------------------- quality
def test_voice_band_tone_survives_the_round_trip():
    """A 1 kHz tone is well inside the telephone band and must come back."""
    src = tone(1000, 200, TWILIO_RATE)
    ulaw = audio.pcm16_to_ulaw(src)

    up = TwilioToOpenAI()(ulaw)
    assert peak_hz(up, OPENAI_RATE) == pytest.approx(1000, abs=40)

    down = OpenAIToTwilio()(up)
    assert peak_hz(audio.ulaw_to_pcm16(down), TWILIO_RATE) == pytest.approx(1000, abs=40)


def test_high_tone_is_attenuated_not_aliased():
    """THE aliasing test.

    A 6 kHz tone at 24 kHz cannot exist at 8 kHz. Decimating without a
    low-pass would fold it down to |8000 - 6000| = 2 kHz — a loud, wrong tone
    sitting in the middle of the voice band. Correct behaviour is for it to be
    filtered away, leaving near-silence.
    """
    loud = tone(6000, 200, OPENAI_RATE)
    out = OpenAIToTwilio()(loud)
    result = audio.ulaw_to_pcm16(out)

    reference = rms(tone(1000, 200, TWILIO_RATE))
    assert rms(result) < reference * 0.2, "6 kHz leaked through — check the low-pass"

    # And specifically: it must NOT have reappeared as a 2 kHz alias.
    if rms(result) > 200:
        assert abs(peak_hz(result, TWILIO_RATE) - 2000) > 300, "aliased to 2 kHz"


def test_amplitude_is_roughly_preserved():
    src = tone(800, 200, TWILIO_RATE, amplitude=10000)
    up = TwilioToOpenAI()(audio.pcm16_to_ulaw(src))
    assert rms(up) == pytest.approx(rms(src), rel=0.25)


def test_silence_stays_silent():
    up = TwilioToOpenAI()(audio.ulaw_silence(100))
    assert rms(up) < 30
    down = OpenAIToTwilio()(bytes(2 * 2400))
    assert rms(audio.ulaw_to_pcm16(down)) < 30


# ------------------------------------------------------------- the hard ones
def test_no_discontinuity_across_chunk_boundaries():
    """THE filter-state test.

    Feed one continuous tone as many small chunks. If the filter forgets its
    tail between calls, each boundary gets a step change — an audible click at
    the chunk rate. Compare against the same signal converted in one go.
    """
    src = tone(1000, 400, TWILIO_RATE)
    ulaw = audio.pcm16_to_ulaw(src)

    whole = TwilioToOpenAI()(ulaw)

    chunked_converter = TwilioToOpenAI()
    frame = 160                                   # 20 ms, as Twilio sends
    chunked = b"".join(
        chunked_converter(ulaw[i:i + frame]) for i in range(0, len(ulaw), frame)
    )

    assert len(chunked) == len(whole)
    a, b = pcm_to_array(whole), pcm_to_array(chunked)
    assert np.max(np.abs(a - b)) < 2, "chunked output differs — filter is stateless"


def test_decimation_phase_does_not_slip():
    """Chunk lengths are not multiples of 3, so the phase must carry.

    Restarting at index 0 each chunk would both duplicate and drop samples,
    slowly drifting the playback clock over a long call.
    """
    converter = OpenAIToTwilio()
    total_in = 0
    total_out = 0
    for size_ms in (37, 41, 23, 200, 17):          # deliberately awkward
        chunk = tone(500, size_ms, OPENAI_RATE)
        total_in += len(chunk) // 2
        total_out += len(converter(chunk))

    assert total_out == pytest.approx(total_in / RATIO, abs=2)


def test_odd_byte_chunks_do_not_corrupt_the_stream():
    """A chunk ending mid-sample must be carried, not dropped."""
    pcm = tone(700, 120, OPENAI_RATE)
    converter = OpenAIToTwilio()
    out = b"".join((converter(pcm[:301]), converter(pcm[301:])))

    clean = OpenAIToTwilio()(pcm)
    assert abs(len(out) - len(clean)) <= 2


def test_reset_clears_state():
    converter = TwilioToOpenAI()
    converter(audio.pcm16_to_ulaw(tone(1000, 100, TWILIO_RATE)))
    converter.reset()
    after = converter(audio.ulaw_silence(100))
    assert rms(after) < 30, "reset left energy in the filter"


def test_converters_are_independent():
    """Two concurrent calls must not share filter state."""
    a, b = TwilioToOpenAI(), TwilioToOpenAI()
    loud = audio.pcm16_to_ulaw(tone(1000, 100, TWILIO_RATE, amplitude=20000))

    a(loud)                                        # only a sees signal
    quiet = b(audio.ulaw_silence(100))

    assert rms(quiet) < 30, "state leaked between converter instances"
