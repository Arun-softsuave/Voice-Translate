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

# Barge-in: cutting the translation someone is hearing because they started to
# talk. A demo call cut translations 52 times in 4 minutes (~28 s of speech
# lost), almost all on coughs, line noise and echo of the translation itself
# coming back through the phone's microphone. So it needs far more evidence
# than "someone may have started talking":
BARGE_IN_FRAMES = 18            # ~360 ms of loud frames in one burst, not 60 ms
BARGE_IN_RELEASE_FRAMES = 10    # ~200 ms of quiet ends a burst
BARGE_IN_THRESHOLD = DEFAULT_THRESHOLD
# While their translation is playing, what the microphone hears is mostly that
# translation's echo. Only clearly louder, direct speech should interrupt it.
BARGE_IN_PLAYING_THRESHOLD = 1400.0


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
        return self.feed_level(frame_rms(ulaw))

    def feed_level(self, rms: float) -> bool:
        """`feed` for a frame whose RMS the caller has already computed."""
        loud = rms >= self.threshold

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


@dataclass
class BargeInDetector:
    """Decides when someone is really talking over the translation they hear.

    Separate from SpeechDetector on purpose: that one marks when speech starts
    (for latency timing) and should react fast; this one throws translated
    audio away, so it must be slow to fire.

    It fires once per burst of speech, after BARGE_IN_FRAMES loud frames. The
    gaps between syllables don't reset the count; only BARGE_IN_RELEASE_FRAMES
    of quiet do. While a translation is playing to this person, "loud" means
    BARGE_IN_PLAYING_THRESHOLD, so its echo cannot trigger it.
    """

    frames: int = BARGE_IN_FRAMES
    release_frames: int = BARGE_IN_RELEASE_FRAMES
    threshold: float = BARGE_IN_THRESHOLD
    playing_threshold: float = BARGE_IN_PLAYING_THRESHOLD

    fired: int = 0          # cuts allowed
    ignored: int = 0        # bursts of sound that were too short or too quiet
    _loud: int = field(default=0, repr=False)
    _quiet: int = field(default=0, repr=False)
    _done: bool = field(default=False, repr=False)

    def feed(self, rms: float, *, playing: bool) -> bool:
        """True only on the frame where a real interruption is confirmed."""
        limit = self.playing_threshold if playing else self.threshold
        if rms >= limit:
            self._quiet = 0
            self._loud += 1
            if not self._done and self._loud >= self.frames:
                self._done = True
                self.fired += 1
                return True
            return False

        if self._loud:
            self._quiet += 1
            if self._quiet >= self.release_frames:
                if not self._done:
                    self.ignored += 1
                self._loud = 0
                self._quiet = 0
                self._done = False
        return False
