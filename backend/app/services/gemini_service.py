"""Google `gemini-3.5-live-translate` backend.

A purpose-built streaming translation model on the Gemini Live API. Like
`gpt-realtime-translate` it translates while the speaker is still talking, but
it documents 70+ languages as BOTH source and target — including Tamil, Telugu,
Kannada, Malayalam, Marathi, Bengali and Gujarati, none of which OpenAI's
translate model lists as targets.

The contract, from ai.google.dev/gemini-api/docs/live-api/live-translate:

1. **Audio in is PCM16 16 kHz, audio out is PCM16 24 kHz.** Neither is
   negotiable, so this class converts both ways and presents µ-law 8 kHz to the
   rest of the app, exactly as the other backends do (see `translator.py`).
2. **The source language is detected, never set.** Only the target is
   configured, through `translation_config.target_language_code`.
3. **Send ~100 ms chunks.** Twilio delivers 20 ms frames, so they are batched.
4. **Sessions are not forever.** The server announces the end of a session
   with `go_away`, and a connection can also simply drop. Either way a fresh
   session is opened with the same configuration. A translator needs no
   memory of earlier sentences, so nothing is lost but the audio in flight.

Each person's chosen language is sent as a transcription hint (`language_codes`):
the speaker's on the "what was said" side, the listener's on the translation side.
Without it Gemini guesses from 8 kHz phone audio, and on a real call it wrote a
Tamil speaker's words out in Vietnamese. The model has no source-language
setting for the translation itself; the hint is what it does accept. Verified
live: the server accepts it for gemini-3.5-live-translate. If a server ever
refuses it, the session reconnects once without hints rather than lose the
call's translation.

`echo_target_language` is left off: if someone speaks the listener's language
already, the model stays silent rather than repeating it.

There is no cancel. Barge-in is handled upstream, in the media stream layer,
for every backend alike.
"""

from __future__ import annotations

import asyncio
import logging
import time
from functools import lru_cache
from typing import Awaitable, Callable

from google import genai
from google.genai import types

from app.config import Settings
from app.services import languages
from app.services.pricing import Usage
from app.utils import resample
from app.utils.audio import b64_decode, b64_encode

log = logging.getLogger(__name__)

INPUT_MIME = f"audio/pcm;rate={resample.GEMINI_IN_RATE}"

# Google recommends 100 ms chunks. Five Twilio frames.
CHUNK_MS = 100
CHUNK_BYTES = resample.GEMINI_IN_RATE * 2 * CHUNK_MS // 1000

# A session that dies sooner than this after opening is treated as a failure
# rather than a normal rotation, so a config the server keeps rejecting cannot
# turn into an endless reconnect loop.
MIN_HEALTHY_S = 10.0
MAX_FAILED_RECONNECTS = 3
RECONNECT_BACKOFF_S = 0.5
RTT_TIMEOUT_S = 2.0

TranscriptCallback = Callable[[str, str], Awaitable[None]]


@lru_cache
def client_for(api_key: str) -> genai.Client:
    """One SDK client per key, shared by every session.

    Building a client costs about a second of synchronous work (it sets up
    HTTP and TLS machinery). Done per session on the event loop, that freezes
    audio on EVERY live call while a leg connects. Callers on the loop go
    through `asyncio.to_thread`, and startup warms it so no call pays at all.
    """
    return genai.Client(api_key=api_key)


def _hint(code: str | None) -> str | None:
    """A language we know, in Gemini's BCP-47 form; None for "auto" or unknown."""
    if not code or code not in languages.ALL:
        return None
    return languages.gemini_code(code)


def _transcription(code: str | None) -> types.AudioTranscriptionConfig:
    if code is None:
        return types.AudioTranscriptionConfig()
    return types.AudioTranscriptionConfig(language_codes=[code])


class _GoAway(Exception):
    """The server asked us to move to a new session."""


class GeminiTranslateSession:
    """One direction of translation on the Gemini Live API."""

    def __init__(
        self,
        settings: Settings,
        *,
        source_language: str,          # ISO code; auto-detected, kept for logs
        target_language: str,          # ISO code, e.g. "ta"
        on_audio: Callable[[str], Awaitable[None]],
        on_speech_started: Callable[[], Awaitable[None]] | None = None,
        on_transcript: TranscriptCallback | None = None,
        label: str = "",
    ) -> None:
        self._settings = settings
        self.source_language = source_language
        self.target_language = target_language
        self._on_audio = on_audio
        self._on_speech_started = on_speech_started   # unused; kept for parity
        # ("in" | "out", text). Used by the probe script only; the app never
        # keeps what was said.
        self._on_transcript = on_transcript
        self.label = label

        self._client: genai.Client | None = None
        self._cm = None                 # the SDK's connect() context manager
        self._session = None
        self._opened_at = 0.0
        self._reader: asyncio.Task | None = None
        self._closed = False
        self.ready = False

        # Twilio speaks µ-law 8k; Gemini takes PCM16 16k and returns 24k.
        self._to_model = resample.TwilioToGemini()
        self._to_twilio = resample.OpenAIToTwilio()
        self._pending = bytearray()

        self._heard_at: float | None = None
        self.first_audio_ms: float | None = None
        self.responses = 0
        self.reconnects = 0
        # Send the chosen languages as transcription hints; turned off for the
        # rest of the session if the server refuses them.
        self._hints = True
        self.rtt_ms: int | None = None   # network round trip to Google
        self._rtt_task: asyncio.Task | None = None

        self.usage = Usage(model=settings.gemini_translate_model)
        self._audio_in_ms = 0.0
        self._audio_out_ms = 0.0

    # --- lifecycle --------------------------------------------------------
    @property
    def source_hint(self) -> str | None:
        """Gemini's code for the speaker's language, if we know it."""
        return _hint(self.source_language) if self._hints else None

    @property
    def target_hint(self) -> str | None:
        return _hint(self.target_language) if self._hints else None

    def _config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            input_audio_transcription=_transcription(self.source_hint),
            output_audio_transcription=_transcription(self.target_hint),
            translation_config=types.TranslationConfig(
                target_language_code=languages.gemini_code(self.target_language),
                echo_target_language=False,
            ),
        )

    async def connect(self) -> None:
        self._client = await asyncio.to_thread(client_for, self._settings.gemini_api_key)
        try:
            await self._open()
        except Exception as exc:  # noqa: BLE001
            if not (self._hints and (self.source_hint or self.target_hint)):
                raise
            # Never lose a call's translation over a hint: drop it and retry.
            log.warning("gemini_language_hint_rejected",
                        extra={"label": self.label, "source_hint": self.source_hint,
                               "target_hint": self.target_hint,
                               "reason": type(exc).__name__, "detail": str(exc)[:200]})
            self._hints = False
            await self._open()
        self._reader = asyncio.create_task(self._read_loop())
        ws = getattr(self._session, "_ws", None)
        if ws is not None:
            self._rtt_task = asyncio.create_task(self._measure_rtt(ws))
        log.info(
            "gemini_connected",
            extra={"label": self.label,
                   "model": self._settings.gemini_translate_model,
                   "direction": f"{self.source_language}->{self.target_language}",
                   "source_hint": self.source_hint, "target_hint": self.target_hint},
        )

    async def _measure_rtt(self, ws) -> None:
        """One WebSocket ping, for the latency logs. Never blocks the call."""
        try:
            started = time.monotonic()
            pong = await ws.ping()
            await asyncio.wait_for(pong, RTT_TIMEOUT_S)
            self.rtt_ms = round((time.monotonic() - started) * 1000)
        except Exception:  # noqa: BLE001 - a missing figure, not a failure
            self.rtt_ms = None

    async def _open(self) -> None:
        """Open one Live session. Raises if the server refuses the setup."""
        cm = self._client.aio.live.connect(
            model=self._settings.gemini_translate_model, config=self._config()
        )
        # Entered by hand rather than with `async with`: the session has to
        # outlive this call, and is exited in _shut_session.
        self._session = await cm.__aenter__()
        self._cm = cm
        self._opened_at = time.monotonic()
        self._pending.clear()
        self.ready = True

    async def _shut_session(self) -> None:
        self.ready = False
        cm, self._cm, self._session = self._cm, None, None
        if cm is None:
            return
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 - the socket may already be gone
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        for task in (self._reader, self._rtt_task):
            if task:
                task.cancel()
        await asyncio.gather(*(t for t in (self._reader, self._rtt_task) if t),
                             return_exceptions=True)
        await self._shut_session()

        self.usage.set_audio_minutes(self._audio_in_ms / 60000.0,
                                     self._audio_out_ms / 60000.0)
        log.info(
            "gemini_closed",
            extra={"label": self.label,
                   "first_audio_ms": self.first_audio_ms,
                   "reconnects": self.reconnects,
                   "audio_in_s": round(self._audio_in_ms / 1000, 1),
                   "audio_out_s": round(self._audio_out_ms / 1000, 1),
                   **self.usage.summary()},
        )

    # --- sending ----------------------------------------------------------
    async def append_audio(self, payload_b64: str) -> None:
        """Feed one base64 µ-law frame; send once 100 ms has built up."""
        if not self.ready or self._closed:
            return

        ulaw = b64_decode(payload_b64)
        self._audio_in_ms += resample.ulaw_ms(ulaw)
        self._pending += self._to_model(ulaw)
        if len(self._pending) < CHUNK_BYTES:
            return

        chunk = bytes(self._pending)
        self._pending.clear()
        try:
            await self._session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=INPUT_MIME)
            )
        except Exception as exc:  # noqa: BLE001
            # The read loop notices the dead socket and reconnects.
            log.warning("gemini_send_failed",
                        extra={"label": self.label, "reason": type(exc).__name__})

    async def cancel_response(self) -> None:
        """No-op: there is no cancel. Barge-in is handled upstream."""
        return

    # --- receiving --------------------------------------------------------
    async def _read_loop(self) -> None:
        failures = 0
        while not self._closed:
            if self._session is not None:
                try:
                    # receive() ends at every turn boundary; keep asking.
                    while not self._closed:
                        async for message in self._session.receive():
                            await self._handle(message)
                except asyncio.CancelledError:
                    raise
                except _GoAway:
                    log.info("gemini_go_away", extra={"label": self.label})
                except Exception as exc:  # noqa: BLE001
                    if self._closed:
                        return
                    log.warning("gemini_disconnected",
                                extra={"label": self.label,
                                       "reason": type(exc).__name__,
                                       "detail": str(exc)[:200]})
                lived = time.monotonic() - self._opened_at
                failures = failures + 1 if lived < MIN_HEALTHY_S else 0
            else:
                failures += 1          # the last reconnect did not open

            if failures > MAX_FAILED_RECONNECTS:
                log.error("gemini_gave_up",
                          extra={"label": self.label, "failures": failures - 1})
                await self._shut_session()
                return
            await self._reconnect(failures)

    async def _reconnect(self, failures: int) -> None:
        """Replace the session. Leaves `_session` None if it cannot."""
        await self._shut_session()
        if failures:
            await asyncio.sleep(RECONNECT_BACKOFF_S * failures)
        if self._closed:
            return
        try:
            await self._open()
        except Exception as exc:  # noqa: BLE001
            log.warning("gemini_reconnect_failed",
                        extra={"label": self.label, "reason": type(exc).__name__,
                               "detail": str(exc)[:200]})
            return
        self.reconnects += 1
        log.info("gemini_reconnected",
                 extra={"label": self.label, "reconnects": self.reconnects})

    async def _handle(self, message: types.LiveServerMessage) -> None:
        if message.go_away is not None:
            raise _GoAway()

        content = message.server_content
        if content is None:
            return

        heard = content.input_transcription
        if heard is not None and heard.text:
            # First sign the model has heard speech; used only for latency.
            if self._heard_at is None:
                self._heard_at = time.monotonic()
            await self._transcript("in", heard.text)

        turn = content.model_turn
        if turn is not None and turn.parts:
            for part in turn.parts:
                blob = part.inline_data
                if blob is not None and blob.data:
                    await self._deliver(blob.data)

        spoke = content.output_transcription
        if spoke is not None and spoke.text:
            self.responses += 1
            await self._transcript("out", spoke.text)

    async def _deliver(self, pcm24k: bytes) -> None:
        if self.first_audio_ms is None and self._heard_at is not None:
            self.first_audio_ms = round((time.monotonic() - self._heard_at) * 1000)
            log.info("translation_latency",
                     extra={"label": self.label, "first_audio_ms": self.first_audio_ms})

        self._audio_out_ms += resample.pcm24k_ms(pcm24k)
        ulaw = self._to_twilio(pcm24k)
        if ulaw:
            await self._on_audio(b64_encode(ulaw))

    async def _transcript(self, direction: str, text: str) -> None:
        if self._on_transcript is not None:
            await self._on_transcript(direction, text)

