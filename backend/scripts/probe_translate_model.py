"""Decide whether gpt-realtime-translate can serve this project.

Generates real speech with OpenAI TTS, streams it to the translation endpoint,
and reports what comes back — transcript and audio. The transcript answers the
Tamil question in text, without anyone needing to listen.

    python scripts/probe_translate_model.py --to ta
    python scripts/probe_translate_model.py --to hi --say "..." --voice alloy

Writes the translated audio to translate_probe_<lang>.wav so you can listen.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import struct
import time
from pathlib import Path

import httpx
import websockets
from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
TRANSLATE_URL = "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate"

# OpenAI TTS `pcm` output is 24 kHz, 16-bit, mono — the same shape the
# translation endpoint expects, so no resampling is needed for this probe.
TTS_RATE = 24000
CHUNK_MS = 40


def wav(path: Path, pcm: bytes, rate: int = TTS_RATE) -> None:
    """Minimal 16-bit mono WAV wrapper so the result is playable."""
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(pcm))
    path.write_bytes(header + pcm)


def ws_kwargs(key: str) -> dict:
    headers = {"Authorization": f"Bearer {key}"}
    kwargs: dict = {"max_size": None}
    param = ("additional_headers"
             if "additional_headers" in inspect.signature(websockets.connect).parameters
             else "extra_headers")
    kwargs[param] = headers
    return kwargs


async def synthesise(key: str, text: str, voice: str) -> bytes:
    print(f"  1. synthesising source speech ({len(text)} chars, voice={voice})")
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "gpt-4o-mini-tts", "voice": voice,
                  "input": text, "response_format": "pcm"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"TTS failed {r.status_code}: {r.text[:200]}")
    pcm = r.content
    print(f"     {len(pcm)} bytes = {len(pcm)/2/TTS_RATE:.1f}s of audio")
    return pcm


async def translate(key: str, pcm: bytes, target: str) -> dict:
    out_audio = bytearray()
    out_text: list[str] = []
    in_text: list[str] = []
    events: dict[str, int] = {}
    first_audio_at: float | None = None

    async with await websockets.connect(TRANSLATE_URL, **ws_kwargs(key)) as ws:
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "audio": {
                    "input": {"transcription": {"model": "gpt-realtime-whisper"}},
                    "output": {"language": target},
                },
            },
        }))

        async def reader():
            nonlocal first_audio_at
            try:
                while True:
                    ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                    kind = ev.get("type", "?")
                    events[kind] = events.get(kind, 0) + 1

                    if kind.endswith("output_audio.delta"):
                        if first_audio_at is None:
                            first_audio_at = time.monotonic()
                        out_audio.extend(base64.b64decode(ev["delta"]))
                    elif kind.endswith("output_transcript.delta"):
                        out_text.append(ev.get("delta", ""))
                    elif kind.endswith("input_transcript.delta"):
                        in_text.append(ev.get("delta", ""))
                    elif kind == "error":
                        e = ev.get("error", {})
                        print(f"     ERROR {e.get('code')}: {e.get('message')}")
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass

        task = asyncio.create_task(reader())

        print(f"  2. streaming audio in {CHUNK_MS}ms chunks, target={target!r}")
        started = time.monotonic()
        step = int(TTS_RATE * 2 * CHUNK_MS / 1000)

        async def send_chunk(data: bytes) -> None:
            await ws.send(json.dumps({
                "type": "session.input_audio_buffer.append",
                "audio": base64.b64encode(data).decode(),
            }))
            await asyncio.sleep(CHUNK_MS / 1000)   # real time, as a call would

        for i in range(0, len(pcm), step):
            await send_chunk(pcm[i:i + step])

        # The model expects a CONTINUOUS stream, silence included — it uses the
        # ongoing audio to decide when an utterance has finished. Stopping the
        # stream at end of speech yields transcripts but no audio.
        print("  3. holding the stream open with silence")
        silence = bytes(step)   # PCM16 silence
        for _ in range(int(8000 / CHUNK_MS)):
            await send_chunk(silence)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return {
        "audio": bytes(out_audio),
        "out_text": "".join(out_text).strip(),
        "in_text": "".join(in_text).strip(),
        "events": events,
        "first_audio_ms": round((first_audio_at - started) * 1000) if first_audio_at else None,
    }


def looks_tamil(text: str) -> bool:
    return any("஀" <= ch <= "௿" for ch in text)


def looks_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", default="ta", help="target language code")
    ap.add_argument("--voice", default="alloy")
    ap.add_argument("--say", default=(
        "नमस्ते, मैं आपकी मदद करना चाहता हूँ। कृपया मुझे बताइए कि "
        "आपका नाम क्या है और आप कहाँ से बात कर रहे हैं।"
    ), help="source text (Hindi by default)")
    args = ap.parse_args()

    key = (dotenv_values(ENV_PATH).get("OPENAI_API_KEY") or "").strip()
    if not key:
        print("\n  ERROR  OPENAI_API_KEY missing from .env\n")
        return 1

    print(f"\n  PROBE  gpt-realtime-translate  ->  {args.to}\n")
    pcm = await synthesise(key, args.say, args.voice)
    result = await translate(key, pcm, args.to)

    out = Path(__file__).resolve().parent.parent / f"translate_probe_{args.to}.wav"
    if result["audio"]:
        wav(out, result["audio"])

    print("\n  RESULT")
    print(f"    events seen        {result['events']}")
    print(f"    audio returned     {len(result['audio'])} bytes "
          f"({len(result['audio'])/2/TTS_RATE:.1f}s)")
    print(f"    first audio after  {result['first_audio_ms']} ms")
    print(f"    heard  (source)    {result['in_text'][:160]!r}")
    print(f"    spoke  (target)    {result['out_text'][:160]!r}")
    if result["audio"]:
        print(f"    saved              {out}")

    print("\n  VERDICT")
    text = result["out_text"]
    if not result["audio"] and not text:
        print(f"    NO OUTPUT - {args.to!r} appears unsupported as a target.")
        return 1
    if args.to == "ta":
        if looks_tamil(text):
            print("    Output is in TAMIL SCRIPT - Tamil works as a target.")
        elif looks_devanagari(text):
            print("    Output is DEVANAGARI, not Tamil - it ignored the target and")
            print("    returned the source language. Tamil is NOT usable.")
        elif text:
            print("    Output is neither Tamil nor Devanagari - inspect it above.")
    else:
        print(f"    Produced output for {args.to!r}; check the transcript above.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
