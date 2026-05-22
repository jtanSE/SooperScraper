from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DB_URL


class Base(DeclarativeBase):
    pass


_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    # Import models so they register on Base before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """Add columns that were introduced after the first release.

    SQLite-only; uses ALTER TABLE ADD COLUMN which is idempotent because
    we check for column existence first. Run after create_all so a fresh
    install is a no-op.
    """
    if not engine.url.get_backend_name().startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "scheduled_jobs" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("scheduled_jobs")}
    additions: list[str] = []
    if "auth_config" not in existing:
        additions.append("ALTER TABLE scheduled_jobs ADD COLUMN auth_config JSON")
    if "credentials_encrypted" not in existing:
        additions.append("ALTER TABLE scheduled_jobs ADD COLUMN credentials_encrypted BLOB")
    if "cookies_encrypted" not in existing:
        additions.append("ALTER TABLE scheduled_jobs ADD COLUMN cookies_encrypted BLOB")
    if "zip_records" not in existing:
        additions.append(
            "ALTER TABLE scheduled_jobs ADD COLUMN zip_records BOOLEAN NOT NULL DEFAULT 0"
        )
    if "sort_config" not in existing:
        additions.append("ALTER TABLE scheduled_jobs ADD COLUMN sort_config JSON")
    if "notify_config" not in existing:
        additions.append("ALTER TABLE scheduled_jobs ADD COLUMN notify_config JSON")
    if additions:
        with engine.begin() as conn:
            for stmt in additions:
                conn.execute(text(stmt))


def get_session() -> Session:
    """FastAPI dependency that yields a session and closes it after the request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Context manager for use outside the request cycle (e.g. scheduler jobs)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
