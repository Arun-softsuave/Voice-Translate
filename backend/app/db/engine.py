"""The SQLite database: one file, created and migrated on first use.

Nothing to install or set up on a new host. The file lives at DATABASE_PATH
(default backend/data/app.db); copying it moves every account and every call.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import BACKEND_DIR, get_settings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def _pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")      # readers never wait for a writer
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def migrate(engine: Engine) -> None:
    """Bring the schema up to date. Safe to run on every start."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def get_engine() -> Engine:
    global _engine, _factory
    if _engine is None:
        with _lock:
            if _engine is None:
                path = Path(get_settings().database_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                engine = create_engine(
                    f"sqlite:///{path}",
                    # Sync routes run on FastAPI's thread pool.
                    connect_args={"check_same_thread": False, "timeout": 5},
                )
                event.listen(engine, "connect", _pragmas)
                migrate(engine)
                _factory = sessionmaker(engine, expire_on_commit=False)
                _engine = engine
                log.info("database_ready", extra={"path": str(path)})
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """A unit of work: committed on success, rolled back on any error."""
    get_engine()
    assert _factory is not None
    db = _factory()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def dispose() -> None:
    """Forget the engine (tests point DATABASE_PATH somewhere new)."""
    global _engine, _factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _factory = None
