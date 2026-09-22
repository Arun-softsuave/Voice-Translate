"""End-to-end test of the Twilio Media Streams WebSocket endpoint.

Drives the real endpoint with the real Twilio message shapes (design doc §11)
through two simultaneous connections, which is the actual call topology.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.session_service import registry
from app.utils import audio

PAYLOAD = audio.b64_encode(audio.ulaw_silence(20))


def _start_msg(session_id: str, participant: str, stream_sid: str) -> dict:
    """Exactly the shape Twilio sends on stream start."""
    return {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": stream_sid,
        "start": {
            "streamSid": stream_sid,
            "accountSid": "AC" + "0" * 32,
            "callSid": "CA" + participant * 32,
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000,
                            "channels": 1},
            "customParameters": {"session_id": session_id,
                                 "participant": participant},
        },
    }


def _media_msg(stream_sid: str, payload: str) -> dict:
    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"track": "inbound", "chunk": "1", "timestamp": "20",
                  "payload": payload},
    }


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def session():
    s = registry.create(source_language="ta", target_language="hi",
                        participant_b_kind="browser")
    yield s
    registry.remove(s.session_id)


def test_audio_from_leg_a_is_delivered_to_leg_b(client, session):
    """The headline test: User 1's audio comes out of User 2's socket."""
    with client.websocket_connect("/ws/media-stream") as ws_a:
        ws_a.send_text(json.dumps({"event": "connected", "protocol": "Call"}))
        ws_a.send_text(json.dumps(_start_msg(session.session_id, "A", "MZ_A")))

        with client.websocket_connect("/ws/media-stream") as ws_b:
            ws_b.send_text(json.dumps({"event": "connected", "protocol": "Call"}))
            ws_b.send_text(json.dumps(_start_msg(session.session_id, "B", "MZ_B")))

            ws_a.send_text(json.dumps(_media_msg("MZ_A", PAYLOAD)))

            received = ws_b.receive_json()
            assert received["event"] == "media"
            assert received["streamSid"] == "MZ_B"
            assert received["media"]["payload"] == PAYLOAD


def test_reverse_direction_also_works(client, session):
    with client.websocket_connect("/ws/media-stream") as ws_a:
        ws_a.send_text(json.dumps(_start_msg(session.session_id, "A", "MZ_A")))
        with client.websocket_connect("/ws/media-stream") as ws_b:
            ws_b.send_text(json.dumps(_start_msg(session.session_id, "B", "MZ_B")))

            ws_b.send_text(json.dumps(_media_msg("MZ_B", PAYLOAD)))

            received = ws_a.receive_json()
            assert received["streamSid"] == "MZ_A"
            assert received["media"]["payload"] == PAYLOAD


def test_unknown_session_is_rejected(client):
    """§13: a stream that cannot prove which session it belongs to is closed."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/media-stream") as ws:
            ws.send_text(json.dumps(_start_msg("sess_does_not_exist", "A", "MZ_X")))
            ws.receive_json()


def test_stream_counters_are_recorded(client, session):
    with client.websocket_connect("/ws/media-stream") as ws_a:
        ws_a.send_text(json.dumps(_start_msg(session.session_id, "A", "MZ_A")))
        with client.websocket_connect("/ws/media-stream") as ws_b:
            ws_b.send_text(json.dumps(_start_msg(session.session_id, "B", "MZ_B")))
            for _ in range(3):
                ws_a.send_text(json.dumps(_media_msg("MZ_A", PAYLOAD)))
            ws_b.receive_json()
            ws_b.receive_json()
            ws_b.receive_json()

    assert session.a.frames_in == 3
    assert session.b.frames_out == 3
    assert session.a.frames_out == 0, "leg A must never receive its own audio"


def test_status_becomes_connected_when_both_legs_bind(client, session):
    with client.websocket_connect("/ws/media-stream") as ws_a:
        ws_a.send_text(json.dumps(_start_msg(session.session_id, "A", "MZ_A")))
        with client.websocket_connect("/ws/media-stream") as ws_b:
            ws_b.send_text(json.dumps(_start_msg(session.session_id, "B", "MZ_B")))
            ws_a.send_text(json.dumps(_media_msg("MZ_A", PAYLOAD)))
            ws_b.receive_json()
            assert session.status.value == "TRANSLATING"
