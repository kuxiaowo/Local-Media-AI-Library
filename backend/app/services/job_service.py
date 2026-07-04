from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db_models import Job, MediaFile

ACTIVE_JOB_STATUSES = ("queued", "running")


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    target_id: uuid.UUID | None = None,
    target_path: str | None = None,
    payload: dict | None = None,
) -> Job:
    job = Job(
        id=uuid.uuid4(),
        job_type=job_type,
        target_id=target_id,
        target_path=target_path,
        payload=payload or {},
    )
    db.add(job)
    return job


def create_job(
    db: Session,
    *,
    job_type: str,
    target_id: uuid.UUID | None = None,
    target_path: str | None = None,
    payload: dict | None = None,
) -> Job:
    job = enqueue_job(
        db,
        job_type=job_type,
        target_id=target_id,
        target_path=target_path,
        payload=payload,
    )
    db.commit()
    db.refresh(job)
    return job


def active_job_target_ids(db: Session, job_types: set[str] | tuple[str, ...]) -> set[uuid.UUID]:
    if not job_types:
        return set()
    return set(
        db.scalars(
            select(Job.target_id).where(
                Job.job_type.in_(list(job_types)),
                Job.status.in_(ACTIVE_JOB_STATUSES),
                Job.target_id.is_not(None),
            )
        ).all()
    )


def mark_running(job: Job) -> None:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.error_message = None


def mark_completed(job: Job) -> None:
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)


def mark_failed(job: Job, error: str) -> None:
    job.status = "failed"
    job.error_message = error
    job.finished_at = datetime.now(timezone.utc)


def scan_status(db: Session) -> dict[str, int]:
    job_counts = dict(
        db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    )
    media_counts = dict(
        db.execute(select(MediaFile.status, func.count(MediaFile.id)).group_by(MediaFile.status)).all()
    )
    media_total = db.scalar(select(func.count(MediaFile.id))) or 0
    return {
        "queued": job_counts.get("queued", 0),
        "running": job_counts.get("running", 0),
        "failed": job_counts.get("failed", 0),
        "completed": job_counts.get("completed", 0),
        "media_total": media_total,
        "media_done": media_counts.get("done", 0),
        "media_failed": media_counts.get("failed", 0),
        "media_missing": media_counts.get("missing", 0),
    }
