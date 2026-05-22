from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..models import JobRun, ScheduledJob
from ..schemas import RunListOut, RunOut


router = APIRouter(tags=["runs"])


@router.get("/api/jobs/{job_id}/runs", response_model=RunListOut)
def list_runs(
    job_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> RunListOut:
    job = session.get(ScheduledJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")

    total = session.execute(
        select(func.count(JobRun.id)).where(JobRun.job_id == job_id)
    ).scalar_one()

    rows = (
        session.execute(
            select(JobRun)
            .where(JobRun.job_id == job_id)
            .order_by(JobRun.started_at.desc(), JobRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return RunListOut(items=[RunOut.model_validate(r) for r in rows], total=int(total))


@router.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: Session = Depends(get_session)) -> RunOut:
    run = session.get(JobRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return RunOut.model_validate(run)
