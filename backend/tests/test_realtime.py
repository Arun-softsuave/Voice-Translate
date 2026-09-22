"""OpenAI Realtime translator tests (design doc §6, §7).

The wire format is asserted against the GA session shape: nested
`audio.input` / `audio.output` objects and `audio/pcmu` on both sides. Getting
this wrong is the single most likely integration failure, because most public
examples still show the beta shape.
"""

import asyncio
import json

import pytest

from app.config import get_settings
from app.services import realtime_service
from app.services.realtime_service import RealtimeTranslator


class FakeSocket:
    """Stands in for the OpenAI WebSocket."""

    def __init__(self, inbound=()):
        self.sent: list[dict] = []
        self.closed = False
        self._inbound = asyncio.Queue()
        for event in inbound:
            self._inbound.put_nowait(json.dumps(event))

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


@pytest.fixture
def patched(monkeypatch):
    socket = FakeSocket()

    async def fake_connect(url, **kwargs):
        socket.url = url
        socket.kwargs = kwargs
        return socket

    monkeypatch.setattr(realtime_service.websockets, "connect", fake_connect)
    return socket


async def build(patched, **overrides):
    received: list[str] = []
    interrupts: list[int] = []

    async def on_audio(b64):
        received.append(b64)

    async def on_speech_started():
        interrupts.append(1)

    translator = RealtimeTranslator(
        get_settings(),
        source_language=overrides.get("source", "ta"),
        target_language=overrides.get("target", "hi"),
        on_audio=on_audio,
        on_speech_started=on_speech_started,
        label="test/A",
    )
    await translator.connect()
    return translator, received, interrupts


def test_translation_is_disabled_during_the_test_suite():
    """Guards against a populated .env making tests call OpenAI for real."""
    assert get_settings().translation_enabled is False


@pytest.mark.asyncio
async def test_session_update_uses_the_ga_shape(patched):
    translator, _, _ = await build(patched)

    update = patched.of_type("session.update")[0]["session"]

    assert update["type"] == "realtime"
    assert update["output_modalities"] == ["audio"]
    # µ-law both ways — this is what removes transcoding from the hot path.
    assert update["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert update["audio"]["output"]["format"] == {"type": "audio/pcmu"}
    # The beta's flat fields must not appear.
    assert "input_audio_format" not in update
    assert "modalities" not in update

    await translator.close()


@pytest.mark.asyncio
async def test_iso_codes_are_resolved_to_names_in_the_prompt(patched):
    translator, _, _ = await build(patched, source="ta", target="hi")

    instructions = patched.of_type("session.update")[0]["session"]["instructions"]

    # The interface carries codes; the prompt must say the English names.
    assert "Tamil" in instructions and "Hindi" in instructions
    assert "ta" != instructions.strip()[:2]
    assert "interpreter" in instructions.lower()
    assert "never answer" in instructions.lower()

    await translator.close()


@pytest.mark.asyncio
async def test_turn_detection_is_configured_for_low_latency(patched):
    translator, _, _ = await build(patched)

    vad = patched.of_type("session.update")[0]["session"]["audio"]["input"]["turn_detection"]

    assert vad["type"] == "server_vad"
    assert vad["interrupt_response"] is True
    # Every millisecond here is added to every translated utterance.
    assert vad["silence_duration_ms"] <= 500

    await translator.close()


@pytest.mark.asyncio
async def test_audio_deltas_reach_the_callback(patched):
    translator, received, _ = await build(patched)
    patched.push({"type": "session.updated"})
    patched.push({"type": "response.output_audio.delta", "delta": "AAAA"})
    await asyncio.sleep(0.05)

    assert received == ["AAAA"]

    await translator.close()


@pytest.mark.asyncio
async def test_speech_started_fires_the_interrupt_callback(patched):
    translator, _, interrupts = await build(patched)
    patched.push({"type": "input_audio_buffer.speech_started"})
    await asyncio.sleep(0.05)

    assert interrupts == [1]

    await translator.close()


@pytest.mark.asyncio
async def test_audio_is_not_sent_before_the_session_is_ready(patched):
    """Appending before session.created would be discarded by the API."""
    translator, _, _ = await build(patched)

    await translator.append_audio("AAAA")
    assert patched.of_type("input_audio_buffer.append") == []

    patched.push({"type": "session.created"})
    await asyncio.sleep(0.05)
    await translator.append_audio("AAAA")

    appended = patched.of_type("input_audio_buffer.append")
    assert appended and appended[0]["audio"] == "AAAA"

    await translator.close()


@pytest.mark.asyncio
async def test_latency_is_measured_from_end_of_speech(patched):
    translator, _, _ = await build(patched)
    patched.push({"type": "session.updated"})
    patched.push({"type": "input_audio_buffer.speech_stopped"})
    await asyncio.sleep(0.02)
    patched.push({"type": "response.output_audio.delta", "delta": "AAAA"})
    await asyncio.sleep(0.05)

    assert translator.first_audio_ms is not None
    assert translator.first_audio_ms >= 0

    await translator.close()


@pytest.mark.asyncio
async def test_error_event_does_not_kill_the_session(patched):
    translator, received, _ = await build(patched)
    patched.push({"type": "session.updated"})
    patched.push({"type": "error", "error": {"code": "x", "message": "y"}})
    patched.push({"type": "response.output_audio.delta", "delta": "BBBB"})
    await asyncio.sleep(0.05)

    assert received == ["BBBB"], "a logged error must not stop translation"

    await translator.close()


@pytest.mark.asyncio
async def test_connects_with_bearer_auth_and_no_beta_header(patched):
    translator, _, _ = await build(patched)

    headers = patched.kwargs.get("additional_headers") or patched.kwargs.get("extra_headers")
    assert headers["Authorization"].startswith("Bearer ")
    # The GA API rejects the beta header.
    assert "OpenAI-Beta" not in headers
    assert "model=" in patched.url

    await translator.close()


@pytest.mark.asyncio
async def test_cancel_sends_response_cancel(patched):
    translator, _, _ = await build(patched)
    patched.push({"type": "session.updated"})
    await asyncio.sleep(0.05)

    await translator.cancel_response()

    assert patched.of_type("response.cancel")

    await translator.close()
