"""OpenAI `gpt-realtime-translate` backend.

A purpose-built streaming translation model. Probed against the live API on
22 Sep 2026; the contract below is what it actually does, not what the docs
describe.

Why it is worth the extra machinery: it emits translated audio **while the
source is still being spoken**. In testing, first audio arrived 1,094 ms after
the stream opened, against source speech 8.2 s long. The conversational model
cannot do that — it waits for end-of-speech plus a VAD silence window.

Three behaviours differ from `/v1/realtime` and each one bites:

1. **Events are `session.`-prefixed.** `session.input_audio_buffer.append`,
   `session.output_audio.delta`. Reusing the other names silently does nothing.
2. **The stream must never stop.** Sending speech then pausing yields
   transcripts and *zero audio*. Silence has to keep flowing, so gaps are
   filled rather than skipped.
3. **Shutdown is a handshake.** `session.close`, then wait for
   `session.closed`. Closing the socket directly drops audio still draining.

There is no way to cancel a response — no `response.cancel` equivalent exists.
Barge-in is therefore handled upstream, in the media stream layer.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from typing import Awaitable, Callable

import websockets

from app.config import Settings
from app.services.pricing import Usage
from app.utils import resample
from app.utils.audio import b64_decode, b64_encode

log = logging.getLogger(__name__)

TRANSLATE_URL = "wss://api.openai.com/v1/realtime/translations"

# Filling gaps keeps the model producing (see point 2 above). 20 ms matches a
# Twilio frame.
SILENCE_FRAME_MS = 20
MAX_GAP_MS = 400.0          # beyond this we stop back-filling and just resync
CLOSE_TIMEOUT_S = 3.0


class TranslateSession:
    """One direction of translation on the dedicated translation endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        source_language: str,          # ISO code; auto-detected, kept for logs
        target_language: str,          # ISO code, e.g. "ta"
        on_audio: Callable[[str], Awaitable[None]],
        on_speech_started: Callable[[], Awaitable[None]] | None = None,
        label: str = "",
    ) -> None:
        self._settings = settings
        self.source_language = source_language
        self.target_language = target_language
        self._on_audio = on_audio
        self._on_speech_started = on_speech_started   # unused; kept for parity
        self.label = label

        self._ws = None
        self._reader: asyncio.Task | None = None
        self._closed = False
        self._closed_ack = asyncio.Event()
        self.ready = False

        # Twilio speaks µ-law 8k, this endpoint only speaks PCM16 24k.
        self._to_model = resample.TwilioToOpenAI()
        self._to_twilio = resample.OpenAIToTwilio()

        self._last_append: float | None = None
        self._speech_stopped_at: float | None = None
        self.first_audio_ms: float | None = None
        self.responses = 0

        self.usage = Usage(model=settings.openai_translate_model)
        self._audio_in_ms = 0.0
        self._audio_out_ms = 0.0

    # --- lifecycle --------------------------------------------------------
    async def connect(self) -> None:
        url = f"{TRANSLATE_URL}?model={self._settings.openai_translate_model}"
        headers = {"Authorization": f"Bearer {self._settings.openai_api_key}"}

        kwargs: dict = {"max_size": None}
        if "additional_headers" in inspect.signature(websockets.connect).parameters:
            kwargs["additional_headers"] = headers
        else:  # pragma: no cover - older websockets
            kwargs["extra_headers"] = headers

        self._ws = await websockets.connect(url, **kwargs)
        await self._configure_session()
        self._reader = asyncio.create_task(self._read_loop())
        log.info(
            "translate_connected",
            extra={"label": self.label,
                   "direction": f"{self.source_language}->{self.target_language}"},
        )

    async def _configure_session(self) -> None:
        """Only these three fields are accepted.

        `audio.input.format`, `audio.output.format` and `audio.output.voice`
        are all rejected with `unknown_parameter` — verified by probe. The
        source language is detected automatically and cannot be set.
        """
        await self._send({
            "type": "session.update",
            "session": {
                "audio": {
                    "input": {
                        "transcription": {"model": "gpt-realtime-whisper"},
                        "noise_reduction": {"type": "far_field"},
                    },
                    "output": {"language": self.target_language},
                },
            },
        })

    async def close(self) -> None:
        """Graceful shutdown: ask, then wait, then hang up.

        Closing the socket outright discards translated audio still draining
        out of the session.
        """
        if self._closed:
            return
        self._closed = True

        try:
            if self._ws is not None and self.ready:
                await self._send({"type": "session.close"}, force=True)
                try:
                    await asyncio.wait_for(self._closed_ack.wait(), CLOSE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    log.debug("translate_close_timeout", extra={"label": self.label})
        except Exception:  # noqa: BLE001
            pass

        if self._reader:
            self._reader.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

        self.usage.set_audio_minutes(self._audio_in_ms / 60000.0,
                                     self._audio_out_ms / 60000.0)
        log.info(
            "translate_closed",
            extra={"label": self.label,
                   "first_audio_ms": self.first_audio_ms,
                   "audio_in_s": round(self._audio_in_ms / 1000, 1),
                   "audio_out_s": round(self._audio_out_ms / 1000, 1),
                   **self.usage.summary()},
        )

    # --- sending ----------------------------------------------------------
    async def append_audio(self, payload_b64: str) -> None:
        """Feed one base64 µ-law frame, resampling to PCM16 24 kHz."""
        if not self.ready or self._closed:
            return

        await self._fill_gap()

        ulaw = b64_decode(payload_b64)
        self._audio_in_ms += resample.ulaw_ms(ulaw)
        pcm = self._to_model(ulaw)
        if pcm:
            await self._send({
                "type": "session.input_audio_buffer.append",
                "audio": b64_encode(pcm),
            })
        self._last_append = time.monotonic()

    async def _fill_gap(self) -> None:
        """Keep the stream unbroken across pauses.

        The model stops producing audio if the stream stalls, so a gap is
        back-filled with silence rather than simply skipped. Large gaps are
        not filled — that means the call itself stalled, and pumping seconds of
        silence would only push the translation further behind.
        """
        if self._last_append is None:
            return
        gap_ms = (time.monotonic() - self._last_append) * 1000
        if gap_ms <= SILENCE_FRAME_MS * 2 or gap_ms > MAX_GAP_MS:
            return

        frames = int(gap_ms // SILENCE_FRAME_MS)
        silence = self._to_model(bytes([0xFF]) * (8 * SILENCE_FRAME_MS))
        for _ in range(frames):
            await self._send({
                "type": "session.input_audio_buffer.append",
                "audio": b64_encode(silence),
            })
        self._audio_in_ms += frames * SILENCE_FRAME_MS

    async def cancel_response(self) -> None:
        """No-op: this endpoint has no cancel. Barge-in is handled upstream."""
        return

    async def _send(self, message: dict, *, force: bool = False) -> None:
        if self._ws is None or (self._closed and not force):
            return
        try:
            await self._ws.send(json.dumps(message))
        except Exception as exc:  # noqa: BLE001
            log.warning("translate_send_failed",
                        extra={"label": self.label, "reason": type(exc).__name__})

    # --- receiving --------------------------------------------------------
    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                await self._handle(json.loads(raw))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not self._closed:
                log.warning("translate_disconnected",
                            extra={"label": self.label, "reason": type(exc).__name__})

    async def _handle(self, event: dict) -> None:
        etype = event.get("type", "")

        if etype in ("session.created", "session.updated"):
            self.ready = True
            return

        if etype == "session.output_audio.delta":
            delta = event.get("delta")
            if not delta:
                return
            if self.first_audio_ms is None and self._speech_stopped_at:
                self.first_audio_ms = round(
                    (time.monotonic() - self._speech_stopped_at) * 1000)

            pcm = b64_decode(delta)
            self._audio_out_ms += resample.pcm24k_ms(pcm)
            ulaw = self._to_twilio(pcm)
            if ulaw:
                await self._on_audio(b64_encode(ulaw))
            return

        if etype == "session.output_transcript.delta":
            self.responses += 1
            return

        if etype == "session.input_transcript.delta":
            # First sign the model has heard speech; used only for latency.
            if self._speech_stopped_at is None:
                self._speech_stopped_at = time.monotonic()
            return

        if etype == "session.closed":
            self._closed_ack.set()
            return

        if etype == "error":
            err = event.get("error", {})
            # "message" is reserved on LogRecord; using it raises KeyError.
            log.error("translate_error",
                      extra={"label": self.label, "code": err.get("code"),
                             "detail": err.get("message")})
