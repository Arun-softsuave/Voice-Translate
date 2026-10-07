"""Call control endpoints (design doc §10)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.logging_config import mask_phone
from app.models.session import CallStatus, ParticipantId
from app.schemas.call import (
    SessionResponse,
    StartCallRequest,
    StartCallResponse,
    TokenRequest,
    TokenResponse,
)
from app.services import twilio_service
from app.services.captions import view
from app.services.session_service import registry
from app.websocket.media_stream import prewarm_translator

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/call", tags=["call"])

TOKEN_TTL = 3600
# Twilio's final CallStatus values, and what we tell the user about each.
TERMINAL = ("completed", "busy", "failed", "no-answer", "canceled")
SIP_DECLINE = "603"
# A comment line this often keeps ngrok and other proxies from closing an
# idle caption stream during a long pause in the conversation.
CAPTION_PING_S = 15.0


def _settings() -> Settings:
    return get_settings()


@router.post("/token", response_model=TokenResponse)
def create_token(body: TokenRequest, settings: Settings = Depends(_settings)):
    """Short-lived Twilio Access Token for the browser's Voice SDK."""
    token = twilio_service.create_access_token(settings, body.identity, TOKEN_TTL)
    log.info("token_issued", extra={"identity": body.identity})
    return TokenResponse(token=token, identity=body.identity, expires_in=TOKEN_TTL)


@router.post("/start", response_model=StartCallResponse, status_code=201)
def start_call(body: StartCallRequest, settings: Settings = Depends(_settings)):
    """Create the session before any Twilio call exists.

    The session_id is minted here and travels into Twilio as a TwiML custom
    parameter, so both legs can identify themselves unambiguously (§5.5).
    """
    if not settings.demo_mode:
        if not body.phone_number:
            raise HTTPException(400, {"code": "PHONE_NUMBER_REQUIRED",
                                      "message": "A destination number is required."})
        if not body.phone_number.startswith(settings.allowed_destination_prefixes):
            # §13: a stray request must not be able to dial arbitrary countries.
            raise HTTPException(422, {"code": "UNSUPPORTED_DESTINATION",
                                      "message": "This destination is not enabled."})
        if not settings.twilio_phone_number:
            raise HTTPException(500, {"code": "NO_CALLER_ID",
                                      "message": "No outbound caller ID configured."})

    session = registry.create(
        source_language=body.source_language,
        target_language=body.target_language,
        participant_b_kind="browser" if settings.demo_mode else "pstn",
        phone_number=body.phone_number,
    )

    if not settings.demo_mode:
        try:
            client = twilio_service.TwilioClient(settings)
            call_sid = client.originate_pstn_leg(
                to=body.phone_number, session_id=session.session_id
            )
        except twilio_service.CallOriginationError as exc:
            registry.remove(session.session_id)
            log.warning("pstn_originate_failed",
                        extra={"to": mask_phone(body.phone_number),
                               "twilio_code": exc.code})
            raise HTTPException(502, {"code": f"TWILIO_{exc.code}",
                                      "message": exc.message}) from exc
        except Exception as exc:  # noqa: BLE001
            registry.remove(session.session_id)
            log.exception("pstn_originate_failed",
                          extra={"to": mask_phone(body.phone_number)})
            raise HTTPException(502, {"code": "TWILIO_ERROR",
                                      "message": "Could not place the call."}) from exc
        session.b.call_sid = call_sid
        session.status = CallStatus.RINGING
        log.info("pstn_leg_created",
                 extra={"session_id": session.session_id,
                        "to": mask_phone(body.phone_number)})

    return StartCallResponse(
        session_id=session.session_id,
        status=session.status.value,
        demo_mode=settings.demo_mode,
    )


@router.post("/twiml/{participant}")
async def twiml(participant: str, request: Request, settings: Settings = Depends(_settings)):
    """TwiML webhook for either leg. Returns `<Connect><Stream>`."""
    form = dict(await request.form())
    url = twilio_service.public_url_for(settings, request)
    if not twilio_service.validate_signature(
        settings, url, form, request.headers.get("X-Twilio-Signature")
    ):
        log.warning("twiml_bad_signature", extra={"participant": participant})
        raise HTTPException(403, {"code": "INVALID_SIGNATURE",
                                  "message": "Request rejected."})

    if participant not in ("A", "B"):
        raise HTTPException(404, {"code": "UNKNOWN_PARTICIPANT", "message": "Unknown leg."})

    session_id = request.query_params.get("session_id") or form.get("session_id")
    session = registry.get(session_id) if session_id else None
    if session is None:
        log.warning("twiml_unknown_session", extra={"session_id": session_id})
        raise HTTPException(404, {"code": "UNKNOWN_SESSION",
                                  "message": "This call is no longer available."})

    pid = ParticipantId(participant)
    call_sid = form.get("CallSid")
    if call_sid:
        session.participant(pid).call_sid = call_sid
    session.participant(pid).twiml_at = time.monotonic()

    # Open this leg's translator now, while Twilio is still setting up its
    # media stream (~0.8 s), so the first sentence does not wait for it.
    prewarm_translator(settings, session, pid)

    xml = twilio_service.build_stream_twiml(settings, session.session_id, pid)
    log.info("twiml_served",
             extra={"session_id": session.session_id, "participant": participant})
    return Response(content=xml, media_type="application/xml")


@router.post("/status")
async def status_callback(request: Request, settings: Settings = Depends(_settings)):
    """Twilio call status callback for both legs (design doc §12)."""
    form = dict(await request.form())
    if not twilio_service.validate_signature(
        settings,
        twilio_service.public_url_for(settings, request),
        form,
        request.headers.get("X-Twilio-Signature"),
    ):
        raise HTTPException(403, {"code": "INVALID_SIGNATURE", "message": "Rejected."})

    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    found = registry.find_by_call_sid(call_sid)
    if not found:
        return Response(status_code=204)

    session, pid = found
    sip_code = form.get("SipResponseCode") or None
    log.info("call_status",
             extra={"session_id": session.session_id, "participant": pid.value,
                    "call_status": call_status, "sip_code": sip_code,
                    "error_code": form.get("ErrorCode") or None})

    # Twilio is the only party that knows what the phone did, so it alone
    # moves the far side through ringing -> answered -> ended.
    if call_status in TERMINAL:
        reason = call_status
        if call_status == "busy" and sip_code == SIP_DECLINE:
            reason = "declined"
        _teardown(session, settings, reason=reason)
    elif call_status == "ringing":
        if session.status in (CallStatus.CONNECTING, CallStatus.RINGING):
            session.status = CallStatus.RINGING
    elif call_status == "in-progress":
        session.participant(pid).answered_at = time.monotonic()
        if session.status in (CallStatus.CONNECTING, CallStatus.RINGING):
            session.status = CallStatus.ANSWERED

    return Response(status_code=204)


@router.post("/end/{session_id}")
async def end_call(session_id: str, settings: Settings = Depends(_settings)):
    session = registry.get(session_id)
    if session is None:
        ended = registry.get_ended(session_id)
        if ended is not None:                       # already over: say how
            return {"session_id": session_id, "status": ended.status.value,
                    "end_reason": ended.end_reason}
        raise HTTPException(404, {"code": "UNKNOWN_SESSION", "message": "No such call."})
    # Hanging up before the other side picked up is a cancelled call.
    b = session.b
    answered = b.answered_at is not None or b.connected
    _teardown(session, settings, reason="user_ended" if answered else "canceled")
    return {"session_id": session_id, "status": session.status.value,
            "end_reason": session.end_reason}


@router.get("/{session_id}/captions")
async def captions(session_id: str, viewer: str = "A",
                   settings: Settings = Depends(_settings)):
    """Live captions for one participant, as Server-Sent Events.

    Sends a `snapshot` of every line so far, then one `caption` event per
    update. Each event carries the line's full text, so the client replaces
    by id. Ends with an `end` event when the call does.
    """
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(404, {"code": "UNKNOWN_SESSION", "message": "No such call."})
    if viewer not in ("A", "B"):
        raise HTTPException(422, {"code": "UNKNOWN_PARTICIPANT", "message": "Unknown leg."})

    pid = ParticipantId(viewer)
    echo = settings.echo_mode
    lines, queue = session.captions.subscribe()

    def event(name: str, data) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def stream():
        try:
            shown = [v for v in (view(l, pid, session, echo_mode=echo) for l in lines) if v]
            yield event("snapshot", shown)
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), CAPTION_PING_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if line is None:
                    yield event("end", {})
                    return
                shown = view(line, pid, session, echo_mode=echo)
                if shown:
                    yield event("caption", shown)
        finally:
            session.captions.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str):
    # Ended calls stay readable for a while so the browser can learn why.
    session = registry.get(session_id) or registry.get_ended(session_id)
    if session is None:
        raise HTTPException(404, {"code": "UNKNOWN_SESSION", "message": "No such call."})
    return SessionResponse(**session.snapshot())


def _teardown(session, settings: Settings, *, reason: str) -> None:
    """Hang up the peer leg, then drop the session (design doc §18)."""
    session.status = CallStatus.ENDING
    if not settings.demo_mode:
        client = twilio_service.TwilioClient(settings)
        for participant in (session.a, session.b):
            if participant.call_sid:
                client.hangup(participant.call_sid)
    log.info("call_ended", extra={"session_id": session.session_id, "reason": reason})
    registry.end(session, reason)
