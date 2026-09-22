"""Twilio access tokens, TwiML and webhook authentication.

Design doc §5 and §13.
"""

from __future__ import annotations

import logging

from twilio.base.exceptions import TwilioRestException
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.config import Settings
from app.models.session import ParticipantId

log = logging.getLogger(__name__)


def create_access_token(settings: Settings, identity: str, ttl: int = 3600) -> str:
    """Short-lived token for the browser. The auth token never leaves the server."""
    token = AccessToken(
        settings.twilio_account_sid,
        settings.twilio_api_key,
        settings.twilio_api_secret,
        identity=identity,
        ttl=ttl,
    )
    token.add_grant(
        VoiceGrant(
            outgoing_application_sid=settings.twilio_twiml_app_sid,
            incoming_allow=True,
        )
    )
    jwt = token.to_jwt()
    return jwt.decode() if isinstance(jwt, bytes) else jwt


def build_stream_twiml(
    settings: Settings, session_id: str, participant: ParticipantId
) -> str:
    """`<Connect><Stream>` — the only TwiML that both receives and injects audio.

    Custom parameters are how a WebSocket connection identifies itself when it
    arrives; they come back to us in the stream's `start` message.
    """
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=settings.media_stream_wss_url)
    stream.parameter(name="session_id", value=session_id)
    stream.parameter(name="participant", value=participant.value)
    response.append(connect)
    return str(response)


def public_url_for(settings: Settings, request) -> str:
    """Rebuild the URL exactly as Twilio saw it.

    Twilio signs the public https:// URL it requested. Behind a tunnel or a
    proxy our app sees http:// and an internal host, so `str(request.url)`
    would not match and every webhook would fail validation. Deriving the URL
    from BACKEND_PUBLIC_URL is deterministic and does not depend on the proxy
    forwarding headers correctly.
    """
    url = f"{settings.backend_public_url}{request.url.path}"
    query = request.url.query
    return f"{url}?{query}" if query else url


def validate_signature(
    settings: Settings, url: str, form: dict[str, str], signature: str | None
) -> bool:
    if not settings.validate_twilio_signature:
        return True
    if not signature:
        return False
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(url, form, signature)


# Twilio error codes worth explaining rather than showing as "call failed".
# Safe to surface: these describe OUR configuration, not account internals.
CALL_ERROR_HINTS: dict[int, str] = {
    21215: "Calling India is not enabled on this Twilio account. "
           "Enable it in Console -> Voice -> Settings -> Geo Permissions.",
    21210: "The caller ID is not a number owned by this account.",
    21211: "That destination number is not valid.",
    21212: "The caller ID is not a valid, voice-capable Twilio number.",
    21214: "That destination number is not reachable.",
    21219: "Trial account: this destination has not been verified.",
    21606: "The caller ID cannot place outbound calls. Use your Twilio number.",
    21608: "Trial account: upgrade to reach unverified numbers.",
    13224: "Twilio will not connect a call to that destination.",
}


class CallOriginationError(Exception):
    """Carries a message that is safe to show the user."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TwilioClient:
    """Thin wrapper so call origination and hangup live in one place."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Client(
            settings.twilio_api_key,
            settings.twilio_api_secret,
            settings.twilio_account_sid,
        )

    def originate_pstn_leg(self, *, to: str, session_id: str) -> str:
        """Dial User 2. `from` must be a non-Indian number (design doc §14)."""
        try:
            return self._create_call(to, session_id)
        except TwilioRestException as exc:
            hint = CALL_ERROR_HINTS.get(exc.code)
            log.error(
                "pstn_originate_rejected",
                extra={"twilio_code": exc.code, "twilio_status": exc.status,
                       "twilio_message": exc.msg},
            )
            raise CallOriginationError(
                exc.code, hint or f"Twilio rejected the call (error {exc.code})."
            ) from exc

    def _create_call(self, to: str, session_id: str) -> str:
        s = self._settings
        call = self._client.calls.create(
            to=to,
            from_=s.twilio_phone_number,
            url=f"{s.backend_public_url}/api/call/twiml/B?session_id={session_id}",
            status_callback=f"{s.backend_public_url}/api/call/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST",
        )
        return call.sid

    def hangup(self, call_sid: str) -> None:
        try:
            self._client.calls(call_sid).update(status="completed")
        except Exception as exc:  # noqa: BLE001 - already-ended calls are fine
            log.warning(
                "hangup_failed",
                extra={"call_sid": call_sid, "reason": type(exc).__name__},
            )
