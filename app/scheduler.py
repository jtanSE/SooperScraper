from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .db import session_scope
from .models import ScheduledJob


log = logging.getLogger(__name__)


_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    if _scheduler is None:
        raise RuntimeError("scheduler not started")
    return _scheduler


def _job_id(job_id: int) -> str:
    return f"job-{job_id}"


def trigger_from_schedule(schedule_type: str, schedule_config: dict[str, Any]) -> BaseTrigger:
    """Convert our stored schedule into an APScheduler trigger.

    Pydantic has already validated the inputs at the API boundary, but we
    re-check the shape here so calls from internal code (e.g. hydrate) fail
    loudly on corrupt rows instead of silently misfiring.
    """
    if schedule_type == "hourly":
        return IntervalTrigger(hours=1, timezone=timezone.utc)
    if schedule_type == "interval":
        minutes = int(schedule_config["minutes"])
        start_at = schedule_config.get("start_at")
        start_date = None
        if start_at:
            hh, mm = (int(p) for p in start_at.split(":"))
            now = datetime.now(timezone.utc)
            anchor = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            # If today's anchor is in the future, APScheduler will fire there first;
            # if in the past, it computes the next aligned tick forward.
            start_date = anchor
        return IntervalTrigger(minutes=minutes, start_date=start_date, timezone=timezone.utc)
    if schedule_type == "daily":
        return CronTrigger(
            hour=schedule_config["hour"],
            minute=schedule_config["minute"],
            timezone=timezone.utc,
        )
    if schedule_type == "weekly":
        return CronTrigger(
            day_of_week=schedule_config["day_of_week"],
            hour=schedule_config["hour"],
            minute=schedule_config["minute"],
            timezone=timezone.utc,
        )
    if schedule_type == "cron":
        return CronTrigger.from_crontab(schedule_config["expression"], timezone=timezone.utc)
    raise ValueError(f"unknown schedule_type: {schedule_type!r}")


def _run_job_entrypoint(job_id: int) -> None:
    """Top-level callable APScheduler invokes. Has its own DB session."""
    from .scraper import run_job  # local import to avoid import cycles at module load

    try:
        with session_scope() as session:
            run_job(session, job_id)
    except LookupError:
        # Job was deleted between scheduling and firing — remove from scheduler.
        log.info("job %s no longer exists; removing from scheduler", job_id)
        try:
            get_scheduler().remove_job(_job_id(job_id))
        except Exception:  # pragma: no cover - defensive
            pass
    except Exception:
        log.exception("unexpected error running job %s", job_id)


def start() -> None:
    """Start the background scheduler and hydrate it from the DB."""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    hydrate_from_db()


def stop() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None


def hydrate_from_db() -> None:
    """Schedule all active jobs from the database."""
    with session_scope() as session:
        jobs = session.query(ScheduledJob).all()
        for job in jobs:
            if job.status == "active":
                try:
                    _upsert(job)
                except Exception:
                    log.exception("failed to schedule job %s on hydrate", job.id)


def _upsert(job: ScheduledJob) -> None:
    sched = get_scheduler()
    trigger = trigger_from_schedule(job.schedule_type, job.schedule_config)
    sched.add_job(
        _run_job_entrypoint,
        trigger=trigger,
        id=_job_id(job.id),
        args=[job.id],
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def reschedule(job: ScheduledJob) -> None:
    """Add or update the APScheduler entry for `job` and write back next_run_at.

    Caller is responsible for committing the session that owns `job` after this.
    """
    if job.status != "active":
        remove(job)
        job.next_run_at = None
        return
    _upsert(job)
    apjob = get_scheduler().get_job(_job_id(job.id))
    job.next_run_at = apjob.next_run_time if apjob else None


def pause(job: ScheduledJob) -> None:
    sched = get_scheduler()
    try:
        sched.pause_job(_job_id(job.id))
    except Exception:
        # Job may not be registered (e.g. was previously failed); add it paused.
        trigger = trigger_from_schedule(job.schedule_type, job.schedule_config)
        sched.add_job(
            _run_job_entrypoint,
            trigger=trigger,
            id=_job_id(job.id),
            args=[job.id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        sched.pause_job(_job_id(job.id))
    job.status = "paused"
    job.next_run_at = None


def resume(job: ScheduledJob) -> None:
    job.status = "active"
    _upsert(job)
    apjob = get_scheduler().get_job(_job_id(job.id))
    job.next_run_at = apjob.next_run_time if apjob else None


def remove(job: ScheduledJob) -> None:
    try:
        get_scheduler().remove_job(_job_id(job.id))
    except Exception:
        # Already absent — fine.
        pass


def trigger_now(job: ScheduledJob) -> None:
    """Fire the job once, asynchronously, via the scheduler."""
    sched = get_scheduler()
    sched.add_job(
        _run_job_entrypoint,
        args=[job.id],
        id=f"{_job_id(job.id)}-manual-{int(job.id)}-{id(job)}",
        replace_existing=False,
        max_instances=1,
        misfire_grace_time=None,
    )
