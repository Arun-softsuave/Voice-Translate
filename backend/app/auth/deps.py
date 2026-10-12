"""Who is asking, and may they: FastAPI dependencies for browser routes.

Twilio's routes (TwiML, status callbacks, the media WebSocket) never use
these. Twilio cannot sign in; those routes check Twilio's signature instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request

from app.auth import sessions
from app.config import Settings, get_settings
from app.db.engine import session_scope
from app.db.models import ACTIVE, ADMIN

LOCAL = ("127.0.0.1", "::1", "localhost")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    name: str
    email: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role}


def _settings() -> Settings:
    return get_settings()


def client_ip(request: Request) -> str | None:
    """The browser's address. Behind the tunnel every request comes from
    cloudflared on this machine, which passes the real one along."""
    host = request.client.host if request.client else None
    if host in LOCAL:
        return request.headers.get("cf-connecting-ip") or host
    return host


def current_user(request: Request) -> CurrentUser:
    """The signed-in, active user, read fresh from the database every time,
    so a demotion or a disable takes effect on the very next request."""
    with session_scope() as db:
        user = sessions.lookup(db, request.cookies.get(sessions.COOKIE))
        found = (CurrentUser(user.id, user.name, user.email, user.role)
                 if user is not None and user.status == ACTIVE else None)
    if found is None:
        raise HTTPException(401, {"code": "AUTH_REQUIRED",
                                  "message": "Please sign in to continue."})
    return found


def require_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(403, {"code": "FORBIDDEN",
                                  "message": "This page is for administrators."})
    return user


def _origin(url: str) -> str | None:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else None


def same_origin(request: Request, settings: Settings = Depends(_settings)) -> None:
    """Refuse state-changing requests that another website's page made.

    The SameSite=Lax cookie already stops most of these; this is the second
    lock. Browsers always label their requests (Sec-Fetch-Site, Origin), so a
    request carrying neither header did not come from a web page, and a
    cookie cannot ride along on it uninvited.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site == "same-origin":
        return
    origin = request.headers.get("origin")
    if origin is None and fetch_site is None:
        return
    host = request.headers.get("host", "")
    allowed = {
        _origin(settings.backend_public_url), _origin(settings.frontend_url),
        f"http://{host}".lower(), f"https://{host}".lower(),
    }
    if origin and origin.lower() in allowed:
        return
    raise HTTPException(403, {"code": "CROSS_SITE",
                              "message": "Request rejected."})
