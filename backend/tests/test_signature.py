"""Webhook signature validation behind a tunnel (design doc §13).

Twilio signs the public https:// URL it requested. Our app, sitting behind
ngrok, sees a different scheme and host. These tests prove we validate against
the URL Twilio actually signed, using Twilio's own RequestValidator to produce
the signature.
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


class FakeURL:
    def __init__(self, path, query=""):
        self.path = path
        self.query = query


class FakeRequest:
    def __init__(self, path, query=""):
        self.url = FakeURL(path, query)


def test_public_url_uses_the_configured_public_host():
    settings = get_settings()
    url = twilio_service.public_url_for(settings, FakeRequest("/api/call/twiml/A"))
    assert url == "https://test.example.com/api/call/twiml/A"


def test_public_url_preserves_the_query_string():
    """Twilio signs the query string too — leg B's URL carries session_id."""
    settings = get_settings()
    url = twilio_service.public_url_for(
        settings, FakeRequest("/api/call/twiml/B", "session_id=sess_abc")
    )
    assert url == "https://test.example.com/api/call/twiml/B?session_id=sess_abc"


@pytest.fixture
def signed_client():
    """App with signature validation ON, as it runs in production."""
    strict = replace(get_settings(), validate_twilio_signature=True)
    app.dependency_overrides[call_route._settings] = lambda: strict
    yield TestClient(app), strict
    app.dependency_overrides.clear()


@pytest.fixture
def session():
    s = registry.create(source_language="ta", target_language="hi",
                        participant_b_kind="browser")
    yield s
    registry.remove(s.session_id)


def test_correctly_signed_webhook_is_accepted(signed_client, session):
    client, strict = signed_client
    form = {"CallSid": "CA" + "1" * 32, "session_id": session.session_id}
    url = f"{strict.backend_public_url}/api/call/twiml/A"

    signature = RequestValidator(strict.twilio_auth_token).compute_signature(url, form)

    response = client.post(
        "/api/call/twiml/A", data=form, headers={"X-Twilio-Signature": signature}
    )

    assert response.status_code == 200, response.text
    assert "<Stream" in response.text


def test_signature_computed_over_the_local_url_is_rejected(signed_client, session):
    """This is the bug the fix prevents: signing http://testserver must fail."""
    client, strict = signed_client
    form = {"CallSid": "CA" + "1" * 32, "session_id": session.session_id}

    wrong = RequestValidator(strict.twilio_auth_token).compute_signature(
        "http://testserver/api/call/twiml/A", form
    )

    response = client.post(
        "/api/call/twiml/A", data=form, headers={"X-Twilio-Signature": wrong}
    )
    assert response.status_code == 403


def test_missing_signature_is_rejected(signed_client, session):
    client, _ = signed_client
    response = client.post(
        "/api/call/twiml/A", data={"session_id": session.session_id}
    )
    assert response.status_code == 403


def test_tampered_form_is_rejected(signed_client, session):
    """The signature covers the POST body, not just the URL."""
    client, strict = signed_client
    url = f"{strict.backend_public_url}/api/call/twiml/A"
    signature = RequestValidator(strict.twilio_auth_token).compute_signature(
        url, {"CallSid": "CA" + "1" * 32, "session_id": session.session_id}
    )

    response = client.post(
        "/api/call/twiml/A",
        data={"CallSid": "CA" + "9" * 32, "session_id": session.session_id},
        headers={"X-Twilio-Signature": signature},
    )
    assert response.status_code == 403
