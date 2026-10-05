"""`gemini-3.5-live-translate` backend tests.

These run the REAL google-genai SDK against a fake WebSocket, so what is
asserted is the JSON that would actually go over the wire, not what we hoped
the SDK would send. The details that matter:

* the target language travels as `setup.generationConfig.translationConfig`
* input is PCM16 at 16 kHz and output PCM16 at 24 kHz, but this class must
  present µ-law 8 kHz to the rest of the app, because `route_audio` measures
  duration assuming µ-law
* audio goes up in ~100 ms chunks, not one message per 20 ms Twilio frame
* a `goAway` or a dropped socket opens a fresh session instead of silently
  ending translation for the rest of the call
"""

import asyncio
import base64
import json
from dataclasses import replace

import pytest
import websockets
from google.genai import live as sdk_live

from app.config import get_settings
from app.services import gemini_service
from app.services.gemini_service import CHUNK_BYTES, GeminiTranslateSession
from app.utils import audio, resample

SETUP_COMPLETE = {"setupComplete": {}}


class FakeWire:
    """One Gemini Live WebSocket, as the SDK sees it."""

    def __init__(self, uri: str, headers: dict | None, *, refuse: bool = False):
        self.uri = uri
        self.headers = headers or {}
        self.sent: list[dict] = []
        self.closed = False
        self._inbound: asyncio.Queue = asyncio.Queue()
        if refuse:
            self.drop(1008, "invalid argument")
        else:
            self.push(SETUP_COMPLETE)

    # --- what the SDK calls -------------------------------------------
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self, decode=None):
        item = await self._inbound.get()
        if isinstance(item, Exception):
            raise item
        return json.dumps(item).encode()

    async def close(self) -> None:
        self.closed = True

    # --- what the tests call ------------------------------------------
    def push(self, message: dict) -> None:
        self._inbound.put_nowait(message)

    def drop(self, code: int = 1011, reason: str = "internal") -> None:
        self._inbound.put_nowait(websockets.exceptions.ConnectionClosedError(
            rcvd=websockets.frames.Close(code, reason), sent=None))

    @property
    def setup(self) -> dict:
        return self.sent[0]["setup"]

    def audio_messages(self) -> list[dict]:
        """The SDK writes snake_case here; the server accepts either spelling."""
        out = []
        for m in self.sent:
            realtime = m.get("realtime_input") or m.get("realtimeInput") or {}
            if realtime.get("audio"):
                out.append(realtime["audio"])
        return out

    def audio_chunks(self) -> list[bytes]:
        return [base64.b64decode(blob["data"]) for blob in self.audio_messages()]


def model_audio(pcm24k: bytes) -> dict:
    return {"serverContent": {"modelTurn": {"parts": [{"inlineData": {
        "mimeType": "audio/pcm;rate=24000",
        "data": base64.b64encode(pcm24k).decode()}}]}}}


@pytest.fixture
def wires(monkeypatch):
    """Every socket the SDK opens, in order. Set `wires.refuse_from` to make
    connections from that index onwards fail their setup."""
    opened: list[FakeWire] = []

    class Registry(list):
        refuse_from: int | None = None

    registry = Registry()

    def fake_ws_connect(uri, additional_headers=None, **kwargs):
        index = len(registry)
        refuse = registry.refuse_from is not None and index >= registry.refuse_from
        wire = FakeWire(uri, additional_headers, refuse=refuse)
        registry.append(wire)
        return wire

    monkeypatch.setattr(sdk_live, "ws_connect", fake_ws_connect)
    return registry


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(gemini_service, "RECONNECT_BACKOFF_S", 0.0)


def settings():
    return replace(get_settings(), translation_backend="gemini",
                   gemini_api_key="g-test-key",
                   gemini_translate_model="gemini-3.5-live-translate-preview")


async def build(target="ta", source="hi", **kwargs):
    received: list[str] = []
    transcripts: list[tuple[str, str]] = []

    async def on_audio(b64):
        received.append(b64)

    async def on_transcript(direction, text):
        transcripts.append((direction, text))

    session = GeminiTranslateSession(
        settings(), source_language=source, target_language=target,
        on_audio=on_audio, on_transcript=on_transcript, label="test/A", **kwargs,
    )
    await session.connect()
    return session, received, transcripts


async def settle():
    await asyncio.sleep(0.05)


def frame_20ms() -> str:
    return audio.b64_encode(audio.ulaw_silence(20))


# --------------------------------------------------------------- the wire
@pytest.mark.asyncio
async def test_setup_names_the_model_and_the_target_language(wires):
    session, _, _ = await build(target="ta")

    setup = wires[0].setup
    assert setup["model"] == "models/gemini-3.5-live-translate-preview"
    gen = setup["generationConfig"]
    assert gen["translationConfig"]["targetLanguageCode"] == "ta"
    assert gen["translationConfig"]["echoTargetLanguage"] is False
    assert gen["responseModalities"] == ["AUDIO"]

    await session.close()


@pytest.mark.asyncio
async def test_there_is_no_source_language_or_prompt_in_setup(wires):
    """Gemini detects the source; a prompt would turn it into a chatbot."""
    session, _, _ = await build(source="hi", target="ta")

    raw = json.dumps(wires[0].setup)
    assert "systemInstruction" not in raw
    assert '"hi"' not in raw

    await session.close()


@pytest.mark.asyncio
async def test_api_key_is_sent_and_is_the_gemini_one(wires):
    session, _, _ = await build()

    wire = wires[0]
    sent_key = wire.headers.get("x-goog-api-key") or ""
    assert sent_key == "g-test-key" or "key=g-test-key" in wire.uri

    await session.close()


@pytest.mark.asyncio
async def test_zh_and_pt_are_sent_with_the_variant_google_expects(wires):
    s1, _, _ = await build(target="zh")
    s2, _, _ = await build(target="pt")

    assert wires[0].setup["generationConfig"]["translationConfig"]["targetLanguageCode"] == "zh-Hans"
    assert wires[1].setup["generationConfig"]["translationConfig"]["targetLanguageCode"] == "pt-BR"

    await s1.close()
    await s2.close()


@pytest.mark.asyncio
async def test_a_refused_setup_raises_so_the_call_falls_back(wires):
    """_open_translator catches this and logs translator_connect_failed."""
    wires.refuse_from = 0
    session = GeminiTranslateSession(
        settings(), source_language="hi", target_language="ta",
        on_audio=lambda b: None, label="test/A")

    with pytest.raises(Exception):
        await session.connect()


# ------------------------------------------------------------ conversion
@pytest.mark.asyncio
async def test_inbound_ulaw_is_sent_as_pcm16_16k(wires):
    session, _, _ = await build()

    for _ in range(5):                                 # 5 x 20 ms = 100 ms
        await session.append_audio(frame_20ms())

    blob = wires[0].audio_messages()[0]
    assert (blob.get("mime_type") or blob.get("mimeType")) == "audio/pcm;rate=16000"
    pcm = wires[0].audio_chunks()[0]
    assert resample.pcm16k_ms(pcm) == pytest.approx(100, abs=1)

    await session.close()


@pytest.mark.asyncio
async def test_twilio_frames_are_batched_into_100ms_chunks(wires):
    """50 messages a second is what Google advises against."""
    session, _, _ = await build()

    for _ in range(4):
        await session.append_audio(frame_20ms())
    assert wires[0].audio_chunks() == [], "sent before a full chunk built up"

    for _ in range(46):                                # 50 frames = 1 s total
        await session.append_audio(frame_20ms())

    chunks = wires[0].audio_chunks()
    assert len(chunks) == 10
    assert all(len(c) == CHUNK_BYTES for c in chunks)

    await session.close()


@pytest.mark.asyncio
async def test_outbound_pcm24k_is_converted_back_to_ulaw(wires):
    """The rest of the app measures duration assuming µ-law, so this must hold."""
    session, received, _ = await build()

    wires[0].push(model_audio(bytes(2 * 24000 * 200 // 1000)))     # 200 ms
    await settle()

    assert received, "no audio reached the callback"
    ulaw = audio.b64_decode(received[0])
    assert resample.ulaw_ms(ulaw) == pytest.approx(200, abs=5)

    await session.close()


@pytest.mark.asyncio
async def test_a_real_tone_survives_the_trip_to_twilio(wires):
    """Not just the right length: actual signal, at roughly the right level."""
    import numpy as np

    session, received, _ = await build()
    t = np.arange(4800) / 24000                                     # 200 ms
    pcm = (8000 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes()
    wires[0].push(model_audio(pcm))
    await settle()

    back = np.frombuffer(audio.ulaw_to_pcm16(audio.b64_decode(received[0])),
                         dtype="<i2").astype(float)
    level = np.sqrt(np.mean(back[100:] ** 2))
    assert 4000 < level < 7000        # 8000 peak sine is ~5657 RMS

    await session.close()


# --------------------------------------------------------- transcripts
@pytest.mark.asyncio
async def test_transcripts_are_reported_in_both_directions(wires):
    session, _, transcripts = await build()

    wires[0].push({"serverContent": {"inputTranscription": {"text": "नमस्ते"}}})
    wires[0].push({"serverContent": {"outputTranscription": {"text": "வணக்கம்"}}})
    await settle()

    assert ("in", "नमस्ते") in transcripts
    assert ("out", "வணக்கம்") in transcripts
    assert session.responses == 1

    await session.close()


@pytest.mark.asyncio
async def test_latency_is_measured_from_first_heard_speech(wires):
    session, _, _ = await build()

    wires[0].push({"serverContent": {"inputTranscription": {"text": "hello"}}})
    await asyncio.sleep(0.05)
    wires[0].push(model_audio(bytes(960)))
    await settle()

    assert session.first_audio_ms is not None
    assert session.first_audio_ms >= 40

    await session.close()


@pytest.mark.asyncio
async def test_turn_boundaries_do_not_stop_the_reader(wires):
    """The SDK's receive() returns at every turnComplete; we must keep going."""
    session, received, _ = await build()

    wires[0].push(model_audio(bytes(960)))
    wires[0].push({"serverContent": {"turnComplete": True}})
    wires[0].push(model_audio(bytes(960)))
    await settle()

    assert len(received) == 2
    assert len(wires) == 1, "a turn boundary must not trigger a reconnect"

    await session.close()


# ----------------------------------------------------------- resilience
@pytest.mark.asyncio
async def test_go_away_moves_to_a_fresh_session(wires):
    session, received, _ = await build(target="ta")
    wires[0].push({"goAway": {"timeLeft": "10s"}})
    await settle()

    assert len(wires) == 2, "no new session after goAway"
    assert wires[0].closed
    assert wires[1].setup["generationConfig"]["translationConfig"]["targetLanguageCode"] == "ta"
    assert session.reconnects == 1

    # And the new session actually carries audio both ways.
    for _ in range(5):
        await session.append_audio(frame_20ms())
    assert wires[1].audio_chunks()
    wires[1].push(model_audio(bytes(960)))
    await settle()
    assert received

    await session.close()


@pytest.mark.asyncio
async def test_a_dropped_socket_is_reconnected(wires, monkeypatch):
    monkeypatch.setattr(gemini_service, "MIN_HEALTHY_S", 0.0)
    session, _, _ = await build()

    wires[0].drop(1011, "internal error")
    await settle()

    assert len(wires) == 2
    assert session.ready

    await session.close()


@pytest.mark.asyncio
async def test_a_session_that_keeps_dying_is_given_up_on(wires):
    """Otherwise a config the server rejects after setup reconnects forever."""
    session, _, _ = await build()

    wires.refuse_from = 1                     # every reconnect is refused
    wires[0].drop()
    await asyncio.sleep(0.3)

    attempts = len(wires)
    assert attempts == 1 + gemini_service.MAX_FAILED_RECONNECTS
    assert session.ready is False
    assert session._reader.done()

    await asyncio.sleep(0.1)
    assert len(wires) == attempts, "still reconnecting after giving up"

    await session.close()


@pytest.mark.asyncio
async def test_audio_is_dropped_not_queued_while_reconnecting(wires):
    """Stale audio arriving late is worse than a gap."""
    session, _, _ = await build()
    session.ready = False

    for _ in range(10):
        await session.append_audio(frame_20ms())

    assert wires[0].audio_chunks() == []

    await session.close()


@pytest.mark.asyncio
async def test_an_error_on_send_does_not_raise_into_the_media_stream(wires):
    session, _, _ = await build()

    async def boom(raw):
        raise RuntimeError("socket gone")

    wires[0].send = boom
    for _ in range(5):
        await session.append_audio(frame_20ms())       # must not raise

    await session.close()


# --------------------------------------------------------------- close
@pytest.mark.asyncio
async def test_close_shuts_the_socket_and_stops_the_reader(wires):
    session, _, _ = await build()

    await asyncio.wait_for(session.close(), timeout=2)

    assert wires[0].closed
    assert session._reader.done()
    await session.close()                                   # idempotent


@pytest.mark.asyncio
async def test_nothing_is_sent_after_close(wires):
    session, _, _ = await build()
    await session.close()

    for _ in range(10):
        await session.append_audio(frame_20ms())

    assert wires[0].audio_chunks() == []


@pytest.mark.asyncio
async def test_cancel_is_a_no_op(wires):
    session, _, _ = await build()
    before = len(wires[0].sent)

    await session.cancel_response()

    assert len(wires[0].sent) == before
    await session.close()


# --------------------------------------------------------------- billing
@pytest.mark.asyncio
async def test_usage_is_billed_per_minute_at_gemini_rates(wires):
    session, _, _ = await build()

    for _ in range(3000):                          # 3000 x 20 ms = 1 minute in
        await session.append_audio(frame_20ms())
    wires[0].push(model_audio(bytes(2 * 24000 * 30)))          # 30 s out
    await settle()
    await session.close()

    summary = session.usage.summary()
    assert summary["billing"] == "per_minute"
    assert summary["audio_in_min"] == pytest.approx(1.0, abs=0.01)
    assert summary["audio_out_min"] == pytest.approx(0.5, abs=0.01)
    # $0.0053/min in + $0.0315/min out
    assert summary["usd"] == pytest.approx(0.0053 + 0.5 * 0.0315, abs=1e-4)


# ---------------------------------------------------------- event loop
@pytest.mark.asyncio
async def test_connecting_does_not_block_the_event_loop(wires, monkeypatch):
    """Building an SDK client takes ~1 s of synchronous work. On the loop, that
    freezes audio for every call in progress while one leg connects."""
    import time as _time

    real = gemini_service.genai.Client

    def slow_client(**kwargs):
        _time.sleep(0.3)                  # stands in for the real cost
        return real(**kwargs)

    gemini_service.client_for.cache_clear()
    monkeypatch.setattr(gemini_service.genai, "Client", slow_client)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    session, _, _ = await build()
    beat.cancel()
    gemini_service.client_for.cache_clear()

    # 0.3 s of blocking would allow ~0 ticks; off the loop, ~30.
    assert ticks >= 10, "client creation blocked the event loop"

    await session.close()


@pytest.mark.asyncio
async def test_sessions_share_one_client_per_key(wires):
    s1, _, _ = await build()
    s2, _, _ = await build()

    assert s1._client is s2._client

    await s1.close()
    await s2.close()
