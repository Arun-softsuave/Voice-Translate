"""Signing in, signing out, and asking for access.

There is no open sign-up: a request creates a *pending* account that an admin
must approve. Answers never reveal whether an email is known, except to
someone who has just proved they hold its password.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth import passwords, ratelimit, sessions
from app.auth.deps import CurrentUser, client_ip, current_user, same_origin
from app.config import Settings, get_settings
from app.db import repo
from app.db.engine import session_scope
from app.db.models import ACTIVE, DISABLED, PENDING, REJECTED, USER, User, utcnow
from app.schemas.auth import AccessRequest, LoginRequest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

REQUEST_RECEIVED = ("Request received. You can sign in once an administrator "
                    "approves it.")
BAD_LOGIN = {"code": "INVALID_CREDENTIALS", "message": "Incorrect email or password."}


def _settings() -> Settings:
    return get_settings()


def _too_many(wait_s: float) -> HTTPException:
    seconds = max(1, int(wait_s + 0.999))
    return HTTPException(
        429, {"code": "TOO_MANY_ATTEMPTS",
              "message": f"Too many attempts. Try again in {seconds} s."},
        headers={"Retry-After": str(seconds)})


@router.post("/request-access", status_code=202, dependencies=[Depends(same_origin)])
def request_access(body: AccessRequest, request: Request,
                   settings: Settings = Depends(_settings)):
    wait = ratelimit.access_requests.hit(f"ip:{client_ip(request)}")
    if wait:
        raise _too_many(wait)
    problem = passwords.problem_with(body.password, email=body.email, name=body.name)
    if problem:
        raise HTTPException(422, {"code": "WEAK_PASSWORD", "message": problem})

    # Hash first, whatever happens next, so every answer takes the same time.
    password_hash = passwords.hash_password(body.password)
    with session_scope() as db:
        user = repo.user_by_email(db, body.email)
        if user is None:
            if body.email != settings.admin_email:  # the owner is set up on the host
                db.add(User(name=body.name, email=body.email,
                            password_hash=password_hash, role=USER, status=PENDING))
                log.info("access_requested", extra={"outcome": "new"})
        elif user.status == REJECTED:
            # Asking again after a no: back in the queue, with these details.
            user.name, user.password_hash = body.name, password_hash
            user.status, user.created_at = PENDING, utcnow()
            log.info("access_requested", extra={"outcome": "again", "user_id": user.id})
    return {"message": REQUEST_RECEIVED}


@router.post("/login", dependencies=[Depends(same_origin)])
def login(body: LoginRequest, request: Request, response: Response,
          settings: Settings = Depends(_settings)):
    ip = client_ip(request)
    keys = (f"email:{body.email}", f"ip:{ip}")
    wait = max(ratelimit.logins.wait_s(k) for k in keys)
    if wait:
        raise _too_many(wait)

    with session_scope() as db:
        user = repo.user_by_email(db, body.email)
        if user is None:
            passwords.burn_time(body.password)
            ok = False
        else:
            ok = passwords.verify_password(user.password_hash, body.password)
        # A rejected request looks exactly like an unknown email.
        if not ok or user.status == REJECTED:
            for key in keys:
                ratelimit.logins.failed(key)
            log.info("login_failed", extra={"user_id": user.id if user else None})
            raise HTTPException(401, BAD_LOGIN)

        for key in keys:
            ratelimit.logins.succeeded(key)
        # The password was right, so saying why they cannot come in is safe.
        if user.status == PENDING:
            raise HTTPException(403, {"code": "PENDING_APPROVAL",
                                      "message": "Your request is waiting for an "
                                                 "administrator's approval."})
        if user.status == DISABLED:
            raise HTTPException(403, {"code": "ACCOUNT_DISABLED",
                                      "message": "Your access has been turned off. "
                                                 "Contact the administrator."})
        assert user.status == ACTIVE
        if passwords.needs_rehash(user.password_hash):
            user.password_hash = passwords.hash_password(body.password)
        # A fresh token at every sign-in; any old one on this browser is dropped.
        sessions.revoke(db, request.cookies.get(sessions.COOKIE))
        token = sessions.create(db, user, ip=ip,
                                user_agent=request.headers.get("user-agent"))
        me = CurrentUser(user.id, user.name, user.email, user.role)

    response.set_cookie(
        sessions.COOKIE, token, max_age=int(sessions.ABSOLUTE.total_seconds()),
        httponly=True, secure=settings.session_cookie_secure, samesite="lax", path="/")
    log.info("login", extra={"user_id": me.id, "role": me.role})
    return {"user": me.public()}


@router.post("/logout", status_code=204, dependencies=[Depends(same_origin)])
def logout(request: Request, settings: Settings = Depends(_settings)):
    with session_scope() as db:
        sessions.revoke(db, request.cookies.get(sessions.COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(sessions.COOKIE, path="/", httponly=True,
                           secure=settings.session_cookie_secure, samesite="lax")
    return response


@router.get("/me")
def me(user: CurrentUser = Depends(current_user)):
    return {"user": user.public()}
