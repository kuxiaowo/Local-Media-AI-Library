from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import Job
from app.models.schemas import JobRead
from app.services.job_service import create_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRead])
def list_jobs(limit: int = 100, db: Session = Depends(get_db)) -> list[Job]:
    return list(db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all())


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/retry", response_model=JobRead)
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be retried")
    return create_job(
        db,
        job_type=job.job_type,
        target_id=job.target_id,
        target_path=job.target_path,
        payload=job.payload,
    )
