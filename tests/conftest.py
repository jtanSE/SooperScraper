from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[str]:
    """Point the app at a fresh SQLite file and disable the background scheduler."""
    db_file = tmp_path / "test.db"
    os.environ["SOOPERSCRAPER_DB_URL"] = f"sqlite:///{db_file}"
    os.environ["SOOPERSCRAPER_DISABLE_SCHEDULER"] = "1"

    # Reset any cached modules so the new env vars take effect.
    import importlib
    import sys

    for mod in [
        "app.main",
        "app.api.jobs",
        "app.api.runs",
        "app.scheduler",
        "app.scraper",
        "app.models",
        "app.db",
        "app.config",
    ]:
        sys.modules.pop(mod, None)

    yield str(db_file)

    os.environ.pop("SOOPERSCRAPER_DB_URL", None)
    os.environ.pop("SOOPERSCRAPER_DISABLE_SCHEDULER", None)
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]


@pytest.fixture
def client(tmp_db):
    """FastAPI TestClient against the temp DB. Starts a real (paused) scheduler
    so endpoint code that touches scheduler.* runs the production path."""
    from fastapi.testclient import TestClient

    # We DON'T disable the scheduler here because the API exercises it. Override:
    os.environ.pop("SOOPERSCRAPER_DISABLE_SCHEDULER", None)

    import importlib
    import sys
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]

    from app.main import app  # noqa: E402

    with TestClient(app) as c:
        yield c


@pytest.fixture
def session(tmp_db):
    """A raw DB session against a freshly-initialised schema (no scheduler)."""
    import importlib
    import sys
    for mod in list(sys.modules):
        if mod.startswith("app"):
            del sys.modules[mod]
    from app.db import SessionLocal, init_db  # noqa: E402
    init_db()
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
