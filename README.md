# Sooper Scraper

Schedule recurring web scraping jobs. Each job has one or more target URLs,
a list of CSS-selector-based extractors, and a schedule (hourly, daily, weekly,
or a custom cron expression). Results and errors are kept per run.

## Stack
- FastAPI + Uvicorn
- SQLite + SQLAlchemy 2
- APScheduler (in-process)
- httpx + BeautifulSoup4 (lxml)
- Vanilla JS frontend (no build step)

## Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Run

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/>.

## Test

```bash
pytest
```

## Free cloud automation

The local FastAPI app uses APScheduler while your computer is on. For jobs to
keep running when your computer is off, use the included GitHub Actions runner
with a free hosted Postgres database such as Supabase.

How it works:

1. Your local app and GitHub Actions both point at the same database.
2. You create and edit jobs locally in the UI.
3. GitHub Actions wakes up every 5 minutes.
4. It runs `python -m app.runner`, which executes due jobs once, stores run
   history, sends Discord notifications, advances `next_run_at`, and exits.

### 1. Create a free Supabase database

Create a Supabase project, then copy the Postgres connection string. Use the
pooled URI if Supabase offers one. It should look similar to:

```text
postgresql+psycopg://postgres.PROJECT:PASSWORD@aws-...pooler.supabase.com:6543/postgres
```

If Supabase gives you a `postgresql://...` URL, replace the scheme with
`postgresql+psycopg://...` so SQLAlchemy uses the installed driver.

### 2. Run your local app against Supabase

Set the same environment variables before starting the app:

```powershell
$env:SOOPERSCRAPER_DB_URL="postgresql+psycopg://..."
$env:SOOPERSCRAPER_SECRET_KEY="paste-your-fernet-key"
uvicorn app.main:app --reload
```

Generate a Fernet key if you do not already have one:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Create your jobs in the UI while connected to Supabase. The first app startup
will create the database tables.

### 3. Add GitHub repository secrets

In GitHub, go to:

```text
Repo -> Settings -> Secrets and variables -> Actions -> New repository secret
```

Add:

| Secret | Value |
| ------ | ----- |
| `SOOPERSCRAPER_DB_URL` | Your Supabase SQLAlchemy URL |
| `SOOPERSCRAPER_SECRET_KEY` | The same Fernet key used locally |

The Discord webhook URL stays in the job's `notify_config` in the shared
database. Keep the repository private if you store production jobs or webhooks.

### 4. Enable the scheduled workflow

Push this repo to GitHub. The workflow at
`.github/workflows/scheduled-scrapes.yml` runs every 5 minutes and can also be
started manually from the GitHub Actions tab with "Run workflow".

GitHub scheduled workflows are not exact real-time timers. They are appropriate
for hourly or every-30-minute scraping, but not second-accurate trading alerts.

You can test the runner locally without starting the web server:

```bash
python -m app.runner
```

or after installing the package:

```bash
sooperscraper-run-due
```

## Environment

| Variable                          | Default                             | Purpose                                            |
| --------------------------------- | ----------------------------------- | -------------------------------------------------- |
| `SOOPERSCRAPER_DB_URL`            | `sqlite:///./sooperscraper.db`      | SQLAlchemy URL                                     |
| `SOOPERSCRAPER_HTTP_TIMEOUT`      | `20`                                | Per-URL fetch timeout in seconds                   |
| `SOOPERSCRAPER_RUN_HISTORY`       | `100`                               | Max runs retained per job                          |
| `SOOPERSCRAPER_USER_AGENT`        | `SooperScraper/0.1 …`               | User-Agent header sent on fetches                  |
| `SOOPERSCRAPER_DISABLE_SCHEDULER` | unset                               | Set to `1` to skip starting the background scheduler |
| `SOOPERSCRAPER_LOG_LEVEL`         | `INFO`                              | Python logging level                               |
| `SOOPERSCRAPER_SECRET_KEY`        | unset                               | Fernet key used to encrypt stored credentials. Required only if any job has auth configured. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

## API

| Method | Path                          | Notes                                  |
| ------ | ----------------------------- | -------------------------------------- |
| POST   | `/api/jobs`                   | Create a scheduled job                 |
| GET    | `/api/jobs`                   | List jobs                              |
| GET    | `/api/jobs/{id}`              | One job                                |
| PATCH  | `/api/jobs/{id}`              | Partial update; reschedules if changed |
| DELETE | `/api/jobs/{id}`              | Removes job + runs                     |
| POST   | `/api/jobs/{id}/pause`        | Pause                                  |
| POST   | `/api/jobs/{id}/resume`       | Resume                                 |
| POST   | `/api/jobs/{id}/run`          | Trigger an immediate run               |
| GET    | `/api/jobs/{id}/runs`         | Run history (`?limit=&offset=`)        |
| GET    | `/api/runs/{run_id}`          | Single run with full results           |
| POST   | `/api/jobs/{id}/credentials`  | Store/replace login credentials (encrypted) |
| DELETE | `/api/jobs/{id}/credentials`  | Remove stored credentials                   |

### Authenticated scraping

If a target site needs a form login, set `SOOPERSCRAPER_SECRET_KEY` first (see env table above), then enable "Site requires login" in the job form, or POST an `auth` block:

```json
{
  "name": "MyDaxa darkpool",
  "urls": ["https://mydaxa.com/us-darkpool-trades/"],
  "extractors": [{ "name": "rows", "selector": "table tr", "multiple": true }],
  "schedule": { "type": "cron", "expression": "0 * * * *" },
  "auth": {
    "login_url": "https://mydaxa.com/my-daxa-login/",
    "method": "post",
    "username_field": "login_username",
    "password_field": "login_password",
    "extra_fields": {
      "login_submit": "Log In",
      "login_form_id": "3",
      "pp_current_url": "https://mydaxa.com/my-daxa-login/",
      "login_referrer_page": ""
    },
    "success_check": { "type": "selector_absent", "value": "input[name='login_password']" }
  },
  "credentials": { "username": "you@example.com", "password": "…" }
}
```

Credentials are encrypted at rest with Fernet and never returned by the API. `success_check.type` can be `selector_absent`, `selector_present`, `url_contains`, `url_not_contains`, `text_contains`, or `text_absent`.

### Example payload

```json
{
  "name": "Hacker News front page",
  "description": "track top story titles",
  "urls": ["https://news.ycombinator.com/"],
  "extractors": [
    { "name": "titles", "selector": ".titleline > a", "attribute": "text", "multiple": true }
  ],
  "schedule": { "type": "cron", "expression": "*/15 * * * *" }
}
```
