"""How often does a transcript come back in the wrong language?

Gemini is told what to speak but never told what it is hearing: the source
language is detected, and the hint we send may be overruled. When it is, the
whole call is transcribed in another language while the translated audio stays
correct - the decision is made on the first utterance and everything follows.

Two things now work against that: a clip of the speaker's own language fed to
the session before the call, and a check on every transcript against the
Unicode block the chosen language is written in. This reports what they are
achieving, per call, so a change can be judged on numbers rather than on
whether the last call happened to go well.

    cd backend
    ..\\.venv\\Scripts\\python.exe scripts/script_report.py
    ..\\.venv\\Scripts\\python.exe scripts/script_report.py --since 2026-11-02

Only calls in a script-distinct language can be judged. English, Spanish and
French share an alphabet, so their lines are counted as "not judged" rather
than silently passed.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LOG = Path(__file__).resolve().parent.parent / "logs" / "backend.log"


def main(argv: list[str]) -> None:
    since = argv[argv.index("--since") + 1] if "--since" in argv else ""
    path = Path(argv[argv.index("--log") + 1]) if "--log" in argv else LOG
    if not path.exists():
        raise SystemExit(f"no log at {path}")

    calls: dict[str, dict] = defaultdict(
        lambda: {"when": "", "legs": {}, "primed": 0, "wrong": []})

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        when, event, sid = d.get("ts", ""), d.get("event"), d.get("session_id")
        if since and when < since:
            continue
        if sid is None:
            continue

        if event == "session_created":
            calls[sid]["when"] = when
        elif event == "prime_sent":
            calls[sid]["primed"] += 1
        elif event == "transcript_wrong_script":
            calls[sid]["wrong"].append(f"{d.get('expected')}->{d.get('found')}")
        elif event == "stream_closed" and d.get("heard_lines") is not None:
            calls[sid]["legs"][d.get("participant")] = (
                d.get("heard_lines", 0), d.get("wrong_script_lines", 0))

    rows = [(c["when"], sid, c) for sid, c in calls.items() if c["legs"]]
    rows.sort()
    if not rows:
        raise SystemExit("no finished calls in the log"
                         + (f" since {since}" if since else ""))

    print(f"{'call':<22} {'when':<17} {'primed':>6} {'lines':>6} {'wrong':>6}  detail")
    total_lines = total_wrong = primed_calls = 0
    for when, sid, c in rows:
        lines = sum(n for n, _ in c["legs"].values())
        wrong = sum(w for _, w in c["legs"].values())
        total_lines += lines
        total_wrong += wrong
        primed_calls += 1 if c["primed"] else 0
        detail = ", ".join(sorted(set(c["wrong"]))) if c["wrong"] else ""
        flag = "  <-- wrong language" if wrong else ""
        print(f"{sid[:22]:<22} {when[5:16]:<17} {c['primed']:>6} "
              f"{lines:>6} {wrong:>6}  {detail}{flag}")

    print()
    print(f"calls            : {len(rows)}  ({primed_calls} primed)")
    print(f"transcript lines : {total_lines}")
    if total_lines:
        rate = total_wrong / total_lines * 100
        print(f"wrong language   : {total_wrong}  ({rate:.1f}%)")
        print(f"correct          : {100 - rate:.1f}%")
    bad = [r for r in rows if sum(w for _, w in r[2]['legs'].values())]
    print(f"calls affected   : {len(bad)} of {len(rows)}")
    print()
    print("Lines in a Latin-script language are not judged and never counted")
    print("as wrong - see app/services/scripts.py.")


if __name__ == "__main__":
    main(sys.argv[1:])
