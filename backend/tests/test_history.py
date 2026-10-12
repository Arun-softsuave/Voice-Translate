"""Call history: every call a signed-in user places is saved with what was said.

* The call row follows the call: started, answered, ended, and why.
* Each caption line is saved once it is finished, so a long call loses
  nothing (the live log keeps only the last 200 lines).
* People see only their own calls; admins see everyone's.
* Saving can fail without the call noticing.
"""

import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.db.engine import session_scope
from app.db.models import Call, TranscriptLine
from app.main import app
from app.models.session import ParticipantId
from app.services import history
from app.services.captions import GAP_S, HEARD, MAX_LINES, SPOKE, CaptionLog
from app.services.session_service import registry
from tests.conftest import make_user, sign_in

A, B = ParticipantId.A, ParticipantId.B
START = {"source_language": "en", "target_language": "ta", "phone_number": "+919876543210"}


def start(client):
    r = client.post("/api/call/start", json=START)
    assert r.status_code == 201, r.text
    return registry.get(r.json()["session_id"])


def test_a_call_and_its_words_are_saved(signed_in):
    client, user_id = signed_in
    session = start(client)
    session.captions.add(A, HEARD, "Hello, can you")
    session.captions.add(A, HEARD, " hear me?")
    session.captions.add(A, SPOKE, "வணக்கம்")
    session.captions.add(B, HEARD, "ஆமாம்")
    with session_scope() as db:
        call = db.query(Call).one()
        assert (call.user_id, call.status, call.phone_number) == \
            (user_id, "in_progress", "+919876543210")
        assert db.query(TranscriptLine).count() == 0, "lines still growing"

    assert client.post(f"/api/call/end/{session.session_id}").status_code == 200

    calls = client.get("/api/calls").json()
    assert calls["total"] == 1
    item = calls["items"][0]
    assert (item["status"], item["end_reason"], item["lines"]) == ("ended", "canceled", 3)
    assert item["started_at"].endswith("Z")

    detail = client.get(f"/api/calls/{item['id']}").json()
    assert [(l["owner"], l["kind"], l["lang"], l["text"]) for l in detail["transcript"]] == [
        ("A", "heard", "en", "Hello, can you hear me?"),
        ("A", "spoke", "ta", "வணக்கம்"),          # the translation, in B's language
        ("B", "heard", "ta", "ஆமாம்"),
    ]


def test_a_finished_line_is_saved_while_the_call_goes_on():
    now = [0.0]
    log = CaptionLog(clock=lambda: now[0])
    saved = []
    log.on_final = saved.append
    log.add(A, HEARD, "first")
    now[0] += GAP_S + 0.1
    log.add(A, HEARD, "second")
    assert [l.text for l in saved] == ["first"]
    log.add(B, HEARD, "other stream")
    log.close()
    assert [l.text for l in saved] == ["first", "second", "other stream"]


def test_a_long_call_keeps_every_line(signed_in):
    client, _ = signed_in
    session = start(client)
    now = [0.0]
    session.captions._clock = lambda: now[0]
    for i in range(MAX_LINES + 50):
        session.captions.add(A, HEARD, f"line {i}")
        now[0] += GAP_S + 0.1
    client.post(f"/api/call/end/{session.session_id}")
    with session_scope() as db:
        assert db.query(TranscriptLine).count() == MAX_LINES + 50


def test_answer_time_and_duration(signed_in):
    client, _ = signed_in
    session = start(client)
    session.b.answered_at = session.created_at + 5.0
    session.ended_at = None
    registry.end(session, "completed")
    with session_scope() as db:
        call = db.query(Call).one()
        assert call.answered_at is not None and call.duration_s is not None
        assert call.end_reason == "completed"


def test_an_unanswered_call_has_no_duration(signed_in):
    client, _ = signed_in
    session = start(client)
    client.post(f"/api/call/end/{session.session_id}")
    with session_scope() as db:
        assert db.query(Call).one().duration_s is None


def test_people_see_only_their_own_calls(signed_in):
    client, _ = signed_in
    mine = start(client)
    other_client = TestClient(app)
    make_user("other@example.com")
    sign_in(other_client, "other@example.com")
    theirs = start(other_client)
    for s in (mine, theirs):
        registry.end(s, "completed")
    with session_scope() as db:
        their_id = db.query(Call).filter_by(session_id=theirs.session_id).one().id

    assert client.get("/api/calls").json()["total"] == 1
    assert client.get(f"/api/calls/{their_id}").status_code == 404
    # Live call routes hide someone else's call the same way.
    assert client.get(f"/api/call/{theirs.session_id}").status_code == 404


def test_someone_elses_live_call_is_not_found(signed_in):
    client, _ = signed_in
    other = make_user("other@example.com")
    theirs = registry.create(source_language="en", target_language="ta", user_id=other)
    try:
        for r in (client.get(f"/api/call/{theirs.session_id}"),
                  client.post(f"/api/call/end/{theirs.session_id}"),
                  client.get(f"/api/call/{theirs.session_id}/captions")):
            assert r.status_code == 404
        assert registry.get(theirs.session_id) is not None, "must not have ended it"
    finally:
        registry.remove(theirs.session_id)


def test_admins_see_every_call(signed_in):
    client, _ = signed_in
    registry.end(start(client), "completed")
    admin = TestClient(app)
    make_user("admin@example.com", role="admin", name="Admin")
    sign_in(admin, "admin@example.com")
    registry.end(start(admin), "completed")

    every = admin.get("/api/admin/calls").json()
    assert every["total"] == 2
    one = admin.get("/api/admin/calls?q=test%20user").json()
    assert one["total"] == 1 and one["items"][0]["user"]["name"] == "Test User"
    detail = admin.get(f"/api/admin/calls/{one['items'][0]['id']}")
    assert detail.status_code == 200 and "transcript" in detail.json()
    assert admin.get("/api/admin/calls?from=2000-01-01T00:00:00Z&to=2000-01-02T00:00:00Z"
                     ).json()["total"] == 0


def test_a_refused_call_is_saved_as_failed(signed_in, monkeypatch):
    from dataclasses import replace

    from app.api.routes import call as call_routes
    from app.config import get_settings
    from app.services import twilio_service

    class Refusing:
        def __init__(self, settings):
            pass

        def originate_pstn_leg(self, **kwargs):
            raise twilio_service.CallOriginationError("21215", "Not allowed.")

    monkeypatch.setattr(twilio_service, "TwilioClient", Refusing)
    app.dependency_overrides[call_routes._settings] = lambda: replace(
        get_settings(), demo_mode=False)
    try:
        client, _ = signed_in
        assert client.post("/api/call/start", json=START).status_code == 502
    finally:
        app.dependency_overrides.clear()
    with session_scope() as db:
        call = db.query(Call).one()
        assert (call.status, call.end_reason) == ("failed", "twilio_21215")


def test_calls_cut_off_by_a_restart_are_closed(signed_in):
    client, _ = signed_in
    session = start(client)
    assert history.close_orphans() == 1
    with session_scope() as db:
        assert db.query(Call).one().end_reason == "server_restart"
    registry.remove(session.session_id)


def test_model_cost_adds_up_per_leg(signed_in):
    client, _ = signed_in
    session = start(client)
    history.add_model_cost(session, 0.0125)
    history.add_model_cost(session, 0.0100)
    registry.end(session, "completed")
    assert client.get("/api/calls").json()["items"][0]["model_usd"] == pytest.approx(0.0225)


def test_a_database_failure_never_reaches_the_call(signed_in, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = signed_in
    session = start(client)

    def broken():
        raise RuntimeError("disk full")

    monkeypatch.setattr(history, "session_scope", lambda: broken())
    session.captions.add(A, HEARD, "PRIVATE WORDS")
    now = session.captions._clock() + GAP_S + 1
    session.captions._clock = lambda: now
    session.captions.add(A, HEARD, "more")                  # finishes the first line
    history.add_model_cost(session, 1.0)
    registry.end(session, "completed")                      # flush + record_end
    assert session.end_reason == "completed"
    failures = [r for r in caplog.records if r.getMessage() == "history_write_failed"]
    assert {r.step for r in failures} >= {"line", "cost", "end"}
    assert all("PRIVATE" not in str(r.__dict__) for r in caplog.records)


def test_sessions_without_an_owner_are_not_saved():
    session = registry.create(source_language="en", target_language="ta")
    session.captions.add(A, HEARD, "hello")
    registry.end(session, "completed")
    with session_scope() as db:
        assert db.query(Call).count() == 0


def test_a_deep_link_loads_the_page(tmp_path, monkeypatch):
    """/history/12 or /admin, refreshed, must load the page, not a 404."""
    import app.main as main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>page</html>")
    (dist / "assets" / "app.js").write_text("js")
    (dist / "favicon.svg").write_text("svg")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist.resolve())
    page = main.serve_page
    assert page("history/12").path.name == "index.html"
    assert page("favicon.svg").path.name == "favicon.svg"
    assert page("../../secret").path.name == "index.html"
    for path in ("api/nope", "ws/x", "health"):
        with pytest.raises(HTTPException):
            page(path)
