# SooperScraper Operational Notes

## Current Production Secret Situation

The production DAXA scheduled scrape runs from GitHub Actions and uses the
existing repository secrets:

- `SOOPERSCRAPER_DB_URL`
- `SOOPERSCRAPER_SECRET_KEY`

These values are currently not available in the local development environment.
GitHub Actions can still use them, but GitHub does not allow viewing existing
secret values after they are saved.

Because the local environment does not have these variables set, running the UI
locally at `http://127.0.0.1:8000/` falls back to the local SQLite database:

```text
sqlite:///./sooperscraper.db
```

That local database does not contain the production DAXA scheduled job, so the
local UI appears empty.

## What Can Be Recovered

`SOOPERSCRAPER_DB_URL` can be recovered or regenerated from Supabase. If the
database password is reset, update the GitHub Actions secret with the new full
SQLAlchemy URL.

`SOOPERSCRAPER_SECRET_KEY` cannot be recovered from Supabase. It is an app-level
Fernet encryption key used by SooperScraper to encrypt stored credentials and
cookies. It was stored as a GitHub secret, but GitHub cannot reveal it.

Do not overwrite `SOOPERSCRAPER_SECRET_KEY` casually. If it is replaced, existing
encrypted cookies and credentials in the database become unreadable and must be
re-entered.

## DAXA Cookie Refresh Workaround

Commit `9775956` added a GitHub Actions workflow that can refresh the DAXA cookie
without needing the production DB URL or Fernet key locally:

```text
.github/workflows/update-daxa-cookie.yml
app/update_cookies.py
tests/test_update_cookies.py
```

To refresh the DAXA cookie:

1. Log into MyDAXA in a browser.
2. Open DevTools and copy the full `Cookie:` request header value from a request
   to `mydaxa.com`.
3. In GitHub, create or update the repository secret:

```text
DAXA_COOKIE_RAW
```

4. Paste the full cookie header value into that secret.
5. Run:

```text
Actions -> Update DAXA cookie -> Run workflow
```

6. Leave `job_name` as `DAXA` unless the production scheduled job has been renamed.
7. Run the normal `DAXA scheduled scrapes` workflow once to confirm the scrape is
   working again.

The update workflow uses the existing hidden GitHub secrets to connect to the
production database and encrypt the new cookie before storing it on the DAXA job.

## Future Cleanup Plan

At the end of summer, or whenever production maintenance is planned:

1. Recover or reset the Supabase database password.
2. Recreate and securely store the full `SOOPERSCRAPER_DB_URL`.
3. Decide whether to rotate `SOOPERSCRAPER_SECRET_KEY`.
4. If rotating the key, do it as a controlled migration:
   - Run code in GitHub Actions while the old hidden key still exists.
   - Decrypt existing encrypted cookies and credentials with the old key.
   - Re-encrypt them with a new Fernet key.
   - Store the new key somewhere secure.
   - Update the GitHub `SOOPERSCRAPER_SECRET_KEY` secret.
5. After rotation, verify that local UI access works against Supabase by setting:

```powershell
$env:SOOPERSCRAPER_DB_URL="postgresql+psycopg://..."
$env:SOOPERSCRAPER_SECRET_KEY="..."
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/
```

## DAXA Failure Interpretation

Recent observed failures:

- `HTTPStatusError: 429 Too Many Requests` from WordPress means MyDAXA or
  WordPress is rate-limiting the GitHub Actions runner/IP/request pattern.
- `success / 0 record(s)` means the page returned HTTP 200 but the configured
  CSS selectors matched no rows. This can happen if the cookie expired and the
  returned page is a login/paywall/blocked page, or if MyDAXA changed its markup.
- Repeated `Duplicate warning` messages on empty results mean SooperScraper is
  correctly detecting that consecutive successful runs extracted the same data.

