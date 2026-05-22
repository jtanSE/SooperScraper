from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest


class _FakeRun:
    def __init__(self, status, duration_ms=5000, error=None, results=None):
        self.status = status
        self.duration_ms = duration_ms
        self.error = error
        self.results = results or [{"url": "https://example.com/", "ok": True, "data": {"records": [{"x": 1}, {"x": 2}]}}]
        self.started_at = datetime(2026, 5, 21, 22, 0, tzinfo=timezone.utc)
        self.finished_at = datetime(2026, 5, 21, 22, 0, 5, tzinfo=timezone.utc)


class _FakeJob:
    id = 7
    name = "test-job"


def test_payload_success(tmp_db):
    from app.notifications import _build_payload
    payload = _build_payload(_FakeJob(), _FakeRun("success"))
    embed = payload["embeds"][0]
    assert "test-job" in embed["title"]
    assert "success" in embed["title"]
    assert embed["color"] == 0x16A34A
    names = [f["name"] for f in embed["fields"]]
    assert "Status" in names and "Duration" in names and "URLs" in names and "Result" in names


def test_payload_includes_top_n(tmp_db):
    from app.notifications import _build_payload
    results = [{
        "url": "https://x/", "ok": True,
        "data": {"records": [
            {"ticker": "AAA", "premium": "$1,000"},
            {"ticker": "BBB", "premium": "$500"},
            {"ticker": "CCC", "premium": "$250"},
            {"ticker": "DDD", "premium": "$100"},
        ]},
    }]
    run = _FakeRun("success", results=results)
    payload = _build_payload(_FakeJob(), run, {"include_top_n": 2})
    field = next(f for f in payload["embeds"][0]["fields"] if f["name"].startswith("Top "))
    assert "AAA" in field["value"] and "BBB" in field["value"]
    assert "CCC" not in field["value"]


def test_preview_fields_order_and_filter(tmp_db):
    from app.notifications import _format_record
    record = {
        "date": "05/21", "ticker": "NVDA", "size": "11,127", "premium": "2,445,965.55",
        "price": "219.83", "trade_code": "", "settlement": "regular",
        "volume": "200M", "nbbo_bid": "219.82", "nbbo_ask": "219.85", "side": "Buy",
    }
    out = _format_record(record, preview_fields=["ticker", "side", "premium", "size"])
    # Fields appear in the requested order, others omitted, and side has the emoji.
    assert out.index("NVDA") < out.index("Buy") < out.index("2,445,965.55") < out.index("11,127")
    assert "🟢" in out
    assert "date" not in out  # not in preview list -> not shown


def test_decorate_value_sell_red(tmp_db):
    from app.notifications import _decorate_value
    assert "🔴" in _decorate_value("side", "Sell")


def test_payload_with_preview_fields(tmp_db):
    from app.notifications import _build_payload
    records = [{"ticker": "AAA", "side": "Buy", "premium": "$1,000", "noise": "ignored"}]
    run = _FakeRun("success", results=[{"url": "https://x/", "ok": True, "data": {"records": records}}])
    payload = _build_payload(
        _FakeJob(), run,
        {"include_top_n": 1, "preview_fields": ["ticker", "side", "premium"]},
    )
    field = next(f for f in payload["embeds"][0]["fields"] if f["name"].startswith("Top"))
    assert "noise" not in field["value"]
    # preview order respected
    assert field["value"].index("AAA") < field["value"].index("Buy") < field["value"].index("$1,000")


def test_payload_threshold_alerts(tmp_db):
    from app.notifications import _build_payload
    results = [{
        "url": "https://x/", "ok": True,
        "data": {"records": [
            {"ticker": "BIG1", "premium": "$80,000,000"},
            {"ticker": "small", "premium": "$1,000"},
            {"ticker": "BIG2", "premium": "$55,500,000"},
        ]},
    }]
    run = _FakeRun("success", results=results)
    payload = _build_payload(
        _FakeJob(), run,
        {"alert_field": "premium", "alert_threshold": 50_000_000},
    )
    alert = next(f for f in payload["embeds"][0]["fields"] if f["name"].startswith("Alert:"))
    assert "2 match" in alert["name"]
    assert "BIG1" in alert["value"] and "BIG2" in alert["value"]
    assert "small" not in alert["value"]


def test_top_n_excludes_alert_records(tmp_db):
    """When alerts are configured, top_n shows records NOT already in alerts."""
    from app.notifications import _build_payload
    # 8 records sorted desc by premium: first 6 above $50M, last 2 below.
    records = [
        {"ticker": "T1", "premium": "$80,000,000"},
        {"ticker": "T2", "premium": "$70,000,000"},
        {"ticker": "T3", "premium": "$65,000,000"},
        {"ticker": "T4", "premium": "$60,000,000"},
        {"ticker": "T5", "premium": "$55,000,000"},
        {"ticker": "T6", "premium": "$51,000,000"},
        {"ticker": "T7", "premium": "$10,000"},
        {"ticker": "T8", "premium": "$5,000"},
    ]
    run = _FakeRun("success", results=[{"url": "https://x/", "ok": True, "data": {"records": records}}])
    payload = _build_payload(_FakeJob(), run, {
        "include_top_n": 5,
        "alert_field": "premium",
        "alert_threshold": 50_000_000,
    })
    fields = payload["embeds"][0]["fields"]
    top = next(f for f in fields if f["name"].startswith("Top"))
    alert = next(f for f in fields if f["name"].startswith("Alert"))

    # Alert section shows all 6 above-threshold trades.
    assert "6 match" in alert["name"]
    for t in ("T1", "T2", "T3", "T4", "T5", "T6"):
        assert t in alert["value"]

    # Top section shows the rest (only T7 and T8 left — 2 records, not 5).
    assert "T7" in top["value"] and "T8" in top["value"]
    for t in ("T1", "T2", "T3", "T4", "T5", "T6"):
        assert t not in top["value"]
    assert "excluding alerts" in top["name"]


def test_top_n_unchanged_when_no_alerts(tmp_db):
    from app.notifications import _build_payload
    records = [{"ticker": f"T{i}", "premium": "$100"} for i in range(8)]
    run = _FakeRun("success", results=[{"url": "https://x/", "ok": True, "data": {"records": records}}])
    payload = _build_payload(_FakeJob(), run, {"include_top_n": 3})
    top = next(f for f in payload["embeds"][0]["fields"] if f["name"].startswith("Top"))
    assert "T0" in top["value"] and "T1" in top["value"] and "T2" in top["value"]
    assert "excluding alerts" not in top["name"]


def test_payload_no_alerts_when_threshold_unmet(tmp_db):
    from app.notifications import _build_payload
    results = [{"url": "https://x/", "ok": True,
                "data": {"records": [{"ticker": "x", "premium": "$1"}]}}]
    run = _FakeRun("success", results=results)
    payload = _build_payload(
        _FakeJob(), run,
        {"alert_field": "premium", "alert_threshold": 50_000_000},
    )
    names = [f["name"] for f in payload["embeds"][0]["fields"]]
    assert not any(n.startswith("Alert:") for n in names)


def test_payload_error_includes_error_block(tmp_db):
    from app.notifications import _build_payload
    payload = _build_payload(_FakeJob(), _FakeRun("error", error="LoginFailed: bad creds\nstacktrace..."))
    embed = payload["embeds"][0]
    assert embed["color"] == 0xDC2626
    error_field = next(f for f in embed["fields"] if f["name"] == "Error")
    assert "LoginFailed" in error_field["value"]


def test_notify_run_respects_on_success_false(tmp_db, monkeypatch):
    from app import notifications
    called = {"count": 0}
    def fake_post(*a, **kw):
        called["count"] += 1
        return httpx.Response(204, request=httpx.Request("POST", a[0]))
    monkeypatch.setattr(httpx, "post", fake_post)

    cfg = {"discord_webhook_url": "https://discord.com/api/webhooks/1/abc",
           "on_success": False, "on_error": True}
    notifications.notify_run(cfg, _FakeJob(), _FakeRun("success"))
    assert called["count"] == 0  # success suppressed

    notifications.notify_run(cfg, _FakeJob(), _FakeRun("error", error="boom"))
    assert called["count"] == 1  # error fired


def test_notify_run_swallows_post_errors(tmp_db, monkeypatch):
    from app import notifications

    def raising_post(*a, **kw):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "post", raising_post)
    cfg = {"discord_webhook_url": "https://discord.com/api/webhooks/1/abc"}
    # Should not raise:
    ok = notifications.notify_run(cfg, _FakeJob(), _FakeRun("success"))
    assert ok is False


def test_create_job_with_notify(client):
    payload = {
        "name": "notif-job",
        "urls": ["https://example.com/"],
        "extractors": [{"name": "h", "selector": "h1"}],
        "schedule": {"type": "daily", "hour": 9, "minute": 0},
        "notify": {
            "discord_webhook_url": "https://discord.com/api/webhooks/123/token-abc",
            "on_success": True,
            "on_error": True,
        },
    }
    r = client.post("/api/jobs", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["notify"]["discord_webhook_url"].startswith("https://discord.com/")


def test_notify_config_accepts_missing_webhook_url(tmp_db):
    """A NotifyConfig with no URL is valid — preferences can be configured
    without the URL, which gets pasted via the UI per machine."""
    from app.schemas import NotifyConfig
    nc = NotifyConfig.model_validate({
        "on_success": True,
        "on_error": True,
        "include_top_n": 5,
        "preview_fields": ["ticker", "side"],
    })
    assert nc.discord_webhook_url is None
    assert nc.include_top_n == 5


def test_notify_run_silent_when_webhook_missing(tmp_db, monkeypatch):
    """Runtime: notifications are skipped (not raised) when no URL is set."""
    from app import notifications
    called = {"count": 0}
    monkeypatch.setattr(__import__("httpx"), "post", lambda *a, **kw: called.__setitem__("count", called["count"] + 1))
    sent = notifications.notify_run(
        {"on_success": True, "on_error": True},  # no discord_webhook_url
        _FakeJob(), _FakeRun("success"),
    )
    assert sent is False
    assert called["count"] == 0


def test_create_job_with_bad_webhook_url(client):
    r = client.post("/api/jobs", json={
        "name": "bad",
        "urls": ["https://example.com/"],
        "extractors": [{"name": "h", "selector": "h1"}],
        "schedule": {"type": "daily", "hour": 9, "minute": 0},
        "notify": {"discord_webhook_url": "https://evil.example.com/wh"},
    })
    assert r.status_code == 422


def test_run_job_sends_notification(tmp_db, session, monkeypatch):
    from app import scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="t",
        urls=["http://good.test/"],
        extractors=[{"name": "h", "selector": "h1"}],
        schedule_type="hourly",
        schedule_config={},
        status="active",
        notify_config={
            "discord_webhook_url": "https://discord.com/api/webhooks/1/abc",
            "on_success": True,
            "on_error": True,
        },
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr(scraper, "fetch", lambda url, **kw: "<h1>hi</h1>")
    seen = {"calls": []}
    def fake_post(url, *, json=None, timeout=None):
        seen["calls"].append((url, json))
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    run = scraper.run_job(session, job.id)
    assert run.status == "success"
    assert len(seen["calls"]) == 1
    url, body = seen["calls"][0]
    assert url == job.notify_config["discord_webhook_url"]
    assert body["embeds"][0]["title"].startswith(job.name)


def test_clear_notify_via_patch(client):
    payload = {
        "name": "x",
        "urls": ["https://example.com/"],
        "extractors": [{"name": "h", "selector": "h1"}],
        "schedule": {"type": "daily", "hour": 9, "minute": 0},
        "notify": {"discord_webhook_url": "https://discord.com/api/webhooks/1/abc"},
    }
    job = client.post("/api/jobs", json=payload).json()
    assert job["notify"] is not None
    r = client.patch(f"/api/jobs/{job['id']}", json={"clear_notify": True})
    assert r.status_code == 200
    assert r.json()["notify"] is None
