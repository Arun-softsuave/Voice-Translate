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
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("ECHO_MODE", "false")
os.environ.setdefault("VALIDATE_TWILIO_SIGNATURE", "false")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")
