"""Read-only diagnostic of the Twilio account.

Answers, without placing a call: is this account trial or full, which numbers
are verified, which numbers we own, and whether India is enabled for dialling.

Each section is independent — a permissions failure in one must not hide the
answer from another.

    python scripts/check_twilio.py +918754677067
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import dotenv_values
from twilio.rest import Client

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def mask(number: str) -> str:
    digits = "".join(c for c in number if c.isdigit())
    return "+" + "*" * max(len(digits) - 4, 0) + digits[-4:]


def section(title: str) -> None:
    print(f"\n  {title}")


def main() -> int:
    env = dotenv_values(ENV_PATH)
    account_sid = (env.get("TWILIO_ACCOUNT_SID") or "").strip()
    auth_token = (env.get("TWILIO_AUTH_TOKEN") or "").strip()
    api_key = (env.get("TWILIO_API_KEY") or "").strip()
    api_secret = (env.get("TWILIO_API_SECRET") or "").strip()
    caller_id = (env.get("TWILIO_PHONE_NUMBER") or "").strip()

    if not account_sid:
        print("\n  ERROR  TWILIO_ACCOUNT_SID missing from .env\n")
        return 1

    # Two clients: a Standard API key cannot read the Accounts resource, and
    # some deployments only have one of the two credential pairs available.
    by_token = Client(account_sid, auth_token) if auth_token else None
    by_key = Client(api_key, api_secret, account_sid) if api_key and api_secret else None

    def try_each(fn, label):
        errors = []
        for name, client in (("auth token", by_token), ("api key", by_key)):
            if client is None:
                continue
            try:
                return fn(client), None
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
        return None, f"{label} unreadable -> " + " | ".join(errors)

    trial = None
    section("ACCOUNT")
    account, err = try_each(lambda c: c.api.accounts(account_sid).fetch(), "account")
    if account is not None:
        print(f"    friendly name    {account.friendly_name}")
        print(f"    type             {account.type}")
        print(f"    status           {account.status}")
        trial = str(account.type).lower() == "trial"
        if trial:
            print("    -> TRIAL: outbound calls only reach VERIFIED numbers")
    else:
        print(f"    {err}")

    section("VERIFIED CALLER IDs (the only numbers a trial can call)")
    verified, err = try_each(lambda c: list(c.outgoing_caller_ids.list(limit=50)), "caller ids")
    if verified is not None:
        if not verified:
            print("    (none)")
        for item in verified:
            print(f"    {item.phone_number:18} {item.friendly_name}")
    else:
        print(f"    {err}")

    section("NUMBERS OWNED BY THIS ACCOUNT")
    owned, err = try_each(lambda c: list(c.incoming_phone_numbers.list(limit=50)), "numbers")
    if owned is not None:
        if not owned:
            print("    (none)")
        for number in owned:
            caps = number.capabilities or {}
            flags = ",".join(k for k, v in caps.items() if v)
            marker = "  <- TWILIO_PHONE_NUMBER" if number.phone_number == caller_id else ""
            print(f"    {number.phone_number:18} [{flags}]{marker}")
        if caller_id and not any(n.phone_number == caller_id for n in owned):
            print(f"    WARNING  {caller_id} in .env is not owned by this account")
    else:
        print(f"    {err}")

    section("DIALLING PERMISSIONS - INDIA")
    india, err = try_each(
        lambda c: c.voice.dialing_permissions.countries("IN").fetch(), "geo permissions"
    )
    if india is not None:
        print(f"    low risk numbers     {india.low_risk_numbers_enabled}")
        print(f"    high risk special    {india.high_risk_special_numbers_enabled}")
        print(f"    high risk tollfraud  {india.high_risk_tollfraud_numbers_enabled}")
        if not india.low_risk_numbers_enabled:
            print("    -> DISABLED. Console -> Voice -> Settings -> Geo Permissions")
    else:
        print(f"    {err}")

    target = next((a for a in sys.argv[1:] if a.startswith("+")), None)
    section("VERDICT")
    if target and verified is not None:
        ok = any(v.phone_number == target for v in verified)
        print(f"    {mask(target)} verified: {'YES' if ok else 'NO'}")
        if not ok:
            print("    -> This is why the call is rejected with error 21219.")
            print("       Console -> Phone Numbers -> Manage -> Verified Caller IDs")
            print("       -> Add a new Caller ID")
    elif trial:
        print("    Trial account: only the verified numbers above can be called.")
    elif trial is False:
        print("    Full account: any destination allowed by geo permissions.")
    else:
        print("    Could not determine. See errors above.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
