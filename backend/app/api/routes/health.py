"""Health and diagnostics (design doc §34)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.services.session_service import registry

router = APIRouter(tags=["health"])


def _settings() -> Settings:
    return get_settings()


@router.get("/health")
def health(settings: Settings = Depends(_settings)):
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "echo_mode": settings.echo_mode,
        "media_stream_url": settings.media_stream_wss_url,
        "active_sessions": len(list(registry.all())),
    }


@router.get("/api/diagnostics/sessions")
def sessions(settings: Settings = Depends(_settings)):
    """Live session view. Contains no phone numbers, audio or credentials."""
    return {"sessions": [s.snapshot() for s in registry.all()]}
