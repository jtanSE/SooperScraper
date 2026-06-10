from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import ScheduledJob
from .scheduler import trigger_from_schedule
from .scraper import run_job


log = logging.getLogger(__name__)


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_s, minute_s = value.split(":", 1)
    hour = int(hour_s)
    minute = int(minute_s)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError
    return hour, minute


def _active_window(
    spec: str | None,
    now: datetime,
) -> tuple[str, datetime] | None:
    """Return (window_spec, end_utc) when now is inside any configured window.

    Window format: "America/New_York:16:00-18:00". Multiple windows can be
    separated by commas. Windows that cross midnight are supported.
    """
    if not spec:
        return None
    now_utc = _as_aware_utc(now)
    if now_utc is None:
        return None
    for raw_window in [part.strip() for part in spec.split(",") if part.strip()]:
        try:
            tz_name, time_range = raw_window.split(":", 1)
            start_s, end_s = time_range.split("-", 1)
            start_hour, start_minute = _parse_hhmm(start_s)
            end_hour, end_minute = _parse_hhmm(end_s)
            tz = ZoneInfo(tz_name)
        except Exception:
            log.warning("ignoring invalid runner time window %r", raw_window)
            continue

        local_now = now_utc.astimezone(tz)
        start = local_now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end = local_now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        if end <= start:
            if local_now >= start:
                end += timedelta(days=1)
            else:
                start -= timedelta(days=1)
        if start <= local_now < end:
            return raw_window, end.astimezone(timezone.utc)
    return None


def configure_logging(level: str | None = None) -> None:
    logging.basicConfig(level=level or os.environ.get("SOOPERSCRAPER_LOG_LEVEL", "INFO"))
    # httpx logs full request URLs at INFO, which can expose webhook tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


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
    next_fire_time = trigger.get_next_fire_time(previous_fire_time, now)
    while next_fire_time is not None and next_fire_time <= now:
        previous_fire_time = next_fire_time
        next_fire_time = trigger.get_next_fire_time(previous_fire_time, now)
    return next_fire_time


def _job_is_due(job: ScheduledJob, now: datetime, *, lookahead: timedelta | None = None) -> bool:
    next_run_at = _as_aware_utc(job.next_run_at)
    cutoff = now + (lookahead or timedelta())
    return next_run_at is not None and next_run_at <= cutoff


def _due_jobs(
    session: Session,
    now: datetime,
    include_failed: bool,
    *,
    lookahead: timedelta | None = None,
) -> list[ScheduledJob]:
    statuses = ["active", "failed"] if include_failed else ["active"]
    jobs = session.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.status.in_(statuses))
        .order_by(ScheduledJob.next_run_at.asc(), ScheduledJob.id.asc())
    ).all()
    return [job for job in jobs if _job_is_due(job, now, lookahead=lookahead)]


def _runnable_jobs(session: Session, include_failed: bool) -> list[ScheduledJob]:
    statuses = ["active", "failed"] if include_failed else ["active"]
    return session.scalars(
        select(ScheduledJob)
        .where(ScheduledJob.status.in_(statuses))
        .order_by(ScheduledJob.id.asc())
    ).all()


def _claim_due_job(
    session: Session,
    job: ScheduledJob,
    now: datetime,
    include_failed: bool,
    lookahead: timedelta,
    *,
    force: bool = False,
    force_min_interval: timedelta | None = None,
) -> datetime | None:
    """Atomically advance a due job before scraping so parallel runners skip it."""
    statuses = ["active", "failed"] if include_failed else ["active"]
    cutoff = now + lookahead
    next_run_at = _as_aware_utc(job.next_run_at)
    force_min_interval = force_min_interval or timedelta(minutes=20)
    last_run_at = _as_aware_utc(job.last_run_at)
    if force and last_run_at is not None and last_run_at > now - force_min_interval:
        return None
    if not force and (next_run_at is None or next_run_at > cutoff):
        return None

    if force:
        claimed_next_run_at = now + force_min_interval
        stale_after = now + force_min_interval
        claim_filter = (
            (ScheduledJob.next_run_at == None)  # noqa: E711
            | (ScheduledJob.next_run_at <= cutoff)
            | (ScheduledJob.next_run_at > stale_after)
        )
    else:
        claimed_next_run_at = next_run_after(job, now)
        claim_filter = ScheduledJob.next_run_at <= cutoff

    result = session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.id == job.id)
        .where(ScheduledJob.status.in_(statuses))
        .where(claim_filter)
        .values(next_run_at=claimed_next_run_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return claimed_next_run_at


def log_job_summary(session: Session) -> None:
    rows = session.execute(
        select(ScheduledJob.status, func.count(ScheduledJob.id))
        .group_by(ScheduledJob.status)
        .order_by(ScheduledJob.status)
    ).all()
    if not rows:
        log.info("job summary: no jobs found in database")
        return
    summary = ", ".join(f"{status}={count}" for status, count in rows)
    log.info("job summary: %s", summary)

    jobs = session.scalars(
        select(ScheduledJob).order_by(ScheduledJob.id.asc()).limit(10)
    ).all()
    for job in jobs:
        log.info(
            "job %s: name=%r status=%s next_run_at=%s notify=%s urls=%s",
            job.id,
            job.name,
            job.status,
            job.next_run_at,
            bool(job.notify_config),
            len(job.urls or []),
        )


def run_due_jobs(
    *,
    session: Session | None = None,
    now: datetime | None = None,
    include_failed: bool = True,
    limit: int | None = None,
    run_all: bool = False,
    lookahead_seconds: int = 0,
) -> int:
    """Run scheduled jobs once, then advance each job's next_run_at.

    This is meant for external schedulers such as GitHub Actions. It does not
    start the FastAPI app or the in-process APScheduler loop.
    """
    init_db()
    owns_session = session is None
    session = session or SessionLocal()
    now = now or _utcnow()
    lookahead = timedelta(seconds=max(0, lookahead_seconds))
    blackout = _active_window(os.environ.get("SOOPERSCRAPER_RUNNER_BLACKOUT_WINDOWS"), now)
    force_window = _active_window(os.environ.get("SOOPERSCRAPER_RUNNER_FORCE_WINDOWS"), now)
    force_min_interval = timedelta(
        seconds=max(1, int(os.environ.get("SOOPERSCRAPER_RUNNER_FORCE_MIN_INTERVAL_SECONDS", "1200")))
    )
    ran = 0

    try:
        log.info("runner utc_now=%s due_cutoff=%s", now, now + lookahead)
        if blackout is not None and not run_all:
            log.info("runner blackout window active (%s); skipping due jobs", blackout[0])
            return 0
        if force_window is not None and not run_all:
            log.info("runner force window active (%s); stale/cooldown jobs may run", force_window[0])
        log_job_summary(session)
        jobs = (
            _runnable_jobs(session, include_failed)
            if run_all or force_window is not None
            else _due_jobs(session, now, include_failed, lookahead=lookahead)
        )
        if limit is not None:
            jobs = jobs[:limit]

        for job in jobs:
            job_id = job.id
            claimed_next_run_at = None
            if not run_all:
                claimed_next_run_at = _claim_due_job(
                    session,
                    job,
                    now,
                    include_failed,
                    lookahead,
                    force=force_window is not None,
                    force_min_interval=force_min_interval,
                )
                if claimed_next_run_at is None:
                    log.info("job %s was already claimed or is no longer due", job_id)
                    continue

            log.info("running due job %s (%s)", job.id, job.name)
            run_job(session, job.id)

            if force_window is not None and not run_all:
                refreshed = session.get(ScheduledJob, job_id)
                if refreshed is not None:
                    normal_next_run_at = next_run_after(refreshed, now)
                    if normal_next_run_at is not None and normal_next_run_at < force_window[1]:
                        refreshed.next_run_at = normal_next_run_at
                        session.commit()
                        claimed_next_run_at = normal_next_run_at

            if run_all:
                refreshed = session.get(ScheduledJob, job_id)
                if refreshed is None:
                    continue
                refreshed.next_run_at = next_run_after(refreshed, now)
                session.commit()
                claimed_next_run_at = refreshed.next_run_at
            ran += 1
            log.info("job %s next_run_at=%s", job_id, claimed_next_run_at)
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
    parser.add_argument(
        "--lookahead-seconds",
        type=int,
        default=int(os.environ.get("SOOPERSCRAPER_RUNNER_LOOKAHEAD_SECONDS", "0")),
        help="Also run jobs due within this many seconds; useful for external cron dispatch jitter.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Log a safe summary of jobs visible to this runner, then exit.",
    )
    args = parser.parse_args(argv)

    configure_logging()
    if args.list:
        init_db()
        with SessionLocal() as session:
            log_job_summary(session)
        return 0
    count = run_due_jobs(
        include_failed=not args.active_only,
        limit=args.limit,
        run_all=args.all,
        lookahead_seconds=args.lookahead_seconds,
    )
    log.info("completed cloud runner; ran %s job(s)", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
