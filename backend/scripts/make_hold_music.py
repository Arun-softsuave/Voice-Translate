"""Generate the waiting music played to the person who answers.

Nothing can be translated until enough of a sentence has been said, so the
person who answered hears a few seconds of nothing after they pick up. Real
calls have ended with "Hello? Am I audible?" and a hang-up. Music says "wait,
something is coming" in every language at once.

Five styles are offered because this is a taste decision, not a technical one.
Audition them, then pick one:

    cd backend
    ..\\.venv\\Scripts\\python.exe scripts/make_hold_music.py --options
    ..\\.venv\\Scripts\\python.exe scripts/make_hold_music.py --use bells

`--options` writes every style to `app/data/music/options/` and playable
copies to your Downloads folder. `--use` promotes one to `wait.ulaw`, which is
the file a live call plays. To use a track of your own instead:

    ..\\.venv\\Scripts\\python.exe scripts/make_hold_music.py --from-wav track.wav

Mono 16-bit at 8000 or 24000 Hz, looping cleanly - the file repeats end to end
for as long as the wait lasts.

Every style is deliberately quiet. Their handset microphone picks the music up
and sends it to the translator along with their voice, so loud music would
corrupt what you hear.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.utils import audio, resample          # noqa: E402

MUSIC_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "music"
OUT = MUSIC_DIR / "wait.ulaw"
OPTIONS_DIR = MUSIC_DIR / "options"
AUDITION_DIR = Path.home() / "Downloads" / "hold_music_options"

RATE = 8000               # what Twilio plays
# Quiet on purpose, and the number that matters is not the peak but how this
# sits against speech. The detectors call audio "speech" above an RMS of 500.
# At 0.16 the music measured ~14600, twenty times over; at 0.07 it was still
# 759, louder than a quiet voice - which is why "quieter" did not sound
# quieter on a handset. This lands near 270: clearly underneath a
# conversation, and low enough that its echo in a microphone cannot be
# mistaken for somebody talking.
PEAK = 0.025
WRAP_S = 0.35             # crossfade so the loop seam is inaudible

# Notes, in Hz.
C4, D4, E4, F4, G4, A4, B4 = 261.6, 293.7, 329.6, 349.2, 392.0, 440.0, 493.9
C5, D5, E5, G5, A5 = 523.3, 587.3, 659.3, 784.0, 880.0
A3, C3, E3, F3, G3 = 220.0, 130.8, 164.8, 174.6, 196.0


def sine(hz: float, t: float) -> float:
    return math.sin(2 * math.pi * hz * t)


def add(buf: list[float], start_s: float, length_s: float, fn) -> None:
    """Mix a sound into the buffer over one span, without touching the rest."""
    begin = max(0, int(start_s * RATE))
    end = min(len(buf), int((start_s + length_s) * RATE))
    for n in range(begin, end):
        buf[n] += fn(n / RATE - start_s)


# --- the five styles -------------------------------------------------------

def style_bells(seconds: float = 8.0) -> list[float]:
    """Soft struck bells over a quiet sustained root. Calm, a little formal."""
    buf = [0.0] * int(RATE * seconds)
    chords = [(A3, [A4, C5, E5]), (F3, [A4, C5, F4 * 2]),
              (C3, [E4, G4, C5]), (G3, [G4, C5, E5])]
    span = seconds / len(chords)
    for i, (root, notes) in enumerate(chords):
        at = i * span
        add(buf, at, span + 0.6, lambda t, r=root, s=span:
            0.22 * min(t * 3, 1.0, max(0.0, (s + 0.6 - t) * 2)) * sine(r, t))
        for j, hz in enumerate(notes):
            add(buf, at + j * (span / len(notes)), seconds,
                lambda t, h=hz: 0.5 * math.exp(-t * 2.2)
                * (sine(h, t) + 0.25 * sine(h * 2, t)))
    return buf


def style_hum(seconds: float = 6.0) -> list[float]:
    """Two detuned tones beating slowly. Almost not music - just not silence."""
    buf = [0.0] * int(RATE * seconds)
    for hz, amp in ((A3, 0.5), (A3 * 1.005, 0.5), (E4, 0.22), (A4, 0.12)):
        add(buf, 0, seconds, lambda t, h=hz, a=amp:
            a * (0.75 + 0.25 * sine(0.18, t)) * sine(h, t))
    return buf


def style_melody(seconds: float = 8.0) -> list[float]:
    """A simple repeating phrase. The most recognisably 'on hold' of the five."""
    buf = [0.0] * int(RATE * seconds)
    phrase = [(E5, 0), (D5, 0.5), (C5, 1.0), (D5, 1.5),
              (E5, 2.0), (E5, 2.5), (D5, 3.0), (C5, 3.5)]
    for cycle in (0.0, 4.0):
        for hz, at in phrase:
            add(buf, cycle + at, 1.2, lambda t, h=hz:
                0.45 * math.exp(-t * 3.0) * (sine(h, t) + 0.2 * sine(h * 3, t)))
        add(buf, cycle, 4.2, lambda t:
            0.18 * min(t * 2, 1.0, max(0.0, (4.2 - t) * 2)) * sine(A3, t))
    return buf


def style_drone(seconds: float = 8.0) -> list[float]:
    """A sustained chord that drifts. No notes to follow, nothing to annoy."""
    buf = [0.0] * int(RATE * seconds)
    for hz, amp, rate in ((C3, 0.55, 0.07), (G3, 0.35, 0.11),
                          (E4, 0.22, 0.13), (B4, 0.12, 0.17)):
        add(buf, 0, seconds, lambda t, h=hz, a=amp, r=rate:
            a * (0.6 + 0.4 * sine(r, t)) * sine(h, t))
    return buf


def style_plucks(seconds: float = 8.0) -> list[float]:
    """Sparse short notes with space between them. Modern and unobtrusive."""
    buf = [0.0] * int(RATE * seconds)
    pattern = [(A4, 0.0), (C5, 0.75), (E5, 1.5), (D5, 2.75),
               (C5, 3.5), (A4, 4.25), (G4, 5.5), (C5, 6.25)]
    for hz, at in pattern:
        add(buf, at, 1.0, lambda t, h=hz:
            0.5 * math.exp(-t * 5.5) * (sine(h, t) + 0.3 * sine(h * 2, t)))
    add(buf, 0, seconds, lambda t: 0.1 * sine(A3, t))
    return buf


STYLES = {
    "bells": (style_bells, "Soft struck bells over a quiet root - calm, formal"),
    "hum": (style_hum, "Two detuned tones beating slowly - barely music at all"),
    "melody": (style_melody, "A short repeating phrase - classic 'on hold'"),
    "drone": (style_drone, "A sustained chord that drifts - nothing to follow"),
    "plucks": (style_plucks, "Sparse short notes with space - modern, unobtrusive"),
}


# --- shared finishing ------------------------------------------------------

def finish(buf: list[float]) -> bytes:
    """Wrap the tail into the head, normalise quietly, and encode for Twilio."""
    total = len(buf)
    wrap = int(WRAP_S * RATE)
    for n in range(wrap):
        blend = n / wrap
        buf[n] = buf[n] * blend + buf[total - wrap + n] * (1 - blend)
    del buf[total - wrap:]

    loudest = max((abs(s) for s in buf), default=1.0) or 1.0
    scale = PEAK * 32767 / loudest
    pcm = b"".join(struct.pack("<h", int(max(-32768, min(32767, s * scale))))
                   for s in buf)
    return audio.pcm16_to_ulaw(pcm)


def audition(name: str, ulaw: bytes) -> Path:
    """A playable copy, looped three times so the seam can be judged."""
    AUDITION_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDITION_DIR / f"{name}.wav"
    pcm = audio.ulaw_to_pcm16(ulaw)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm * 3)
    return path


def from_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1:
            raise SystemExit(f"{path.name}: needs to be mono")
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: needs to be 16-bit PCM")
        rate, frames = w.getframerate(), w.readframes(w.getnframes())
    if rate == 24000:
        return resample.OpenAIToTwilio()(frames)
    if rate == 8000:
        return audio.pcm16_to_ulaw(frames)
    raise SystemExit(f"{path.name}: {rate} Hz - save it as 8000 or 24000 Hz mono")


# --- commands --------------------------------------------------------------

def do_options() -> None:
    OPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(STYLES)} styles\n")
    for name, (make, blurb) in STYLES.items():
        ulaw = finish(make())
        (OPTIONS_DIR / f"{name}.ulaw").write_bytes(ulaw)
        wav = audition(name, ulaw)
        print(f"  {name:8} {audio.duration_ms(ulaw) / 1000:4.1f}s   {blurb}")
        print(f"           {wav}")
    print(f"\nListen, then choose:")
    print(f"  python scripts/make_hold_music.py --use <name>")


def do_use(name: str) -> None:
    source = OPTIONS_DIR / f"{name}.ulaw"
    if not source.exists():
        if name not in STYLES:
            raise SystemExit(f"no style called {name!r}. "
                             f"Choose from: {', '.join(STYLES)}")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(finish(STYLES[name][0]()))
    OUT.write_bytes(source.read_bytes())
    print(f"{name} is now the waiting music "
          f"({audio.duration_ms(OUT.read_bytes()) / 1000:.1f}s)")
    print("Restart the backend for it to take effect.")


def main(argv: list[str]) -> None:
    if "--options" in argv:
        return do_options()

    if "--use" in argv:
        try:
            return do_use(argv[argv.index("--use") + 1])
        except IndexError:
            raise SystemExit(f"usage: --use <{'|'.join(STYLES)}>")

    if "--from-wav" in argv:
        try:
            src = Path(argv[argv.index("--from-wav") + 1])
        except IndexError:
            raise SystemExit("usage: --from-wav <file.wav>")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_bytes(from_wav(src))
        print(f"{src.name} is now the waiting music")
        return

    raise SystemExit(__doc__.split("Five styles")[1].split("Every style")[0].strip())


if __name__ == "__main__":
    main(sys.argv[1:])
