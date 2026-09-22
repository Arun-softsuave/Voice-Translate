"""Configuration loaded from the environment.

Secrets never have defaults. If a secret is missing the app fails loudly at
startup rather than at the first API call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    # A left-over placeholder from .env.example is worse than a missing value:
    # it starts the server and then fails deep inside a live call.
    if "xxxx" in value.lower():
        raise RuntimeError(
            f"{name} still contains the placeholder from .env.example "
            f"({value[:6]}...). Replace it with the real value."
        )
    return value


def _opt(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _bool(name: str, default: bool = False) -> bool:
    return _opt(name, str(default)).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # --- Twilio -----------------------------------------------------------
    twilio_account_sid: str
    twilio_auth_token: str          # webhook signature validation only
    twilio_api_key: str             # access tokens
    twilio_api_secret: str
    twilio_twiml_app_sid: str
    twilio_phone_number: str        # non-Indian caller ID for the PSTN leg

    # --- OpenAI -----------------------------------------------------------
    openai_api_key: str
    openai_realtime_model: str

    # --- URLs -------------------------------------------------------------
    backend_public_url: str         # https://<tunnel>  (no trailing slash)
    frontend_url: str

    # --- Behaviour --------------------------------------------------------
    demo_mode: bool
    echo_mode: bool                 # milestone 2: route audio back to sender
    validate_twilio_signature: bool
    log_level: str

    allowed_destination_prefixes: tuple[str, ...] = field(default=("+91",))

    @property
    def translation_enabled(self) -> bool:
        """No OpenAI key means pass-through audio, not a crash.

        This keeps the echo test usable before phase 7 is configured.
        """
        return bool(self.openai_api_key)

    @property
    def media_stream_wss_url(self) -> str:
        host = urlparse(self.backend_public_url).netloc
        if not host:
            raise RuntimeError(
                f"BACKEND_PUBLIC_URL must be a full URL, got: {self.backend_public_url!r}"
            )
        return f"wss://{host}/ws/media-stream"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        twilio_account_sid=_req("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=_req("TWILIO_AUTH_TOKEN"),
        twilio_api_key=_req("TWILIO_API_KEY"),
        twilio_api_secret=_req("TWILIO_API_SECRET"),
        twilio_twiml_app_sid=_req("TWILIO_TWIML_APP_SID"),
        twilio_phone_number=_opt("TWILIO_PHONE_NUMBER"),
        openai_api_key=_opt("OPENAI_API_KEY"),          # not needed until phase 7
        openai_realtime_model=_opt("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1"),
        backend_public_url=_req("BACKEND_PUBLIC_URL").rstrip("/"),
        frontend_url=_opt("FRONTEND_URL", "http://localhost:5173").rstrip("/"),
        demo_mode=_bool("DEMO_MODE", True),
        echo_mode=_bool("ECHO_MODE", False),
        validate_twilio_signature=_bool("VALIDATE_TWILIO_SIGNATURE", True),
        log_level=_opt("LOG_LEVEL", "INFO").upper(),
    )
