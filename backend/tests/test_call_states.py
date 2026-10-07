"""The far side of the call, as Twilio reports it, and what follows from it.

* Twilio's CallStatus moves the session through ringing -> answered -> ended,
  and the reason a call ended (busy, declined, no answer, ...) stays readable
  after the call is gone, so the browser can show it.
* Nothing is translated until both people are on the call.
* A leg's translator is opened while Twilio is still setting up its stream,
  reused by the stream, and closed if the stream never comes.
"""

import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models.session import CallStatus, ParticipantId
from app.services import session_service
from app.services.session_service import SessionRegistry, registry
from app.utils import audio
from app.websocket import media_stream

FRAME = audio.b64_encode(audio.ulaw_silence(20))


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def call():
    s = registry.create(source_language="en", target_language="ta",
                        phone_number="+919876543210")
    s.b.call_sid = "CA" + "b" * 32
    yield s
    registry.remove(s.session_id)


def twilio_status(client, call, status, **extra):
    return client.post("/api/call/status",
                       data={"CallSid": call.b.call_sid, "CallStatus": status, **extra})


# ------------------------------------------------------ ringing / answered
def test_ringing_then_answered(client, call):
    twilio_status(client, call, "ringing")
    assert call.status is CallStatus.RINGING

    twilio_status(client, call, "in-progress")
    assert call.status is CallStatus.ANSWERED
    body = client.get(f"/api/call/{call.session_id}").json()
    assert body["status"] == "ANSWERED"
    assert body["participants"]["B"]["answered"] is True


def test_a_late_ringing_event_does_not_undo_answered(client, call):
    twilio_status(client, call, "in-progress")
    twilio_status(client, call, "ringing")
    assert call.status is CallStatus.ANSWERED


# ------------------------------------------------------ why it ended
@pytest.mark.parametrize("status,extra,reason", [
    ("busy", {}, "busy"),
    ("busy", {"SipResponseCode": "603"}, "declined"),
    ("no-answer", {}, "no-answer"),
    ("failed", {}, "failed"),
    ("canceled", {}, "canceled"),
    ("completed", {}, "completed"),
])
def test_the_end_reason_reaches_the_browser(client, call, status, extra, reason):
    twilio_status(client, call, status, **extra)

    body = client.get(f"/api/call/{call.session_id}").json()
    assert body["status"] == "ENDED"
    assert body["end_reason"] == reason


def test_an_ended_call_is_forgotten_after_a_while(client, call, monkeypatch):
    twilio_status(client, call, "no-answer")
    assert client.get(f"/api/call/{call.session_id}").status_code == 200

    monkeypatch.setattr(session_service, "ENDED_TTL_S", 0.0)
    time.sleep(0.01)
    assert client.get(f"/api/call/{call.session_id}").status_code == 404


def test_an_ended_call_cannot_be_rejoined(client, call):
    twilio_status(client, call, "busy")

    r = client.post(f"/api/call/twiml/B?session_id={call.session_id}", data={})
    assert r.status_code == 404
    r = client.get(f"/api/call/{call.session_id}/captions")
    assert r.status_code == 404


def test_hanging_up_before_an_answer_is_a_cancelled_call(client, call):
    body = client.post(f"/api/call/end/{call.session_id}").json()
    assert body["end_reason"] == "canceled"


def test_hanging_up_after_an_answer_is_a_normal_end(client, call):
    twilio_status(client, call, "in-progress")
    body = client.post(f"/api/call/end/{call.session_id}").json()
    assert body["end_reason"] == "user_ended"


def test_hanging_up_an_already_ended_call_says_how_it_ended(client, call):
    twilio_status(client, call, "no-answer")
    r = client.post(f"/api/call/end/{call.session_id}")
    assert r.status_code == 200
    assert r.json()["end_reason"] == "no-answer"


def test_ring_timeout_is_passed_to_twilio(monkeypatch):
    from app.services import twilio_service

    captured = {}

    class FakeCalls:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Call", (), {"sid": "CA1"})()

    settings = replace(get_settings(), ring_timeout_s=25, twilio_phone_number="+15550000000")
    client = twilio_service.TwilioClient(settings)
    client._client = type("C", (), {"calls": FakeCalls()})()
    client.originate_pstn_leg(to="+919876543210", session_id="sess_x")

    assert captured["timeout"] == 25


# ------------------------------------------------------ fake translator
class FakeTranslator:
    instances: list = []

    def __init__(self, settings, **kwargs):
        self.kwargs = kwargs
        self.appended = 0
        self.connects = 0
        self.closed = False
        self.rtt_ms = None
        FakeTranslator.instances.append(self)

    async def connect(self):
        self.connects += 1

    async def append_audio(self, payload):
        self.appended += 1

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_translation(monkeypatch):
    FakeTranslator.instances = []
    from app.api.routes import call as call_routes

    settings = replace(get_settings(), openai_api_key="sk-test")
    monkeypatch.setattr(media_stream, "get_settings", lambda: settings)
    monkeypatch.setattr(media_stream, "translator_class", lambda s: FakeTranslator)
    # The HTTP routes read settings through FastAPI's dependency.
    app.dependency_overrides[call_routes._settings] = lambda: settings
    yield settings
    app.dependency_overrides.pop(call_routes._settings, None)


def _start(session_id, participant, sid):
    return {"event": "start", "streamSid": sid,
            "start": {"streamSid": sid, "callSid": "CA" + participant * 32,
                      "customParameters": {"session_id": session_id,
                                           "participant": participant}}}


def _media(sid, n):
    return {"event": "media", "streamSid": sid,
            "media": {"timestamp": str(n * 20), "payload": FRAME}}


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# ------------------------------------------------------ the answer gate
def test_nothing_is_translated_while_the_phone_rings(client, call, fake_translation):
    with client.websocket_connect("/ws/media-stream") as ws_a:
        ws_a.send_text(json.dumps(_start(call.session_id, "A", "MZ_A")))
        for n in range(20):
            ws_a.send_text(json.dumps(_media("MZ_A", n)))
        assert wait_for(lambda: call.a.frames_in == 20)
        translator_a = FakeTranslator.instances[0]
        assert translator_a.appended == 0, "audio went to the model before an answer"
        assert call.latency[ParticipantId.A].records == []

        with client.websocket_connect("/ws/media-stream") as ws_b:
            ws_b.send_text(json.dumps(_start(call.session_id, "B", "MZ_B")))
            assert wait_for(lambda: call.b.connected)
            for n in range(20, 30):
                ws_a.send_text(json.dumps(_media("MZ_A", n)))
            assert wait_for(lambda: translator_a.appended == 10)
            ws_b.send_text(json.dumps({"event": "stop", "streamSid": "MZ_B"}))
        ws_a.send_text(json.dumps({"event": "stop", "streamSid": "MZ_A"}))


def test_echo_mode_translates_a_single_leg(client, fake_translation, monkeypatch):
    settings = replace(fake_translation, echo_mode=True)
    monkeypatch.setattr(media_stream, "get_settings", lambda: settings)
    s = registry.create(source_language="en", target_language="ta",
                        participant_b_kind="browser")
    try:
        with client.websocket_connect("/ws/media-stream") as ws_a:
            ws_a.send_text(json.dumps(_start(s.session_id, "A", "MZ_A")))
            for n in range(5):
                ws_a.send_text(json.dumps(_media("MZ_A", n)))
            assert wait_for(lambda: FakeTranslator.instances
                            and FakeTranslator.instances[0].appended == 5)
            assert s.status in (CallStatus.CONNECTED, CallStatus.TRANSLATING)
            ws_a.send_text(json.dumps({"event": "stop", "streamSid": "MZ_A"}))
    finally:
        registry.remove(s.session_id)


def test_echo_mode_binding_counts_as_connected():
    reg = SessionRegistry()
    s = reg.create(source_language="en", target_language="ta")
    reg.bind_stream(s, ParticipantId.A, stream_sid="MZ", call_sid=None, ws=object(),
                    echo_mode=True)
    assert s.status is CallStatus.CONNECTED


# ------------------------------------------------------ prewarming
def test_twiml_prewarms_and_the_stream_adopts_it(client, call, fake_translation):
    r = client.post(f"/api/call/twiml/B?session_id={call.session_id}",
                    data={"CallSid": call.b.call_sid})
    assert r.status_code == 200
    assert ParticipantId.B in call.translator_tasks

    with client.websocket_connect("/ws/media-stream") as ws_b:
        ws_b.send_text(json.dumps(_start(call.session_id, "B", "MZ_B")))
        assert wait_for(lambda: ParticipantId.B not in call.translator_tasks)
        ws_b.send_text(json.dumps({"event": "stop", "streamSid": "MZ_B"}))

    assert len(FakeTranslator.instances) == 1, "the stream opened a second translator"
    assert FakeTranslator.instances[0].connects == 1


def test_a_prewarmed_translator_is_closed_if_nobody_answers(client, call, fake_translation):
    client.post(f"/api/call/twiml/B?session_id={call.session_id}",
                data={"CallSid": call.b.call_sid})
    assert wait_for(lambda: call.translator_tasks[ParticipantId.B].done())

    twilio_status(client, call, "no-answer")

    assert wait_for(lambda: FakeTranslator.instances[0].closed)
    assert call.translator_tasks == {}


def test_prewarming_is_skipped_without_translation(client, call):
    """The suite's default settings have no API key."""
    client.post(f"/api/call/twiml/B?session_id={call.session_id}",
                data={"CallSid": call.b.call_sid})
    assert call.translator_tasks == {}
