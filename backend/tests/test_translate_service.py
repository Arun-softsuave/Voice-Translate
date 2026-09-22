"""`gpt-realtime-translate` backend tests.

The wire format here was established by probing the live API, and every detail
below is one that silently breaks things if it drifts:

* events are `session.`-prefixed — the `/v1/realtime` names do nothing
* only three session fields are accepted; `format` and `voice` are rejected
* the endpoint speaks PCM16 24 kHz, but this class must present µ-law to the
  rest of the app, because `route_audio` measures duration assuming µ-law
* shutdown is a handshake, not a socket close
"""

import asyncio
import json
from dataclasses import replace

import pytest

from app.config import get_settings
from app.services import translate_service
from app.services.translate_service import TranslateSession
from app.utils import audio, resample


class FakeSocket:
    """Stands in for the OpenAI translations WebSocket."""

    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False
        self._inbound = asyncio.Queue()

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self) -> None:
        self.closed = True

    def push(self, event: dict) -> None:
        self._inbound.put_nowait(json.dumps(event))

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._inbound.get()

    def of_type(self, kind: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == kind]


@pytest.fixture(autouse=True)
def fast_close(monkeypatch):
    """Most tests never send the close ack, so don't wait the real 3s for it."""
    monkeypatch.setattr(translate_service, "CLOSE_TIMEOUT_S", 0.05)


@pytest.fixture
def patched(monkeypatch):
    socket = FakeSocket()

    async def fake_connect(url, **kwargs):
        socket.url = url
        socket.kwargs = kwargs
        return socket

    monkeypatch.setattr(translate_service.websockets, "connect", fake_connect)
    return socket


def settings():
    return replace(get_settings(), openai_api_key="sk-test",
                   openai_translate_model="gpt-realtime-translate")


async def build(**overrides):
    received: list[str] = []

    async def on_audio(b64):
        received.append(b64)

    session = TranslateSession(
        settings(),
        source_language=overrides.get("source", "hi"),
        target_language=overrides.get("target", "ta"),
        on_audio=on_audio,
        label="test/A",
    )
    await session.connect()
    return session, received


# --------------------------------------------------------------- the wire
@pytest.mark.asyncio
async def test_connects_to_the_translations_endpoint(patched):
    session, _ = await build()

    assert "/v1/realtime/translations" in patched.url
    assert "model=gpt-realtime-translate" in patched.url
    headers = patched.kwargs.get("additional_headers") or patched.kwargs.get("extra_headers")
    assert headers["Authorization"].startswith("Bearer ")

    await session.close()


@pytest.mark.asyncio
async def test_session_update_sends_only_accepted_fields(patched):
    """`format` and `voice` are rejected by this endpoint with unknown_parameter."""
    session, _ = await build(target="ta")

    payload = patched.of_type("session.update")[0]["session"]
    audio_cfg = payload["audio"]

    assert audio_cfg["output"]["language"] == "ta"
    assert "format" not in audio_cfg["output"]
    assert "voice" not in audio_cfg["output"]
    assert "format" not in audio_cfg["input"]
    # No prompt, no modalities, no turn detection — none exist here.
    assert "instructions" not in payload
    assert "output_modalities" not in payload
    assert "turn_detection" not in audio_cfg["input"]

    await session.close()


@pytest.mark.asyncio
async def test_append_uses_the_session_prefixed_event(patched):
    """The `/v1/realtime` event name is silently ignored by this endpoint."""
    session, _ = await build()
    patched.push({"type": "session.created"})
    await asyncio.sleep(0.05)

    await session.append_audio(audio.b64_encode(audio.ulaw_silence(20)))

    assert patched.of_type("session.input_audio_buffer.append")
    assert patched.of_type("input_audio_buffer.append") == [], "wrong event name"

    await session.close()


# ------------------------------------------------------------ conversion
@pytest.mark.asyncio
async def test_inbound_ulaw_is_resampled_to_pcm16_24k(patched):
    session, _ = await build()
    patched.push({"type": "session.created"})
    await asyncio.sleep(0.05)

    await session.append_audio(audio.b64_encode(audio.ulaw_silence(20)))

    sent = patched.of_type("session.input_audio_buffer.append")[0]
    pcm = audio.b64_decode(sent["audio"])
    # 20 ms of µ-law is 160 bytes; 20 ms of PCM16 at 24 kHz is 960.
    assert len(pcm) == 960
    assert resample.pcm24k_ms(pcm) == pytest.approx(20, abs=1)

    await session.close()


@pytest.mark.asyncio
async def test_outbound_pcm_is_converted_back_to_ulaw(patched):
    """The rest of the app measures duration assuming µ-law, so this must hold."""
    session, received = await build()
    patched.push({"type": "session.updated"})
    await asyncio.sleep(0.05)

    pcm_200ms = bytes(2 * 24000 * 200 // 1000)
    patched.push({"type": "session.output_audio.delta",
                  "delta": audio.b64_encode(pcm_200ms)})
    await asyncio.sleep(0.05)

    assert received, "no audio reached the callback"
    ulaw = audio.b64_decode(received[0])
    assert resample.ulaw_ms(ulaw) == pytest.approx(200, abs=5)

    await session.close()


# ---------------------------------------------------------------- guards
@pytest.mark.asyncio
async def test_audio_is_not_sent_before_the_session_is_ready(patched):
    session, _ = await build()

    await session.append_audio(audio.b64_encode(audio.ulaw_silence(20)))
    assert patched.of_type("session.input_audio_buffer.append") == []

    await session.close()


@pytest.mark.asyncio
async def test_error_event_does_not_kill_the_session(patched):
    """Regression: logging `message` in `extra` raised and killed the reader."""
    session, received = await build()
    patched.push({"type": "session.updated"})
    patched.push({"type": "error", "error": {"code": "x", "message": "y"}})
    patched.push({"type": "session.output_audio.delta",
                  "delta": audio.b64_encode(bytes(960))})
    await asyncio.sleep(0.05)

    assert received, "an error event stopped translation"

    await session.close()


@pytest.mark.asyncio
async def test_close_is_a_handshake_not_a_socket_slam(patched):
    """Closing outright drops audio still draining out of the session."""
    session, _ = await build()
    patched.push({"type": "session.updated"})
    await asyncio.sleep(0.05)

    async def ack():
        await asyncio.sleep(0.02)
        patched.push({"type": "session.closed"})

    asyncio.create_task(ack())
    await session.close()

    assert patched.of_type("session.close"), "never asked the session to close"
    assert patched.closed


@pytest.mark.asyncio
async def test_close_gives_up_rather_than_hanging(patched):
    """If the ack never arrives we must still shut down."""
    session, _ = await build()
    patched.push({"type": "session.updated"})
    await asyncio.sleep(0.05)

    await asyncio.wait_for(session.close(), timeout=2)
    assert patched.closed


@pytest.mark.asyncio
async def test_cancel_is_a_no_op(patched):
    """This endpoint has no cancel; barge-in is handled upstream."""
    session, _ = await build()
    patched.push({"type": "session.updated"})
    await asyncio.sleep(0.05)

    await session.cancel_response()

    assert patched.of_type("response.cancel") == []

    await session.close()


# --------------------------------------------------------------- billing
@pytest.mark.asyncio
async def test_usage_is_billed_per_minute_of_audio(patched):
    session, _ = await build()
    patched.push({"type": "session.updated"})
    await asyncio.sleep(0.05)

    for _ in range(50):                       # 50 x 20 ms = 1 second in
        await session.append_audio(audio.b64_encode(audio.ulaw_silence(20)))

    await session.close()

    summary = session.usage.summary()
    assert summary["billing"] == "per_minute"
    assert summary["audio_in_min"] == pytest.approx(1 / 60, abs=0.005)
    assert summary["usd"] > 0
