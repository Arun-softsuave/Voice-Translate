"""Asking for access, signing in and out, and what stays closed without it.

* A request makes a *pending* account; only an admin's approval lets it in.
* Answers never reveal whether an email is known, except to someone who has
  just typed its correct password.
* Guessing is slowed down; other websites cannot act with the cookie.
"""

import pytest
from fastapi.testclient import TestClient

from app.auth import passwords, ratelimit
from app.db.engine import session_scope
from app.db.models import User
from app.main import app
from tests.conftest import PASSWORD, make_user, sign_in

GOOD = {"name": "Priya Raman", "email": "Priya@Example.com ", "password": PASSWORD}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def stored(email):
    with session_scope() as db:
        return db.query(User).filter_by(email=email).one_or_none()


# ------------------------------------------------------------- request access
def test_a_request_creates_a_pending_member(client):
    r = client.post("/api/auth/request-access", json=GOOD)
    assert r.status_code == 202
    user = stored("priya@example.com")              # trimmed and lowercased
    assert (user.status, user.role, user.name) == ("pending", "user", "Priya Raman")
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash


def test_the_answer_is_the_same_whoever_asks(client):
    first = client.post("/api/auth/request-access", json=GOOD).json()
    make_user("active@example.com")
    again = client.post("/api/auth/request-access", json=GOOD).json()
    known = client.post("/api/auth/request-access",
                        json={**GOOD, "email": "active@example.com"}).json()
    assert first == again == known


@pytest.mark.parametrize("status", ["pending", "active", "disabled"])
def test_asking_again_changes_nothing(client, status):
    make_user("x@example.com", status=status, name="Original")
    client.post("/api/auth/request-access",
                json={**GOOD, "email": "x@example.com", "password": "another long phrase"})
    user = stored("x@example.com")
    assert (user.status, user.name) == (status, "Original")
    assert passwords.verify_password(user.password_hash, PASSWORD)


def test_a_rejected_person_may_ask_again(client):
    make_user("x@example.com", status="rejected")
    client.post("/api/auth/request-access",
                json={**GOOD, "email": "x@example.com", "password": "another long phrase"})
    user = stored("x@example.com")
    assert user.status == "pending"
    assert passwords.verify_password(user.password_hash, "another long phrase")


def test_nobody_can_ask_to_be_an_admin(client):
    client.post("/api/auth/request-access", json={**GOOD, "role": "admin",
                                                   "status": "active"})
    user = stored("priya@example.com")
    assert (user.role, user.status) == ("user", "pending")


def test_the_owner_email_cannot_be_requested(client):
    r = client.post("/api/auth/request-access", json={**GOOD, "email": "owner@example.com"})
    assert r.status_code == 202
    assert stored("owner@example.com") is None


@pytest.mark.parametrize("password,why", [
    ("short", "at least 12"),
    ("password1234", "too common"),
    ("aaaaaaaaaaaaaaaa", "too common"),
    ("priyaramanpriyaraman"[:10] + "xx", None),        # fine: 12 chars, not the name
])
def test_weak_passwords_are_refused_with_a_reason(client, password, why):
    r = client.post("/api/auth/request-access", json={**GOOD, "password": password})
    if why is None:
        assert r.status_code == 202
    else:
        assert r.status_code == 422
        assert why in r.json()["detail"]["message"]


def test_bad_email_or_name_is_refused(client):
    assert client.post("/api/auth/request-access",
                       json={**GOOD, "email": "not-an-email"}).status_code == 422
    assert client.post("/api/auth/request-access",
                       json={**GOOD, "name": " "}).status_code == 422


def test_requests_from_one_address_are_limited(client):
    for i in range(ratelimit.access_requests.limit):
        client.post("/api/auth/request-access", json={**GOOD, "email": f"p{i}@example.com"})
    r = client.post("/api/auth/request-access", json={**GOOD, "email": "late@example.com"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


# ------------------------------------------------------------------ sign in
def test_an_active_member_signs_in_and_gets_a_safe_cookie(client):
    user_id = make_user()
    r = client.post("/api/auth/login",
                    json={"email": " USER@example.com", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["user"] == {"id": user_id, "name": "Test User",
                                "email": "user@example.com", "role": "user"}
    cookie = r.headers["set-cookie"].lower()
    assert "vt_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["user"]["id"] == user_id


def test_wrong_password_and_unknown_email_look_the_same(client, monkeypatch):
    make_user()
    burned = []
    monkeypatch.setattr(passwords, "burn_time", lambda p: burned.append(p))
    wrong = client.post("/api/auth/login", json={"email": "user@example.com",
                                                 "password": "wrong password!"})
    unknown = client.post("/api/auth/login", json={"email": "nobody@example.com",
                                                   "password": "wrong password!"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert burned == ["wrong password!"], "an unknown email must still cost a hash"


@pytest.mark.parametrize("status,code", [("pending", "PENDING_APPROVAL"),
                                         ("disabled", "ACCOUNT_DISABLED")])
def test_the_right_password_learns_why_it_cannot_come_in(client, status, code):
    make_user(status=status)
    r = client.post("/api/auth/login", json={"email": "user@example.com",
                                             "password": PASSWORD})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == code
    assert "set-cookie" not in r.headers


def test_a_rejected_request_looks_like_an_unknown_email(client):
    make_user(status="rejected")
    r = client.post("/api/auth/login", json={"email": "user@example.com",
                                             "password": PASSWORD})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "INVALID_CREDENTIALS"


def test_guessing_is_slowed_down(client):
    make_user()
    for _ in range(ratelimit.FREE_FAILURES):
        client.post("/api/auth/login", json={"email": "user@example.com",
                                             "password": "wrong password!"})
    r = client.post("/api/auth/login", json={"email": "user@example.com",
                                             "password": PASSWORD})
    assert r.status_code == 429, "even the right password waits once throttled"
    assert "Try again in" in r.json()["detail"]["message"]


def test_backoff_doubles_and_a_success_clears_it():
    now = [0.0]
    backoff = ratelimit.Backoff(clock=lambda: now[0])
    for _ in range(ratelimit.FREE_FAILURES):
        backoff.failed("k")
    assert backoff.wait_s("k") == ratelimit.FIRST_WAIT_S
    backoff.failed("k")
    assert backoff.wait_s("k") == ratelimit.FIRST_WAIT_S * 2
    now[0] += 1000
    assert backoff.wait_s("k") == 0
    backoff.succeeded("k")
    backoff.failed("k")
    assert backoff.wait_s("k") == 0


# ------------------------------------------------------------- signed out
def test_logout_ends_the_session_on_the_server(client):
    make_user()
    sign_in(client)
    token = client.cookies.get("vt_session")
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("vt_session", token)            # replaying the old cookie
    assert client.get("/api/auth/me").status_code == 401


def test_signing_in_again_replaces_the_old_session(client):
    make_user()
    sign_in(client)
    old = client.cookies.get("vt_session")
    sign_in(client)
    assert client.cookies.get("vt_session") != old
    other = TestClient(app)
    other.cookies.set("vt_session", old)
    assert other.get("/api/auth/me").status_code == 401


def test_an_expired_session_is_refused(client):
    from datetime import timedelta

    from app.auth import sessions
    from app.db.models import AuthSession

    make_user()
    sign_in(client)
    with session_scope() as db:
        for row in db.query(AuthSession):
            row.last_seen_at -= sessions.IDLE + timedelta(seconds=1)
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("method,path", [
    ("post", "/api/call/token"),
    ("post", "/api/call/start"),
    ("get", "/api/call/sess_x"),
    ("post", "/api/call/end/sess_x"),
    ("get", "/api/call/sess_x/captions"),
    ("get", "/api/calls"),
    ("get", "/api/calls/1"),
    ("get", "/api/admin/users"),
    ("get", "/api/diagnostics/sessions"),
])
def test_browser_routes_need_a_signed_in_user(client, method, path):
    kwargs = {"json": {"source_language": "en", "target_language": "ta"}} \
        if path.endswith("/start") else {}
    assert getattr(client, method)(path, **kwargs).status_code == 401


def test_twilio_and_health_stay_open(client):
    assert client.get("/health").status_code == 200
    # Twilio cannot sign in: these answer on their own terms (here, an
    # unknown call), never with 401.
    assert client.post("/api/call/status", data={"CallSid": "CA1",
                                                 "CallStatus": "ringing"}).status_code == 204
    assert client.post("/api/call/twiml/A", data={"session_id": "nope"}).status_code == 404


# ------------------------------------------------------------- cross-site
def test_another_website_cannot_act_with_the_cookie(client):
    make_user()
    r = client.post("/api/auth/login", json={"email": "user@example.com", "password": PASSWORD},
                    headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    sign_in(client)
    r = client.post("/api/call/start", json={"source_language": "en", "target_language": "ta"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-site"},   # Vite in dev
    {"Origin": "https://test.example.com"},                              # the tunnel
])
def test_our_own_pages_are_let_through(client, headers):
    make_user()
    r = client.post("/api/auth/login", json={"email": "user@example.com",
                                             "password": PASSWORD}, headers=headers)
    assert r.status_code == 200


# ------------------------------------------------------------- the token
def test_the_twilio_identity_is_the_signed_in_user(signed_in, monkeypatch):
    from app.services import twilio_service

    client, user_id = signed_in
    seen = []
    monkeypatch.setattr(twilio_service, "create_access_token",
                        lambda s, identity, ttl: seen.append(identity) or "tok")
    r = client.post("/api/call/token", json={"identity": "someone_else"})
    assert r.status_code == 200
    assert seen == [f"u_{user_id}"] and r.json()["identity"] == f"u_{user_id}"
