"""FastAPI application entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import call, health
from app.config import get_settings
from app.logging_config import configure_logging
from app.websocket.media_stream import media_stream_endpoint

settings = get_settings()
configure_logging(settings.log_level)
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
    log.info(
        "startup",
        extra={
            "demo_mode": settings.demo_mode,
            "echo_mode": settings.echo_mode,
            "media_stream_url": settings.media_stream_wss_url,
            "signature_validation": settings.validate_twilio_signature,
        },
    )
