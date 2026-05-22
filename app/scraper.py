from __future__ import annotations

import logging
import re
import traceback
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import crypto, notifications
from .config import HTTP_TIMEOUT, RUN_HISTORY_LIMIT, USER_AGENT
from .models import JobRun, ScheduledJob


log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"}


# httpx client is built per-call rather than reused so that timeouts and headers
# can be overridden in tests. The cost is negligible for scheduled jobs.
def fetch(url: str, *, timeout: float = HTTP_TIMEOUT, client: httpx.Client | None = None) -> str:
    headers = _default_headers()
    if client is None:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as c:
            resp = c.get(url)
    else:
        resp = client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text


class LoginFailed(Exception):
    """Raised when the success_check on a login response does not pass."""


def _check_login_success(check: dict[str, Any], resp: httpx.Response) -> None:
    kind = check["type"]
    value = check["value"]
    if kind == "url_contains":
        if value not in str(resp.url):
            raise LoginFailed(f"final URL {resp.url!r} does not contain {value!r}")
    elif kind == "url_not_contains":
        if value in str(resp.url):
            raise LoginFailed(f"final URL {resp.url!r} still contains {value!r}")
    elif kind == "selector_present":
        soup = BeautifulSoup(resp.text, "lxml")
        if not soup.select(value):
            raise LoginFailed(f"expected selector {value!r} not found in response")
    elif kind == "selector_absent":
        soup = BeautifulSoup(resp.text, "lxml")
        if soup.select(value):
            raise LoginFailed(f"selector {value!r} still present (login likely failed)")
    elif kind == "text_contains":
        if value not in resp.text:
            raise LoginFailed(f"text {value!r} not found in response")
    elif kind == "text_absent":
        if value in resp.text:
            raise LoginFailed(f"text {value!r} still present (login likely failed)")
    else:
        raise LoginFailed(f"unknown success_check type: {kind!r}")


def login(
    auth: dict[str, Any],
    credentials: dict[str, str],
    *,
    timeout: float = HTTP_TIMEOUT,
    client: httpx.Client | None = None,
) -> httpx.Client:
    """Log in with `credentials` against the `auth` config; return the authed Client.

    The returned client carries the session cookies. Caller is responsible for
    closing it (use `with`).
    """
    owned = client is None
    if client is None:
        client = httpx.Client(
            timeout=timeout, follow_redirects=True, headers=_default_headers()
        )
    try:
        # Prime cookies / pick up any initial session cookie.
        client.get(auth["login_url"])

        form_data: dict[str, str] = dict(auth.get("extra_fields") or {})
        form_data[auth["username_field"]] = credentials["username"]
        form_data[auth["password_field"]] = credentials["password"]

        method = (auth.get("method") or "post").lower()
        if method == "post":
            resp = client.post(auth["login_url"], data=form_data)
        else:
            resp = client.get(auth["login_url"], params=form_data)
        resp.raise_for_status()

        _check_login_success(auth["success_check"], resp)
        return client
    except Exception:
        if owned:
            client.close()
        raise


def _extract_value(element: Tag, attribute: str) -> Any:
    if attribute == "text":
        return element.get_text(strip=True)
    if attribute == "html":
        return element.decode_contents().strip()
    if attribute == "outerhtml":
        return str(element)
    return element.get(attribute)


def extract(html: str, extractors: list[dict[str, Any]]) -> dict[str, Any]:
    """Run each extractor against the parsed HTML and return a {name: value} dict.

    Per-extractor failures (e.g. invalid selector) raise; callers wrap this in a
    try/except so one bad extractor doesn't sink an entire URL.
    """
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, Any] = {}
    for spec in extractors:
        name = spec["name"]
        selector = spec["selector"]
        attribute = spec.get("attribute") or "text"
        multiple = bool(spec.get("multiple"))
        matches = soup.select(selector)
        if multiple:
            out[name] = [_extract_value(m, attribute) for m in matches]
        else:
            out[name] = _extract_value(matches[0], attribute) if matches else None
    return out


def zip_into_records(data: dict[str, Any]) -> dict[str, Any]:
    """Zip parallel-list extractor outputs into a list of record dicts.

    - Keys whose value is a list become columns; rows are zipped by index up to
      the longest list (shorter lists pad with None so rows aren't silently
      dropped — a length mismatch is a real data signal worth seeing).
    - Keys with scalar (non-list) values go under `meta` so they aren't repeated
      per row.
    - If there are no list-valued extractors, returns the input unchanged.

    Returns `{"records": [...], "meta": {...}}` (meta omitted when empty).
    """
    list_keys = [k for k, v in data.items() if isinstance(v, list)]
    if not list_keys:
        return data
    max_len = max(len(data[k]) for k in list_keys)
    records: list[dict[str, Any]] = []
    for i in range(max_len):
        row: dict[str, Any] = {}
        for k in list_keys:
            row[k] = data[k][i] if i < len(data[k]) else None
        records.append(row)
    meta = {k: v for k, v in data.items() if not isinstance(v, list)}
    out: dict[str, Any] = {"records": records}
    if meta:
        out["meta"] = meta
    return out


_NUM_CLEANUP = re.compile(r"[,\s$€£%]")


def _parse_number(value: Any) -> float:
    """Parse a possibly-formatted numeric string. Unparseable / empty -> -inf so
    those records sink to the bottom when sorting descending."""
    if value is None:
        return float("-inf")
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = _NUM_CLEANUP.sub("", str(value)).strip()
    if not cleaned:
        return float("-inf")
    try:
        return float(cleaned)
    except ValueError:
        return float("-inf")


def sort_records(data: dict[str, Any], sort_config: dict[str, Any]) -> dict[str, Any]:
    """Sort the `records` list in `data` by the configured field/direction.

    Only applies when `data` has a `records` key (i.e. zip_records output).
    Non-zipped data is returned untouched.
    """
    records = data.get("records")
    if not isinstance(records, list):
        return data
    field = sort_config["field"]
    direction = sort_config.get("direction", "desc")
    numeric = sort_config.get("numeric", True)
    reverse = direction == "desc"
    if numeric:
        key_fn = lambda r: _parse_number(r.get(field))  # noqa: E731
    else:
        key_fn = lambda r: (r.get(field) is None, str(r.get(field) or ""))  # noqa: E731
    data["records"] = sorted(records, key=key_fn, reverse=reverse)
    return data


def _format_exc(exc: BaseException) -> str:
    # Short, single-line form for the per-URL `error` field; full traceback for logs.
    return f"{type(exc).__name__}: {exc}"


def _prune_old_runs(session: Session, job_id: int, keep: int) -> None:
    if keep <= 0:
        return
    # Find the cutoff id: the keep-th most recent run for this job.
    stmt = (
        select(JobRun.id)
        .where(JobRun.job_id == job_id)
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .offset(keep)
    )
    old_ids = [row[0] for row in session.execute(stmt).all()]
    if old_ids:
        session.execute(delete(JobRun).where(JobRun.id.in_(old_ids)))


def run_job(session: Session, job_id: int) -> JobRun:
    """Execute one run of the given job. Always returns a persisted JobRun row.

    Errors are stored on the run (`error` for job-level failures, per-URL
    `results[*].error` for URL failures). Nothing is silently swallowed.
    """
    job = session.get(ScheduledJob, job_id)
    if job is None:
        raise LookupError(f"job {job_id} not found")

    run = JobRun(job_id=job.id, started_at=_utcnow(), status="running", results=[])
    session.add(run)
    session.flush()

    results: list[dict[str, Any]] = []
    ok_count = 0
    err_count = 0
    job_level_error: str | None = None
    authed_client: httpx.Client | None = None

    try:
        if job.auth_config is not None:
            if job.credentials_encrypted is None:
                raise LoginFailed("auth is configured but no credentials are stored")
            creds = crypto.decrypt_json(job.credentials_encrypted)
            authed_client = login(job.auth_config, creds)
        if job.cookies_encrypted is not None:
            cookies = crypto.decrypt_json(job.cookies_encrypted).get("cookies") or {}
            if authed_client is None:
                authed_client = httpx.Client(
                    timeout=HTTP_TIMEOUT, follow_redirects=True, headers=_default_headers()
                )
            # Pasted cookies seed the jar. For mydaxa we set the parent domain so
            # the cookie is sent for both `mydaxa.com` and any subdomain. If the
            # job has multiple URLs, we use the netloc of the first URL.
            from urllib.parse import urlparse
            domain = urlparse(job.urls[0]).hostname or ""
            for name, value in cookies.items():
                authed_client.cookies.set(name, value, domain=domain)

        for url in job.urls:
            try:
                html = fetch(url, client=authed_client)
                data = extract(html, job.extractors)
                if getattr(job, "zip_records", False):
                    data = zip_into_records(data)
                if getattr(job, "sort_config", None):
                    data = sort_records(data, job.sort_config)
                results.append({"url": url, "ok": True, "data": data})
                ok_count += 1
            except Exception as exc:
                log.warning("scrape failed for %s: %s", url, exc, exc_info=True)
                results.append({"url": url, "ok": False, "error": _format_exc(exc)})
                err_count += 1
    except Exception as exc:
        # Login or other pre-loop failure: mark every URL as errored so the user
        # sees clearly which ones did not run.
        log.exception("job %s crashed during run", job_id)
        job_level_error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        if not results:
            for url in job.urls:
                results.append({"url": url, "ok": False, "error": job_level_error})
                err_count += 1
    finally:
        if authed_client is not None:
            authed_client.close()

    finished = _utcnow()
    run.finished_at = finished
    run.duration_ms = int((finished - run.started_at).total_seconds() * 1000)
    run.results = results
    run.error = job_level_error

    if job_level_error is not None:
        run.status = "error"
    elif err_count == 0:
        run.status = "success"
    elif ok_count == 0:
        run.status = "error"
    else:
        run.status = "partial"

    job.last_run_at = finished
    # Only escalate to 'failed' when there's a clear all-error result; don't
    # downgrade a user-set status like 'paused' or 'completed'.
    if run.status == "error" and job.status == "active":
        job.status = "failed"
    elif run.status in ("success", "partial") and job.status == "failed":
        # Auto-clear failed once a run succeeds again.
        job.status = "active"

    _prune_old_runs(session, job.id, RUN_HISTORY_LIMIT)
    session.commit()

    # Fire notification after commit so any Discord posts reflect the final
    # persisted state. Failures inside notify_run are swallowed and logged.
    if job.notify_config:
        notifications.notify_run(job.notify_config, job, run)

    return run
