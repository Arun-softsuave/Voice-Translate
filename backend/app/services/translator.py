"""The interface both translation backends satisfy.

Two implementations exist:

* `realtime_service.RealtimeTranslator` — gpt-realtime-2.1 on /v1/realtime.
  A conversational model held to interpreting by its prompt.
* `translate_service.TranslateSession` — gpt-realtime-translate on
  /v1/realtime/translations. Purpose-built, starts translating mid-sentence.

The important contract: **both speak Twilio's format on both sides** — base64
G.711 µ-law 8 kHz in, the same out. The translate endpoint only accepts 24 kHz
PCM16, but that conversion is its own private business.

That is not an arbitrary choice. `session_service.route_audio` measures
playback duration as `len(bytes) / 8`, which is true only for 8 kHz µ-law.
Letting any other format reach it would inflate `queued_ms` and break barge-in
accounting. Keeping the boundary at µ-law means nothing downstream has to know
which backend is running.

Languages are passed as ISO codes ("ta"), not display names. The translate
endpoint wants codes; the realtime prompt wants names and resolves them itself.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol, runtime_checkable

AudioCallback = Callable[[str], Awaitable[None]]


@runtime_checkable
class Translator(Protocol):
    """One direction of translation: source language in, target out."""

    label: str
    first_audio_ms: float | None

    async def connect(self) -> None:
        """Open the session. Raises if it cannot be established."""

    async def append_audio(self, payload_b64: str) -> None:
        """Feed one base64 µ-law frame. Must no-op before the session is ready."""

    async def close(self) -> None:
        """Shut down cleanly, without discarding audio still draining."""
