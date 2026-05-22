from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_run_due_jobs_runs_due_active_job(tmp_db, monkeypatch, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    job = ScheduledJob(
        name="due",
        urls=["http://example.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="interval",
        schedule_config={"minutes": 30},
        status="active",
        next_run_at=now - timedelta(minutes=1),
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr("app.scraper.fetch", lambda url, **kw: "<h1>ok</h1>")

    ran = runner.run_due_jobs(session=session, now=now)

    session.refresh(job)
    assert ran == 1
    assert job.last_run_at is not None
    assert job.next_run_at is not None
    assert runner._as_aware_utc(job.next_run_at) > now


def test_run_due_jobs_skips_paused_and_future_jobs(tmp_db, monkeypatch, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    for name, status, next_run_at in [
        ("paused", "paused", now - timedelta(minutes=1)),
        ("future", "active", now + timedelta(minutes=1)),
    ]:
        session.add(
            ScheduledJob(
                name=name,
                urls=["http://example.test/"],
                extractors=[{"name": "title", "selector": "h1"}],
                schedule_type="interval",
                schedule_config={"minutes": 30},
                status=status,
                next_run_at=next_run_at,
            )
        )
    session.commit()

    called = False

    def fake_fetch(url, **kw):
        nonlocal called
        called = True
        return "<h1>ok</h1>"

    monkeypatch.setattr("app.scraper.fetch", fake_fetch)

    assert runner.run_due_jobs(session=session, now=now) == 0
    assert called is False


def test_run_due_jobs_retries_failed_jobs_by_default(tmp_db, monkeypatch, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    job = ScheduledJob(
        name="failed",
        urls=["http://example.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="interval",
        schedule_config={"minutes": 30},
        status="failed",
        next_run_at=now - timedelta(minutes=1),
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr("app.scraper.fetch", lambda url, **kw: "<h1>ok</h1>")

    assert runner.run_due_jobs(session=session, now=now) == 1
    session.refresh(job)
    assert job.status == "active"


def test_run_due_jobs_all_runs_future_active_job(tmp_db, monkeypatch, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    job = ScheduledJob(
        name="future",
        urls=["http://example.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="interval",
        schedule_config={"minutes": 30},
        status="active",
        next_run_at=now + timedelta(hours=1),
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr("app.scraper.fetch", lambda url, **kw: "<h1>ok</h1>")

    assert runner.run_due_jobs(session=session, now=now, run_all=True) == 1
    session.refresh(job)
    assert job.last_run_at is not None
