"""Energy-based voice activity detection, used for barge-in.

Design doc §8.2. The translation endpoint emits no `speech_started` event and
offers no way to cancel a response, so interruption cannot depend on provider
events. Detecting it ourselves also means both translation backends behave
identically, rather than one having better barge-in than the other.

This is deliberately simple: root-mean-square energy over a frame, with a
consecutive-frame requirement to reject clicks and a hangover to stop a single
pause mid-sentence from being treated as the end of speech.

It runs on the µ-law frames Twilio already delivers, so it costs one table
lookup per sample and no extra network or model work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.utils import audio

# Tuned for telephone-band speech at 8 kHz. µ-law full scale is 32767, ordinary
# speech sits around 1000-6000 RMS, and line noise well below 300.
DEFAULT_THRESHOLD = 700.0
DEFAULT_ONSET_FRAMES = 3        # ~60 ms at 20 ms/frame — ignores clicks
DEFAULT_HANGOVER_FRAMES = 25    # ~500 ms — a pause is not the end of a turn


def frame_rms(ulaw: bytes) -> float:
    """RMS energy of one µ-law frame, in PCM16 units."""
    if not ulaw:
        return 0.0
    pcm = audio.ulaw_to_pcm16(ulaw)
    total = 0
    for i in range(0, len(pcm), 2):
        sample = int.from_bytes(pcm[i:i + 2], "little", signed=True)
        total += sample * sample
    return (total / (len(pcm) // 2)) ** 0.5


@dataclass
class SpeechDetector:
    """Per-stream speech state.

    `feed()` returns True on the single frame where speech *starts*, so callers
    can treat it as an edge rather than having to track the previous value.
    """

    threshold: float = DEFAULT_THRESHOLD
    onset_frames: int = DEFAULT_ONSET_FRAMES
    hangover_frames: int = DEFAULT_HANGOVER_FRAMES

    speaking: bool = False
    _loud_run: int = field(default=0, repr=False)
    _quiet_run: int = field(default=0, repr=False)

    def feed(self, ulaw: bytes) -> bool:
        """Returns True only on the frame where speech begins."""
        loud = frame_rms(ulaw) >= self.threshold

        if loud:
            self._quiet_run = 0
            self._loud_run += 1
            if not self.speaking and self._loud_run >= self.onset_frames:
                self.speaking = True
                return True
            return False

        self._loud_run = 0
        if self.speaking:
            self._quiet_run += 1
            if self._quiet_run >= self.hangover_frames:
                self.speaking = False
                self._quiet_run = 0
        return False

    def reset(self) -> None:
        self.speaking = False
        self._loud_run = 0
        self._quiet_run = 0
