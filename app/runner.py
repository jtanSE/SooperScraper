from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import ScheduledJob
from .scheduler import trigger_from_schedule
from .scraper import run_job


log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def next_run_after(job: ScheduledJob, now: datetime | None = None) -> datetime | None:
    """Compute the next scheduled fire time after now for a persisted job."""
    now = now or _utcnow()
    trigger = trigger_from_schedule(job.schedule_type, job.schedule_config)
    previous_fire_time = _as_aware_utc(job.next_run_at)
    return trigger.get_next_fire_time(previous_fire_time, now)


def _job_is_due(job: ScheduledJob, now: datetime) -> bool:
    next_run_at = _as_aware_utc(job.next_run_at)
    return next_run_at is not None and next_run_at <= now


def _due_jobs(session: Session, now: datetime, include_failed: bool) -> list[ScheduledJob]:
    statuses = ["active", "failed"] if include_failed else ["active"]
    jobs = session.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.status.in_(statuses))
        .order_by(ScheduledJob.next_run_at.asc(), ScheduledJob.id.asc())
    ).all()
    return [job for job in jobs if _job_is_due(job, now)]


def _runnable_jobs(session: Session, include_failed: bool) -> list[ScheduledJob]:
    statuses = ["active", "failed"] if include_failed else ["active"]
    return session.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.status.in_(statuses))
        .order_by(ScheduledJob.id.asc())
    ).all()


def run_due_jobs(
    *,
    session: Session | None = None,
    now: datetime | None = None,
    include_failed: bool = True,
    limit: int | None = None,
    run_all: bool = False,
) -> int:
    """Run scheduled jobs once, then advance each job's next_run_at.

    This is meant for external schedulers such as GitHub Actions. It does not
    start the FastAPI app or the in-process APScheduler loop.
    """
    init_db()
    owns_session = session is None
    session = session or SessionLocal()
    now = now or _utcnow()
    ran = 0

    try:
        jobs = (
            _runnable_jobs(session, include_failed)
            if run_all
            else _due_jobs(session, now, include_failed)
        )
        if limit is not None:
            jobs = jobs[:limit]

        for job in jobs:
            job_id = job.id
            log.info("running due job %s (%s)", job.id, job.name)
            run_job(session, job.id)

            refreshed = session.get(ScheduledJob, job_id)
            if refreshed is None:
                continue
            refreshed.next_run_at = next_run_after(refreshed, now)
            session.commit()
            ran += 1
            log.info("job %s next_run_at=%s", job_id, refreshed.next_run_at)
        return ran
    finally:
        if owns_session:
            session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run due Sooper Scraper jobs once.")
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Only run active jobs; by default failed jobs are retried too.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of due jobs to run in this invocation.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all active jobs now, even if next_run_at is in the future.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("SOOPERSCRAPER_LOG_LEVEL", "INFO"))
    count = run_due_jobs(
        include_failed=not args.active_only,
        limit=args.limit,
        run_all=args.all,
    )
    log.info("completed cloud runner; ran %s job(s)", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
