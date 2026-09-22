"""Session and participant model.

Design doc §9. The session object is the only place that knows which stream
belongs to whom, which is what makes the routing rule in §8 enforceable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CallStatus(str, Enum):
    CONNECTING = "CONNECTING"
    RINGING = "RINGING"
    CONNECTED = "CONNECTED"
    TRANSLATING = "TRANSLATING"
    RECONNECTING = "RECONNECTING"
    ENDING = "ENDING"
    ENDED = "ENDED"
    ERROR = "ERROR"


class ParticipantId(str, Enum):
    A = "A"      # browser / React user
    B = "B"      # PSTN mobile (or second browser in demo mode)

    @property
    def peer(self) -> "ParticipantId":
        return ParticipantId.B if self is ParticipantId.A else ParticipantId.A


@dataclass
class Participant:
    participant_id: ParticipantId
    language: str                       # BCP-47-ish code, e.g. "ta"
    kind: str                           # "browser" | "pstn"
    call_sid: str | None = None
    stream_sid: str | None = None
    ws: Any = None                      # fastapi.WebSocket once the stream binds
    phone_number: str | None = None     # masked before it ever reaches a log

    frames_in: int = 0
    frames_out: int = 0
    dropped_no_peer: int = 0
    played_ms: float = 0.0              # advanced by Twilio `mark` acks
    queued_ms: float = 0.0              # audio handed to Twilio, not yet acked

    @property
    def connected(self) -> bool:
        return self.ws is not None and self.stream_sid is not None


@dataclass
class TranslationSession:
    session_id: str
    a: Participant
    b: Participant
    status: CallStatus = CallStatus.CONNECTING
    created_at: float = field(default_factory=time.monotonic)
    error: str | None = None

    def participant(self, pid: ParticipantId) -> Participant:
        return self.a if pid is ParticipantId.A else self.b

    def peer_of(self, pid: ParticipantId) -> Participant:
        return self.participant(pid.peer)

    @property
    def both_connected(self) -> bool:
        return self.a.connected and self.b.connected

    def snapshot(self) -> dict:
        """Safe for the UI and for logs — no phone numbers, no audio."""
        return {
            "session_id": self.session_id,
            "status": self.status.value,
            "uptime_s": round(time.monotonic() - self.created_at, 1),
            "error": self.error,
            "participants": {
                p.participant_id.value: {
                    "kind": p.kind,
                    "language": p.language,
                    "connected": p.connected,
                    "frames_in": p.frames_in,
                    "frames_out": p.frames_out,
                    "dropped_no_peer": p.dropped_no_peer,
                    "played_ms": round(p.played_ms),
                }
                for p in (self.a, self.b)
            },
        }
