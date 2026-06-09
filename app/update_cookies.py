from __future__ import annotations

import argparse
import logging
import os

from sqlalchemy import select

from . import crypto
from .db import SessionLocal, init_db
from .models import ScheduledJob
from .schemas import CookiesInput


log = logging.getLogger(__name__)


def update_job_cookies(job_name: str, raw_cookie: str) -> int:
    cookies = CookiesInput(raw=raw_cookie).cookies
    if cookies is None:
        raise ValueError("no cookies parsed from input")

    init_db()
    with SessionLocal() as session:
        jobs = session.scalars(
            select(ScheduledJob).where(ScheduledJob.name == job_name).order_by(ScheduledJob.id.asc())
        ).all()
        if not jobs:
            raise LookupError(f"job named {job_name!r} not found")
        if len(jobs) > 1:
            ids = ", ".join(str(job.id) for job in jobs)
            raise RuntimeError(f"multiple jobs named {job_name!r} found: {ids}")

        job = jobs[0]
        job.cookies_encrypted = crypto.encrypt_json({"cookies": cookies})
        session.commit()
        log.info("updated cookies for job %s (%s); stored %s cookie(s)", job.id, job.name, len(cookies))
        return job.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update stored cookies for a scheduled job.")
    parser.add_argument("--job-name", default=os.environ.get("SOOPERSCRAPER_COOKIE_JOB_NAME", "DAXA"))
    parser.add_argument(
        "--cookie-env",
        default="DAXA_COOKIE_RAW",
        help="Environment variable containing the raw Cookie header value.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("SOOPERSCRAPER_LOG_LEVEL", "INFO"))
    raw_cookie = os.environ.get(args.cookie_env)
    if not raw_cookie:
        raise SystemExit(f"{args.cookie_env} is not set")

    update_job_cookies(args.job_name, raw_cookie)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
