from __future__ import annotations

import pytest


def test_trigger_hourly(tmp_db):
    from apscheduler.triggers.interval import IntervalTrigger
    from app.scheduler import trigger_from_schedule

    trig = trigger_from_schedule("hourly", {})
    assert isinstance(trig, IntervalTrigger)


def test_trigger_interval(tmp_db):
    from apscheduler.triggers.interval import IntervalTrigger
    from app.scheduler import trigger_from_schedule

    trig = trigger_from_schedule("interval", {"minutes": 30})
    assert isinstance(trig, IntervalTrigger)
    # IntervalTrigger stores its interval as a timedelta
    assert trig.interval.total_seconds() == 30 * 60


def test_trigger_interval_with_start_at_aligns_minutes(tmp_db):
    from datetime import datetime, timezone

    from apscheduler.triggers.interval import IntervalTrigger
    from app.scheduler import trigger_from_schedule

    trig = trigger_from_schedule("interval", {"minutes": 5, "start_at": "00:03"})
    assert isinstance(trig, IntervalTrigger)
    # start_date carries the HH:MM anchor; computed next fire should land on a
    # minute that is congruent to 3 mod 5.
    next_fire = trig.get_next_fire_time(None, datetime.now(timezone.utc))
    assert next_fire is not None
    assert next_fire.minute % 5 == 3


def test_trigger_daily(tmp_db):
    from apscheduler.triggers.cron import CronTrigger
    from app.scheduler import trigger_from_schedule

    trig = trigger_from_schedule("daily", {"hour": 9, "minute": 30})
    assert isinstance(trig, CronTrigger)
    # CronTrigger doesn't expose hour/minute as attributes directly; spot-check str().
    s = str(trig)
    assert "hour='9'" in s and "minute='30'" in s


def test_trigger_weekly(tmp_db):
    from apscheduler.triggers.cron import CronTrigger
    from app.scheduler import trigger_from_schedule

    trig = trigger_from_schedule("weekly", {"day_of_week": "mon", "hour": 7, "minute": 0})
    assert isinstance(trig, CronTrigger)
    assert "day_of_week='mon'" in str(trig)


def test_trigger_cron(tmp_db):
    from apscheduler.triggers.cron import CronTrigger
    from app.scheduler import trigger_from_schedule

    trig = trigger_from_schedule("cron", {"expression": "*/15 * * * *"})
    assert isinstance(trig, CronTrigger)


def test_trigger_unknown_type(tmp_db):
    from app.scheduler import trigger_from_schedule
    with pytest.raises(ValueError):
        trigger_from_schedule("yearly", {})


def test_bad_cron_raises(tmp_db):
    from app.scheduler import trigger_from_schedule
    with pytest.raises(Exception):
        trigger_from_schedule("cron", {"expression": "not a cron"})
