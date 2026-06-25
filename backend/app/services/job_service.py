from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.db_models import Job, MediaFile


def create_job(
    db: Session,
    *,
    job_type: str,
    target_id: uuid.UUID | None = None,
    target_path: str | None = None,
    payload: dict | None = None,
) -> Job:
    job = Job(job_type=job_type, target_id=target_id, target_path=target_path, payload=payload or {})
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_running(job: Job) -> None:
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    job.error_message = None


def mark_completed(job: Job) -> None:
    job.status = "completed"
    job.finished_at = datetime.now(timezone.utc)


def mark_failed(job: Job, error: str) -> None:
    job.status = "failed"
    job.error_message = error[:4000]
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
