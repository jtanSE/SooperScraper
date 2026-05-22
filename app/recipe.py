"""Job-recipe import/export.

A "recipe" is the safe-to-commit slice of a job: everything except secrets.
External projects can hold their job definition in a JSON recipe and load it
into a fresh SooperScraper database with `python -m app.recipe load <path>`.
Re-loading a recipe with the same `name` updates the existing job rather than
creating a duplicate, so the recipe file is the source of truth.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal, init_db
from .models import ScheduledJob


RECIPE_VERSION = 1

log = logging.getLogger(__name__)


def dump_recipe(job: ScheduledJob) -> dict[str, Any]:
    """Build a recipe dict from a job. Excludes credentials, cookies, and the
    Discord webhook URL — anything that would be unsafe to commit."""
    notify: dict[str, Any] | None = None
    if job.notify_config:
        notify = {k: v for k, v in job.notify_config.items() if k != "discord_webhook_url"}
        if not notify:
            notify = None
    return {
        "version": RECIPE_VERSION,
        "name": job.name,
        "description": job.description,
        "urls": list(job.urls or []),
        "extractors": list(job.extractors or []),
        "schedule": {"type": job.schedule_type, **(job.schedule_config or {})},
        "zip_records": bool(job.zip_records),
        "sort": job.sort_config,
        "notify": notify,
    }


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} placeholders in strings. Unknown vars are
    kept verbatim so the failure point is obvious downstream."""
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_recipe(session: Session, recipe: dict[str, Any]) -> ScheduledJob:
    """Create or update a job from a recipe dict. Idempotent on `name`."""
    version = recipe.get("version", 1)
    if version != RECIPE_VERSION:
        raise ValueError(f"unsupported recipe version: {version}")

    expanded = _expand_env(recipe)
    name = expanded.get("name")
    if not name:
        raise ValueError("recipe is missing required field 'name'")

    # Reuse the API's Pydantic validation so a recipe goes through the same
    # checks an interactive create would.
    from .schemas import JobCreate, schedule_to_storage

    payload: dict[str, Any] = {
        "name": name,
        "urls": expanded["urls"],
        "extractors": expanded["extractors"],
        "schedule": expanded["schedule"],
        "zip_records": bool(expanded.get("zip_records", False)),
    }
    if expanded.get("description") is not None:
        payload["description"] = expanded["description"]
    if expanded.get("sort"):
        payload["sort"] = expanded["sort"]
    if expanded.get("notify"):
        payload["notify"] = expanded["notify"]

    parsed = JobCreate.model_validate(payload)
    schedule_type, schedule_config = schedule_to_storage(parsed.schedule)

    existing = session.scalars(
        select(ScheduledJob).where(ScheduledJob.name == name)
    ).first()

    notify_config = None
    if parsed.notify is not None:
        notify_config = parsed.notify.model_dump(exclude_none=True)
        # Preserve any existing webhook URL when the recipe doesn't carry one.
        if (
            existing
            and existing.notify_config
            and existing.notify_config.get("discord_webhook_url")
            and not notify_config.get("discord_webhook_url")
        ):
            notify_config["discord_webhook_url"] = existing.notify_config["discord_webhook_url"]

    if existing is None:
        job = ScheduledJob(
            name=parsed.name,
            description=parsed.description,
            urls=parsed.urls,
            extractors=[e.model_dump() for e in parsed.extractors],
            schedule_type=schedule_type,
            schedule_config=schedule_config,
            zip_records=parsed.zip_records,
            sort_config=parsed.sort.model_dump() if parsed.sort else None,
            notify_config=notify_config,
            status="active",
        )
        session.add(job)
        action = "created"
    else:
        existing.description = parsed.description
        existing.urls = parsed.urls
        existing.extractors = [e.model_dump() for e in parsed.extractors]
        existing.schedule_type = schedule_type
        existing.schedule_config = schedule_config
        existing.zip_records = parsed.zip_records
        existing.sort_config = parsed.sort.model_dump() if parsed.sort else None
        existing.notify_config = notify_config
        job = existing
        action = "updated"

    session.flush()
    session.commit()
    log.info("recipe %s job %r (id=%s)", action, name, job.id)
    return job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.recipe",
        description="Dump and load SooperScraper job recipes",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dump = sub.add_parser("dump", help="Write a recipe JSON for the named job")
    p_dump.add_argument("name", help="Job name to dump")
    p_dump.add_argument("--out", default="-", help='Output path (or "-" for stdout)')

    p_load = sub.add_parser("load", help="Create or update a job from a recipe JSON")
    p_load.add_argument("path", help="Recipe JSON file")

    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("SOOPERSCRAPER_LOG_LEVEL", "INFO"))
    init_db()

    if args.cmd == "dump":
        with SessionLocal() as session:
            job = session.scalars(
                select(ScheduledJob).where(ScheduledJob.name == args.name)
            ).first()
            if job is None:
                print(f"no job named {args.name!r}", file=sys.stderr)
                return 1
            recipe = dump_recipe(job)
            text = json.dumps(recipe, indent=2)
            if args.out == "-":
                print(text)
            else:
                with open(args.out, "w", encoding="utf-8") as f:
                    f.write(text + "\n")
                log.info("wrote recipe to %s", args.out)
        return 0

    if args.cmd == "load":
        with open(args.path, encoding="utf-8") as f:
            recipe = json.load(f)
        with SessionLocal() as session:
            load_recipe(session, recipe)
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
