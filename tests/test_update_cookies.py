from __future__ import annotations


_TEST_KEY = "T3b9ZGJ8m5zWIrpI-rO_CMO_QbHtf5K67aS-A58QFFY="


def test_update_job_cookies(tmp_db, session, monkeypatch):
    monkeypatch.setenv("SOOPERSCRAPER_SECRET_KEY", _TEST_KEY)

    from app import crypto
    from app.models import ScheduledJob
    from app.update_cookies import update_job_cookies

    crypto.reset_for_tests()

    job = ScheduledJob(
        name="DAXA",
        urls=["https://mydaxa.com/us-darkpool-trades/"],
        extractors=[],
        schedule_type="hourly",
        schedule_config={},
        status="active",
    )
    session.add(job)
    session.commit()

    job_id = update_job_cookies("DAXA", "wordpress_logged_in_x=abc; other=123")

    session.refresh(job)
    assert job_id == job.id
    assert job.cookies_encrypted is not None
    assert crypto.decrypt_json(job.cookies_encrypted) == {
        "cookies": {"wordpress_logged_in_x": "abc", "other": "123"}
    }
