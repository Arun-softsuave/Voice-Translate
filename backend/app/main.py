"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import call, health
from app.config import BACKEND_DIR, get_settings
from app.logging_config import configure_logging
from app.websocket.media_stream import media_stream_endpoint

settings = get_settings()
log_path = configure_logging(settings.log_level, settings.log_file)
log = logging.getLogger(__name__)

app = FastAPI(
    title="Real-Time Voice Translation",
    version="0.1.0",
    description="Browser <-> PSTN two-way speech translation (R&D POC)",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],   # §13: not "*"
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(call.router)


@app.websocket("/ws/media-stream")
async def media_stream(ws: WebSocket) -> None:
    await media_stream_endpoint(ws)


@app.exception_handler(Exception)
async def unhandled(_request, exc: Exception) -> JSONResponse:
    """§13: never leak a stack trace to the caller."""
    log.exception("unhandled_error", extra={"kind": type(exc).__name__})
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR",
                           "message": "Something went wrong on our side."}},
    )


@app.on_event("startup")
async def startup() -> None:
    if settings.use_gemini_backend and settings.gemini_api_key:
        # Building the SDK client blocks for about a second; do it now, off the
        # loop, rather than during the first call.
        from app.services.gemini_service import client_for

        await asyncio.to_thread(client_for, settings.gemini_api_key)
    log.info(
        "startup",
        extra={
            "demo_mode": settings.demo_mode,
            "echo_mode": settings.echo_mode,
            "media_stream_url": settings.media_stream_wss_url,
            "signature_validation": settings.validate_twilio_signature,
            "log_file": str(log_path) if log_path else None,
            "translation_backend": settings.translation_backend,
            "translation_model": settings.translation_model,
            "translation_enabled": settings.translation_enabled,
        },
    )


# The built web page (`npm run build` in frontend/), served from this same
# origin so one public address carries everything: the page at /, the API at
# /api and /health, Twilio's audio at /ws. Mounted last, so every route above
# wins. In development the page comes from Vite on :5173 instead, and without
# a build this mount is simply skipped.
FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
