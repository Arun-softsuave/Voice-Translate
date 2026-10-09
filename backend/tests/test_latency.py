"""Per-stage latency measurement.

The property that matters most: the delay must not grow with how long someone
speaks. A 6-second sentence and a 1-second one, translated with the same lag,
must report the same start lag and end lag.

Also covered: near-silent model output is not "translation started", output is
tied to the right sentence, untranslated speech is counted, Twilio marks used
for timing never disturb the barge-in accounting, and nothing but numbers is
logged.
"""

import asyncio
import json
import logging
import time
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models.session import ParticipantId
from app.services import latency as L
from app.services.latency import InboundClock, LatencyTracker, MarkClock
from app.services.session_service import SessionRegistry, registry
from app.utils import audio
from app.websocket import media_stream

A, B = ParticipantId.A, ParticipantId.B


def speak(tracker, start, stop):
    tracker.speech_started(start)
    tracker.speech_stopped(stop)


def translate(tracker, first, last, step=0.1, twilio_delay=0.4):
    """Feed speech chunks from `first` to `last`; echo every mark after a delay."""
    t = first
    while t <= last + 1e-9:
        mark = tracker.audio_out(t, is_speech=True)
        if mark:
            tracker.played(mark, t + twilio_delay)
        t = round(t + step, 6)


# ------------------------------------------------------------- the two lags
def test_a_long_sentence_does_not_inflate_the_lag():
    """6 s of speech translated 3 s behind: both lags are ~3.4 s, not 9 s."""
    tr = LatencyTracker("A")
    speak(tr, 10.0, 16.0)
    translate(tr, 13.0, 19.0)

    (rec,) = tr.flush(30.0)
    assert rec["speech_ms"] == 6000
    assert rec["model_ms"] == 3000
    assert rec["twilio_ms"] == 400
    assert rec["start_lag_ms"] == 3400
    assert rec["end_lag_ms"] == 3400          # 19.0 + 0.4 - 16.0
    assert rec["no_output"] is False


def test_short_and_long_sentences_report_the_same_lag():
    lags = []
    for length in (1.0, 6.0):
        tr = LatencyTracker("A")
        speak(tr, 0.0, length)
        translate(tr, 3.0, 3.0 + length)
        (rec,) = tr.flush(60.0)
        lags.append((rec["start_lag_ms"], rec["end_lag_ms"]))
    assert lags[0] == lags[1]


def test_near_silent_output_is_not_translation():
    """Gemini streams quiet filler before the real speech; it must not count."""
    tr = LatencyTracker("A")
    speak(tr, 0.0, 2.0)
    assert tr.audio_out(1.5, is_speech=False) is None
    translate(tr, 3.1, 4.0)

    (rec,) = tr.flush(20.0)
    assert rec["model_ms"] == 3100


def test_heard_is_measured_from_speech_start():
    tr = LatencyTracker("A")
    speak(tr, 5.0, 7.0)
    tr.heard(7.6)
    translate(tr, 7.7, 8.5)
    (rec,) = tr.flush(20.0)
    assert rec["heard_ms"] == 2600


# ------------------------------------------------------------- turns
def test_a_short_pause_continues_the_same_sentence():
    tr = LatencyTracker("A")
    speak(tr, 0.0, 2.0)
    speak(tr, 2.0 + L.TURN_GAP_S - 0.5, 6.0)        # resumes within the gap
    translate(tr, 3.0, 9.0)

    (rec,) = tr.flush(30.0)
    assert rec["speech_ms"] == 6000


def test_a_long_pause_starts_a_new_sentence():
    tr = LatencyTracker("A")
    speak(tr, 0.0, 2.0)
    translate(tr, 3.0, 5.0)
    speak(tr, 2.0 + L.TURN_GAP_S + 1.0, 8.0)
    translate(tr, 9.0, 11.0)

    records = tr.flush(30.0)
    assert [r["turn"] for r in records] == [1, 2]
    assert [r["model_ms"] for r in records] == [3000, 3000]


def test_output_right_after_a_new_sentence_belongs_to_the_previous_one():
    """The model never answers within MIN_LAG_S, so this is still turn 1's tail."""
    tr = LatencyTracker("A")
    speak(tr, 0.0, 1.0)
    translate(tr, 3.0, 4.5)
    speak(tr, 4.2, 6.0)                              # new turn: 4.2 > 1.0 + gap
    tr.audio_out(4.2 + L.MIN_LAG_S - 0.3, is_speech=True)   # 4.9: turn 1
    translate(tr, 7.2, 8.0)                          # turn 2

    t1, t2 = tr.flush(30.0)
    assert t1["output_ms"] == round((4.9 - 3.0) * 1000)
    assert t2["model_ms"] == 3000


def test_speech_that_is_never_translated_is_counted():
    tr = LatencyTracker("A")
    speak(tr, 0.0, 1.0)
    assert tr.finalize_due(1.0 + L.NO_OUTPUT_S - 0.1) == []
    (rec,) = tr.finalize_due(1.0 + L.NO_OUTPUT_S + 0.1)
    assert rec["no_output"] is True
    assert rec["model_ms"] is None
    assert tr.summary()["no_output"] == 1


def test_a_turn_is_finalised_when_its_translation_goes_quiet():
    tr = LatencyTracker("A")
    speak(tr, 0.0, 2.0)
    translate(tr, 3.0, 5.0)
    assert tr.finalize_due(5.0 + L.IDLE_FINAL_S - 0.1) == []
    assert len(tr.finalize_due(5.0 + L.IDLE_FINAL_S + 0.1)) == 1


def test_flush_closes_a_turn_still_being_spoken():
    tr = LatencyTracker("A")
    tr.speech_started(0.0)
    (rec,) = tr.flush(4.0)
    assert rec["speech_ms"] == 4000


def test_output_before_any_speech_is_counted_as_orphaned():
    tr = LatencyTracker("A")
    assert tr.audio_out(5.0, is_speech=True) is None
    assert tr.summary()["orphan_chunks"] == 1


# ------------------------------------------------------------- marks
def test_marks_are_throttled():
    tr = LatencyTracker("A")
    speak(tr, 0.0, 1.0)
    marks = [tr.audio_out(3.0 + i * 0.1, True) for i in range(20)]   # 2 s of chunks
    sent = [m for m in marks if m]
    assert sent[0] == "lat:A:1:1", "first speech chunk must always be marked"
    assert len(sent) == 4                       # one per MARK_EVERY_S


def test_unknown_or_duplicate_marks_are_ignored():
    tr = LatencyTracker("A")
    speak(tr, 0.0, 1.0)
    mark = tr.audio_out(3.0, True)
    tr.played("lat:A:99:1", 3.2)
    tr.played("garbage", 3.2)
    tr.played(mark, 3.4)
    tr.played(mark, 9.9)                        # duplicate echo
    (rec,) = tr.flush(20.0)
    assert rec["twilio_ms"] == 400


def test_mark_clock_takes_the_quietest_round_trip():
    mc = MarkClock()
    for i, rtt in enumerate([0.9, 0.31, 0.5]):
        mc.sent(f"chk-{i}", 10.0)
        mc.acked(f"chk-{i}", 10.0 + rtt)
    assert mc.acked("chk-unknown", 99.0) is None
    assert mc.rtt_ms == 310


def test_inbound_clock_sees_a_stall():
    """Frames delayed 700 ms behind Twilio's own clock, then caught up."""
    ic = InboundClock()
    for i in range(100):
        ts = i * 20
        arrival = 50.0 + ts / 1000 + (0.7 if 40 <= i < 50 else 0.0)
        ic.frame(str(ts), arrival)
    ic.frame(None, 99.0)                        # malformed timestamps are skipped
    s = ic.summary()
    assert s["inbound_backlog_max_ms"] == 700
    assert s["inbound_backlog_p90_ms"] <= 700


# ------------------------------------------------------------- summary
def test_summary_reports_median_p90_and_worst():
    tr = LatencyTracker("A")
    t = 0.0
    for model_s in (2.8, 3.0, 3.2, 3.4, 5.0):
        speak(tr, t, t + 1.0)
        translate(tr, t + model_s, t + model_s + 0.5)
        t += 20.0
        tr.finalize_due(t)
    s = tr.summary()
    assert s["turns"] == 5
    assert s["model_ms_p50"] == 3200
    assert s["model_ms_max"] == 5000
    assert 3400 <= s["model_ms_p90"] <= 5000


def test_barge_in_cuts_are_totalled():
    tr = LatencyTracker("A")
    tr.playback_cleared(500)
    tr.playback_cleared(1250)
    tr.playback_cleared(-3)                     # never negative
    s = tr.summary()
    assert (s["cleared"], s["dropped_ms"]) == (3, 1750)


# ------------------------------------------------------------- wiring
class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


class Recorder:
    def __init__(self):
        self.kwargs = None

    def __call__(self, settings, **kwargs):
        self.kwargs = kwargs
        return self

    @property
    def __name__(self):
        return "Recorder"

    async def connect(self):
        return None


def loud_chunk() -> str:
    t = np.arange(800) / 8000
    pcm = (9000 * np.sin(2 * np.pi * 300 * t)).astype("<i2").tobytes()
    return audio.b64_encode(audio.pcm16_to_ulaw(pcm))


@pytest.fixture
def wired(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(media_stream, "translator_class", lambda s: rec)
    s = SessionRegistry().create(source_language="en", target_language="es")
    s.a.ws, s.a.stream_sid = FakeWS(), "MZ_A"
    s.b.ws, s.b.stream_sid = FakeWS(), "MZ_B"
    return s, rec


def settings(echo=False):
    return replace(get_settings(), openai_api_key="sk-test", echo_mode=echo)


@pytest.mark.asyncio
async def test_translated_speech_gets_a_mark_on_the_listeners_leg(wired):
    s, rec = wired
    await media_stream._open_translator(settings(), s, A)
    s.latency[A].speech_started(time.monotonic() - 3.0)

    await rec.kwargs["on_audio"](loud_chunk())

    marks = [m for m in s.b.ws.sent if m["event"] == "mark"]
    assert marks and marks[0]["mark"]["name"] == "lat:A:1:1"
    assert marks[0]["streamSid"] == "MZ_B"
    assert not [m for m in s.a.ws.sent if m["event"] == "mark"]


@pytest.mark.asyncio
async def test_in_echo_mode_the_mark_goes_back_to_the_speaker(wired):
    s, rec = wired
    await media_stream._open_translator(settings(echo=True), s, A)
    s.latency[A].speech_started(time.monotonic() - 3.0)

    await rec.kwargs["on_audio"](loud_chunk())

    assert [m for m in s.a.ws.sent if m["event"] == "mark"]


@pytest.mark.asyncio
async def test_quiet_model_output_sends_no_mark(wired):
    s, rec = wired
    await media_stream._open_translator(settings(), s, A)
    s.latency[A].speech_started(time.monotonic() - 3.0)

    await rec.kwargs["on_audio"](audio.b64_encode(audio.ulaw_silence(100)))

    assert not [m for m in s.b.ws.sent if m["event"] == "mark"]


@pytest.mark.asyncio
async def test_transcripts_feed_the_heard_timestamp(wired):
    s, rec = wired
    await media_stream._open_translator(settings(), s, A)
    s.latency[A].speech_started(time.monotonic() - 3.0)
    await rec.kwargs["on_transcript"]("in", "x")
    await rec.kwargs["on_audio"](loud_chunk())

    (record,) = s.latency[A].flush(time.monotonic())
    assert record["heard_ms"] is not None and record["heard_ms"] >= 2900


# ------------------------------------------- mark echoes over the real socket
def _start(session_id, participant, sid):
    return {"event": "start", "streamSid": sid,
            "start": {"streamSid": sid, "callSid": "CA" + participant * 32,
                      "customParameters": {"session_id": session_id,
                                           "participant": participant}}}


def test_latency_marks_never_touch_barge_in_accounting():
    """played_ms assumes one chk- mark per 500 ms; lat: marks must not count."""
    s = registry.create(source_language="en", target_language="es",
                        participant_b_kind="browser")
    seen = []

    class Spy:
        def played(self, name, t):
            seen.append(name)

        def flush(self, now):
            return []

        def summary(self):
            return {}

    s.latency[A] = Spy()
    try:
        with TestClient(app) as client, client.websocket_connect("/ws/media-stream") as ws:
            ws.send_text(json.dumps(_start(s.session_id, "B", "MZ_B")))
            s.b.queued_ms = 10_000
            ws.send_text(json.dumps({"event": "mark", "streamSid": "MZ_B",
                                     "mark": {"name": "lat:A:1:1"}}))
            ws.send_text(json.dumps({"event": "mark", "streamSid": "MZ_B",
                                     "mark": {"name": "chk-25"}}))
            ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ_B"}))
        assert seen == ["lat:A:1:1"]
        assert s.b.played_ms == media_stream.MARK_EVERY_FRAMES * 20   # chk- only
    finally:
        registry.remove(s.session_id)


# ------------------------------------------------------------- privacy
def test_latency_lines_hold_only_numbers(caplog):
    caplog.set_level(logging.INFO)
    s = SessionRegistry().create(source_language="en", target_language="es")
    tr = s.latency[A]
    speak(tr, 0.0, 2.0)
    translate(tr, 3.0, 4.0)
    for record in tr.flush(20.0):
        media_stream._log_turn(s, A, record)
    media_stream._log_latency_summary(s, A, None)

    lines = [r for r in caplog.records if r.getMessage().startswith("latency_")]
    assert {r.getMessage() for r in lines} == {"latency_turn", "latency_summary"}
    allowed_text = {"session_id", "speaker", "direction"}
    for r in lines:
        extras = {k: v for k, v in r.__dict__.items()
                  if k not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__
                  and k not in ("message", "asctime")}
        for key, value in extras.items():
            if key in allowed_text:
                continue
            assert value is None or isinstance(value, (int, float, bool)), (key, value)


def test_playback_start_excludes_the_first_chunks_own_length():
    """Twilio echoes the mark when the chunk has FINISHED playing."""
    tr = LatencyTracker("A")
    speak(tr, 0.0, 2.0)
    mark = tr.audio_out(3.0, is_speech=True, duration_s=0.24)
    tr.played(mark, 3.0 + 0.30 + 0.24)        # network + the chunk playing
    (rec,) = tr.flush(20.0)
    assert rec["twilio_ms"] == 300            # not 540
    assert rec["start_lag_ms"] == 3300
