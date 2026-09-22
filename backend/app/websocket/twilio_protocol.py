"""Twilio Media Streams wire protocol.

Design doc §11. Every message Twilio accepts must carry the `streamSid` of the
stream it is destined for — this is the mechanism that makes mis-routing
impossible as long as the SID comes from the session object.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# --- inbound (Twilio -> us) -------------------------------------------------
EVENT_CONNECTED = "connected"
EVENT_START = "start"
EVENT_MEDIA = "media"
EVENT_STOP = "stop"
EVENT_MARK = "mark"
EVENT_DTMF = "dtmf"


def build_media(stream_sid: str, payload_b64: str) -> dict[str, Any]:
    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": payload_b64},
    }


def build_mark(stream_sid: str, name: str) -> dict[str, Any]:
    return {"event": "mark", "streamSid": stream_sid, "mark": {"name": name}}


def build_clear(stream_sid: str) -> dict[str, Any]:
    """Discard everything Twilio has buffered but not yet played (barge-in)."""
    return {"event": "clear", "streamSid": stream_sid}


async def send(ws: Any, message: dict[str, Any]) -> bool:
    """Send one message, swallowing a closed socket.

    Returns False if the socket was already gone. Callers treat that as
    'this leg is finished' rather than as an error worth raising.
    """
    try:
        await ws.send_json(message)
        return True
    except Exception as exc:  # noqa: BLE001 - socket teardown races are normal
        log.warning("twilio_send_failed", extra={"reason": type(exc).__name__})
        return False
