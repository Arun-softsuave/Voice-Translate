"""Structured logging with mandatory redaction.

Rule from the design doc §13: logs never contain API keys, tokens, raw audio,
or full phone numbers.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

_NON_DIGITS = re.compile(r"\D")


def mask_phone(number: str | None) -> str:
    """+918754677067 -> +********7067

    Only the last four digits survive. We deliberately do not try to preserve
    the country code: country-code length varies (+91 vs +1), and guessing it
    wrong leaks a subscriber digit.
    """
    if not number:
        return ""
    digits = _NON_DIGITS.sub("", number)
    if not digits:
        return ""
    if len(digits) <= 4:
        return "+" + "*" * len(digits)
    return "+" + "*" * (len(digits) - 4) + digits[-4:]


class JsonFormatter(logging.Formatter):
    RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
        "asctime", "message", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn's access log is noisy next to structured logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
