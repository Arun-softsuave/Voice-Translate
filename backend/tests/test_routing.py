"""Routing and session-isolation tests.

These are the most important tests in the project. Design doc §17 of the task
spec: audio must reach the opposite participant and must never return to its
source, and concurrent calls must not share audio.
"""

import pytest

from app.models.session import ParticipantId
from app.services import session_service
from app.services.session_service import SessionRegistry
from app.utils import audio


class FakeWS:
    """Minimal stand-in for fastapi.WebSocket."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, message: dict) -> None:
        if self.closed:
            raise RuntimeError("socket closed")
        self.sent.append(message)

    def media_payloads(self) -> list[str]:
        return [m["media"]["payload"] for m in self.sent if m["event"] == "media"]


@pytest.fixture
def wired():
    """A session with both legs bound to fake sockets."""
    registry = SessionRegistry()
    session = registry.create(source_language="ta", target_language="hi")
    ws_a, ws_b = FakeWS(), FakeWS()
    registry.bind_stream(session, ParticipantId.A, stream_sid="MZ_A",
                         call_sid="CA_A", ws=ws_a)
    registry.bind_stream(session, ParticipantId.B, stream_sid="MZ_B",
                         call_sid="CA_B", ws=ws_b)
    return registry, session, ws_a, ws_b


PAYLOAD = audio.b64_encode(audio.ulaw_silence(20))


@pytest.mark.asyncio
async def test_a_audio_reaches_b_only(wired):
    _, session, ws_a, ws_b = wired
    await session_service.route_audio(session, ParticipantId.A, PAYLOAD)
    assert ws_b.media_payloads() == [PAYLOAD]
    assert ws_a.media_payloads() == [], "audio must never return to its source"


@pytest.mark.asyncio
async def test_b_audio_reaches_a_only(wired):
    _, session, ws_a, ws_b = wired
    await session_service.route_audio(session, ParticipantId.B, PAYLOAD)
    assert ws_a.media_payloads() == [PAYLOAD]
    assert ws_b.media_payloads() == []


@pytest.mark.asyncio
async def test_media_message_carries_the_destination_stream_sid(wired):
    _, session, _, ws_b = wired
    await session_service.route_audio(session, ParticipantId.A, PAYLOAD)
    assert ws_b.sent[0]["streamSid"] == "MZ_B"


@pytest.mark.asyncio
async def test_echo_mode_returns_to_sender(wired):
    """Milestone 2 only — proves bidirectional injection before a peer exists."""
    _, session, ws_a, ws_b = wired
    await session_service.route_audio(session, ParticipantId.A, PAYLOAD, echo_mode=True)
    assert ws_a.media_payloads() == [PAYLOAD]
    assert ws_b.media_payloads() == []


@pytest.mark.asyncio
async def test_audio_is_dropped_when_peer_not_yet_connected():
    """User 1 speaking before User 2 answers has nowhere to go — count it."""
    registry = SessionRegistry()
    session = registry.create(source_language="ta", target_language="hi")
    ws_a = FakeWS()
    registry.bind_stream(session, ParticipantId.A, stream_sid="MZ_A",
                         call_sid="CA_A", ws=ws_a)

    delivered = await session_service.route_audio(session, ParticipantId.A, PAYLOAD)

    assert delivered is False
    assert session.b.dropped_no_peer == 1
    assert ws_a.media_payloads() == []


@pytest.mark.asyncio
async def test_concurrent_sessions_do_not_share_audio():
    registry = SessionRegistry()
    one = registry.create(source_language="ta", target_language="hi")
    two = registry.create(source_language="en", target_language="hi")

    sockets = {}
    for name, session in (("one", one), ("two", two)):
        for pid in (ParticipantId.A, ParticipantId.B):
            ws = FakeWS()
            sockets[(name, pid)] = ws
            registry.bind_stream(session, pid, stream_sid=f"MZ_{name}_{pid.value}",
                                 call_sid=f"CA_{name}_{pid.value}", ws=ws)

    await session_service.route_audio(one, ParticipantId.A, PAYLOAD)

    assert sockets[("one", ParticipantId.B)].media_payloads() == [PAYLOAD]
    for key, ws in sockets.items():
        if key != ("one", ParticipantId.B):
            assert ws.media_payloads() == [], f"{key} received audio from another call"


@pytest.mark.asyncio
async def test_clear_flushes_queued_playback(wired):
    _, session, _, ws_b = wired
    for _ in range(5):
        await session_service.route_audio(session, ParticipantId.A, PAYLOAD)
    assert session.b.queued_ms == pytest.approx(100.0)

    await session_service.clear_playback(session, ParticipantId.B)

    assert ws_b.sent[-1] == {"event": "clear", "streamSid": "MZ_B"}
    assert session.b.queued_ms == session.b.played_ms


@pytest.mark.asyncio
async def test_send_on_dead_socket_is_not_fatal(wired):
    _, session, _, ws_b = wired
    ws_b.closed = True
    assert await session_service.route_audio(session, ParticipantId.A, PAYLOAD) is False


def test_double_binding_is_rejected(wired):
    registry, session, _, _ = wired
    with pytest.raises(ValueError):
        registry.bind_stream(session, ParticipantId.A, stream_sid="MZ_X",
                             call_sid="CA_X", ws=FakeWS())


def test_lookup_by_call_sid(wired):
    registry, session, _, _ = wired
    found = registry.find_by_call_sid("CA_B")
    assert found is not None
    assert found[0].session_id == session.session_id
    assert found[1] is ParticipantId.B
    assert registry.find_by_call_sid("CA_NOPE") is None


def test_peer_relationship_is_symmetric():
    assert ParticipantId.A.peer is ParticipantId.B
    assert ParticipantId.B.peer is ParticipantId.A
