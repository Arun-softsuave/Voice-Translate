"""A signed-in user's own calls and what was said on them.

Always filtered to the caller: someone else's call is "not found", not
"forbidden", so its existence is not given away.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import CurrentUser, current_user
from app.db import repo
from app.db.engine import session_scope

router = APIRouter(prefix="/api/calls", tags=["history"])


@router.get("")
def my_calls(date_from: datetime | None = Query(None, alias="from"),
             date_to: datetime | None = Query(None, alias="to"),
             q: str = Query("", max_length=100),
             limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
             user: CurrentUser = Depends(current_user)):
    with session_scope() as db:
        return repo.list_calls(db, user_id=user.id, q=q, date_from=repo.utc(date_from),
                               date_to=repo.utc(date_to), limit=limit, offset=offset)


@router.get("/{call_id}")
def my_call(call_id: int, user: CurrentUser = Depends(current_user)):
    with session_scope() as db:
        detail = repo.call_detail(db, call_id, user_id=user.id)
    if detail is None:
        raise HTTPException(404, {"code": "UNKNOWN_CALL", "message": "No such call."})
    return detail
