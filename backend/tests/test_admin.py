"""The admin page: approving requests and managing members.

Only admins get in, checked on every request. The app cannot lock itself
out: nobody changes their own account or the owner's, and the last admin
stays an admin.
"""

import pytest
from fastapi.testclient import TestClient

from app.db.engine import session_scope
from app.db.models import AuthSession, User
from app.main import app
from app.services.session_service import registry
from tests.conftest import make_user, sign_in


@pytest.fixture
def admin():
    """A TestClient signed in as an admin (not the owner), and its id."""
    client = TestClient(app)
    admin_id = make_user("admin@example.com", role="admin", name="Admin")
    sign_in(client, "admin@example.com")
    return client, admin_id


def act(client, user_id, action):
    return client.post(f"/api/admin/users/{user_id}/{action}")


def row(user_id):
    with session_scope() as db:
        user = db.get(User, user_id)
        return user.status, user.role


def sessions_of(user_id):
    with session_scope() as db:
        return db.query(AuthSession).filter_by(user_id=user_id).count()


def test_members_cannot_open_the_admin_page(signed_in):
    client, user_id = signed_in
    assert client.get("/api/admin/users").status_code == 403
    assert client.get("/api/admin/calls").status_code == 403
    assert act(client, user_id, "promote").status_code == 403


def test_approving_lets_a_request_sign_in(admin):
    client, admin_id = admin
    pending = make_user("new@example.com", status="pending")
    other = TestClient(app)
    assert other.post("/api/auth/login", json={"email": "new@example.com",
                                               "password": "correct horse battery"}
                      ).status_code == 403

    r = act(client, pending, "approve")
    assert r.status_code == 200 and r.json()["user"]["status"] == "active"
    with session_scope() as db:
        assert db.get(User, pending).approved_by == admin_id
    sign_in(other, "new@example.com")


def test_the_list_shows_everyone_with_counts(admin):
    client, _ = admin
    make_user("p1@example.com", status="pending", name="Pat")
    make_user("p2@example.com", status="pending")
    body = client.get("/api/admin/users").json()
    assert body["counts"] == {"pending": 2, "active": 1, "rejected": 0, "disabled": 0}
    assert {u["email"] for u in body["items"]} == {"admin@example.com", "p1@example.com",
                                                    "p2@example.com"}
    pending = client.get("/api/admin/users?status=pending&q=pat").json()["items"]
    assert [u["email"] for u in pending] == ["p1@example.com"]
    assert "password_hash" not in pending[0]


@pytest.mark.parametrize("start,action,end", [
    (("pending", "user"), "approve", ("active", "user")),
    (("pending", "user"), "reject", ("rejected", "user")),
    (("active", "user"), "disable", ("disabled", "user")),
    (("disabled", "user"), "enable", ("active", "user")),
    (("active", "user"), "promote", ("active", "admin")),
    (("active", "admin"), "demote", ("active", "user")),
])
def test_each_action(admin, start, action, end):
    client, _ = admin
    target = make_user("t@example.com", status=start[0], role=start[1])
    assert act(client, target, action).status_code == 200
    assert row(target) == end


@pytest.mark.parametrize("start,action", [
    (("active", "user"), "approve"),         # approving twice
    (("disabled", "user"), "approve"),
    (("pending", "user"), "disable"),
    (("active", "admin"), "promote"),
    (("active", "user"), "demote"),
    (("rejected", "user"), "enable"),
])
def test_actions_that_make_no_sense_are_refused(admin, start, action):
    client, _ = admin
    target = make_user("t@example.com", status=start[0], role=start[1])
    r = act(client, target, action)
    assert r.status_code == 409
    assert row(target) == start


def test_nobody_changes_their_own_account(admin):
    client, admin_id = admin
    for action in ("demote", "disable"):
        r = act(client, admin_id, action)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "OWN_ACCOUNT"


def test_nobody_changes_the_owner(admin):
    client, _ = admin
    owner = make_user("owner@example.com", role="admin")
    for action in ("demote", "disable"):
        r = act(client, owner, action)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "OWNER_ACCOUNT"


def test_an_admin_check_happens_on_every_request(admin):
    client, admin_id = admin
    with session_scope() as db:
        db.get(User, admin_id).status = "disabled"
    assert client.get("/api/admin/users").status_code == 401
    with session_scope() as db:
        db.get(User, admin_id).status = "active"
        db.get(User, admin_id).role = "user"
    assert client.get("/api/admin/users").status_code == 403


def test_disabling_signs_out_everywhere_and_ends_live_calls(admin):
    client, _ = admin
    target = make_user("t@example.com")
    member = TestClient(app)
    sign_in(member, "t@example.com")
    call = registry.create(source_language="en", target_language="ta", user_id=target)

    assert act(client, target, "disable").status_code == 200
    assert sessions_of(target) == 0
    assert member.get("/api/auth/me").status_code == 401
    assert registry.get(call.session_id) is None
    assert call.end_reason == "user_disabled"


def test_demoting_takes_effect_on_the_next_request(admin):
    client, _ = admin
    target = make_user("t@example.com", role="admin")
    other = TestClient(app)
    sign_in(other, "t@example.com")
    assert other.get("/api/admin/users").status_code == 200
    act(client, target, "demote")
    assert other.get("/api/admin/users").status_code in (401, 403)


def test_the_owner_is_made_admin_at_startup():
    owner = make_user("owner@example.com", role="user")
    with TestClient(app):
        pass
    assert row(owner) == ("active", "admin")


def test_set_password_from_the_host_creates_the_owner():
    from app.auth.owner import set_password

    assert "Created admin" in set_password("Owner@Example.com", "a long owner phrase")
    with session_scope() as db:
        owner = db.query(User).filter_by(email="owner@example.com").one()
        assert (owner.status, owner.role) == ("active", "admin")
    client = TestClient(app)
    sign_in(client, "owner@example.com", "a long owner phrase")
    assert client.get("/api/admin/users").status_code == 200

    # Resetting signs the owner out everywhere.
    set_password("owner@example.com", "a newer owner phrase")
    assert client.get("/api/auth/me").status_code == 401
    with pytest.raises(ValueError):
        set_password("owner@example.com", "short")
