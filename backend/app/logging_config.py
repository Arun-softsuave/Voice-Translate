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
from logging.handlers import RotatingFileHandler
from pathlib import Path

# A log file is kept automatically so a call can be examined after it ends.
# Rotated at 10 MB, five old files kept: ~60 MB at most, never unbounded.
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 5

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


def configure_logging(level: str = "INFO", log_file: str | Path | None = None) -> Path | None:
    """Log JSON lines to the terminal and, if `log_file` is set, to that file.

    Returns the file path in use, or None if file logging is off or the file
    could not be opened (logging to the terminal must never be lost to that).
    """
    formatter = JsonFormatter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    for old in root.handlers:
        old.close()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn's access log is noisy next to structured logs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # The Twilio SDK logs every request's headers at INFO; our own events
    # already say what happened, so keep only its warnings.
    logging.getLogger("twilio.http_client").setLevel(logging.WARNING)

    if not log_file:
        return None
    path = Path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES,
                                           backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "log_file_unavailable", extra={"path": str(path), "reason": str(exc)})
        return None
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    return path
