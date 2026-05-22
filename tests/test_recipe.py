from __future__ import annotations


def _make_job(session, **overrides):
    from app.models import ScheduledJob
    defaults = dict(
        name="recipe-test",
        description="testing",
        urls=["https://example.com/"],
        extractors=[{"name": "title", "selector": "h1", "attribute": "text", "multiple": False}],
        schedule_type="interval",
        schedule_config={"minutes": 5, "start_at": "00:03"},
        status="active",
        zip_records=True,
        sort_config={"field": "premium", "direction": "desc", "numeric": True},
    )
    defaults.update(overrides)
    job = ScheduledJob(**defaults)
    session.add(job)
    session.commit()
    return job


def test_dump_recipe_excludes_secrets(tmp_db, session):
    from app.recipe import dump_recipe
    job = _make_job(
        session,
        notify_config={
            "discord_webhook_url": "https://discord.com/api/webhooks/1/secret",
            "on_success": True,
            "on_error": True,
            "include_top_n": 5,
            "preview_fields": ["ticker", "side", "premium"],
            "alert_field": "premium",
            "alert_threshold": 50_000_000,
        },
        credentials_encrypted=b"opaque",
        cookies_encrypted=b"opaque",
    )
    recipe = dump_recipe(job)
    # Round-trip via JSON to confirm everything is JSON-safe.
    import json
    json.dumps(recipe)
    # Non-secret fields preserved:
    assert recipe["name"] == job.name
    assert recipe["urls"] == job.urls
    assert recipe["schedule"]["type"] == "interval"
    assert recipe["sort"]["field"] == "premium"
    assert recipe["notify"]["include_top_n"] == 5
    assert recipe["notify"]["preview_fields"] == ["ticker", "side", "premium"]
    # Secrets gone:
    assert "discord_webhook_url" not in recipe["notify"]
    assert "credentials_encrypted" not in recipe
    assert "cookies_encrypted" not in recipe


def test_load_recipe_creates_then_updates(tmp_db, session):
    from app.recipe import dump_recipe, load_recipe
    src = _make_job(session)
    recipe = dump_recipe(src)
    # Wipe and reload into a fresh DB-by-name match:
    from app.models import ScheduledJob
    session.delete(src)
    session.commit()
    # First load -> create
    job1 = load_recipe(session, recipe)
    assert job1.name == "recipe-test"
    assert session.query(ScheduledJob).filter_by(name="recipe-test").count() == 1
    # Second load with a tweak -> update existing
    recipe2 = dict(recipe)
    recipe2["description"] = "updated text"
    job2 = load_recipe(session, recipe2)
    assert job2.id == job1.id
    assert job2.description == "updated text"
    # Still only one row by name:
    assert session.query(ScheduledJob).filter_by(name="recipe-test").count() == 1


def test_load_recipe_rejects_unknown_version(tmp_db, session):
    import pytest
    from app.recipe import load_recipe
    with pytest.raises(ValueError):
        load_recipe(session, {"version": 999, "name": "x", "urls": [], "extractors": [], "schedule": {"type": "hourly"}})


def test_load_recipe_preserves_existing_webhook(tmp_db, session):
    """Recipes never carry the webhook URL, but loading shouldn't wipe one
    that the user already configured locally."""
    from app.recipe import dump_recipe, load_recipe
    src = _make_job(session, notify_config={
        "discord_webhook_url": "https://discord.com/api/webhooks/1/keep-me",
        "on_success": True, "on_error": True, "include_top_n": 5,
    })
    recipe = dump_recipe(src)
    # Reload the recipe over the same job — webhook URL on disk should survive.
    reloaded = load_recipe(session, recipe)
    assert reloaded.notify_config["discord_webhook_url"] == "https://discord.com/api/webhooks/1/keep-me"
    assert reloaded.notify_config["include_top_n"] == 5


def test_load_recipe_expands_env_vars(tmp_db, session, monkeypatch):
    from app.recipe import load_recipe
    monkeypatch.setenv("MYDAXA_URL", "https://example.com/page")
    recipe = {
        "version": 1,
        "name": "env-test",
        "urls": ["${MYDAXA_URL}"],
        "extractors": [{"name": "title", "selector": "h1"}],
        "schedule": {"type": "hourly"},
    }
    job = load_recipe(session, recipe)
    assert job.urls == ["https://example.com/page"]
