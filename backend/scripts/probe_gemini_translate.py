"""Check gemini-3.5-live-translate end to end, through the real phone path.

Takes recorded speech, degrades it to what a phone call actually carries
(G.711 µ-law, 8 kHz), and streams it in 20 ms frames, in real time, into the
SAME `GeminiTranslateSession` a live call uses. What comes back is again
µ-law 8 kHz, exactly what the other caller would hear.

    python scripts/probe_gemini_translate.py                      # Hindi -> Tamil
    python scripts/probe_gemini_translate.py --wav translate_probe_ta.wav --to hi
    python scripts/probe_gemini_translate.py --to te              # Hindi -> Telugu
    python scripts/probe_gemini_translate.py --wav translate_probe_ta.wav --from ta --to en --no-hint

The speaker's language (--from) and the target (--to) are sent to Gemini as
transcription hints, exactly as the app does; --no-hint leaves Gemini to guess,
for comparison. Both the "heard" transcript and the translation are checked for
the right script, which is how a Tamil speaker transcribed as Vietnamese shows up.

Needs GEMINI_API_KEY in backend/.env. The transcript answers "is it the right
language?" without anyone listening; the WAV lets you judge quality. Writes
gemini_probe_<to>.wav (8 kHz, as heard on the phone).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from dotenv import dotenv_values  # noqa: E402

from app.utils import audio, resample  # noqa: E402

FRAME_MS = 20
TAIL_S = 8.0          # keep streaming silence so the model can finish speaking

# Unicode blocks, so the verdict can say whether the OUTPUT is in the target's
# script rather than the source's.
SCRIPTS = {
    "ta": ("Tamil", "஀", "௿"),
    "hi": ("Devanagari", "ऀ", "ॿ"),
    "mr": ("Devanagari", "ऀ", "ॿ"),
    "ne": ("Devanagari", "ऀ", "ॿ"),
    "te": ("Telugu", "ఀ", "౿"),
    "kn": ("Kannada", "ಀ", "೿"),
    "ml": ("Malayalam", "ഀ", "ൿ"),
    "bn": ("Bengali", "ঀ", "৿"),
    "gu": ("Gujarati", "઀", "૿"),
    "pa": ("Gurmukhi", "਀", "੿"),
}
# Latin-script languages: basic + extended Latin, including Vietnamese letters.
LATIN = ((0x41, 0x24F), (0x1E00, 0x1EFF))
for _code in ("en", "es", "fr", "de", "it", "pt", "id", "vi"):
    SCRIPTS[_code] = ("Latin", None, None)


def read_wav_as_phone_audio(path: Path) -> bytes:
    """Any 24 kHz mono PCM16 WAV -> µ-law 8 kHz, as Twilio would deliver it."""
    with wave.open(str(path)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (24000, 1, 2):
            raise SystemExit(f"{path.name}: need 24 kHz mono 16-bit WAV, got "
                             f"{w.getframerate()} Hz x{w.getnchannels()} "
                             f"{8 * w.getsampwidth()}-bit")
        pcm24k = w.readframes(w.getnframes())
    return resample.OpenAIToTwilio()(pcm24k)


def write_ulaw_as_wav(path: Path, ulaw: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(resample.TWILIO_RATE)
        w.writeframes(audio.ulaw_to_pcm16(ulaw))


def in_script(text: str, code: str) -> float:
    """Share of letters in that language's script (0..1); -1 if unknown."""
    if code not in SCRIPTS:
        return -1.0
    _, lo, hi = SCRIPTS[code]
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    if lo is None:                                   # Latin
        ok = lambda c: any(a <= ord(c) <= b for a, b in LATIN)
    else:
        ok = lambda c: lo <= c <= hi
    return sum(ok(c) for c in letters) / len(letters)


def script_verdict(label: str, text: str, code: str) -> bool:
    share = in_script(text, code)
    if share < 0:
        print(f"    {label}: no script check for {code!r}")
        return True
    name = SCRIPTS[code][0]
    good = share >= 0.8
    print(f"    {'PASS' if good else 'FAIL'}  {label} is {share:.0%} {name} script"
          + ("" if good else " (wrong language detected)"))
    return good


async def run(args) -> int:
    env = dotenv_values(BACKEND / ".env")
    key = (env.get("GEMINI_API_KEY") or "").strip()
    if not key:
        print("\n  ERROR  GEMINI_API_KEY is missing from backend/.env")
        print("         Get one at https://aistudio.google.com/apikey\n")
        return 1

    # Imported late: app.config reads the environment at import time.
    from app.config import get_settings
    from app.services.gemini_service import GeminiTranslateSession

    settings = replace(get_settings(), translation_backend="gemini",
                       gemini_api_key=key,
                       gemini_translate_model=args.model or
                       env.get("GEMINI_TRANSLATE_MODEL") or
                       "gemini-3.5-live-translate-preview")

    ulaw_in = read_wav_as_phone_audio(BACKEND / args.wav)
    print(f"\n  PROBE  {settings.gemini_translate_model}  {args.source} -> {args.to}"
          f"   hints {'off' if args.no_hint else 'on'}")
    print(f"  input  {args.wav}: {resample.ulaw_ms(ulaw_in) / 1000:.1f}s, "
          f"sent as µ-law 8 kHz in {FRAME_MS} ms frames, real time\n")

    out = bytearray()
    heard: list[str] = []
    spoke: list[str] = []
    first_out_at: float | None = None

    async def on_audio(b64: str) -> None:
        nonlocal first_out_at
        if first_out_at is None:
            first_out_at = time.monotonic()
        out.extend(audio.b64_decode(b64))

    async def on_transcript(direction: str, text: str) -> None:
        (heard if direction == "in" else spoke).append(text)

    session = GeminiTranslateSession(
        settings, source_language=args.source, target_language=args.to,
        on_audio=on_audio, on_transcript=on_transcript, label="probe",
    )
    if args.no_hint:
        session._hints = False

    t0 = time.monotonic()
    try:
        await session.connect()
    except Exception as exc:  # noqa: BLE001
        print(f"  CONNECT FAILED  {type(exc).__name__}: {str(exc)[:300]}\n")
        return 1
    print(f"  1. connected in {(time.monotonic() - t0) * 1000:.0f} ms")

    frame = audio.BYTES_PER_MS * FRAME_MS
    silence = audio.ulaw_silence(FRAME_MS)
    started = time.monotonic()
    sent = 0

    async def send(chunk: bytes) -> None:
        nonlocal sent
        await session.append_audio(audio.b64_encode(chunk))
        sent += 1
        # Pace against the wall clock, not a fixed sleep, so drift can't build.
        delay = started + sent * FRAME_MS / 1000 - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    print("  2. streaming speech")
    for i in range(0, len(ulaw_in), frame):
        await send(ulaw_in[i:i + frame])
    speech_end = time.monotonic()

    print(f"  3. streaming {TAIL_S:.0f}s of silence, as a live line would")
    for _ in range(int(TAIL_S * 1000 / FRAME_MS)):
        await send(silence)

    await session.close()

    heard_text = "".join(heard).strip()
    spoke_text = "".join(spoke).strip()
    wav_path = BACKEND / f"gemini_probe_{args.to}.wav"
    if out:
        write_ulaw_as_wav(wav_path, bytes(out))

    print("\n  RESULT")
    print(f"    audio returned      {resample.ulaw_ms(out) / 1000:.1f}s (µ-law 8 kHz)")
    if first_out_at:
        print(f"    first audio after   {(first_out_at - started) * 1000:.0f} ms "
              f"from start of speech")
        if first_out_at < speech_end:
            print("    started             DURING the speech (streaming, not turn-based)")
        else:
            print(f"    started             {(first_out_at - speech_end) * 1000:.0f} ms "
                  "after speech ended")
    print(f"    model-reported      first_audio_ms={session.first_audio_ms}, "
          f"reconnects={session.reconnects}")
    print(f"    language hints      source={session.source_hint} target={session.target_hint}")
    print(f"    heard  (source)     {heard_text[:200]!r}")
    print(f"    spoke  (target)     {spoke_text[:200]!r}")
    print(f"    cost at paid rates  ${session.usage.usd:.4f}")
    if out:
        print(f"    saved               {wav_path}")

    print("\n  VERDICT")
    if not out and not spoke_text:
        print(f"    NO OUTPUT. {args.to!r} did not produce any translation.\n")
        return 1
    ok_heard = script_verdict("heard transcript", heard_text, args.source)
    ok_spoke = script_verdict("translation", spoke_text, args.to)
    print()
    return 0 if ok_heard and ok_spoke else 1


def main() -> int:
    # The Windows console defaults to cp1252, which cannot print Tamil or
    # Devanagari and would crash the report after the test itself succeeded.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wav", default="translate_probe_hi.wav",
                    help="24 kHz mono PCM16 speech, relative to backend/")
    ap.add_argument("--from", dest="source", default="hi",
                    help="the speaker's language code (default hi, matching the default WAV)")
    ap.add_argument("--to", default="ta", help="target language code")
    ap.add_argument("--no-hint", action="store_true",
                    help="don't tell Gemini the languages (compare with the default)")
    ap.add_argument("--model", default=None, help="override the model id")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
