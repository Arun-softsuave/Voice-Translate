"""Request/response schemas and input validation (design doc §10, §13)."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.config import get_settings
from app.services.languages import ALL, name_of, source_codes, target_codes

# Re-exported so existing imports keep working. The authoritative definition,
# including which codes are valid in which direction, lives in
# app/services/languages.py.
SUPPORTED_LANGUAGES: dict[str, str] = {c: l.label for c, l in ALL.items()}

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class TokenRequest(BaseModel):
    identity: str = Field(default="user1", max_length=64, pattern=r"^[A-Za-z0-9_\-]+$")


class TokenResponse(BaseModel):
    token: str
    identity: str
    expires_in: int


class StartCallRequest(BaseModel):
    """Source and target are validated against DIFFERENT sets.

    The translate backend accepts far more source languages than it can
    produce as targets, so a single shared allowlist would let a caller ask for
    a target the model cannot speak — which only surfaces mid-call.
    """

    source_language: str
    target_language: str
    phone_number: str | None = None      # not required in demo mode

    @field_validator("source_language", "target_language")
    @classmethod
    def _normalise(cls, value: str) -> str:
        return value.lower().strip()

    @model_validator(mode="after")
    def _check_language_pair(self):
        translate = get_settings().use_translate_backend

        if self.source_language not in source_codes(translate):
            raise ValueError(
                f"{self.source_language!r} is not available as a source language."
            )

        if self.target_language not in target_codes(translate):
            name = name_of(self.target_language)
            raise ValueError(
                f"{name} is not available as a target language on the current "
                f"translation model."
            )

        # Translating a language into itself is always a mistake, and the API
        # accepted it until now — only the UI blocked it.
        if self.source_language == self.target_language:
            raise ValueError("Source and target languages must be different.")

        return self

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
