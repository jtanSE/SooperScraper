from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest


SAMPLE_HTML = """
<html><body>
  <h1 class="headline">Hello world</h1>
  <a id="primary" href="/about">About</a>
  <ul class="items">
    <li>one</li><li>two</li><li>three</li>
  </ul>
</body></html>
"""


def test_extract_text(tmp_db):
    from app.scraper import extract
    out = extract(SAMPLE_HTML, [
        {"name": "title", "selector": "h1.headline", "attribute": "text", "multiple": False},
    ])
    assert out == {"title": "Hello world"}


def test_extract_attribute(tmp_db):
    from app.scraper import extract
    out = extract(SAMPLE_HTML, [
        {"name": "link", "selector": "a#primary", "attribute": "href"},
    ])
    assert out == {"link": "/about"}


def test_extract_multiple(tmp_db):
    from app.scraper import extract
    out = extract(SAMPLE_HTML, [
        {"name": "items", "selector": "ul.items li", "attribute": "text", "multiple": True},
    ])
    assert out == {"items": ["one", "two", "three"]}


def test_extract_html_attribute(tmp_db):
    from app.scraper import extract
    out = extract("<div class='x'><span>hello <b>world</b></span></div>", [
        {"name": "inner", "selector": "div.x", "attribute": "html"},
    ])
    assert out == {"inner": "<span>hello <b>world</b></span>"}


def test_extract_outerhtml_attribute(tmp_db):
    from app.scraper import extract
    out = extract("<div class='x'>a</div>", [
        {"name": "full", "selector": "div.x", "attribute": "outerhtml"},
    ])
    assert out == {"full": '<div class="x">a</div>'}


def test_extract_missing_selector_yields_none(tmp_db):
    from app.scraper import extract
    out = extract(SAMPLE_HTML, [
        {"name": "missing", "selector": "div.nope"},
    ])
    assert out == {"missing": None}


def test_zip_into_records_basic(tmp_db):
    from app.scraper import zip_into_records
    data = {
        "ticker": ["AAPL", "MSFT", "GOOG"],
        "price":  ["1",    "2",    "3"],
    }
    assert zip_into_records(data) == {
        "records": [
            {"ticker": "AAPL", "price": "1"},
            {"ticker": "MSFT", "price": "2"},
            {"ticker": "GOOG", "price": "3"},
        ],
    }


def test_zip_into_records_with_scalars(tmp_db):
    from app.scraper import zip_into_records
    data = {
        "headline": "Trades for Friday",
        "ticker": ["AAPL", "MSFT"],
        "price":  ["1",    "2"],
    }
    out = zip_into_records(data)
    assert out["meta"] == {"headline": "Trades for Friday"}
    assert out["records"] == [
        {"ticker": "AAPL", "price": "1"},
        {"ticker": "MSFT", "price": "2"},
    ]


def test_zip_into_records_unequal_lengths_pad_with_none(tmp_db):
    from app.scraper import zip_into_records
    out = zip_into_records({"a": [1, 2, 3], "b": [10]})
    assert out == {"records": [{"a": 1, "b": 10}, {"a": 2, "b": None}, {"a": 3, "b": None}]}


def test_zip_into_records_no_lists_passthrough(tmp_db):
    from app.scraper import zip_into_records
    out = zip_into_records({"title": "x"})
    assert out == {"title": "x"}


def test_parse_number_strips_money_format(tmp_db):
    from app.scraper import _parse_number
    assert _parse_number("$48,520,467.00") == 48520467.0
    assert _parse_number("175,695.76") == 175695.76
    assert _parse_number("-12.5") == -12.5
    assert _parse_number(42) == 42.0
    assert _parse_number(None) == float("-inf")
    assert _parse_number("") == float("-inf")
    assert _parse_number("not a number") == float("-inf")


def test_sort_records_descending_numeric(tmp_db):
    from app.scraper import sort_records
    data = {"records": [
        {"ticker": "A", "premium": "$1,000.00"},
        {"ticker": "B", "premium": "$50,000.00"},
        {"ticker": "C", "premium": "$10,000.00"},
        {"ticker": "D", "premium": None},
    ]}
    out = sort_records(data, {"field": "premium", "direction": "desc", "numeric": True})
    assert [r["ticker"] for r in out["records"]] == ["B", "C", "A", "D"]


def test_sort_records_ascending(tmp_db):
    from app.scraper import sort_records
    data = {"records": [{"x": 3}, {"x": 1}, {"x": 2}]}
    out = sort_records(data, {"field": "x", "direction": "asc"})
    assert [r["x"] for r in out["records"]] == [1, 2, 3]


def test_sort_records_noop_when_no_records_key(tmp_db):
    from app.scraper import sort_records
    data = {"title": "x"}
    assert sort_records(data, {"field": "x"}) == {"title": "x"}


def test_run_job_with_zip_and_sort(tmp_db, monkeypatch, session):
    from app import scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="t",
        urls=["http://good.test/"],
        extractors=[
            {"name": "ticker", "selector": "td.t", "attribute": "text", "multiple": True},
            {"name": "premium", "selector": "td.p", "attribute": "text", "multiple": True},
        ],
        schedule_type="hourly",
        schedule_config={},
        status="active",
        zip_records=True,
        sort_config={"field": "premium", "direction": "desc", "numeric": True},
    )
    session.add(job)
    session.commit()
    html = (
        "<table>"
        "<tr><td class=t>AAA</td><td class=p>$1,000</td></tr>"
        "<tr><td class=t>BBB</td><td class=p>$50,000</td></tr>"
        "<tr><td class=t>CCC</td><td class=p>$10,000</td></tr>"
        "</table>"
    )
    monkeypatch.setattr(scraper, "fetch", lambda url, **kw: html)
    run = scraper.run_job(session, job.id)
    tickers = [r["ticker"] for r in run.results[0]["data"]["records"]]
    assert tickers == ["BBB", "CCC", "AAA"]


def test_run_job_with_zip_records(tmp_db, monkeypatch, session):
    from app import scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="t",
        urls=["http://good.test/"],
        extractors=[
            {"name": "ticker", "selector": "td.t", "attribute": "text", "multiple": True},
            {"name": "price",  "selector": "td.p", "attribute": "text", "multiple": True},
        ],
        schedule_type="hourly",
        schedule_config={},
        status="active",
        zip_records=True,
    )
    session.add(job)
    session.commit()

    html = "<table><tr><td class=t>AAPL</td><td class=p>1</td></tr><tr><td class=t>MSFT</td><td class=p>2</td></tr></table>"
    monkeypatch.setattr(scraper, "fetch", lambda url, **kw: html)
    run = scraper.run_job(session, job.id)
    assert run.status == "success"
    assert run.results[0]["data"] == {
        "records": [
            {"ticker": "AAPL", "price": "1"},
            {"ticker": "MSFT", "price": "2"},
        ],
    }


def test_fetch_uses_client(tmp_db):
    """fetch() with an injected client should use that transport (no network)."""
    from app.scraper import fetch

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<h1>ok</h1>", request=request)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        html = fetch("http://example.test/", client=client)
    assert "<h1>ok</h1>" in html


def test_fetch_raises_on_http_error(tmp_db):
    from app.scraper import fetch

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            fetch("http://example.test/", client=client)


def test_run_job_partial(tmp_db, monkeypatch, session):
    """A job with one good URL and one bad URL produces a 'partial' run."""
    from app import scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="t",
        urls=["http://good.test/", "http://bad.test/"],
        extractors=[{"name": "title", "selector": "h1", "attribute": "text", "multiple": False}],
        schedule_type="hourly",
        schedule_config={},
        status="active",
    )
    session.add(job)
    session.commit()

    def fake_fetch(url, **kwargs):
        if "good" in url:
            return "<h1>Hi</h1>"
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(scraper, "fetch", fake_fetch)
    run = scraper.run_job(session, job.id)

    assert run.status == "partial"
    assert run.duration_ms is not None and run.duration_ms >= 0
    assert run.finished_at is not None
    assert len(run.results) == 2
    ok = [r for r in run.results if r["ok"]]
    bad = [r for r in run.results if not r["ok"]]
    assert ok[0]["data"] == {"title": "Hi"}
    assert "ConnectError" in bad[0]["error"]
    # job status stays active because we got a partial success
    session.refresh(job)
    assert job.status == "active"
    assert job.last_run_at is not None


def test_run_job_all_failed_marks_job_failed(tmp_db, monkeypatch, session):
    from app import scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="t",
        urls=["http://bad.test/"],
        extractors=[{"name": "x", "selector": "h1"}],
        schedule_type="hourly",
        schedule_config={},
        status="active",
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr(scraper, "fetch", lambda url, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    run = scraper.run_job(session, job.id)

    assert run.status == "error"
    session.refresh(job)
    assert job.status == "failed"


def test_run_job_clears_failed_after_success(tmp_db, monkeypatch, session):
    from app import scraper
    from app.models import ScheduledJob

    job = ScheduledJob(
        name="t",
        urls=["http://good.test/"],
        extractors=[{"name": "title", "selector": "h1"}],
        schedule_type="hourly",
        schedule_config={},
        status="failed",
    )
    session.add(job)
    session.commit()

    monkeypatch.setattr(scraper, "fetch", lambda url, **kw: "<h1>ok</h1>")
    scraper.run_job(session, job.id)

    session.refresh(job)
    assert job.status == "active"
