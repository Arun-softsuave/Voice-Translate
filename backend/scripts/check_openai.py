"""Pre-flight check for the OpenAI Realtime connection.

Opens a session, sends our exact session.update, and reports whether the API
accepted it. Costs a fraction of a cent — no audio is sent — and isolates
"is OpenAI configured correctly" from "is the call working".

    python scripts/check_openai.py
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from pathlib import Path

import websockets
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.realtime_service import INSTRUCTIONS, REALTIME_URL  # noqa: E402

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


async def main() -> int:
    env = dotenv_values(ENV_PATH)
    key = (env.get("OPENAI_API_KEY") or "").strip()
    model = (env.get("OPENAI_REALTIME_MODEL") or "gpt-realtime-2.1").strip()

    if not key:
        print("\n  ERROR  OPENAI_API_KEY is empty in .env\n")
        return 1

    print(f"  key            {key[:8]}… ({len(key)} chars)")
    print(f"  model          {model}")
    if len(key) < 100:
        print("  warning        that key looks short; project keys are usually"
              " 150+ chars.\n                 If auth fails, re-copy it in full.")

    url = f"{REALTIME_URL}?model={model}"
    headers = {"Authorization": f"Bearer {key}"}
    kwargs = {"max_size": None}
    if "additional_headers" in inspect.signature(websockets.connect).parameters:
        kwargs["additional_headers"] = headers
    else:  # pragma: no cover
        kwargs["extra_headers"] = headers

    print(f"  connecting     {url}")
    try:
        async with await websockets.connect(url, **kwargs) as ws:
            print("  connected      ok")

            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": INSTRUCTIONS.format(source="Tamil", target="Hindi"),
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcmu"},
                            "turn_detection": {"type": "server_vad",
                                               "silence_duration_ms": 400},
                        },
                        "output": {"format": {"type": "audio/pcmu"},
                                   "voice": "alloy"},
                    },
                },
            }))

            for _ in range(10):
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                kind = event.get("type")
                if kind == "session.updated":
                    audio = event.get("session", {}).get("audio", {})
                    print(f"  session        accepted")
                    print(f"  input format   {audio.get('input', {}).get('format')}")
                    print(f"  output format  {audio.get('output', {}).get('format')}")
                    print("\n  PASS - Realtime is configured correctly.\n")
                    return 0
                if kind == "error":
                    err = event.get("error", {})
                    print(f"\n  FAIL - {err.get('code')}: {err.get('message')}\n")
                    return 1
                print(f"  event          {kind}")
    except asyncio.TimeoutError:
        print("\n  FAIL - no response within 15s\n")
        return 1
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        print(f"\n  FAIL - {type(exc).__name__}: {detail[:200]}")
        if "401" in detail or "403" in detail:
            print("\n  HINT   The key was rejected. Check it is complete and that"
                  "\n         billing is enabled for Realtime on the project.")
        elif "404" in detail:
            print(f"\n  HINT   Model {model!r} not found or not available to this"
                  "\n         project. Check OPENAI_REALTIME_MODEL.")
        print()
        return 1

    print("\n  FAIL - session was never confirmed\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
