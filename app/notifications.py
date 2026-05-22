from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


log = logging.getLogger(__name__)


# Discord embed colors (integers, RGB).
_COLORS = {
    "success": 0x16A34A,  # green
    "partial": 0xD97706,  # amber
    "error":   0xDC2626,  # red
    "running": 0x2563EB,  # blue
}


def _summarize_data(data: Any) -> str:
    """Short one-line summary for the embed description."""
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return f"{len(data['records'])} record(s)"
    if isinstance(data, dict):
        # Non-zipped: count list-valued extractors and their max length.
        lists = [v for v in data.values() if isinstance(v, list)]
        if lists:
            return f"{max(len(lst) for lst in lists)} row(s) across {len(lists)} column(s)"
    return ""


def _decorate_value(key: str, value: Any) -> str:
    """Add a small visual cue for known status-like values (buy/sell)."""
    text = str(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low == "buy":
            return f"🟢 {value}"
        if low == "sell":
            return f"🔴 {value}"
    return text


def _format_record(
    record: dict[str, Any],
    *,
    preview_fields: list[str] | None = None,
    max_keys: int = 8,
) -> str:
    """Render one record as a compact Discord-friendly markdown line. Skips empty values.

    If preview_fields is given, render only those fields in that order — useful
    for putting the most important columns first (e.g. ticker, side, premium).
    """
    parts: list[str] = []
    if preview_fields:
        for k in preview_fields:
            if k not in record:
                continue
            v = record[k]
            if v in (None, "", []):
                continue
            if isinstance(v, (list, dict)):
                continue
            parts.append(f"**{k}**: {_decorate_value(k, v)}")
    else:
        for k, v in record.items():
            if v in (None, "", []):
                continue
            if isinstance(v, (list, dict)):
                continue
            parts.append(f"**{k}**: {_decorate_value(k, v)}")
            if len(parts) >= max_keys:
                break
    line = " | ".join(parts)
    return line[:240]


def _display_url(url: Any) -> str:
    text = str(url or "")
    try:
        parts = urlsplit(text)
    except ValueError:
        return text[:140]
    if not parts.scheme or not parts.netloc:
        return text[:140]
    safe = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
    return safe[:140]


def _format_error_text(error: Any, *, limit: int = 420) -> str:
    text = str(error or "Unknown error").strip().replace("```", "'''")
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _failed_url_lines(results: list[dict[str, Any]], *, max_lines: int = 3) -> list[str]:
    failed = [r for r in results if not r.get("ok")]
    lines: list[str] = []
    for result in failed[:max_lines]:
        url = _display_url(result.get("url"))
        error = _format_error_text(result.get("error"), limit=180)
        lines.append(f"- `{url}`\n  ```{error}```")
    if len(failed) > max_lines:
        lines.append(f"- ...+{len(failed) - max_lines} more failed URL(s)")
    return lines


def _build_payload(job, run, notify_config: dict[str, Any] | None = None) -> dict[str, Any]:
    n_ok = sum(1 for r in run.results if r.get("ok"))
    n_err = sum(1 for r in run.results if not r.get("ok"))

    # Find the first successful result to summarize / extract records from.
    first_data: Any = None
    data_summary = ""
    for r in run.results:
        if r.get("ok"):
            first_data = r.get("data")
            data_summary = _summarize_data(first_data)
            break

    fields: list[dict[str, Any]] = [
        {"name": "Status", "value": f"`{run.status}`", "inline": True},
        {"name": "Duration", "value": f"{(run.duration_ms or 0) / 1000:.1f}s", "inline": True},
        {"name": "URLs", "value": f"{n_ok} ok / {n_err} error", "inline": True},
    ]
    if data_summary:
        fields.append({"name": "Result", "value": data_summary, "inline": False})
    if run.error:
        # First line of error, capped so Discord doesn't truncate the whole embed.
        first = run.error.split("\n", 1)[0][:300]
        fields.append({"name": "Error", "value": f"```{first}```", "inline": False})
    failed_lines = _failed_url_lines(run.results)
    if failed_lines:
        fields.append({
            "name": "Failed URLs",
            "value": "\n".join(failed_lines),
            "inline": False,
        })

    # Optional: top-N records + threshold alerts. Only meaningful when the run
    # produced a zip_records-style dict with a `records` list.
    cfg = notify_config or {}
    if isinstance(first_data, dict) and isinstance(first_data.get("records"), list):
        records = first_data["records"]
        top_n = int(cfg.get("include_top_n") or 0)
        preview_fields = cfg.get("preview_fields") or None
        alert_field = cfg.get("alert_field")
        alert_threshold = cfg.get("alert_threshold")

        # Compute alerts first so the top-N can exclude them — the user gets
        # "all alerts + N more" rather than the same trades twice.
        matched: list[dict[str, Any]] = []
        if alert_field and alert_threshold is not None:
            from .scraper import _parse_number  # avoid top-level cycle
            matched = [
                r for r in records
                if _parse_number(r.get(alert_field)) >= float(alert_threshold)
            ]
        matched_ids = {id(r) for r in matched}

        if top_n > 0 and records:
            others = [r for r in records if id(r) not in matched_ids]
            shown = others[:top_n]
            if shown:
                lines = [
                    f"`{i}.` {_format_record(rec, preview_fields=preview_fields)}"
                    for i, rec in enumerate(shown, 1)
                ]
                title = (
                    f"Top {len(shown)} (excluding alerts)"
                    if matched else f"Top {len(shown)}"
                )
                fields.append({
                    "name": title,
                    "value": "\n".join(lines)[:1024],
                    "inline": False,
                })
        if matched:
            shown = matched[:10]  # cap so the embed stays readable
            lines = [
                f"- {_format_record(rec, preview_fields=preview_fields)}"
                for rec in shown
            ]
            more = f"\n…+{len(matched) - len(shown)} more" if len(matched) > len(shown) else ""
            fields.append({
                "name": f"Alert: {alert_field} >= {float(alert_threshold):,g} ({len(matched)} match)",
                "value": ("\n".join(lines) + more)[:1024],
                "inline": False,
            })

    embed = {
        "title": f"{job.name} — {run.status}",
        "color": _COLORS.get(run.status, 0x6B7280),
        "fields": fields,
        "timestamp": (run.finished_at or run.started_at).isoformat(),
    }
    return {"embeds": [embed]}


def notify_run(notify_config: dict[str, Any], job, run) -> bool:
    """Send a Discord embed for the given run. Returns True on success.

    Notification failures are logged but never raised — a broken webhook should
    not surface as a failed run.
    """
    if not notify_config:
        return False
    if run.status == "error" and not notify_config.get("on_error", True):
        return False
    if run.status in ("success", "partial") and not notify_config.get("on_success", True):
        return False

    url = notify_config.get("discord_webhook_url")
    if not url:
        return False

    payload = _build_payload(job, run, notify_config)
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        # Discord returns 204 on success.
        if resp.status_code >= 400:
            log.warning(
                "discord webhook returned %s for job %s: %s",
                resp.status_code, job.id, resp.text[:200],
            )
            return False
        return True
    except Exception as exc:
        log.warning("discord webhook failed for job %s: %s", job.id, exc)
        return False
