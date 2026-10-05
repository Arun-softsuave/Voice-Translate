"""Live captions.

The rule under test: every caption on a viewer's screen is in THAT viewer's
language. A viewer sees
  * their own "heard" line (what they said), and
  * the "spoke" line of whichever translator's audio is played to them,
which is the peer's normally and their own in echo mode, mirroring
`route_audio`.

Also covered: fragments join into lines, a pause starts a new line, the log
is bounded, it ends with the call, and caption text never reaches a log.
"""

import asyncio
import json
import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.routes import call as call_routes
from app.config import get_settings
from app.models.session import ParticipantId
from app.services import captions as C
from app.services.captions import HEARD, SPOKE, CaptionLog, view
from app.services.session_service import SessionRegistry, registry

A, B = ParticipantId.A, ParticipantId.B


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def tick(self, s):
        self.now += s


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def log(clock):
    return CaptionLog(clock=clock)


@pytest.fixture
def session():
    # A speaks English, B speaks Spanish.
    return SessionRegistry().create(source_language="en", target_language="es")


# ------------------------------------------------------------ joining
def test_fragments_join_into_one_line(log, clock):
    for word in ("Can", " you", " come", " tomorrow?"):
        log.add(A, HEARD, word)
        clock.tick(0.2)

    lines = log.lines()
    assert len(lines) == 1
    assert lines[0].text == "Can you come tomorrow?"


def test_a_pause_starts_a_new_line(log, clock):
    log.add(A, HEARD, "First sentence.")
    clock.tick(C.GAP_S + 0.1)
    log.add(A, HEARD, " Second sentence.")

    texts = [l.text for l in log.lines()]
    assert texts == ["First sentence.", "Second sentence."]   # leading space trimmed


def test_a_short_pause_does_not(log, clock):
    log.add(A, HEARD, "Still")
    clock.tick(C.GAP_S - 0.1)
    log.add(A, HEARD, " talking")

    assert [l.text for l in log.lines()] == ["Still talking"]


def test_streams_do_not_interleave(log, clock):
    """Both people talking at once must not merge into one line."""
    log.add(A, HEARD, "Hello")
    log.add(B, HEARD, "Hola")
    log.add(A, HEARD, " there")
    log.add(A, SPOKE, "Hola")
    log.add(B, HEARD, " amigo")

    by_stream = {(l.owner, l.kind): l.text for l in log.lines()}
    assert by_stream == {(A, HEARD): "Hello there", (B, HEARD): "Hola amigo",
                         (A, SPOKE): "Hola"}


def test_blank_fragments_are_ignored(log):
    log.add(A, HEARD, "")
    log.add(A, HEARD, "   ")
    assert log.lines() == []


def test_line_time_is_seconds_since_the_call_started(log, clock):
    clock.tick(65.04)
    log.add(A, HEARD, "hi")
    assert log.lines()[0].t == 65.0


def test_log_is_bounded(log, clock):
    for i in range(C.MAX_LINES + 50):
        log.add(A, HEARD, f"line {i}")
        clock.tick(C.GAP_S + 0.1)

    lines = log.lines()
    assert len(lines) == C.MAX_LINES
    assert lines[-1].text == f"line {C.MAX_LINES + 49}"


# ---------------------------------------------------------- the rule
def setup_call(session, clock):
    session.captions = CaptionLog(clock=clock)
    log = session.captions
    log.add(A, HEARD, "Can you come at ten?")          # A said, English
    log.add(A, SPOKE, "¿Puedes venir a las diez?")     # A's words in Spanish
    log.add(B, HEARD, "Sí, a las diez.")               # B said, Spanish
    log.add(B, SPOKE, "Yes, at ten.")                  # B's words in English
    return log


def seen_by(session, viewer, echo=False):
    return [v for v in (view(l, viewer, session, echo_mode=echo)
                        for l in session.captions.lines()) if v]


def test_a_sees_everything_in_english(session, clock):
    setup_call(session, clock)

    shown = seen_by(session, A)

    assert [(v["speaker"], v["text"]) for v in shown] == [
        ("you", "Can you come at ten?"),
        ("them", "Yes, at ten."),
    ]
    assert {v["lang"] for v in shown} == {"en"}
    assert shown[1]["from"] == "es"


def test_b_sees_everything_in_spanish(session, clock):
    setup_call(session, clock)

    shown = seen_by(session, B)

    assert [(v["speaker"], v["text"]) for v in shown] == [
        ("them", "¿Puedes venir a las diez?"),
        ("you", "Sí, a las diez."),
    ]
    assert {v["lang"] for v in shown} == {"es"}


def test_nobody_sees_the_other_side_s_original_words(session, clock):
    setup_call(session, clock)
    texts_a = {v["text"] for v in seen_by(session, A)}
    assert "Sí, a las diez." not in texts_a
    assert "¿Puedes venir a las diez?" not in texts_a


def test_echo_mode_shows_what_you_actually_hear(session, clock):
    """In echo mode your own translation is played back to you."""
    log = CaptionLog(clock=clock)
    session.captions = log
    log.add(A, HEARD, "Can you come at ten?")
    log.add(A, SPOKE, "¿Puedes venir a las diez?")

    shown = seen_by(session, A, echo=True)

    assert [(v["speaker"], v["text"]) for v in shown] == [
        ("you", "Can you come at ten?"),
        ("them", "¿Puedes venir a las diez?"),
    ]


# --------------------------------------------------------- subscribers
@pytest.mark.asyncio
async def test_subscribers_get_a_snapshot_then_updates(log):
    log.add(A, HEARD, "before")
    snapshot, queue = log.subscribe()
    log.add(A, HEARD, " after")

    assert [l.text for l in snapshot] == ["before"]   # copy of the list
    update = queue.get_nowait()
    assert update.text == "before after"


@pytest.mark.asyncio
async def test_close_ends_every_stream_and_forgets_the_text(log):
    log.add(A, HEARD, "secret")
    _, q1 = log.subscribe()
    _, q2 = log.subscribe()

    log.close()

    assert q1.get_nowait() is None and q2.get_nowait() is None
    assert log.lines() == []
    log.add(A, HEARD, "late")                 # ignored after the call
    assert log.lines() == []


@pytest.mark.asyncio
async def test_subscribing_after_close_ends_at_once(log):
    log.close()
    lines, queue = log.subscribe()
    assert lines == [] and queue.get_nowait() is None


@pytest.mark.asyncio
async def test_a_stuck_watcher_is_dropped_not_buffered_forever(log, clock, monkeypatch):
    monkeypatch.setattr(C, "MAX_QUEUED", 3)
    _, queue = log.subscribe()
    for i in range(10):
        log.add(A, HEARD, f"x{i}")
        clock.tick(C.GAP_S + 0.1)

    assert log.watchers == 0
    assert queue.qsize() == 3


def test_removing_the_session_closes_its_captions():
    reg = SessionRegistry()
    s = reg.create(source_language="en", target_language="es")
    _, queue = s.captions.subscribe()

    reg.remove(s.session_id)

    assert s.captions.closed
    assert queue.get_nowait() is None


# --------------------------------------------------------------- SSE
def parse(chunks: list[str]) -> list[tuple[str, object]]:
    events = []
    for chunk in "".join(chunks).split("\n\n"):
        if not chunk or chunk.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.mark.asyncio
async def test_stream_sends_snapshot_then_live_lines_then_end():
    s = registry.create(source_language="en", target_language="es")
    try:
        s.captions.add(A, HEARD, "Hello")
        s.captions.add(B, SPOKE, "Hi, who is this?")
        s.captions.add(B, HEARD, "Hola, ¿quién es?")          # not for A

        response = await call_routes.captions(s.session_id, "A", get_settings())
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"

        chunks: list[str] = []
        body = response.body_iterator

        chunks.append(await body.__anext__())                 # snapshot
        s.captions.add(A, HEARD, " there")                    # live update
        chunks.append(await body.__anext__())
        registry.remove(s.session_id)                         # call ends
        chunks.append(await body.__anext__())
        with pytest.raises(StopAsyncIteration):
            await body.__anext__()
    finally:
        registry.remove(s.session_id)

    events = parse(chunks)
    assert events[0][0] == "snapshot"
    assert [(c["speaker"], c["text"]) for c in events[0][1]] == [
        ("you", "Hello"), ("them", "Hi, who is this?")]
    assert events[1] == ("caption", {**events[1][1], "text": "Hello there", "speaker": "you"})
    assert events[1][1]["id"] == events[0][1][0]["id"], "update must reuse the line id"
    assert events[2] == ("end", {})


@pytest.mark.asyncio
async def test_lines_for_the_other_viewer_are_not_streamed():
    s = registry.create(source_language="en", target_language="es")
    try:
        response = await call_routes.captions(s.session_id, "A", get_settings())
        body = response.body_iterator
        await body.__anext__()                               # empty snapshot

        s.captions.add(B, HEARD, "Hola")                     # B's own words
        s.captions.add(A, SPOKE, "Hola")                     # played to B
        registry.remove(s.session_id)

        rest = [chunk async for chunk in body]
    finally:
        registry.remove(s.session_id)

    assert parse(rest) == [("end", {})]


@pytest.mark.asyncio
async def test_idle_stream_sends_keepalive_pings(monkeypatch):
    monkeypatch.setattr(call_routes, "CAPTION_PING_S", 0.01)
    s = registry.create(source_language="en", target_language="es")
    try:
        response = await call_routes.captions(s.session_id, "A", get_settings())
        body = response.body_iterator
        await body.__anext__()
        assert await body.__anext__() == ": ping\n\n"
    finally:
        registry.remove(s.session_id)


def test_http_unknown_session_is_404():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/call/sess_nope/captions")
    assert r.status_code == 404


def test_http_bad_viewer_is_rejected():
    from app.main import app

    s = registry.create(source_language="en", target_language="es")
    try:
        with TestClient(app) as client:
            r = client.get(f"/api/call/{s.session_id}/captions?viewer=C")
        assert r.status_code == 422
    finally:
        registry.remove(s.session_id)


def test_http_stream_of_a_finished_call_ends_cleanly():
    """Over real HTTP: correct content type, a snapshot, then `end`."""
    from app.main import app

    s = registry.create(source_language="en", target_language="es")
    s.captions.close()
    try:
        with TestClient(app) as client:
            r = client.get(f"/api/call/{s.session_id}/captions")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert parse([r.text]) == [("snapshot", []), ("end", {})]
    finally:
        registry.remove(s.session_id)


# ------------------------------------------------------------ privacy
@pytest.mark.asyncio
async def test_caption_text_never_reaches_the_logs(caplog, monkeypatch):
    """Drive a Gemini session end to end with transcripts, and the caption
    stream, at DEBUG level; no spoken word may appear in any log record."""
    from google.genai import live as sdk_live

    from app.websocket import media_stream
    from tests.test_gemini_service import FakeWire

    wires = []

    def fake_ws_connect(uri, additional_headers=None, **kwargs):
        wire = FakeWire(uri, additional_headers)
        wires.append(wire)
        return wire

    monkeypatch.setattr(sdk_live, "ws_connect", fake_ws_connect)
    caplog.set_level(logging.DEBUG)

    settings = replace(get_settings(), translation_backend="gemini",
                       gemini_api_key="g-test")
    s = registry.create(source_language="en", target_language="es")
    try:
        translator = await media_stream._open_translator(settings, s, A)
        wires[0].push({"serverContent": {"inputTranscription": {"text": "MYSECRETPIN 4821"}}})
        wires[0].push({"serverContent": {"outputTranscription": {"text": "MIPINSECRETO 4821"}}})
        await asyncio.sleep(0.05)

        assert [l.text for l in s.captions.lines()] == ["MYSECRETPIN 4821", "MIPINSECRETO 4821"]

        response = await call_routes.captions(s.session_id, "A", settings)
        await response.body_iterator.__anext__()
        await translator.close()
    finally:
        registry.remove(s.session_id)

    for record in caplog.records:
        blob = record.getMessage() + " " + json.dumps(
            {k: str(v) for k, v in record.__dict__.items()}, ensure_ascii=False)
        assert "SECRET" not in blob and "4821" not in blob, record.name
