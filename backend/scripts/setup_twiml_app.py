"""Create or update the TwiML App that backs the browser call leg.

Run this once to create the app, and again after every ngrok restart to point
it at the new tunnel. It is idempotent: it finds the app by SID if .env
already has one, otherwise by friendly name, otherwise it creates it.

    python scripts/setup_twiml_app.py
    python scripts/setup_twiml_app.py --url https://abc123.ngrok-free.app

On success the TwiML App SID is written back into .env for you.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from dotenv import dotenv_values
from twilio.rest import Client

APP_NAME = "voice-translation"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def die(message: str) -> None:
    print(f"\n  ERROR  {message}\n", file=sys.stderr)
    raise SystemExit(1)


def write_env_value(path: Path, key: str, value: str) -> bool:
    """Replace `key=...` in place, preserving comments and ordering."""
    if not path.exists():
        return False
    original = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(original):
        updated = pattern.sub(f"{key}={value}", original, count=1)
    else:
        updated = original.rstrip("\n") + f"\n{key}={value}\n"
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Public HTTPS base URL (overrides BACKEND_PUBLIC_URL)")
    parser.add_argument("--name", default=APP_NAME, help="TwiML App friendly name")
    args = parser.parse_args()

    if not ENV_PATH.exists():
        die(f"No .env found at {ENV_PATH}. Copy .env.example to .env first.")

    env = dotenv_values(ENV_PATH)

    account_sid = (env.get("TWILIO_ACCOUNT_SID") or "").strip()
    api_key = (env.get("TWILIO_API_KEY") or "").strip()
    api_secret = (env.get("TWILIO_API_SECRET") or "").strip()
    existing_sid = (env.get("TWILIO_TWIML_APP_SID") or "").strip()

    if not account_sid or account_sid.startswith("ACxxxx"):
        die("TWILIO_ACCOUNT_SID is not set in .env")
    if not api_key or api_key.startswith("SKxxxx") or not api_secret:
        die("TWILIO_API_KEY / TWILIO_API_SECRET are not set in .env")

    base = (args.url or env.get("BACKEND_PUBLIC_URL") or "").strip().rstrip("/")
    if not base:
        die(
            "No public URL. Start ngrok, then either set BACKEND_PUBLIC_URL in .env\n"
            "         or pass it here:  python scripts/setup_twiml_app.py --url https://xxxx.ngrok-free.app"
        )
    if not base.startswith("https://"):
        die(f"The public URL must start with https:// — got {base!r}")

    voice_url = f"{base}/api/call/twiml/A"
    client = Client(api_key, api_secret, account_sid)

    app = None
    if existing_sid and not existing_sid.startswith("APxxxx"):
        try:
            app = client.applications(existing_sid).fetch()
            print(f"  found    app from .env        {app.sid}")
        except Exception:
            print(f"  warning  {existing_sid} no longer exists; looking for another")

    if app is None:
        matches = client.applications.list(friendly_name=args.name, limit=1)
        if matches:
            app = matches[0]
            print(f"  found    app by name          {app.sid}")

    if app is None:
        app = client.applications.create(
            friendly_name=args.name, voice_url=voice_url, voice_method="POST"
        )
        print(f"  created  TwiML App            {app.sid}")
    else:
        app = client.applications(app.sid).update(
            voice_url=voice_url, voice_method="POST"
        )
        print(f"  updated  voice URL")

    print(f"  voice url                     {voice_url}")

    changed = write_env_value(ENV_PATH, "TWILIO_TWIML_APP_SID", app.sid)
    if args.url:
        changed |= write_env_value(ENV_PATH, "BACKEND_PUBLIC_URL", base)

    print(f"  .env                          {'updated' if changed else 'already current'}")
    print("\n  Done. Start the backend:  uvicorn app.main:app --port 8000\n")


if __name__ == "__main__":
    main()
