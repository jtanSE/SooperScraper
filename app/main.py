from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .api import jobs as jobs_api
from .api import runs as runs_api
from .config import STATIC_DIR
from .db import init_db


logging.basicConfig(level=os.environ.get("SOOPERSCRAPER_LOG_LEVEL", "INFO"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if os.environ.get("SOOPERSCRAPER_DISABLE_SCHEDULER") != "1":
        scheduler.start()
    try:
        yield
    finally:
        scheduler.stop()


app = FastAPI(title="Sooper Scraper", lifespan=lifespan)
app.include_router(jobs_api.router)
app.include_router(runs_api.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Mount the static frontend at "/". `html=True` makes "/" serve index.html.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/")
    def _missing_static() -> FileResponse:  # pragma: no cover
        raise RuntimeError(f"static dir missing: {STATIC_DIR}")
