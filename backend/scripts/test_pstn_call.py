"""Dial a real phone from the server — no TwiML App, no browser needed.

Server-originated calls carry their own instruction URL, so this exercises the
PSTN leg and the Media Stream on its own. With ECHO_MODE=true the person who
answers hears their own voice come back, which proves the whole audio path.

    python scripts/test_pstn_call.py --to +918754677067

Requirements:
  * the backend running and reachable on the public URL in .env
  * India enabled in Console -> Voice -> Settings -> Geo Permissions
  * on a trial account, the destination must be a verified number
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# The failures worth explaining rather than dumping a stack trace for.
TWILIO_HINTS = {
    21215: "India is not enabled on this account.\n"
           "           Console -> Voice -> Settings -> Geo Permissions -> enable India.",
    21210: "The 'from' number is not owned by this account or is not voice-capable.",
    21211: "The 'to' number is not a valid E.164 number.",
    21219: "Trial account: this destination is not verified.\n"
           "           Console -> Phone Numbers -> Verified Caller IDs.",
    21606: "The 'from' number cannot place outbound calls. Use your Twilio number.",
}


def die(message: str) -> None:
    print(f"\n  ERROR  {message}\n", file=sys.stderr)
    raise SystemExit(1)


def mask(number: str) -> str:
    digits = "".join(c for c in number if c.isdigit())
    return "+" + "*" * max(len(digits) - 4, 0) + digits[-4:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", required=True, help="Destination in E.164, e.g. +918754677067")
    parser.add_argument("--source-language", default="hi")
    parser.add_argument("--target-language", default="ta")
    parser.add_argument("--local", default="http://localhost:8000",
                        help="Where the backend is running locally")
    args = parser.parse_args()

    env = dotenv_values(ENV_PATH)
    account_sid = (env.get("TWILIO_ACCOUNT_SID") or "").strip()
    api_key = (env.get("TWILIO_API_KEY") or "").strip()
    api_secret = (env.get("TWILIO_API_SECRET") or "").strip()
    caller_id = (env.get("TWILIO_PHONE_NUMBER") or "").strip()
    public_url = (env.get("BACKEND_PUBLIC_URL") or "").strip().rstrip("/")
    echo_mode = (env.get("ECHO_MODE") or "").strip().lower() in ("1", "true", "yes")

    if not all([account_sid, api_key, api_secret]):
        die("Twilio credentials are missing from .env")
    if not caller_id:
        die("TWILIO_PHONE_NUMBER is not set — this is the caller ID for the call")
    if not public_url.startswith("https://"):
        die("BACKEND_PUBLIC_URL must be your public https:// tunnel")
    if caller_id.startswith("+91"):
        die("The caller ID must be a NON-Indian number. Twilio does not support\n"
            "         India-to-India calling; outbound calls to India must come from\n"
            "         an international number.")

    # 1. Create the session inside the running backend so the media stream can
    #    identify itself when Twilio connects.
    try:
        response = httpx.post(
            f"{args.local}/api/call/start",
            json={
                "source_language": args.source_language,
                "target_language": args.target_language,
                "phone_number": args.to,
            },
            timeout=10,
        )
    except httpx.HTTPError:
        die(f"Cannot reach the backend at {args.local}. Is uvicorn running?")

    if response.status_code >= 400:
        die(f"Backend refused to create a session: {response.status_code} {response.text}")

    session_id = response.json()["session_id"]
    print(f"  session                       {session_id}")

    # 2. Dial. The instruction URL travels with the call, so no TwiML App is
    #    involved for this leg.
    twiml_url = f"{public_url}/api/call/twiml/B?session_id={session_id}"
    client = Client(api_key, api_secret, account_sid)

    try:
        call = client.calls.create(
            to=args.to,
            from_=caller_id,
            url=twiml_url,
            status_callback=f"{public_url}/api/call/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
        )
    except TwilioRestException as exc:
        hint = TWILIO_HINTS.get(exc.code)
        die(f"Twilio rejected the call ({exc.code}): {exc.msg}"
            + (f"\n\n  HINT   {hint}" if hint else ""))

    print(f"  calling                       {mask(args.to)}")
    print(f"  from                          {caller_id}")
    print(f"  call sid                      {call.sid}")
    print(f"  twiml url                     {twiml_url}")
    print()
    if echo_mode:
        print("  ECHO_MODE is on — answer the phone and speak. You should hear")
        print("  your own voice come back. That proves the full audio path.")
    else:
        print("  ECHO_MODE is off, so you will hear silence after answering.")
        print("  Set ECHO_MODE=true in .env and restart the backend to hear yourself.")
    print("\n  Watch the backend logs for: twiml_served -> stream_connected -> stream_bound\n")


if __name__ == "__main__":
    main()
