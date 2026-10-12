"""The owner account (ADMIN_EMAIL), and setting passwords from the host.

The owner's password is never typed into the web page or sent anywhere: it is
set on the machine running the app, with `python -m app.cli set-password`.
"""

from __future__ import annotations

import logging

from app.auth import passwords, sessions
from app.config import get_settings
from app.db import repo
from app.db.engine import session_scope
from app.db.models import ACTIVE, ADMIN, USER, User, utcnow

log = logging.getLogger(__name__)


def ensure_owner_admin() -> None:
    """Whatever happened in the admin page, the owner stays an admin."""
    email = get_settings().admin_email
    if not email:
        return
    with session_scope() as db:
        user = repo.user_by_email(db, email)
        if user is not None and user.role != ADMIN:
            user.role = ADMIN
            log.info("owner_promoted", extra={"user_id": user.id})


def set_password(email: str, password: str, *, name: str | None = None) -> str:
    """Set `email`'s password and sign them out everywhere.

    Creates the account if it does not exist: active, and an admin if it is
    the owner's. Returns what happened, for the command line.
    """
    email = repo.normalise_email(email)
    owner = email == get_settings().admin_email
    problem = passwords.problem_with(password, email=email, name=name or "")
    if problem:
        raise ValueError(problem)
    with session_scope() as db:
        user = repo.user_by_email(db, email)
        if user is None:
            db.add(User(name=name or email.split("@")[0], email=email,
                        password_hash=passwords.hash_password(password),
                        role=ADMIN if owner else USER, status=ACTIVE,
                        approved_at=utcnow()))
            return f"Created {'admin' if owner else 'member'} account {email}."
        user.password_hash = passwords.hash_password(password)
        if owner:
            user.role, user.status = ADMIN, ACTIVE
        db.flush()
        sessions.revoke_all(db, user.id)
        return f"Password updated for {email} ({user.role}, {user.status})."
