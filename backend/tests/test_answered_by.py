"""Twilio telling us whether a person or a machine picked up.

The verdict arrives on its own webhook a few seconds after the call connects,
because the call is never held up waiting for it. It is only worth anything if
it reaches the right call and cannot be forged, which is what these cover.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.api.routes import call as call_route
from app.config import get_settings
from app.main import app
from app.services import twilio_service
from app.services.session_service import registry


@pytest.fixture
def call():
    s = registry.create(source_language="en", target_language="es",
                        phone_number="+34654812595")
    s.b.call_sid = "CA" + "d" * 32
    yield s
    registry.remove(s.session_id)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def tell(client, call_sid, verdict):
    return client.post("/api/call/answered-by",
                       data={"CallSid": call_sid, "AnsweredBy": verdict})


def test_the_verdict_is_remembered_against_the_call(client, call):
    assert tell(client, call.b.call_sid, "machine_end_beep").status_code == 204
    assert call.answered_by == "machine_end_beep"


def test_a_person_answering_is_recorded_too(client, call):
    tell(client, call.b.call_sid, "human")
    assert call.answered_by == "human"


def test_the_verdict_reaches_the_page(client, call):
    tell(client, call.b.call_sid, "machine_end_beep")
    assert call.snapshot()["answered_by"] == "machine_end_beep"


def test_a_verdict_for_an_unknown_call_is_shrugged_off(client, call):
    """Twilio retries webhooks; a call we have forgotten must not be an error."""
    assert tell(client, "CA" + "z" * 32, "human").status_code == 204
    assert call.answered_by is None


def test_an_empty_verdict_is_not_recorded(client, call):
    assert tell(client, call.b.call_sid, "").status_code == 204
    assert call.answered_by is None


def test_an_unsigned_verdict_is_refused(call):
    """Anyone could otherwise label a live call as voicemail."""
    strict = replace(get_settings(), validate_twilio_signature=True)
    app.dependency_overrides[call_route._settings] = lambda: strict
    try:
        with TestClient(app) as client:
            reply = client.post("/api/call/answered-by",
                                data={"CallSid": call.b.call_sid,
                                      "AnsweredBy": "machine_end_beep"})
        assert reply.status_code == 403
        assert call.answered_by is None
    finally:
        app.dependency_overrides.clear()


def test_a_properly_signed_verdict_is_accepted(call):
    strict = replace(get_settings(), validate_twilio_signature=True)
    app.dependency_overrides[call_route._settings] = lambda: strict
    try:
        form = {"CallSid": call.b.call_sid, "AnsweredBy": "machine_end_beep"}
        url = f"{strict.backend_public_url}/api/call/answered-by"
        signature = RequestValidator(strict.twilio_auth_token).compute_signature(
            url, form)
        with TestClient(app) as client:
            reply = client.post("/api/call/answered-by", data=form,
                                headers={"X-Twilio-Signature": signature})
        assert reply.status_code == 204
        assert call.answered_by == "machine_end_beep"
    finally:
        app.dependency_overrides.clear()


# --- asking Twilio for the verdict in the first place ------------------------
class FakeCalls:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return type("Call", (), {"sid": "CA" + "e" * 32})()


def originate(**overrides):
    settings = replace(get_settings(), twilio_phone_number="+15550001111",
                       **overrides)
    client = twilio_service.TwilioClient(settings)
    calls = FakeCalls()
    client._client = type("C", (), {"calls": calls})()
    client.originate_pstn_leg(to="+34654812595", session_id="sess_x")
    return calls.kwargs


def test_machine_detection_is_off_unless_it_is_asked_for():
    """It costs a fee per call and is sometimes wrong, so it is opt-in."""
    assert "machine_detection" not in originate(detect_machines=False)


def test_machine_detection_does_not_hold_up_the_call():
    kwargs = originate(detect_machines=True)
    assert kwargs["machine_detection"] == "DetectMessageEnd"
    assert kwargs["async_amd"] == "true", "the call must not wait on the detector"
    assert kwargs["async_amd_status_callback"].endswith("/api/call/answered-by")
