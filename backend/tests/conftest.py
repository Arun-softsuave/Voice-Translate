"""Test environment.

Throwaway credentials so the app can boot. No real secret ever lives in the
test suite (design doc §13).
"""

import os

os.environ.setdefault("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
os.environ.setdefault("TWILIO_AUTH_TOKEN", "a1b2c3d4" * 4)
os.environ.setdefault("TWILIO_API_KEY", "SK" + "0" * 32)
os.environ.setdefault("TWILIO_API_SECRET", "y" * 32)
os.environ.setdefault("TWILIO_TWIML_APP_SID", "AP" + "0" * 32)
os.environ.setdefault("TWILIO_PHONE_NUMBER", "+15550000000")
os.environ.setdefault("BACKEND_PUBLIC_URL", "https://test.example.com")
os.environ.setdefault("FRONTEND_URL", "http://localhost:5173")
# Empty on purpose: tests must never open a real OpenAI session. load_dotenv()
# does not override an existing variable, so this also shields the suite from
# a populated .env sitting next to it.
os.environ.setdefault("OPENAI_API_KEY", "")
# Same shield for Gemini, and pin the backend: a developer's .env selecting
# TRANSLATION_BACKEND=gemini with a real key made the suite open live Gemini
# sessions and hang.
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("TRANSLATION_BACKEND", "translate")
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("ECHO_MODE", "false")
os.environ.setdefault("VALIDATE_TWILIO_SIGNATURE", "false")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")
# Tests must never write into the developer's real log file.
os.environ.setdefault("LOG_FILE", "")
# Accounts and call history go to a throwaway database, never backend/data.
import tempfile  # noqa: E402

os.environ.setdefault("DATABASE_PATH",
                      os.path.join(tempfile.mkdtemp(prefix="vt-test-"), "test.db"))
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
os.environ.setdefault("ADMIN_EMAIL", "owner@example.com")

import pytest  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _fresh_accounts():
    """Every test starts with no accounts, calls or sign-in throttling."""
    from sqlalchemy import delete

    from app.auth import ratelimit
    from app.db.engine import session_scope
    from app.db.models import AuthSession, Call, TranscriptLine, User

    with session_scope() as db:
        for table in (TranscriptLine, Call, AuthSession, User):
            db.execute(delete(table))
    ratelimit.reset()
    yield


def make_user(email="user@example.com", *, role="user", status="active",
              name="Test User", password=PASSWORD) -> int:
    from app.auth.passwords import hash_password
    from app.db.engine import session_scope
    from app.db.models import User

    with session_scope() as db:
        user = User(name=name, email=email, password_hash=hash_password(password),
                    role=role, status=status)
        db.add(user)
        db.flush()
        return user.id


def sign_in(client, email="user@example.com", password=PASSWORD):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["user"]


@pytest.fixture
def signed_in():
    """A TestClient signed in as an active member, and that member's id."""
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    user_id = make_user()
    sign_in(client)
    return client, user_id
