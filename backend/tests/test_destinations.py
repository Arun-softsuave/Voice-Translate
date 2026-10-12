"""Which countries a call may go to.

Any country by default; ALLOWED_DESTINATIONS can narrow it to a list of
country codes. Twilio's own Geo Permissions apply on top of either.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.routes import call as call_routes
from app.config import get_settings
from app.main import app
from app.services import twilio_service
from app.services.session_service import registry

START = {"source_language": "en", "target_language": "es"}


@pytest.mark.parametrize("raw,expected", [
    (None, ()),
    ("*", ()),
    ("", ()),
    ("+91", ("+91",)),
    (" +91, +34 ,+31 ", ("+91", "+34", "+31")),
])
def test_allowed_destinations_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ALLOWED_DESTINATIONS", raising=False)
    else:
        monkeypatch.setenv("ALLOWED_DESTINATIONS", raw)
    assert config._destinations() == expected


def test_a_malformed_setting_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ALLOWED_DESTINATIONS", "91,spain")
    with pytest.raises(RuntimeError, match="ALLOWED_DESTINATIONS"):
        config._destinations()


@pytest.fixture
def placing(monkeypatch):
    """Start calls for real (not demo mode), with Twilio faked."""
    dialled = []

    class FakeTwilio:
        def __init__(self, settings):
            pass

        def originate_pstn_leg(self, *, to, session_id):
            dialled.append(to)
            return "CA" + "1" * 32

    monkeypatch.setattr(twilio_service, "TwilioClient", FakeTwilio)
    client = TestClient(app)

    def place(number, prefixes=()):
        app.dependency_overrides[call_routes._settings] = lambda: replace(
            get_settings(), demo_mode=False, allowed_destination_prefixes=prefixes)
        try:
            r = client.post("/api/call/start", json={**START, "phone_number": number})
        finally:
            app.dependency_overrides.clear()
        if r.status_code == 201:
            registry.remove(r.json()["session_id"])
        return r

    place.dialled = dialled
    return place


@pytest.mark.parametrize("number", ["+919876543210", "+34612345678", "+31630984827",
                                    "+14155550123", "+447700900123"])
def test_any_country_by_default(placing, number):
    assert placing(number).status_code == 201
    assert placing.dialled == [number]


def test_a_narrowed_list_blocks_other_countries(placing):
    assert placing("+34612345678", ("+91", "+34")).status_code == 201
    r = placing("+31630984827", ("+91", "+34"))
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UNSUPPORTED_DESTINATION"
    assert placing.dialled == ["+34612345678"], "a blocked number is never dialled"


def test_twilio_refusing_a_country_says_how_to_fix_it():
    hint = twilio_service.CALL_ERROR_HINTS[21215]
    assert "India" not in hint and "Geo Permissions" in hint
