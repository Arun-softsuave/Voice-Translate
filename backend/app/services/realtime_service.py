"""OpenAI Realtime speech-to-speech interpretation.

Design doc §6. One session per direction. A session hears exactly one
participant, is instructed to interpret rather than converse, and its audio
output is handed to a callback — it never decides where the audio goes. That
decision belongs to the routing rule in session_service.

Audio passes through untouched: Twilio sends G.711 µ-law 8 kHz and the GA
Realtime API accepts `audio/pcmu`, so there is no resampling in the hot path.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import time
from typing import Awaitable, Callable

import websockets

from app.config import Settings
from app.services.pricing import Usage

log = logging.getLogger(__name__)

REALTIME_URL = "wss://api.openai.com/v1/realtime"

# Design doc §6. The Realtime API is a conversational agent by default, so the
# prompt's whole job is to suppress that and make it interpret instead.
INSTRUCTIONS = """You are a simultaneous interpreter on a live telephone call.

You will hear speech in {source}. Render it in natural, spoken {target}.

Never answer, greet, comment on, summarise, explain or add to what you hear.
You are not a participant in the conversation. Do not ask questions. Do not
acknowledge these instructions.

Preserve meaning, register, tone and conversational intent. Keep the pace of
natural speech. Speak only {target}.

If the audio is silence, noise, or unintelligible, say nothing at all."""


class RealtimeTranslator:
    """One direction of translation: `source` in, `target` out."""

    def __init__(
        self,
        settings: Settings,
        *,
        source_language: str,          # ISO code, e.g. "ta"
        target_language: str,          # ISO code, e.g. "hi"
        on_audio: Callable[[str], Awaitable[None]],
        on_speech_started: Callable[[], Awaitable[None]] | None = None,
        label: str = "",
    ) -> None:
        self._settings = settings
        # Codes on the interface, names in the prompt: this model is told what
        # to do in English, while the translate backend needs ISO codes.
        self.source_language = source_language
        self.target_language = target_language
        self.source_name = language_name(source_language)
        self.target_name = language_name(target_language)
        self._on_audio = on_audio
        self._on_speech_started = on_speech_started
        self.label = label

        self._ws = None
        self._reader: asyncio.Task | None = None
        self._closed = False
        self.ready = False

        # latency accounting (design doc §16)
        self._speech_stopped_at: float | None = None
        self.first_audio_ms: float | None = None
        self.responses = 0

        # Real billed usage, reported by the API on every response.done.
        self.usage = Usage(model=settings.openai_realtime_model)

    # --- lifecycle --------------------------------------------------------
    async def connect(self) -> None:
        url = f"{REALTIME_URL}?model={self._settings.openai_realtime_model}"
        headers = {"Authorization": f"Bearer {self._settings.openai_api_key}"}

        # websockets renamed this parameter in v14; support both.
        kwargs = {"max_size": None}
        if "additional_headers" in inspect.signature(websockets.connect).parameters:
            kwargs["additional_headers"] = headers
        else:  # pragma: no cover - older websockets
            kwargs["extra_headers"] = headers

        self._ws = await websockets.connect(url, **kwargs)
        await self._configure_session()
        self._reader = asyncio.create_task(self._read_loop())
        log.info(
            "realtime_connected",
            extra={"label": self.label, "direction": self._direction},
        )

    async def _configure_session(self) -> None:
        """GA session shape: nested audio.input / audio.output objects."""
        await self._send(
            {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "output_modalities": ["audio"],
                    "instructions": INSTRUCTIONS.format(
                        source=self.source_name, target=self.target_name
                    ),
                    "audio": {
                        "input": {
                            # Twilio's native codec — no conversion needed.
                            "format": {"type": "audio/pcmu"},
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.5,
                                "prefix_padding_ms": 300,
                                # Kept short: this delay is added to every
                                # translated utterance (design doc §16).
                                "silence_duration_ms": 400,
                                "create_response": True,
                                "interrupt_response": True,
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcmu"},
                            "voice": "alloy",
                        },
                    },
                },
            }
        )

    async def close(self) -> None:
        self._closed = True
        if self._reader:
            self._reader.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        log.info(
            "realtime_closed",
            extra={
                "label": self.label,
                "first_audio_ms": self.first_audio_ms,
                **self.usage.summary(),
            },
        )

    # --- sending ----------------------------------------------------------
    async def append_audio(self, payload_b64: str) -> None:
        """Feed one Twilio media frame to the model."""
        if not self.ready or self._closed:
            return
        await self._send({"type": "input_audio_buffer.append", "audio": payload_b64})

    async def cancel_response(self) -> None:
        """Stop the model mid-utterance (barge-in, design doc §8.2)."""
        if not self.ready or self._closed:
            return
        await self._send({"type": "response.cancel"})

    async def _send(self, message: dict) -> None:
        if self._ws is None or self._closed:
            return
        try:
            await self._ws.send(json.dumps(message))
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "realtime_send_failed",
                extra={"label": self.label, "reason": type(exc).__name__},
            )

    # --- receiving --------------------------------------------------------
    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                await self._handle(json.loads(raw))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not self._closed:
                log.warning(
                    "realtime_disconnected",
                    extra={"label": self.label, "reason": type(exc).__name__},
                )

    async def _handle(self, event: dict) -> None:
        etype = event.get("type", "")

        if etype in ("session.created", "session.updated"):
            self.ready = True
            return

        # The model is producing translated speech.
        if etype == "response.output_audio.delta":
            delta = event.get("delta")
            if not delta:
                return
            if self._speech_stopped_at is not None:
                self.first_audio_ms = round(
                    (time.monotonic() - self._speech_stopped_at) * 1000
                )
                self._speech_stopped_at = None
                log.info(
                    "translation_latency",
                    extra={"label": self.label, "first_audio_ms": self.first_audio_ms},
                )
            await self._on_audio(delta)
            return

        if etype == "input_audio_buffer.speech_started":
            if self._on_speech_started:
                await self._on_speech_started()
            return

        if etype == "input_audio_buffer.speech_stopped":
            self._speech_stopped_at = time.monotonic()
            return

        if etype == "response.done":
            self.responses += 1
            usage = (event.get("response") or {}).get("usage")
            if usage:
                one = self.usage.add(usage)
                log.info(
                    "translation_usage",
                    extra={"label": self.label, **one,
                           "session_usd": self.usage.usd},
                )
            return

        # Transcripts are logged only to verify the model is interpreting and
        # not conversing (design doc §6, known risk).
        if etype == "response.output_audio_transcript.done":
            text = (event.get("transcript") or "").strip()
            if text and log.isEnabledFor(logging.DEBUG):
                log.debug(
                    "translation_text",
                    extra={"label": self.label, "chars": len(text)},
                )
            return

        if etype == "error":
            err = event.get("error", {})
            # NB: "message" is a reserved LogRecord field — passing it in
            # `extra` raises KeyError and would kill this read loop.
            log.error(
                "realtime_error",
                extra={
                    "label": self.label,
                    "code": err.get("code"),
                    "detail": err.get("message"),
                },
            )

    @property
    def _direction(self) -> str:
        return f"{self.source_language}->{self.target_language}"


def language_name(code: str) -> str:
    from app.schemas.call import SUPPORTED_LANGUAGES

    return SUPPORTED_LANGUAGES.get(code, code)
