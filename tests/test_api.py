from __future__ import annotations


def _create_payload(**overrides):
    p = {
        "name": "example",
        "description": "scrape example.com",
        "urls": ["https://example.com/"],
        "extractors": [{"name": "title", "selector": "h1"}],
        "schedule": {"type": "daily", "hour": 9, "minute": 0},
    }
    p.update(overrides)
    return p


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_create_and_get(client):
    r = client.post("/api/jobs", json=_create_payload())
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["id"] > 0
    assert job["status"] == "active"
    assert job["schedule_type"] == "daily"
    assert job["schedule_config"] == {"hour": 9, "minute": 0}
    assert job["next_run_at"] is not None  # scheduler computed it

    r = client.get(f"/api/jobs/{job['id']}")
    assert r.status_code == 200
    assert r.json()["name"] == "example"


def test_list_jobs(client):
    client.post("/api/jobs", json=_create_payload(name="a"))
    client.post("/api/jobs", json=_create_payload(name="b"))
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert {j["name"] for j in r.json()} == {"a", "b"}


def test_patch_updates_schedule(client):
    job = client.post("/api/jobs", json=_create_payload()).json()
    r = client.patch(
        f"/api/jobs/{job['id']}",
        json={"schedule": {"type": "cron", "expression": "*/30 * * * *"}},
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["schedule_type"] == "cron"
    assert updated["schedule_config"] == {"expression": "*/30 * * * *"}


def test_pause_resume(client):
    job = client.post("/api/jobs", json=_create_payload()).json()

    r = client.post(f"/api/jobs/{job['id']}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"
    assert r.json()["next_run_at"] is None

    r = client.post(f"/api/jobs/{job['id']}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json()["next_run_at"] is not None


def test_delete(client):
    job = client.post("/api/jobs", json=_create_payload()).json()
    r = client.delete(f"/api/jobs/{job['id']}")
    assert r.status_code == 204
    r = client.get(f"/api/jobs/{job['id']}")
    assert r.status_code == 404


def test_404_on_missing_job(client):
    assert client.get("/api/jobs/9999").status_code == 404
    assert client.patch("/api/jobs/9999", json={"name": "x"}).status_code == 404
    assert client.delete("/api/jobs/9999").status_code == 404
    assert client.post("/api/jobs/9999/pause").status_code == 404


def test_validation_no_urls(client):
    r = client.post("/api/jobs", json=_create_payload(urls=[]))
    assert r.status_code == 422


def test_validation_bad_url(client):
    r = client.post("/api/jobs", json=_create_payload(urls=["not-a-url"]))
    assert r.status_code == 422


def test_validation_no_extractors(client):
    r = client.post("/api/jobs", json=_create_payload(extractors=[]))
    assert r.status_code == 422


def test_validation_duplicate_extractor_names(client):
    r = client.post("/api/jobs", json=_create_payload(extractors=[
        {"name": "x", "selector": "h1"},
        {"name": "x", "selector": "h2"},
    ]))
    assert r.status_code == 422


def test_create_with_interval_schedule(client):
    r = client.post("/api/jobs", json=_create_payload(
        schedule={"type": "interval", "minutes": 30},
    ))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["schedule_type"] == "interval"
    assert body["schedule_config"] == {"minutes": 30}
    assert body["next_run_at"] is not None


def test_interval_minutes_must_be_positive(client):
    r = client.post("/api/jobs", json=_create_payload(
        schedule={"type": "interval", "minutes": 0},
    ))
    assert r.status_code == 422


def test_validation_bad_cron(client):
    r = client.post(
        "/api/jobs",
        json=_create_payload(schedule={"type": "cron", "expression": "garbage"}),
    )
    assert r.status_code == 422


def test_runs_endpoint_empty(client):
    job = client.post("/api/jobs", json=_create_payload()).json()
    r = client.get(f"/api/jobs/{job['id']}/runs")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


def test_runs_endpoint_lists_recent_first(client, monkeypatch):
    """Insert two runs directly and verify ordering + pagination."""
    from app import scraper
    from app.db import SessionLocal
    from app.models import JobRun, ScheduledJob

    job = client.post("/api/jobs", json=_create_payload()).json()

    # Drive a real run_job that uses a fake fetch.
    monkeypatch.setattr(scraper, "fetch", lambda url, **kw: "<h1>hi</h1>")
    with SessionLocal() as s:
        scraper.run_job(s, job["id"])
        scraper.run_job(s, job["id"])

    r = client.get(f"/api/jobs/{job['id']}/runs?limit=1")
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "success"

    r = client.get(f"/api/jobs/{job['id']}/runs?limit=1&offset=1")
    assert len(r.json()["items"]) == 1
