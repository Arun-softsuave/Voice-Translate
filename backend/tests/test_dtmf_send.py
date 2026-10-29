"""Sending keypad tones into the far leg.

The tone itself is proved correct in `test_dtmf_tone.py`. What matters here is
everything around it: that the tones reach the phone and nothing else, that
whatever was queued is dropped first so they are heard *now*, and that two
people pressing at once cannot interleave their digits into gibberish.
"""

import asyncio

import pytest

from app.models.session import ParticipantId
from app.services import session_service
from app.services.session_service import SessionRegistry
from app.utils import audio
from app.utils.audio import DTMF_TONE_MS

from tests.test_dtmf_tone import decode


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, message: dict) -> None:
        if self.closed:
            raise RuntimeError("socket closed")
        self.sent.append(message)

    def media_payloads(self) -> list[str]:
        return [m["media"]["payload"] for m in self.sent if m["event"] == "media"]

    def events(self) -> list[str]:
        return [m["event"] for m in self.sent]


@pytest.fixture
def wired():
    registry = SessionRegistry()
    session = registry.create(source_language="en", target_language="es")
    ws_a, ws_b = FakeWS(), FakeWS()
    registry.bind_stream(session, ParticipantId.A, stream_sid="MZ_A",
                         call_sid="CA_A", ws=ws_a)
    registry.bind_stream(session, ParticipantId.B, stream_sid="MZ_B",
                         call_sid="CA_B", ws=ws_b)
    return registry, session, ws_a, ws_b


def digits_heard(ws: FakeWS) -> str:
    """Decode what the far end would actually have heard."""
    out = []
    for payload in ws.media_payloads():
        tone = audio.b64_decode(payload)[:DTMF_TONE_MS * 8]
        out.append(decode(tone))
    return "".join(out)


@pytest.mark.asyncio
async def test_the_tones_reach_the_phone_and_nobody_else(wired):
    _, session, ws_a, ws_b = wired
    await session_service.send_digits(session, "2")
    assert digits_heard(ws_b) == "2"
    assert ws_a.media_payloads() == [], "the caller must not hear their own keypress"


@pytest.mark.asyncio
async def test_a_whole_sequence_arrives_in_order(wired):
    _, session, _, ws_b = wired
    await session_service.send_digits(session, "*1904#")
    assert digits_heard(ws_b) == "*1904#"


@pytest.mark.asyncio
async def test_one_message_per_key(wired):
    _, session, _, ws_b = wired
    await session_service.send_digits(session, "123")
    assert len(ws_b.media_payloads()) == 3


@pytest.mark.asyncio
async def test_queued_speech_is_dropped_before_the_tone(wired):
    """A menu's translation can be ten seconds long; the tone cannot wait."""
    _, session, _, ws_b = wired
    await session_service.route_audio(
        session, ParticipantId.A, audio.b64_encode(audio.ulaw_silence(5000)))

    await session_service.send_digits(session, "1")

    events = ws_b.events()
    assert "clear" in events
    assert events.index("clear") > 0, "the speech was queued first"
    assert events.index("clear") < len(events) - 1, "the tone follows the clear"
    assert events[-1] == "media"


@pytest.mark.asyncio
async def test_nothing_is_sent_when_the_phone_is_not_streaming(wired):
    _, session, _, ws_b = wired
    session.b.ws = None
    with pytest.raises(session_service.NotStreaming):
        await session_service.send_digits(session, "1")
    assert ws_b.media_payloads() == []


@pytest.mark.asyncio
async def test_a_silent_leg_is_not_counted_as_a_dropped_frame(wired):
    """Nobody's speech was lost, so the no-peer counter must not move."""
    _, session, _, _ = wired
    session.b.ws = None
    before = session.b.dropped_no_peer
    with pytest.raises(session_service.NotStreaming):
        await session_service.send_digits(session, "1")
    assert session.b.dropped_no_peer == before


@pytest.mark.asyncio
async def test_the_tones_are_counted_for_the_call_record(wired):
    _, session, _, _ = wired
    await session_service.send_digits(session, "77")
    assert session.b.digits_out == 2


@pytest.mark.asyncio
async def test_no_mark_is_sent_for_a_tone(wired):
    """Marks advance the playback clock by a fixed 500 ms; ours would lie."""
    _, session, _, ws_b = wired
    await session_service.send_digits(session, "5")
    assert "mark" not in ws_b.events()


@pytest.mark.asyncio
async def test_the_tone_occupies_the_playout_clock(wired):
    """Otherwise filler would be stacked on top of a tone still playing."""
    _, session, _, _ = wired
    sent_ms = await session_service.send_digits(session, "12")
    backlog = session_service.playback_backlog_s(session.b)
    assert backlog == pytest.approx(sent_ms / 1000, abs=0.05)


@pytest.mark.asyncio
async def test_the_leg_is_held_quiet_until_the_tones_have_played(wired):
    _, session, _, _ = wired
    sent_ms = await session_service.send_digits(session, "3")
    remaining = session.b.quiet_until - asyncio.get_running_loop().time()
    # The loop clock and time.monotonic() share an origin on CPython.
    assert remaining > sent_ms / 1000 - 0.05


@pytest.mark.asyncio
async def test_two_bursts_at_once_do_not_interleave(wired):
    """Overlapping tones would reach the far end as neither digit."""
    _, session, _, ws_b = wired
    await asyncio.gather(
        session_service.send_digits(session, "111"),
        session_service.send_digits(session, "999"),
    )
    assert digits_heard(ws_b) in ("111999", "999111")


@pytest.mark.asyncio
async def test_concurrent_calls_never_share_tones():
    """The isolation rule holds for generated audio too, not just speech."""
    registry = SessionRegistry()
    one = registry.create(source_language="en", target_language="es")
    two = registry.create(source_language="en", target_language="es")
    sockets = {}
    for session in (one, two):
        ws_a, ws_b = FakeWS(), FakeWS()
        registry.bind_stream(session, ParticipantId.A,
                             stream_sid=f"MZ_A{session.session_id}",
                             call_sid="CA_A", ws=ws_a)
        registry.bind_stream(session, ParticipantId.B,
                             stream_sid=f"MZ_B{session.session_id}",
                             call_sid="CA_B", ws=ws_b)
        sockets[session.session_id] = (ws_a, ws_b)

    await session_service.send_digits(one, "4")

    assert digits_heard(sockets[one.session_id][1]) == "4"
    assert sockets[two.session_id][1].media_payloads() == []
    assert sockets[two.session_id][0].media_payloads() == []


@pytest.mark.asyncio
async def test_the_tone_carries_the_right_stream_id(wired):
    """Clearing or playing into the wrong leg would cut the caller's own ear."""
    _, session, _, ws_b = wired
    await session_service.send_digits(session, "6")
    assert {m["streamSid"] for m in ws_b.sent} == {"MZ_B"}
