from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from .config import HTTP_TIMEOUT, USER_AGENT
from .schemas import CookiesInput


def _snippet(text: str, *, limit: int = 500) -> str:
    soup = BeautifulSoup(text or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:limit]


def probe(url: str, raw_cookie: str | None = None) -> int:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
        if raw_cookie:
            cookies = CookiesInput(raw=raw_cookie).cookies or {}
            domain = urlparse(url).hostname or ""
            for name, value in cookies.items():
                client.cookies.set(name, value, domain=domain)

        try:
            resp = client.get(url)
        except httpx.RequestError as exc:
            print(f"request_error={type(exc).__name__}: {exc}")
            return 1
        print(f"status={resp.status_code} {resp.reason_phrase}")
        print(f"final_url={resp.url}")
        print(f"content_type={resp.headers.get('content-type', '')}")
        retry_after = resp.headers.get("retry-after")
        if retry_after:
            print(f"retry_after={retry_after}")
        print(f"body_snippet={_snippet(resp.text)}")
        return 0 if resp.status_code < 400 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe one URL with optional browser Cookie header.")
    parser.add_argument("--url", default="https://mydaxa.com/us-darkpool-trades/")
    parser.add_argument("--cookie-env", default="DAXA_COOKIE_RAW")
    args = parser.parse_args(argv)

    return probe(args.url, os.environ.get(args.cookie_env))


if __name__ == "__main__":
    raise SystemExit(main())
