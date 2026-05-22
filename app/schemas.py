from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


# --- extractor ---------------------------------------------------------------

class ExtractorSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=100)
    selector: str = Field(min_length=1, max_length=500)
    attribute: str = "text"
    multiple: bool = Field(default=False, validation_alias=AliasChoices("multiple", "all"))

    @field_validator("name", "selector", "attribute")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


# --- schedule (discriminated union) ------------------------------------------

class HourlySchedule(BaseModel):
    type: Literal["hourly"] = "hourly"


class IntervalSchedule(BaseModel):
    type: Literal["interval"] = "interval"
    minutes: int = Field(ge=1, le=1440)
    # Optional anchor: an HH:MM (UTC) that runs align to. Example: minutes=5,
    # start_at="00:03" -> fires at :03, :08, :13, :18, ... If omitted, runs
    # start ~immediately from when the job is created/resumed.
    start_at: str | None = None

    @field_validator("start_at")
    @classmethod
    def _check_start_at(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("start_at must be HH:MM")
        try:
            hh, mm = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError("start_at must be HH:MM") from exc
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError("start_at hour 0-23, minute 0-59")
        return f"{hh:02d}:{mm:02d}"


class DailySchedule(BaseModel):
    type: Literal["daily"] = "daily"
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)


_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


class WeeklySchedule(BaseModel):
    type: Literal["weekly"] = "weekly"
    day_of_week: str
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)

    @field_validator("day_of_week")
    @classmethod
    def _check_day(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in _DAYS:
            raise ValueError(f"day_of_week must be one of {sorted(_DAYS)}")
        return v


class CronSchedule(BaseModel):
    type: Literal["cron"] = "cron"
    expression: str = Field(min_length=1, max_length=200)

    @field_validator("expression")
    @classmethod
    def _check_cron(cls, v: str) -> str:
        v = v.strip()
        # Validate using APScheduler's parser so we surface the same errors here.
        from apscheduler.triggers.cron import CronTrigger  # local import to avoid cycle

        try:
            CronTrigger.from_crontab(v)
        except Exception as exc:  # APScheduler raises generic exceptions on bad input
            raise ValueError(f"invalid cron expression: {exc}") from exc
        return v


Schedule = Annotated[
    HourlySchedule | IntervalSchedule | DailySchedule | WeeklySchedule | CronSchedule,
    Field(discriminator="type"),
]


# --- shared helpers ----------------------------------------------------------

def _validate_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid URL: {url!r}")
    return url


# --- auth --------------------------------------------------------------------

_SUCCESS_CHECK_TYPES = {
    "url_contains",
    "url_not_contains",
    "selector_present",
    "selector_absent",
    "text_contains",
    "text_absent",
}


class SuccessCheck(BaseModel):
    """How we decide whether the post-login response indicates a logged-in session."""

    type: str
    value: str = Field(min_length=1, max_length=500)

    @field_validator("type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        v = v.strip()
        if v not in _SUCCESS_CHECK_TYPES:
            raise ValueError(f"success_check.type must be one of {sorted(_SUCCESS_CHECK_TYPES)}")
        return v


class AuthConfig(BaseModel):
    """Per-job login config (no credentials — those are stored encrypted separately)."""

    login_url: str = Field(min_length=1, max_length=1000)
    method: Literal["post", "get"] = "post"
    username_field: str = Field(min_length=1, max_length=100)
    password_field: str = Field(min_length=1, max_length=100)
    extra_fields: dict[str, str] = Field(default_factory=dict)
    success_check: SuccessCheck

    @field_validator("login_url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        return _validate_url(v)


class CredentialsInput(BaseModel):
    """Write-only: posted by the client, never returned by the API."""

    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)


def _parse_cookie_string(raw: str) -> dict[str, str]:
    """Parse a browser-style "name=value; name2=value2" string into a dict.

    Also accepts one name=value pair per line. Empty input -> ValueError so the
    caller can surface a clear error to the user.
    """
    out: dict[str, str] = {}
    # Normalize separators: newlines and semicolons both split pairs.
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"cookie segment without '=': {part!r}")
        name, _, value = part.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            raise ValueError("cookie name cannot be empty")
        out[name] = value
    if not out:
        raise ValueError("no cookies parsed from input")
    return out


class CookiesInput(BaseModel):
    """Write-only: a raw cookie string or already-parsed dict.

    Accepts either:
      - {"raw": "wordpress_logged_in_xxx=...; other=..."}
      - {"cookies": {"name": "value", ...}}
    """

    raw: str | None = None
    cookies: dict[str, str] | None = None

    @model_validator(mode="after")
    def _normalize(self) -> "CookiesInput":
        if self.cookies is None and self.raw is None:
            raise ValueError("provide either `raw` or `cookies`")
        if self.cookies is None:
            self.cookies = _parse_cookie_string(self.raw or "")
        if not self.cookies:
            raise ValueError("no cookies provided")
        return self


# --- notifications -----------------------------------------------------------

class NotifyConfig(BaseModel):
    """Per-job notification target. Only Discord webhooks for now."""

    discord_webhook_url: str = Field(min_length=1, max_length=500)
    on_success: bool = True
    on_error: bool = True
    # Show the first N records (already sorted by the job's sort config) in the
    # notification. 0 = disabled.
    include_top_n: int = Field(default=0, ge=0, le=25)
    # Highlight records where the named field's numeric value >= threshold.
    alert_field: str | None = None
    alert_threshold: float | None = None
    # Order/whitelist of fields to render in the per-record lines (top-N and
    # alert). If unset, all non-empty fields are shown up to a reasonable cap.
    preview_fields: list[str] | None = None

    @field_validator("preview_fields")
    @classmethod
    def _clean_preview_fields(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = [s.strip() for s in v if s and s.strip()]
        return cleaned or None

    @field_validator("discord_webhook_url")
    @classmethod
    def _check_webhook(cls, v: str) -> str:
        v = _validate_url(v)
        # Discord webhook URLs look like https://discord.com/api/webhooks/<id>/<token>
        if "discord.com/api/webhooks/" not in v and "discordapp.com/api/webhooks/" not in v:
            raise ValueError("not a Discord webhook URL")
        return v

    @model_validator(mode="after")
    def _check_alert_pair(self) -> "NotifyConfig":
        if (self.alert_field is None) != (self.alert_threshold is None):
            raise ValueError("alert_field and alert_threshold must be set together (or both omitted)")
        return self


# --- sort --------------------------------------------------------------------

class SortConfig(BaseModel):
    """How to sort records after zip_into_records. Numeric parsing strips
    $ , % and spaces so '$48,520,467.00' sorts as a number."""

    field: str = Field(min_length=1, max_length=100)
    direction: Literal["asc", "desc"] = "desc"
    numeric: bool = True


# --- jobs --------------------------------------------------------------------

class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    urls: list[str] = Field(min_length=1)
    extractors: list[ExtractorSpec] = Field(min_length=1)
    schedule: Schedule
    auth: AuthConfig | None = None
    credentials: CredentialsInput | None = None
    cookies: CookiesInput | None = None
    zip_records: bool = False
    sort: SortConfig | None = None
    notify: NotifyConfig | None = None

    @model_validator(mode="after")
    def _check_auth(self) -> "JobCreate":
        if self.auth is not None and self.credentials is None:
            raise ValueError("credentials are required when auth is configured")
        if self.auth is None and self.credentials is not None:
            raise ValueError("credentials provided but no auth config")
        return self

    @field_validator("urls")
    @classmethod
    def _check_urls(cls, urls: list[str]) -> list[str]:
        return [_validate_url(u) for u in urls]

    @model_validator(mode="after")
    def _unique_extractor_names(self) -> "JobCreate":
        names = [e.name for e in self.extractors]
        if len(set(names)) != len(names):
            raise ValueError("extractor names must be unique")
        return self


class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    urls: list[str] | None = None
    extractors: list[ExtractorSpec] | None = None
    schedule: Schedule | None = None
    # auth can be set, replaced, or cleared (pass null). Credentials are not
    # accepted here — use POST /api/jobs/{id}/credentials.
    auth: AuthConfig | None = None
    clear_auth: bool = False
    zip_records: bool | None = None
    sort: SortConfig | None = None
    clear_sort: bool = False
    notify: NotifyConfig | None = None
    clear_notify: bool = False

    @field_validator("urls")
    @classmethod
    def _check_urls(cls, urls: list[str] | None) -> list[str] | None:
        if urls is None:
            return None
        if not urls:
            raise ValueError("urls cannot be empty")
        return [_validate_url(u) for u in urls]

    @field_validator("extractors")
    @classmethod
    def _check_extractors(cls, ex: list[ExtractorSpec] | None) -> list[ExtractorSpec] | None:
        if ex is None:
            return None
        if not ex:
            raise ValueError("extractors cannot be empty")
        names = [e.name for e in ex]
        if len(set(names)) != len(names):
            raise ValueError("extractor names must be unique")
        return ex


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    urls: list[str]
    extractors: list[ExtractorSpec]
    schedule_type: str
    schedule_config: dict[str, Any]
    auth: AuthConfig | None = None
    has_credentials: bool = False
    has_cookies: bool = False
    zip_records: bool = False
    sort: SortConfig | None = None
    notify: NotifyConfig | None = None
    status: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, job: Any) -> "JobOut":
        return cls(
            id=job.id,
            name=job.name,
            description=job.description,
            urls=job.urls,
            extractors=job.extractors,
            schedule_type=job.schedule_type,
            schedule_config=job.schedule_config,
            auth=AuthConfig.model_validate(job.auth_config) if job.auth_config else None,
            has_credentials=job.credentials_encrypted is not None,
            has_cookies=job.cookies_encrypted is not None,
            zip_records=bool(job.zip_records),
            sort=SortConfig.model_validate(job.sort_config) if job.sort_config else None,
            notify=NotifyConfig.model_validate(job.notify_config) if job.notify_config else None,
            status=job.status,
            last_run_at=job.last_run_at,
            next_run_at=job.next_run_at,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    status: str
    results: list[dict[str, Any]]
    error: str | None


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


# --- helpers -----------------------------------------------------------------

def schedule_to_storage(schedule: Schedule) -> tuple[str, dict[str, Any]]:
    """Split a Schedule pydantic model into (schedule_type, schedule_config)."""
    data = schedule.model_dump(exclude_none=True)
    schedule_type = data.pop("type")
    return schedule_type, data


def schedule_from_storage(schedule_type: str, schedule_config: dict[str, Any]) -> Schedule:
    payload = {"type": schedule_type, **schedule_config}
    # Re-validate through the discriminated union so we get the right concrete type.
    return _ScheduleEnvelope(schedule=payload).schedule  # type: ignore[return-value]


class _ScheduleEnvelope(BaseModel):
    schedule: Schedule
