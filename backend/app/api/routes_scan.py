from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db_models import DirectoryRule, Job, MediaFile
from app.models.schemas import (
    GenerateAiRecordsRequest,
    JobRead,
    MediaQueueItem,
    MediaQueueResponse,
    ScanStartRequest,
    ScanStatusResponse,
)
from app.services.job_service import create_job, scan_status
from app.services.media_visibility import visible_media_filter

router = APIRouter(prefix="/scan", tags=["scan"])

MEDIA_JOB_TYPES = {
    "extract_metadata",
    "analyze_image",
    "analyze_video",
    "generate_embedding",
    "reanalyze_media",
    "reanalyze_video_summary",
}
ACTIVE_JOB_STATUSES = {"queued", "running", "failed"}
ACTIVE_ANALYSIS_JOB_STATUSES = {"queued", "running"}
ANALYSIS_JOB_TYPES = MEDIA_JOB_TYPES - {"generate_embedding"}


@router.post("/start", response_model=list[JobRead])
def start_scan(payload: ScanStartRequest, db: Session = Depends(get_db)):
    rules = _rules_for_request(
        db,
        payload.directory_rule_id,
        empty_detail="No enabled directory rules to scan",
    )
    return [
        create_job(
            db,
            job_type="scan_directory",
            target_id=rule.id,
            target_path=rule.path,
            payload={"mode": payload.mode, "run_ai": payload.run_ai},
        )
        for rule in rules
    ]


@router.post("/generate-ai-records", response_model=list[JobRead])
def generate_ai_records(payload: GenerateAiRecordsRequest, db: Session = Depends(get_db)) -> list[Job]:
    rules = _rules_for_request(
        db,
        payload.directory_rule_id,
        empty_detail="No enabled directory rules to generate AI records",
    )
    path_filters = [_media_under_rule_filter(rule.normalized_path) for rule in rules]
    if not path_filters:
        return []

    active_analysis_media_ids = set(
        db.scalars(
            select(Job.target_id).where(
                Job.job_type.in_(list(ANALYSIS_JOB_TYPES)),
                Job.status.in_(list(ACTIVE_ANALYSIS_JOB_STATUSES)),
                Job.target_id.is_not(None),
            )
        ).all()
    )
    candidates = list(
        db.scalars(
            select(MediaFile)
            .where(
                MediaFile.status.in_(("metadata_done", "needs_reanalysis")),
                or_(*path_filters),
            )
            .order_by(MediaFile.created_at.asc())
        ).all()
    )

    jobs: list[Job] = []
    queued_media_ids = set()
    for media in candidates:
        if media.id in active_analysis_media_ids or media.id in queued_media_ids:
            continue
        if media.media_type == "video":
            job_type = "analyze_video"
        elif media.media_type == "image":
            job_type = "analyze_image"
        else:
            continue
        media.error_message = None
        db.add(media)
        jobs.append(create_job(db, job_type=job_type, target_id=media.id, target_path=media.path))
        queued_media_ids.add(media.id)
    return jobs


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
    if not active_media_ids:
        return MediaQueueResponse(items=[], total=0)

    visibility_filter = visible_media_filter(db)
    media_filter = MediaFile.id.in_(list(active_media_ids))
    media_files = list(
        db.scalars(
            select(MediaFile)
            .where(visibility_filter, media_filter)
            .order_by(MediaFile.updated_at.desc(), MediaFile.created_at.desc())
        ).all()
    )

    current_entries: list[tuple[MediaFile, Job]] = []
    for media in media_files:
        current_jobs = _current_jobs_for_media(jobs_by_media.get(media.id, []), media)
        job = _select_current_job(current_jobs, media)
        if job is None:
            continue
        current_entries.append((media, job))

    total = len(current_entries)
    page_size = max(0, min(limit, 500))
    items = []
    for media, job in current_entries[:page_size]:
        items.append(
            MediaQueueItem(
                media_id=media.id,
                path=media.path,
                thumbnail_url=f"/api/media/{media.id}/thumbnail" if media.thumbnail_path else None,
                media_status=media.status,
                job_id=job.id if job else None,
                job_type=job.job_type if job else None,
                job_status=job.status if job else None,
                job_progress_current=job.progress_current if job else 0,
                job_progress_total=job.progress_total if job else 0,
                job_payload=job.payload if job else None,
                error_message=_current_error_message(job, media),
                updated_at=media.updated_at,
                job_created_at=job.created_at if job else None,
                job_started_at=job.started_at if job else None,
            )
        )

    return MediaQueueResponse(items=items, total=total)


def _current_jobs_for_media(jobs: list[Job], media: MediaFile) -> list[Job]:
    return [job for job in jobs if not _is_superseded_failed_job(job, media)]


def _is_superseded_failed_job(job: Job, media: MediaFile) -> bool:
    if job.status != "failed":
        return False
    if media.status == "done":
        return True
    if media.status == "embedding_pending" and job.job_type in ANALYSIS_JOB_TYPES:
        return True
    return False


def _select_current_job(jobs: list[Job], media: MediaFile) -> Job | None:
    if not jobs:
        return None
    if media.status in {"done", "embedding_pending"}:
        embedding_jobs = [
            job
            for job in jobs
            if job.job_type == "generate_embedding" and job.status in ACTIVE_JOB_STATUSES
        ]
        if embedding_jobs:
            jobs = embedding_jobs
    status_rank = {"running": 0, "queued": 1, "failed": 2}

    def sort_key(job: Job) -> tuple[int, float]:
        timestamp = job.started_at or job.created_at
        return (status_rank.get(job.status, 9), -timestamp.timestamp())

    return sorted(jobs, key=sort_key)[0]


def _current_error_message(job: Job | None, media: MediaFile) -> str | None:
    if job is None:
        return media.error_message
    if job.error_message:
        return job.error_message
    if job.status in {"queued", "running"}:
        return None
    return media.error_message


def _rules_for_request(
    db: Session,
    directory_rule_id: object | None,
    *,
    empty_detail: str,
) -> list[DirectoryRule]:
    if directory_rule_id:
        rule = db.get(DirectoryRule, directory_rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Directory rule not found")
        return [rule]
    rules = list(db.scalars(select(DirectoryRule).where(DirectoryRule.enabled)).all())
    if not rules:
        raise HTTPException(status_code=400, detail=empty_detail)
    return rules


def _media_under_rule_filter(normalized_path: str):
    normalized = normalized_path.rstrip("/")
    return or_(
        MediaFile.normalized_path == normalized,
        MediaFile.normalized_path.like(f"{_escape_like(normalized)}/%", escape="\\"),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
