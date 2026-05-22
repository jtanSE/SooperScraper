from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Status values are kept as plain strings (not Enum) so the SQLite column stays
# human-readable and migrations are simpler.
JOB_STATUSES = ("active", "paused", "failed", "completed")
RUN_STATUSES = ("running", "success", "error", "partial")
SCHEDULE_TYPES = ("hourly", "interval", "daily", "weekly", "cron")


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    urls: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    extractors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False)
    schedule_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    auth_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    credentials_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cookies_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    zip_records: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    sort_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    notify_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    runs: Mapped[list["JobRun"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobRun.started_at.desc()",
    )


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[ScheduledJob] = relationship(back_populates="runs")

    __table_args__ = (Index("ix_job_runs_job_started", "job_id", "started_at"),)
