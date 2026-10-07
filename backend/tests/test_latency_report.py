"""scripts/latency_report.py turns a saved backend log into a per-call table."""

import importlib.util
import io
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "latency_report.py"
spec = importlib.util.spec_from_file_location("latency_report", SCRIPT)
report_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report_mod)


def line(event, **fields):
    return json.dumps({"ts": "2026-10-07T10:00:00+00:00", "level": "INFO",
                       "logger": "x", "event": event, **fields})


LOG = [
    "INFO:     Uvicorn running on http://127.0.0.1:8000",       # not JSON
    line("session_created", session_id="sess_1"),                # not latency
    line("latency_setup", session_id="sess_1", participant="B",
         answered_to_twiml_ms=140, twiml_to_stream_ms=760, stream_to_ready_ms=710),
    line("latency_turn", session_id="sess_1", speaker="A", turn=1, speech_ms=2000,
         model_ms=3050, twilio_ms=410, start_lag_ms=3460, end_lag_ms=3380,
         no_output=False),
    line("latency_turn", session_id="sess_1", speaker="A", turn=2, speech_ms=800,
         model_ms=None, twilio_ms=None, start_lag_ms=None, end_lag_ms=None,
         no_output=True),
    line("latency_summary", session_id="sess_1", speaker="A", direction="en->es",
         turns=2, no_output=1, cleared=3, dropped_ms=1250,
         model_ms_p50=3050, model_ms_p90=3050, model_ms_max=3050,
         twilio_ms_p50=410, start_lag_ms_p50=3460, twilio_rtt_ms=290,
         model_rtt_ms=40, inbound_backlog_max_ms=730, inbound_backlog_p90_ms=20),
    line("session_created", session_id="sess_2"),
    line("latency_turn", session_id="sess_2", speaker="B", turn=1,
         start_lag_ms=4000, model_ms=3500, twilio_ms=500, no_output=False),
]


def test_groups_lines_by_call_and_skips_everything_else():
    calls = report_mod.load(LOG)
    assert list(calls) == ["sess_1", "sess_2"]
    assert len(calls["sess_1"]["turns"]) == 2
    assert "B" in calls["sess_1"]["setup"]
    assert "A" in calls["sess_1"]["summary"]


def test_report_names_the_stages_with_seconds():
    out = io.StringIO()
    report_mod.report("sess_1", report_mod.load(LOG)["sess_1"], out)
    text = out.getvalue()

    assert "Gemini" in text and " 3.05s" in text
    assert "server<->Twilio round trip  0.29s" in text
    assert "audio arriving late at our server: worst  0.73s" in text
    assert "cut off by barge-in: 3 times" in text
    assert "UNTRANSLATED: 1" in text
    assert "SLOWEST SENTENCES" in text


def test_reads_powershell_utf16_logs(tmp_path, capsys, monkeypatch):
    """Tee-Object in Windows PowerShell writes UTF-16."""
    path = tmp_path / "backend.log"
    path.write_text("\n".join(LOG), encoding="utf-16")
    monkeypatch.setattr("sys.argv", ["latency_report.py", str(path), "--last", "1"])

    assert report_mod.main() == 0
    out = capsys.readouterr().out
    assert "sess_2" in out and "sess_1" not in out
