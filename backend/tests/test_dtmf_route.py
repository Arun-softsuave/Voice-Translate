"""The dial pad endpoint, and the IVR-mode switch beside it.

Both act on a live call, so both are guarded the same way every other call
route is: signed in, same origin, and only on your own call.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.session import ParticipantId
from app.services.session_service import registry

from tests.conftest import make_user, sign_in
from tests.test_dtmf_send import FakeWS


@pytest.fixture
def client(signed_in):
    c, _ = signed_in
    with c:
        yield c


@pytest.fixture
def call(signed_in):
    _, user_id = signed_in
    s = registry.create(source_language="en", target_language="es",
                        phone_number="+34910930511", user_id=user_id)
    s.b.call_sid = "CA" + "b" * 32
    yield s
    registry.remove(s.session_id)


@pytest.fixture
def streaming(call):
    """The far leg actually connected, which is when keys can be sent."""
    registry.bind_stream(call, ParticipantId.B, stream_sid="MZ_B",
                         call_sid=call.b.call_sid, ws=FakeWS())
    return call


def press(client, session_id, digits):
    return client.post(f"/api/call/{session_id}/digits", json={"digits": digits})


def test_a_key_is_sent_to_the_far_end(client, streaming):
    reply = press(client, streaming.session_id, "2")
    assert reply.status_code == 200
    assert reply.json()["sent"] == 1
    assert streaming.b.digits_out == 1


def test_a_short_sequence_is_sent(client, streaming):
    assert press(client, streaming.session_id, "1*23").json()["sent"] == 4


def test_keys_pressed_are_kept_with_the_transcript(client, streaming):
    """Otherwise a menu call reads back as a monologue with no explanation."""
    press(client, streaming.session_id, "12")
    lines = streaming.captions.lines()
    assert [line.kind for line in lines] == ["pressed"]
    # Pressed together they read as one entry, which is how you'd write them.
    assert lines[0].text == "12"


def test_keys_are_shown_only_to_the_caller(client, streaming):
    from app.services.captions import view
    press(client, streaming.session_id, "7")
    line = streaming.captions.lines()[0]
    assert view(line, ParticipantId.A, streaming)["speaker"] == "you"
    assert view(line, ParticipantId.B, streaming) is None


def test_pressing_before_the_phone_connects_says_so(client, call):
    reply = press(client, call.session_id, "1")
    assert reply.status_code == 409
    assert reply.json()["detail"]["code"] == "NOT_CONNECTED"


@pytest.mark.parametrize("bad", ["", "12e", "hello", "1 2", "123456789", "+"])
def test_only_keypad_characters_are_accepted(client, streaming, bad):
    assert press(client, streaming.session_id, bad).status_code == 422


def test_the_rarely_used_letter_keys_still_work(client, streaming):
    """A-D are real DTMF keys, even though no consumer menu asks for them."""
    assert press(client, streaming.session_id, "a").status_code == 200


def test_another_members_call_is_not_found(client, streaming):
    other = TestClient(app)
    make_user("someone.else@example.com")
    sign_in(other, "someone.else@example.com")
    assert press(other, streaming.session_id, "1").status_code == 404


def test_signing_in_is_required(streaming):
    anon = TestClient(app)
    assert press(anon, streaming.session_id, "1").status_code == 401


def test_a_flood_of_keys_is_refused(client, streaming):
    codes = [press(client, streaming.session_id, "1").status_code
             for _ in range(25)]
    assert 429 in codes


def test_the_digits_are_never_written_to_the_log(client, streaming, caplog):
    with caplog.at_level("INFO"):
        press(client, streaming.session_id, "4817")
    assert "4817" not in caplog.text, "a keypad entry may be a PIN"


# --- IVR mode ---------------------------------------------------------------
def test_ivr_mode_is_off_until_asked_for(client, call):
    assert call.ivr_mode is False
    assert client.get(f"/api/call/{call.session_id}").json()["ivr_mode"] is False


def test_ivr_mode_can_be_turned_on_and_off(client, call):
    on = client.post(f"/api/call/{call.session_id}/ivr-mode", json={"enabled": True})
    assert on.status_code == 200
    assert on.json()["ivr_mode"] is True
    assert call.ivr_mode is True

    off = client.post(f"/api/call/{call.session_id}/ivr-mode", json={"enabled": False})
    assert off.json()["ivr_mode"] is False
    assert call.ivr_mode is False


def test_ivr_mode_on_another_members_call_is_not_found(client, call):
    other = TestClient(app)
    make_user("third@example.com")
    sign_in(other, "third@example.com")
    reply = other.post(f"/api/call/{call.session_id}/ivr-mode", json={"enabled": True})
    assert reply.status_code == 404
    assert call.ivr_mode is False


def test_the_caller_is_told_what_is_still_playing_to_the_far_end(client, call):
    """So hanging up does not cut off the end of a voicemail message."""
    assert client.get(f"/api/call/{call.session_id}").json()["far_backlog_s"] == 0.0
