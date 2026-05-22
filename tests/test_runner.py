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


def test_run_due_jobs_uses_lookahead_for_near_future_job(tmp_db, monkeypatch, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    job = ScheduledJob(
        name="near-future",
        urls=["http://example.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="interval",
        schedule_config={"minutes": 30},
        status="active",
        next_run_at=now + timedelta(minutes=2),
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr("app.scraper.fetch", lambda url, **kw: "<h1>ok</h1>")

    ran = runner.run_due_jobs(session=session, now=now, lookahead_seconds=180)

    session.refresh(job)
    assert ran == 1
    assert job.last_run_at is not None
    assert runner._as_aware_utc(job.next_run_at) > now + timedelta(minutes=2)


def test_claim_due_job_skips_when_another_runner_advanced_it(tmp_db, session):
    from app import runner
    from app.db import SessionLocal
    from app.models import ScheduledJob

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    job = ScheduledJob(
        name="claimed",
        urls=["http://example.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="interval",
        schedule_config={"minutes": 30},
        status="active",
        next_run_at=now - timedelta(minutes=1),
    )
    session.add(job)
    session.commit()

    with SessionLocal() as stale_session, SessionLocal() as fresh_session:
        stale_job = stale_session.get(ScheduledJob, job.id)
        fresh_job = fresh_session.get(ScheduledJob, job.id)
        assert stale_job is not None and fresh_job is not None

        fresh_job.next_run_at = now + timedelta(minutes=30)
        fresh_session.commit()

        claimed = runner._claim_due_job(
            stale_session,
            stale_job,
            now,
            include_failed=True,
            lookahead=timedelta(),
        )

    assert claimed is None


def test_run_due_jobs_skips_beyond_lookahead(tmp_db, monkeypatch, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 21, 12, 0, tzinfo=timezone.utc)
    session.add(
        ScheduledJob(
            name="too-far",
            urls=["http://example.test/"],
            extractors=[{"name": "title", "selector": "h1"}],
            schedule_type="interval",
            schedule_config={"minutes": 30},
            status="active",
            next_run_at=now + timedelta(minutes=4),
        )
    )
    session.commit()

    called = False

    def fake_fetch(url, **kw):
        nonlocal called
        called = True
        return "<h1>ok</h1>"

    monkeypatch.setattr("app.scraper.fetch", fake_fetch)

    assert runner.run_due_jobs(session=session, now=now, lookahead_seconds=180) == 0
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


def test_next_run_after_catches_up_stale_interval(tmp_db, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 22, 3, 33, tzinfo=timezone.utc)
    job = ScheduledJob(
        name="stale",
        urls=["http://example.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="interval",
        schedule_config={"minutes": 30, "start_at": "00:03"},
        status="active",
        next_run_at=datetime(2026, 5, 22, 1, 47, tzinfo=timezone.utc),
    )

    assert runner.next_run_after(job, now) == datetime(2026, 5, 22, 3, 47, tzinfo=timezone.utc)


def test_next_run_after_catches_up_stale_cron(tmp_db, session):
    from app import runner
    from app.models import ScheduledJob

    now = datetime(2026, 5, 22, 3, 34, tzinfo=timezone.utc)
    job = ScheduledJob(
        name="stale-cron",
        urls=["http://example.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="cron",
        schedule_config={"expression": "3,33 * * * *"},
        status="active",
        next_run_at=datetime(2026, 5, 22, 1, 47, tzinfo=timezone.utc),
    )

    assert runner.next_run_after(job, now) == datetime(2026, 5, 22, 4, 3, tzinfo=timezone.utc)
