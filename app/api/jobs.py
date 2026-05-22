from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import crypto, scheduler
from ..db import get_session
from ..models import ScheduledJob
from ..schemas import (
    CookiesInput,
    CredentialsInput,
    JobCreate,
    JobOut,
    JobUpdate,
    schedule_to_storage,
)


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _get_job_or_404(session: Session, job_id: int) -> ScheduledJob:
    job = session.get(ScheduledJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreate, session: Session = Depends(get_session)) -> JobOut:
    schedule_type, schedule_config = schedule_to_storage(payload.schedule)
    auth_config = payload.auth.model_dump() if payload.auth else None
    credentials_encrypted: bytes | None = None
    if payload.credentials is not None:
        try:
            credentials_encrypted = crypto.encrypt_json(payload.credentials.model_dump())
        except crypto.SecretKeyMissing as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    cookies_encrypted: bytes | None = None
    if payload.cookies is not None:
        try:
            cookies_encrypted = crypto.encrypt_json({"cookies": payload.cookies.cookies})
        except crypto.SecretKeyMissing as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    job = ScheduledJob(
        name=payload.name,
        description=payload.description,
        urls=payload.urls,
        extractors=[e.model_dump() for e in payload.extractors],
        schedule_type=schedule_type,
        schedule_config=schedule_config,
        auth_config=auth_config,
        credentials_encrypted=credentials_encrypted,
        cookies_encrypted=cookies_encrypted,
        zip_records=payload.zip_records,
        sort_config=payload.sort.model_dump() if payload.sort else None,
        notify_config=payload.notify.model_dump() if payload.notify else None,
        status="active",
    )
    session.add(job)
    session.flush()
    scheduler.reschedule(job)
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)


@router.get("", response_model=list[JobOut])
def list_jobs(session: Session = Depends(get_session)) -> list[JobOut]:
    jobs = session.query(ScheduledJob).order_by(ScheduledJob.created_at.desc()).all()
    return [JobOut.from_model(j) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, session: Session = Depends(get_session)) -> JobOut:
    return JobOut.from_model(_get_job_or_404(session, job_id))


@router.patch("/{job_id}", response_model=JobOut)
def update_job(
    job_id: int, payload: JobUpdate, session: Session = Depends(get_session)
) -> JobOut:
    job = _get_job_or_404(session, job_id)

    schedule_changed = False
    if payload.name is not None:
        job.name = payload.name
    if payload.description is not None:
        job.description = payload.description
    if payload.urls is not None:
        job.urls = payload.urls
    if payload.extractors is not None:
        job.extractors = [e.model_dump() for e in payload.extractors]
    if payload.schedule is not None:
        st, sc = schedule_to_storage(payload.schedule)
        job.schedule_type = st
        job.schedule_config = sc
        schedule_changed = True
    if payload.clear_auth:
        job.auth_config = None
        job.credentials_encrypted = None
    elif payload.auth is not None:
        job.auth_config = payload.auth.model_dump()
        if job.credentials_encrypted is None:
            raise HTTPException(
                status_code=400,
                detail="auth set but no credentials stored; POST credentials first",
            )
    if payload.zip_records is not None:
        job.zip_records = payload.zip_records
    if payload.clear_sort:
        job.sort_config = None
    elif payload.sort is not None:
        job.sort_config = payload.sort.model_dump()
    if payload.clear_notify:
        job.notify_config = None
    elif payload.notify is not None:
        job.notify_config = payload.notify.model_dump()

    session.flush()
    if schedule_changed:
        scheduler.reschedule(job)
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: int, session: Session = Depends(get_session)) -> None:
    job = _get_job_or_404(session, job_id)
    scheduler.remove(job)
    session.delete(job)
    session.commit()


@router.post("/{job_id}/pause", response_model=JobOut)
def pause_job(job_id: int, session: Session = Depends(get_session)) -> JobOut:
    job = _get_job_or_404(session, job_id)
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="completed jobs cannot be paused")
    scheduler.pause(job)
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)


@router.post("/{job_id}/resume", response_model=JobOut)
def resume_job(job_id: int, session: Session = Depends(get_session)) -> JobOut:
    job = _get_job_or_404(session, job_id)
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="completed jobs cannot be resumed")
    scheduler.resume(job)
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)


@router.post("/{job_id}/run", response_model=JobOut)
def run_now(job_id: int, session: Session = Depends(get_session)) -> JobOut:
    job = _get_job_or_404(session, job_id)
    scheduler.trigger_now(job)
    return JobOut.from_model(job)


@router.post("/{job_id}/credentials", response_model=JobOut)
def set_credentials(
    job_id: int,
    payload: CredentialsInput,
    session: Session = Depends(get_session),
) -> JobOut:
    job = _get_job_or_404(session, job_id)
    if job.auth_config is None:
        raise HTTPException(
            status_code=400,
            detail="this job has no auth config; PATCH the job with `auth` first",
        )
    try:
        job.credentials_encrypted = crypto.encrypt_json(payload.model_dump())
    except crypto.SecretKeyMissing as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)


@router.delete("/{job_id}/credentials", response_model=JobOut)
def clear_credentials(job_id: int, session: Session = Depends(get_session)) -> JobOut:
    job = _get_job_or_404(session, job_id)
    job.credentials_encrypted = None
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)


@router.post("/{job_id}/cookies", response_model=JobOut)
def set_cookies(
    job_id: int,
    payload: CookiesInput,
    session: Session = Depends(get_session),
) -> JobOut:
    job = _get_job_or_404(session, job_id)
    try:
        job.cookies_encrypted = crypto.encrypt_json({"cookies": payload.cookies})
    except crypto.SecretKeyMissing as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)


@router.delete("/{job_id}/cookies", response_model=JobOut)
def clear_cookies(job_id: int, session: Session = Depends(get_session)) -> JobOut:
    job = _get_job_or_404(session, job_id)
    job.cookies_encrypted = None
    session.commit()
    session.refresh(job)
    return JobOut.from_model(job)
