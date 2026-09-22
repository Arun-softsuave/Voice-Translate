"""What Twilio has actually charged, from the account's own usage records.

Read-only. Places no calls.

    python scripts/check_twilio_cost.py
    python scripts/check_twilio_cost.py --calls 15
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from dotenv import dotenv_values
from twilio.rest import Client

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
USD_TO_INR = 95.96


def mask(number: str | None) -> str:
    if not number:
        return "-"
    digits = "".join(c for c in number if c.isdigit())
    return "+" + "*" * max(len(digits) - 4, 0) + digits[-4:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--calls", type=int, default=10, help="recent calls to list")
    args = ap.parse_args()

    env = dotenv_values(ENV_PATH)
    sid = (env.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (env.get("TWILIO_AUTH_TOKEN") or "").strip()
    if not sid or not token:
        print("\n  ERROR  Twilio credentials missing from .env\n")
        return 1

    client = Client(sid, token)

    # --- balance ----------------------------------------------------------
    print("\n  BALANCE")
    try:
        bal = client.balance.fetch()
        print(f"    remaining        {bal.balance} {bal.currency}")
    except Exception as exc:  # noqa: BLE001
        print(f"    (unreadable: {exc})")

    # --- all-time usage by category --------------------------------------
    print("\n  USAGE TO DATE (categories with any spend or volume)")
    print(f"    {'category':28} {'count':>7} {'usage':>10} {'USD':>10}")
    print("    " + "-" * 58)
    total = 0.0
    try:
        for rec in client.usage.records.all_time.list(limit=300):
            price = float(rec.price or 0)
            count = int(rec.count or 0)
            usage = float(rec.usage or 0)
            if price == 0 and count == 0:
                continue
            total += price
            print(f"    {rec.category:28} {count:>7} "
                  f"{usage:>7.1f} {rec.usage_unit or '':<3} {price:>9.4f}")
    except Exception as exc:  # noqa: BLE001
        print(f"    (unreadable: {exc})")

    print("    " + "-" * 58)
    print(f"    {'TOTAL':28} {'':>7} {'':>11} {total:>9.4f}")
    print(f"    {'':28} {'':>7} {'':>11} Rs {total * USD_TO_INR:>7.2f}")

    # --- recent calls ------------------------------------------------------
    print(f"\n  LAST {args.calls} CALLS")
    print(f"    {'started':17} {'to':16} {'dir':22} {'sec':>4} {'USD':>9}")
    print("    " + "-" * 74)
    call_spend = 0.0
    try:
        for call in client.calls.list(limit=args.calls):
            price = abs(float(call.price or 0))
            call_spend += price
            started = call.start_time.strftime("%d %b %H:%M") if call.start_time else "-"
            print(f"    {started:17} {mask(call.to):16} {str(call.direction):22} "
                  f"{call.duration or 0:>4} {price:>9.4f}")
    except Exception as exc:  # noqa: BLE001
        print(f"    (unreadable: {exc})")
    print("    " + "-" * 74)
    print(f"    {'':17} {'':16} {'':22} {'':>4} {call_spend:>9.4f}")

    print("\n  NOTE  Twilio prices a call only after it completes, so a call in")
    print("        progress shows 0. Media Streams are billed separately from")
    print("        the voice minutes, under their own category.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
