"""The admin page: access requests, members, and every call.

Every route here requires an active admin, checked against the database on
each request. Guards keep the app from locking itself out: nobody changes
their own account or the owner's (ADMIN_EMAIL), and the last admin stays.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select

from app.auth import sessions
from app.auth.deps import CurrentUser, require_admin, same_origin
from app.config import Settings, get_settings
from app.db import repo
from app.db.engine import session_scope
from app.db.models import (ACTIVE, ADMIN, DISABLED, PENDING, REJECTED, USER, Call,
                           User, utcnow)
from app.services.session_service import registry

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])

# action -> the statuses it applies to
ACTIONS = {
    "approve": (PENDING,),
    "reject": (PENDING,),
    "disable": (ACTIVE,),
    "enable": (DISABLED,),
    "promote": (ACTIVE,),
    "demote": (ACTIVE,),
}


def _settings() -> Settings:
    return get_settings()


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, {"code": code, "message": message})


@router.get("/users")
def list_users(status: str | None = Query(None, pattern="^(pending|active|rejected|disabled)$"),
               q: str = Query("", max_length=100),
               settings: Settings = Depends(_settings)):
    with session_scope() as db:
        stats = (select(Call.user_id, func.count().label("n"),
                        func.max(Call.started_at).label("last"))
                 .group_by(Call.user_id).subquery())
        query = (select(User, func.coalesce(stats.c.n, 0), stats.c.last)
                 .outerjoin(stats, stats.c.user_id == User.id))
        if status:
            query = query.where(User.status == status)
        if q.strip():
            like = f"%{q.strip().lower()}%"
            query = query.where(or_(func.lower(User.name).like(like),
                                    func.lower(User.email).like(like)))
        rows = db.execute(query.order_by(User.created_at.desc())).all()
        counts = dict(db.execute(select(User.status, func.count())
                                 .group_by(User.status)).all())
    return {
        "items": [{**repo.user_public(u, calls=n, last_call_at=last),
                   "owner": bool(settings.admin_email) and u.email == settings.admin_email}
                  for u, n, last in rows],
        "counts": {s: counts.get(s, 0) for s in (PENDING, ACTIVE, REJECTED, DISABLED)},
    }


@router.post("/users/{user_id}/{action}", dependencies=[Depends(same_origin)])
async def change_user(user_id: int, action: str,
                      admin: CurrentUser = Depends(require_admin),
                      settings: Settings = Depends(_settings)):
    # async: ending someone's live calls touches the event loop's call state.
    if action not in ACTIONS:
        raise HTTPException(404, {"code": "UNKNOWN_ACTION", "message": "Unknown action."})
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(404, {"code": "UNKNOWN_USER", "message": "No such member."})
        if user.id == admin.id:
            raise _conflict("OWN_ACCOUNT", "You can't change your own account.")
        if settings.admin_email and user.email == settings.admin_email:
            raise _conflict("OWNER_ACCOUNT", "The owner account can't be changed.")
        if user.status not in ACTIONS[action]:
            raise _conflict("INVALID_TRANSITION",
                            f"Can't {action} a member who is {user.status}.")
        if action == "promote" and user.role == ADMIN:
            raise _conflict("INVALID_TRANSITION", "Already an administrator.")
        if action == "demote" and user.role != ADMIN:
            raise _conflict("INVALID_TRANSITION", "Not an administrator.")
        # The two rules above already leave the acting admin in place; this
        # holds even if they change.
        if action in ("disable", "demote") and user.role == ADMIN:
            admins = db.scalar(select(func.count()).select_from(User).where(
                User.role == ADMIN, User.status == ACTIVE)) or 0
            if admins <= 1:
                raise _conflict("LAST_ADMIN", "The last administrator must stay.")

        if action == "approve":
            user.status, user.approved_at, user.approved_by = ACTIVE, utcnow(), admin.id
        elif action == "reject":
            user.status = REJECTED
        elif action == "disable":
            user.status = DISABLED
        elif action == "enable":
            user.status = ACTIVE
        elif action == "promote":
            user.role = ADMIN
        elif action == "demote":
            user.role = USER
        if action in ("disable", "demote"):
            sessions.revoke_all(db, user.id)     # signed out everywhere, now
        result = repo.user_public(user)

    ended = 0
    if action == "disable":
        from app.api.routes.call import _teardown

        for session in registry.sessions_for_user(user_id):
            _teardown(session, settings, reason="user_disabled")
            ended += 1
    log.info("admin_action", extra={"actor": admin.id, "target": user_id,
                                    "action": action, "calls_ended": ended})
    return {"user": result}


@router.get("/calls")
def list_calls(user_id: int | None = None, q: str = Query("", max_length=100),
               date_from: datetime | None = Query(None, alias="from"),
               date_to: datetime | None = Query(None, alias="to"),
               limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with session_scope() as db:
        return repo.list_calls(db, user_id=user_id, q=q, date_from=repo.utc(date_from),
                               date_to=repo.utc(date_to), limit=limit, offset=offset)


@router.get("/calls/{call_id}")
def get_call(call_id: int):
    with session_scope() as db:
        detail = repo.call_detail(db, call_id)
    if detail is None:
        raise HTTPException(404, {"code": "UNKNOWN_CALL", "message": "No such call."})
    return detail

