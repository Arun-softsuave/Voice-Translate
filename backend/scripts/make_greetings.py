"""Generate the spoken greeting played to the person who answers.

A translated call is silent for about three seconds after someone speaks,
because nothing can be translated until enough of a sentence has been said.
Nobody tells the person who answered, so they read that silence as a dead
line - real calls have ended with "Hello? Am I audible?" and a hang-up.

One short sentence in their own language fixes that. It is played INTO the
open call rather than before it, so it never delays the connection, and it is
cut off the moment there is something real to hear.

Run this once. The wording and the clips are committed, so a live call never
waits on a speech service:

    cd backend
    ..\\.venv\\Scripts\\python.exe scripts/make_greetings.py --translate   # wording
    ..\\.venv\\Scripts\\python.exe scripts/make_greetings.py               # audio
    ..\\.venv\\Scripts\\python.exe scripts/make_greetings.py ta en         # just these
    ..\\.venv\\Scripts\\python.exe scripts/make_greetings.py --from-wav ta mine.wav

Both steps skip what already exists, so an interrupted run resumes where it
stopped. `--force` regenerates anyway.

The wording lives in `app/data/greetings/text.json` as plain editable text.
Machine translation of 70-odd languages cannot be checked by eye, so correct
any sentence there by hand and re-run for that one language; nothing is lost.

A language with no clip simply gets no greeting. That is the safe direction,
and it is why a missing or failed language never needs handling elsewhere.
"""

from __future__ import annotations

import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import languages                # noqa: E402
from app.utils import audio, resample             # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "greetings"
TEXT_FILE = OUT_DIR / "text.json"

# Short on purpose: it competes with the first thing either person says, and
# whatever has not played by then is cut off.
MASTER = "This call is being translated. Please pause after speaking."

TEXT_MODEL = "gemini-3.5-flash"   # 2.5-flash is retired for new keys
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"        # calm and even; this is an announcement, not a greeting
CHUNK = 12                # languages per translation request
MAX_SECONDS = 6.0         # usually cut off around 3s, so longer than this is wasted
PACE_S = 6.0              # the speech model rate-limits in bursts; stay under it
RETRIES = 4


_CLIENT = None


def client():
    """One client for the whole run - a fresh one per call closes itself."""
    global _CLIENT
    if _CLIENT is None:
        from google import genai

        from app.config import get_settings

        key = get_settings().gemini_api_key
        if not key:
            raise SystemExit("GEMINI_API_KEY is not set in backend/.env")
        _CLIENT = genai.Client(api_key=key)
    return _CLIENT


def load_text() -> dict[str, str]:
    if TEXT_FILE.exists():
        return json.loads(TEXT_FILE.read_text(encoding="utf-8"))
    return {"en": MASTER}


def save_text(text: dict[str, str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ordered = {c: text[c] for c in languages.ALL if c in text}
    TEXT_FILE.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def translate(codes: list[str]) -> dict[str, str]:
    """Translate the master sentence. Returns whatever came back cleanly."""
    from google.genai import types

    names = {c: languages.name_of(c) for c in codes}
    listing = "\n".join(f"  {c} = {n}" for c, n in names.items())
    ask = (
        "Translate this announcement into each language below. It is spoken "
        "aloud to someone who has just answered a phone call, so keep it short, "
        "plain and natural - not literal. Use the language's own script.\n\n"
        f"Announcement: {MASTER}\n\n"
        f"Languages:\n{listing}\n\n"
        "Return only a JSON object mapping each code to its translation."
    )
    reply = client().models.generate_content(
        model=TEXT_MODEL,
        contents=ask,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    got = json.loads(reply.text)
    return {c: got[c].strip() for c in codes
            if isinstance(got.get(c), str) and got[c].strip()}


def speak(text: str) -> bytes:
    """Ask Gemini for the sentence as speech. Returns 24 kHz PCM16.

    No language is specified: the model reads it from the script, which keeps
    this working for every language without a table of region codes.
    """
    from google.genai import types

    reply = client().models.generate_content(
        model=TTS_MODEL,
        # Handed bare text the model tries to reply to it instead of reading it.
        contents=f"Say this clearly and calmly, and say nothing else: {text}",
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=TTS_VOICE)),
            ),
        ),
    )
    for part in reply.candidates[0].content.parts:
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            return inline.data
    raise RuntimeError("the model returned no audio")


def speak_patiently(text: str) -> bytes:
    """`speak`, waiting out the rate limiter rather than giving up on it.

    Generating 70-odd clips runs into a per-minute cap roughly half way
    through. Waiting is the whole fix - the cap resets on its own.
    """
    for attempt in range(RETRIES):
        try:
            return speak(text)
        except Exception as exc:                                  # noqa: BLE001
            last = exc
            if attempt + 1 == RETRIES:
                break
            if "RESOURCE_EXHAUSTED" in str(exc) or "429" in str(exc):
                wait = 30 * (attempt + 1)
                print(f"         rate limited, waiting {wait}s", flush=True)
            else:
                wait = 4          # an empty response; usually fine on a retry
            time.sleep(wait)
    raise last


def from_wav(path: Path) -> bytes:
    """A recording of your own, converted the same way."""
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
    raise SystemExit(
        f"{path.name}: {rate} Hz. Save it as 24000 Hz or 8000 Hz mono 16-bit "
        "- the resampler only handles whole-number ratios."
    )


def write(code: str, ulaw: bytes) -> float:
    seconds = audio.duration_ms(ulaw) / 1000
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{code}.ulaw").write_bytes(ulaw)
    return seconds


def do_translate(force: bool) -> None:
    text = load_text()
    todo = [c for c in languages.ALL if force or c not in text]
    if not todo:
        print(f"all {len(text)} languages already have wording")
        return

    print(f"translating {len(todo)} languages in chunks of {CHUNK}")
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        label = " ".join(chunk)
        try:
            got = translate(chunk)
        except Exception as exc:                                  # noqa: BLE001
            print(f"  {label}  failed: {type(exc).__name__}: {str(exc)[:90]}")
            continue
        text.update(got)
        save_text(text)       # after every chunk, so a crash keeps the work
        missing = sorted(set(chunk) - set(got))
        note = f", missing {' '.join(missing)}" if missing else ""
        print(f"  {label}  -> {len(got)} ok{note}")

    print(f"wording for {len(text)} languages in {TEXT_FILE.name}")


def do_audio(wanted: list[str], force: bool) -> None:
    text = load_text()
    if not wanted:
        wanted = list(text)

    absent = [c for c in wanted if c not in text]
    if absent:
        print(f"no wording for {' '.join(absent)} - run --translate first")
    wanted = [c for c in wanted if c in text]

    todo = [c for c in wanted if force or not (OUT_DIR / f"{c}.ulaw").exists()]
    have = len(wanted) - len(todo)
    if have:
        print(f"{have} already generated, skipping")
    if not todo:
        return

    print(f"generating {len(todo)} clips")
    long, failed = [], []
    for n, code in enumerate(todo, 1):
        if n > 1:
            time.sleep(PACE_S)
        try:
            seconds = write(code,
                            resample.OpenAIToTwilio()(speak_patiently(text[code])))
        except Exception as exc:                                  # noqa: BLE001
            failed.append(code)
            print(f"  [{n}/{len(todo)}] {code:4} failed: "
                  f"{type(exc).__name__}: {str(exc)[:80]}")
            continue
        flag = ""
        if seconds > MAX_SECONDS:
            long.append(code)
            flag = "  (long - will usually be cut off)"
        print(f"  [{n}/{len(todo)}] {code:4} {seconds:4.1f}s{flag}")

    if long:
        print(f"\nlonger than {MAX_SECONDS}s: {' '.join(long)}")
        print("  shorten them in text.json, then re-run those codes with --force")
    if failed:
        print(f"\nfailed: {' '.join(failed)}")
        print("  re-run to retry just those - finished clips are skipped")


def main(argv: list[str]) -> None:
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]

    if "--from-wav" in argv:
        i = argv.index("--from-wav")
        try:
            code, wav = argv[i + 1], Path(argv[i + 2])
        except IndexError:
            raise SystemExit("usage: --from-wav <language> <file.wav>")
        print(f"{code}  {write(code, from_wav(wav)):.1f}s  from {wav.name}")
        return

    if "--translate" in argv:
        do_translate(force)
        return

    codes = [a for a in argv if not a.startswith("-")]
    unknown = [c for c in codes if c not in languages.ALL]
    if unknown:
        raise SystemExit(f"not languages this app offers: {' '.join(unknown)}")
    do_audio(codes, force)


if __name__ == "__main__":
    main(sys.argv[1:])
