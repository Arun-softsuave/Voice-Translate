"""Logs are saved to a file automatically, so any call can be examined later."""

import json
import logging

import pytest

from app import config
from app.logging_config import configure_logging


@pytest.fixture
def restore_root():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for h in root.handlers:
        if h not in handlers:
            h.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_events_are_written_to_the_file_as_json(tmp_path, restore_root):
    path = tmp_path / "logs" / "backend.log"           # folder does not exist yet

    assert configure_logging("INFO", path) == path
    logging.getLogger("app.test").info("latency_turn", extra={"model_ms": 3050})
    for h in logging.getLogger().handlers:
        h.flush()

    line = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["event"] == "latency_turn"
    assert line["model_ms"] == 3050


def test_no_file_when_disabled(restore_root):
    assert configure_logging("INFO", "") is None


def test_an_unwritable_path_does_not_break_logging(tmp_path, restore_root):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    assert configure_logging("INFO", blocker / "backend.log") is None


def test_twilio_request_dumps_are_kept_out(restore_root):
    configure_logging("INFO", "")
    assert logging.getLogger("twilio.http_client").getEffectiveLevel() >= logging.WARNING


def test_default_location_is_inside_the_backend_folder(monkeypatch):
    monkeypatch.delenv("LOG_FILE", raising=False)
    assert config._log_file() == str(config.BACKEND_DIR / "logs" / "backend.log")


def test_relative_paths_do_not_depend_on_where_uvicorn_started(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_FILE", "logs/x.log")
    assert config._log_file() == str(config.BACKEND_DIR / "logs" / "x.log")


def test_empty_disables(monkeypatch):
    monkeypatch.setenv("LOG_FILE", "")
    assert config._log_file() == ""
