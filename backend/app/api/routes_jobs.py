from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import Job, MediaFile
from app.models.schemas import JobClearResponse, JobRead
from app.services.job_service import create_job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRead])
def list_jobs(limit: int = 100, db: Session = Depends(get_db)) -> list[Job]:
    return list(db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all())


@router.delete("", response_model=JobClearResponse)
def clear_jobs(db: Session = Depends(get_db)) -> JobClearResponse:
    deleted = db.scalar(select(func.count(Job.id))) or 0
    db.execute(delete(Job))
    db.commit()
    return JobClearResponse(deleted=deleted)


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
    payload = dict(job.payload or {})
    if job.job_type == "analyze_video":
        payload["resume_segments"] = True
    _prepare_target_media_for_retry(db, job)
    return create_job(
        db,
        job_type=job.job_type,
        target_id=job.target_id,
        target_path=job.target_path,
        payload=payload,
    )


def _prepare_target_media_for_retry(db: Session, job: Job) -> None:
    if job.target_id is None:
        return
    media = db.get(MediaFile, job.target_id)
    if media is None or media.status == "missing":
        return

    retry_status_by_job_type = {
        "extract_metadata": "pending",
        "analyze_image": "metadata_done",
        "analyze_video": "metadata_done",
        "generate_embedding": "embedding_pending",
        "reanalyze_media": "needs_reanalysis",
        "reanalyze_video_summary": "embedding_pending",
    }
    retry_status = retry_status_by_job_type.get(job.job_type)
    if retry_status is None:
        return

    media.status = retry_status
    media.error_message = None
    db.add(media)
