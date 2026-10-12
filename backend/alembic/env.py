"""Alembic environment.

The app runs migrations itself on startup (app/db/engine.py) and hands over
its connection. Run by hand, from backend/:

    python -m alembic -c alembic.ini upgrade head
"""

from alembic import context
from sqlalchemy import create_engine

from app.db.models import Base

target_metadata = Base.metadata


def run() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _migrate(connection)
        return
    from app.config import get_settings

    engine = create_engine(f"sqlite:///{get_settings().database_path}")
    with engine.begin() as conn:
        _migrate(conn)


def _migrate(connection) -> None:
    # SQLite cannot ALTER most things; batch mode rebuilds the table instead.
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


run()
