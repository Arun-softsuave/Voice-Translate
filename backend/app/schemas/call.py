"""Request/response schemas and input validation (design doc §10, §13)."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Languages the interpreter prompt is allowed to name. Validated against an
# allowlist so a request can never inject arbitrary text into a model prompt.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "ta": "Tamil",
    "hi": "Hindi",
    "en": "English",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi",
    "bn": "Bengali",
    "gu": "Gujarati",
}

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class TokenRequest(BaseModel):
    identity: str = Field(default="user1", max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class TokenResponse(BaseModel):
    token: str
    identity: str
    expires_in: int


class StartCallRequest(BaseModel):
    source_language: str
    target_language: str
    phone_number: str | None = None      # not required in demo mode

    @field_validator("source_language", "target_language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"unsupported language {value!r}; "
                f"expected one of {sorted(SUPPORTED_LANGUAGES)}"
            )
        return value

    @field_validator("phone_number")
    @classmethod
    def _e164(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.replace(" ", "").replace("-", "")
        if not _E164.match(value):
            raise ValueError("phone_number must be E.164, e.g. +919876543210")
        return value


class StartCallResponse(BaseModel):
    session_id: str
    status: str
    demo_mode: bool


class SessionResponse(BaseModel):
    session_id: str
    status: str
    uptime_s: float
    error: str | None = None
    participants: dict
