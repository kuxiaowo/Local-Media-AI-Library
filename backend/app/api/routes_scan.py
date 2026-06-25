from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import DirectoryRule, Job, MediaFile
from app.models.schemas import JobRead, MediaQueueItem, MediaQueueResponse, ScanStartRequest, ScanStatusResponse
from app.services.job_service import create_job, scan_status
from app.services.media_visibility import visible_media_filter

router = APIRouter(prefix="/scan", tags=["scan"])

MEDIA_JOB_TYPES = {"extract_metadata", "analyze_image", "generate_embedding", "reanalyze_media"}
ACTIVE_JOB_STATUSES = {"queued", "running", "failed"}
QUEUE_MEDIA_STATUSES = {"pending", "metadata_done", "analyzing", "needs_reanalysis", "failed"}


@router.post("/start", response_model=list[JobRead])
def start_scan(payload: ScanStartRequest, db: Session = Depends(get_db)):
    if payload.directory_rule_id:
        rule = db.get(DirectoryRule, payload.directory_rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Directory rule not found")
        rules = [rule]
    else:
        rules = list(db.scalars(select(DirectoryRule).where(DirectoryRule.enabled)).all())
    if not rules:
        raise HTTPException(status_code=400, detail="No enabled directory rules to scan")
    return [
        create_job(
            db,
            job_type="scan_directory",
            target_id=rule.id,
            target_path=rule.path,
            payload={"mode": payload.mode},
        )
        for rule in rules
    ]


@router.get("/status", response_model=ScanStatusResponse)
def get_scan_status(db: Session = Depends(get_db)) -> dict[str, int]:
    return scan_status(db)


@router.get("/media-queue", response_model=MediaQueueResponse)
def get_media_queue(limit: int = 200, db: Session = Depends(get_db)) -> MediaQueueResponse:
    active_jobs = list(
        db.scalars(
            select(Job)
            .where(
                Job.job_type.in_(list(MEDIA_JOB_TYPES)),
                Job.status.in_(list(ACTIVE_JOB_STATUSES)),
                Job.target_id.is_not(None),
            )
            .order_by(Job.created_at.desc())
            .limit(1000)
        ).all()
    )
    jobs_by_media: dict[object, list[Job]] = {}
    for job in active_jobs:
        if job.target_id is None:
            continue
        jobs_by_media.setdefault(job.target_id, []).append(job)

    active_media_ids = set(jobs_by_media.keys())
    queue_filter = MediaFile.status.in_(list(QUEUE_MEDIA_STATUSES))
    if active_media_ids:
        queue_filter = or_(MediaFile.id.in_(list(active_media_ids)), queue_filter)

    visibility_filter = visible_media_filter(db)
    total = db.scalar(select(func.count(MediaFile.id)).where(visibility_filter, queue_filter)) or 0
    media_files = list(
        db.scalars(
            select(MediaFile)
            .where(visibility_filter, queue_filter)
            .order_by(MediaFile.updated_at.desc(), MediaFile.created_at.desc())
            .limit(min(limit, 500))
        ).all()
    )

    items = []
    for media in media_files:
        job = _select_current_job(jobs_by_media.get(media.id, []))
        items.append(
            MediaQueueItem(
                media_id=media.id,
                path=media.path,
                thumbnail_url=f"/api/media/{media.id}/thumbnail" if media.thumbnail_path else None,
                media_status=media.status,
                job_id=job.id if job else None,
                job_type=job.job_type if job else None,
                job_status=job.status if job else None,
                error_message=(job.error_message if job and job.error_message else media.error_message),
                updated_at=media.updated_at,
                job_created_at=job.created_at if job else None,
                job_started_at=job.started_at if job else None,
            )
        )

    return MediaQueueResponse(items=items, total=total)


def _select_current_job(jobs: list[Job]) -> Job | None:
    if not jobs:
        return None
    status_rank = {"running": 0, "queued": 1, "failed": 2}

    def sort_key(job: Job) -> tuple[int, float]:
        timestamp = job.started_at or job.created_at
        return (status_rank.get(job.status, 9), -timestamp.timestamp())

    return sorted(jobs, key=sort_key)[0]
