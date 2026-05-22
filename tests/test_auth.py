from __future__ import annotations

import os

import httpx
import pytest


# A fixed Fernet key used by all auth tests so we don't pay key-generation costs
# and so test failures are reproducible.
_TEST_KEY = "ulnv8Zw9YfTaIyjeMv2gPHcyFMqfYxiBlJqXxx-9CTU="


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("SOOPERSCRAPER_SECRET_KEY", _TEST_KEY)
    # Reset the cached Fernet so the new key takes effect.
    from app import crypto
    crypto.reset_for_tests()
    yield
    crypto.reset_for_tests()


def test_crypto_roundtrip(tmp_db, auth_env):
    from app import crypto
    token = crypto.encrypt_json({"username": "alice", "password": "s3cret"})
    assert token != b'{"username":"alice","password":"s3cret"}'  # not plaintext
    assert crypto.decrypt_json(token) == {"username": "alice", "password": "s3cret"}


def test_crypto_missing_key_raises(tmp_db, monkeypatch):
    monkeypatch.delenv("SOOPERSCRAPER_SECRET_KEY", raising=False)
    from app import crypto
    crypto.reset_for_tests()
    with pytest.raises(crypto.SecretKeyMissing):
        crypto.encrypt_json({"x": "y"})


def _payload_with_auth(**overrides):
    p = {
        "name": "auth-job",
        "urls": ["https://example.com/protected"],
        "extractors": [{"name": "title", "selector": "h1"}],
        "schedule": {"type": "daily", "hour": 9, "minute": 0},
        "auth": {
            "login_url": "https://example.com/login",
            "method": "post",
            "username_field": "user",
            "password_field": "pass",
            "extra_fields": {"submit": "Log In"},
            "success_check": {"type": "selector_absent", "value": "input[name='pass']"},
        },
        "credentials": {"username": "alice", "password": "s3cret"},
    }
    p.update(overrides)
    return p


def test_create_job_with_auth(client, auth_env):
    r = client.post("/api/jobs", json=_payload_with_auth())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_credentials"] is True
    assert body["auth"]["login_url"] == "https://example.com/login"
    # Password must NEVER appear anywhere in the response.
    assert "s3cret" not in r.text
    assert "password" not in body  # JobOut has no `password` field


def test_create_auth_without_credentials_rejected(client, auth_env):
    payload = _payload_with_auth()
    payload.pop("credentials")
    r = client.post("/api/jobs", json=payload)
    assert r.status_code == 422


def test_create_credentials_without_auth_rejected(client, auth_env):
    payload = _payload_with_auth()
    payload.pop("auth")
    r = client.post("/api/jobs", json=payload)
    assert r.status_code == 422


def test_set_credentials_endpoint(client, auth_env):
    job = client.post("/api/jobs", json=_payload_with_auth()).json()
    r = client.post(
        f"/api/jobs/{job['id']}/credentials",
        json={"username": "bob", "password": "new-pw"},
    )
    assert r.status_code == 200
    assert "new-pw" not in r.text
    assert r.json()["has_credentials"] is True


def test_set_credentials_requires_auth_config(client, auth_env):
    # Create a job WITHOUT auth, then try to set credentials -> 400.
    from tests.test_api import _create_payload
    job = client.post("/api/jobs", json=_create_payload()).json()
    r = client.post(
        f"/api/jobs/{job['id']}/credentials",
        json={"username": "x", "password": "y"},
    )
    assert r.status_code == 400


def test_clear_credentials(client, auth_env):
    job = client.post("/api/jobs", json=_payload_with_auth()).json()
    r = client.delete(f"/api/jobs/{job['id']}/credentials")
    assert r.status_code == 200
    assert r.json()["has_credentials"] is False


def test_clear_auth_via_patch(client, auth_env):
    job = client.post("/api/jobs", json=_payload_with_auth()).json()
    r = client.patch(f"/api/jobs/{job['id']}", json={"clear_auth": True})
    assert r.status_code == 200
    body = r.json()
    assert body["auth"] is None
    assert body["has_credentials"] is False


# ---- login flow & end-to-end run_job ---------------------------------------

LOGIN_PAGE_HTML = """
<html><body><form id="pp_login_3" method="post">
  <input name="user" type="text"/>
  <input name="pass" type="password"/>
  <input name="submit" type="submit" value="Log In"/>
</form></body></html>
"""

DASHBOARD_HTML = """
<html><body><h1>Welcome, Alice</h1><a href="/logout">Log out</a></body></html>
"""


def _make_login_transport(*, valid_user, valid_pass, login_url, dashboard_url):
    """Returns an httpx.MockTransport simulating a WordPress-style login."""
    cookies: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and str(request.url) == login_url:
            return httpx.Response(200, text=LOGIN_PAGE_HTML, request=request,
                                  headers={"set-cookie": "PHPSESSID=fresh; Path=/"})
        if request.method == "POST" and str(request.url) == login_url:
            form = dict(httpx.QueryParams(request.content.decode()))
            if form.get("user") == valid_user and form.get("pass") == valid_pass:
                cookies["wp_session"] = "valid"
                return httpx.Response(
                    302,
                    headers={"location": dashboard_url, "set-cookie": "wp_session=valid; Path=/"},
                    request=request,
                )
            return httpx.Response(200, text=LOGIN_PAGE_HTML, request=request)
        if request.method == "GET" and str(request.url) == dashboard_url:
            if cookies.get("wp_session") == "valid":
                return httpx.Response(200, text=DASHBOARD_HTML, request=request)
            return httpx.Response(200, text=LOGIN_PAGE_HTML, request=request)
        return httpx.Response(404, request=request)

    return httpx.MockTransport(handler)


def test_login_success(tmp_db, auth_env):
    from app import scraper
    auth = {
        "login_url": "https://example.test/login",
        "method": "post",
        "username_field": "user",
        "password_field": "pass",
        "extra_fields": {"submit": "Log In"},
        "success_check": {"type": "selector_absent", "value": "input[name='pass']"},
    }
    transport = _make_login_transport(
        valid_user="alice", valid_pass="s3cret",
        login_url="https://example.test/login",
        dashboard_url="https://example.test/dashboard",
    )
    with httpx.Client(transport=transport, follow_redirects=True) as c:
        authed = scraper.login(auth, {"username": "alice", "password": "s3cret"}, client=c)
        # And the authed client can reach the dashboard.
        resp = authed.get("https://example.test/dashboard")
        assert "Welcome" in resp.text


def test_login_failure_raises(tmp_db, auth_env):
    from app import scraper
    auth = {
        "login_url": "https://example.test/login",
        "method": "post",
        "username_field": "user",
        "password_field": "pass",
        "extra_fields": {"submit": "Log In"},
        "success_check": {"type": "selector_absent", "value": "input[name='pass']"},
    }
    transport = _make_login_transport(
        valid_user="alice", valid_pass="s3cret",
        login_url="https://example.test/login",
        dashboard_url="https://example.test/dashboard",
    )
    with httpx.Client(transport=transport, follow_redirects=True) as c:
        with pytest.raises(scraper.LoginFailed):
            scraper.login(auth, {"username": "alice", "password": "WRONG"}, client=c)


def test_run_job_with_auth_end_to_end(tmp_db, auth_env, session, monkeypatch):
    from app import crypto, scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="protected",
        urls=["https://example.test/dashboard"],
        extractors=[{"name": "heading", "selector": "h1"}],
        schedule_type="hourly",
        schedule_config={},
        auth_config={
            "login_url": "https://example.test/login",
            "method": "post",
            "username_field": "user",
            "password_field": "pass",
            "extra_fields": {"submit": "Log In"},
            "success_check": {"type": "selector_absent", "value": "input[name='pass']"},
        },
        credentials_encrypted=crypto.encrypt_json({"username": "alice", "password": "s3cret"}),
        status="active",
    )
    session.add(job)
    session.commit()

    transport = _make_login_transport(
        valid_user="alice", valid_pass="s3cret",
        login_url="https://example.test/login",
        dashboard_url="https://example.test/dashboard",
    )

    # Patch the scraper to use our mock transport for both login() and fetch().
    real_login = scraper.login
    real_fetch = scraper.fetch

    def login_patched(auth, creds, **kw):
        c = httpx.Client(transport=transport, follow_redirects=True)
        try:
            return real_login(auth, creds, client=c)
        except Exception:
            c.close()
            raise

    def fetch_patched(url, **kw):
        # Use the authed client passed in (cookies live there); httpx MockTransport
        # is on the client, so fetch will route through it.
        return real_fetch(url, **kw)

    monkeypatch.setattr(scraper, "login", login_patched)
    monkeypatch.setattr(scraper, "fetch", fetch_patched)

    run = scraper.run_job(session, job.id)
    assert run.status == "success", run.results
    assert run.results[0]["data"] == {"heading": "Welcome, Alice"}


def test_parse_cookie_string(tmp_db):
    from app.schemas import _parse_cookie_string
    out = _parse_cookie_string("a=1; b=2;  c=three=equals")
    assert out == {"a": "1", "b": "2", "c": "three=equals"}


def test_parse_cookie_string_newline_separated(tmp_db):
    from app.schemas import _parse_cookie_string
    out = _parse_cookie_string("a=1\nb=2")
    assert out == {"a": "1", "b": "2"}


def test_parse_cookie_string_rejects_garbage(tmp_db):
    import pytest
    from app.schemas import _parse_cookie_string
    with pytest.raises(ValueError):
        _parse_cookie_string("not a cookie")
    with pytest.raises(ValueError):
        _parse_cookie_string("")


def test_create_job_with_cookies(client, auth_env):
    payload = {
        "name": "cookie-job",
        "urls": ["https://example.com/protected"],
        "extractors": [{"name": "title", "selector": "h1"}],
        "schedule": {"type": "daily", "hour": 9, "minute": 0},
        "cookies": {"raw": "session=abc123; tracking=xyz"},
    }
    r = client.post("/api/jobs", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["has_cookies"] is True
    # Cookie values must never appear in the response.
    assert "abc123" not in r.text
    assert "xyz" not in r.text


def test_set_cookies_endpoint(client, auth_env):
    from tests.test_api import _create_payload
    job = client.post("/api/jobs", json=_create_payload()).json()
    r = client.post(
        f"/api/jobs/{job['id']}/cookies",
        json={"raw": "wordpress_logged_in_xyz=abc; other=1"},
    )
    assert r.status_code == 200
    assert "abc" not in r.text
    assert r.json()["has_cookies"] is True


def test_clear_cookies(client, auth_env):
    from tests.test_api import _create_payload
    job = client.post("/api/jobs", json=_create_payload()).json()
    client.post(f"/api/jobs/{job['id']}/cookies", json={"raw": "a=1"})
    r = client.delete(f"/api/jobs/{job['id']}/cookies")
    assert r.status_code == 200
    assert r.json()["has_cookies"] is False


def test_run_job_with_cookies_only(tmp_db, auth_env, session, monkeypatch):
    """Cookie auth alone (no form login): cookies are present on the fetch."""
    from app import crypto, scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="cookies-only",
        urls=["https://example.test/protected"],
        extractors=[{"name": "h", "selector": "h1"}],
        schedule_type="hourly",
        schedule_config={},
        cookies_encrypted=crypto.encrypt_json({"cookies": {"session": "valid"}}),
        status="active",
    )
    session.add(job)
    session.commit()

    seen_cookies = {}

    def fake_fetch(url, *, client=None, **kw):
        # Capture whatever cookies the scraper put on the client jar.
        if client is not None:
            for c in client.cookies.jar:
                seen_cookies[c.name] = c.value
        return "<h1>hi</h1>"

    monkeypatch.setattr(scraper, "fetch", fake_fetch)
    run = scraper.run_job(session, job.id)
    assert run.status == "success"
    assert seen_cookies.get("session") == "valid"


def test_run_job_login_failure_marks_run_error(tmp_db, auth_env, session, monkeypatch):
    from app import crypto, scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="protected",
        urls=["https://example.test/dashboard"],
        extractors=[{"name": "heading", "selector": "h1"}],
        schedule_type="hourly",
        schedule_config={},
        auth_config={
            "login_url": "https://example.test/login",
            "method": "post",
            "username_field": "user",
            "password_field": "pass",
            "extra_fields": {},
            "success_check": {"type": "selector_absent", "value": "input[name='pass']"},
        },
        credentials_encrypted=crypto.encrypt_json({"username": "alice", "password": "WRONG"}),
        status="active",
    )
    session.add(job)
    session.commit()

    transport = _make_login_transport(
        valid_user="alice", valid_pass="s3cret",
        login_url="https://example.test/login",
        dashboard_url="https://example.test/dashboard",
    )

    real_login = scraper.login

    def login_patched(auth, creds, **kw):
        c = httpx.Client(transport=transport, follow_redirects=True)
        try:
            return real_login(auth, creds, client=c)
        except Exception:
            c.close()
            raise

    monkeypatch.setattr(scraper, "login", login_patched)

    run = scraper.run_job(session, job.id)
    assert run.status == "error"
    assert run.error and "LoginFailed" in run.error
    # Each configured URL appears in results with the login error message.
    assert all(not r["ok"] for r in run.results)
