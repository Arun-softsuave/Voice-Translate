"""Add a few made-up past calls for one account, to look at the history pages
without placing a real call. For local testing only. From backend/:

    python scripts/seed_demo_call.py someone@example.com [--calls 3]

The account must exist already (request access and approve it, or use
`python -m app.cli set-password`).
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import repo  # noqa: E402
from app.db.engine import session_scope  # noqa: E402
from app.db.models import ENDED, Call, TranscriptLine, utcnow  # noqa: E402

CONVERSATION = [
    ("A", "heard", "en", "Hello, is this the Chennai office?", 1.2),
    ("A", "spoke", "ta", "வணக்கம், இது சென்னை அலுவலகமா?", 3.4),
    ("B", "heard", "ta", "ஆமாம், சொல்லுங்கள். உங்களுக்கு என்ன உதவி வேண்டும்?", 6.0),
    ("B", "spoke", "en", "Yes, tell me. What help do you need?", 8.1),
    ("A", "heard", "en", "I wanted to confirm tomorrow's delivery at ten.", 11.5),
    ("A", "spoke", "ta", "நாளை பத்து மணிக்கு டெலிவரியை உறுதிப்படுத்த விரும்பினேன்.", 13.9),
    ("B", "heard", "ta", "சரி, பத்து மணிக்கு உறுதி.", 17.2),
    ("B", "spoke", "en", "Okay, confirmed for ten o'clock.", 19.0),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--calls", type=int, default=3)
    args = parser.parse_args()

    with session_scope() as db:
        user = repo.user_by_email(db, args.email)
        if user is None:
            print(f"No account for {args.email}.", file=sys.stderr)
            return 1
        now = utcnow()
        for n in range(args.calls):
            started = now - timedelta(days=n, minutes=7 * n)
            answered = started + timedelta(seconds=6)
            call = Call(
                session_id=f"seed_{user.id}_{started.timestamp():.0f}_{n}",
                user_id=user.id, phone_number="+919876543210",
                source_lang="en", target_lang="ta", status=ENDED,
                end_reason="user_ended" if n % 2 == 0 else "completed",
                started_at=started, answered_at=answered,
                ended_at=answered + timedelta(seconds=95 + 40 * n),
                duration_s=95.0 + 40 * n, model_usd=0.01 * (n + 1),
            )
            db.add(call)
            db.flush()
            for i, (owner, kind, lang, text, t) in enumerate(CONVERSATION, start=1):
                db.add(TranscriptLine(call_id=call.id, line_no=i, owner=owner, kind=kind,
                                      lang=lang, text=text, t_offset_s=t))
    print(f"Added {args.calls} sample call(s) for {args.email}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
