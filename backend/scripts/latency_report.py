"""Where did a demo call spend its time? Reads the backend log, prints a table.

The backend saves its log to backend/logs/backend.log automatically, so after
a call just run:

    python scripts/latency_report.py --last 1      # newest call only
    python scripts/latency_report.py               # every call in the log
    python scripts/latency_report.py other.log     # a different log file

Uses only the latency_setup / latency_turn / latency_summary lines, which hold
timings and counts, never what anyone said. Non-JSON lines (uvicorn's own) are
skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "logs" / "backend.log"

STAGES = [
    ("model_ms", "Gemini (speaker starts -> translated speech at our server)"),
    ("twilio_ms", "Our server -> Twilio -> played (incl. echo back)"),
    ("start_lag_ms", "START LAG: speaker starts -> translation playing"),
    ("end_lag_ms", "END LAG: speaker stops -> translation finished"),
    ("speech_ms", "How long people spoke (context, not delay)"),
]


def load(lines):
    calls: "OrderedDict[str, dict]" = OrderedDict()
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        event = rec.get("event", "")
        if not event.startswith("latency_") or "session_id" not in rec:
            continue
        call = calls.setdefault(rec["session_id"], {"setup": {}, "turns": [], "summary": {}})
        if event == "latency_setup":
            call["setup"][rec.get("participant")] = rec
        elif event == "latency_turn":
            call["turns"].append(rec)
        elif event == "latency_summary":
            call["summary"][rec.get("speaker")] = rec
    return calls


def fmt(v) -> str:
    return "    -" if v is None else f"{v / 1000:5.2f}s"


def report(session_id: str, call: dict, out) -> None:
    w = out.write
    w(f"\n{'=' * 78}\nCALL {session_id}\n{'=' * 78}\n")

    if call["setup"]:
        w("\nSETUP (per leg, before anyone can be translated)\n")
        for leg, s in sorted(call["setup"].items()):
            w(f"  leg {leg}:  answered->TwiML {fmt(s.get('answered_to_twiml_ms'))}   "
              f"TwiML->stream {fmt(s.get('twiml_to_stream_ms'))}   "
              f"stream->translator ready {fmt(s.get('stream_to_ready_ms'))}\n")

    for speaker, s in sorted(call["summary"].items()):
        w(f"\nSPEAKER {speaker}  ({s.get('direction')})   sentences: {s.get('turns')}   "
          f"untranslated: {s.get('no_output')}\n")
        w(f"  {'stage':58} {'median':>7} {'p90':>7} {'worst':>7}\n")
        for key, label in STAGES:
            w(f"  {label:58} {fmt(s.get(key + '_p50')):>7} {fmt(s.get(key + '_p90')):>7} "
              f"{fmt(s.get(key + '_max')):>7}\n")
        rtt = s.get("twilio_rtt_ms")
        w(f"  network: server<->Twilio round trip {fmt(rtt)} "
          f"(~{fmt(rtt / 2 if rtt else None).strip()} each way)   "
          f"server<->model round trip {fmt(s.get('model_rtt_ms'))}\n")
        w(f"  audio arriving late at our server: worst {fmt(s.get('inbound_backlog_max_ms'))}, "
          f"p90 {fmt(s.get('inbound_backlog_p90_ms'))}\n")
        w(f"  translation cut off by barge-in: {s.get('cleared')} times, "
          f"{fmt(s.get('dropped_ms'))} of audio dropped\n")

    slow = sorted((t for t in call["turns"] if t.get("start_lag_ms") is not None),
                  key=lambda t: t["start_lag_ms"], reverse=True)[:3]
    if slow:
        w("\nSLOWEST SENTENCES\n")
        for t in slow:
            w(f"  speaker {t.get('speaker')} turn {t.get('turn')}: start lag "
              f"{fmt(t.get('start_lag_ms'))} = Gemini {fmt(t.get('model_ms'))} + "
              f"Twilio {fmt(t.get('twilio_ms'))}   (spoke {fmt(t.get('speech_ms'))})\n")

    missing = [t for t in call["turns"] if t.get("no_output")]
    if missing:
        w(f"\nUNTRANSLATED: {len(missing)} stretch(es) of speech got no translation "
          f"(very short speech, echo, or the model skipped it)\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", nargs="?", default=str(DEFAULT_LOG),
                    help="backend log (default: logs/backend.log; '-' for stdin)")
    ap.add_argument("--last", type=int, default=0, help="only the newest N calls")
    args = ap.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.log != "-":
        if not Path(args.log).exists():
            print(f"No log at {args.log}. Start the backend and make a call first.")
            return 1
        text = Path(args.log).read_bytes()
        # PowerShell's Tee-Object writes UTF-16; uvicorn piped elsewhere is UTF-8.
        lines = (text.decode("utf-16") if text[:2] in (b"\xff\xfe", b"\xfe\xff")
                 else text.decode("utf-8", errors="replace")).splitlines()
    else:
        lines = sys.stdin.read().splitlines()

    calls = load(lines)
    if not calls:
        print("No latency_* lines found. Make a call with the new backend running first.")
        return 1
    items = list(calls.items())[-args.last:] if args.last else list(calls.items())
    for session_id, call in items:
        report(session_id, call, sys.stdout)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
