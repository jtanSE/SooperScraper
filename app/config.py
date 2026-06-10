from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _db_url() -> str:
    override = os.environ.get("SOOPERSCRAPER_DB_URL")
    if override:
        return override
    return f"sqlite:///{BASE_DIR / 'sooperscraper.db'}"


DB_URL = _db_url()
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Per-URL HTTP fetch timeout in seconds.
HTTP_TIMEOUT = float(os.environ.get("SOOPERSCRAPER_HTTP_TIMEOUT", "20"))

# Maximum number of runs to retain per job. Older runs are pruned after each run.
RUN_HISTORY_LIMIT = int(os.environ.get("SOOPERSCRAPER_RUN_HISTORY", "100"))

# When every URL in a run fails because the target rate-limited us, push the next
# attempt out instead of retrying at the normal cadence and extending the block.
RATE_LIMIT_COOLDOWN_MINUTES = int(os.environ.get("SOOPERSCRAPER_RATE_LIMIT_COOLDOWN_MINUTES", "60"))

# User-Agent used by the scraper.
USER_AGENT = os.environ.get(
    "SOOPERSCRAPER_USER_AGENT",
    "SooperScraper/0.1 (+https://github.com/sooperscraper)",
)
