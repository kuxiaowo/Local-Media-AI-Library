from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from queue import Empty, Queue

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models.db_models import DirectoryRule, EmbeddingProfile, Job, MediaEmbedding, MediaFile
from app.services.ai_analyzer import analyze_image, analyze_video, regenerate_video_final_summary
from app.services.embedding_service import generate_embedding
from app.services.job_service import mark_completed, mark_failed, mark_running
from app.services.media_visibility import is_media_visible, is_rule_effectively_enabled
from app.services.metadata_extractor import extract_image_metadata, extract_video_metadata
from app.services.ollama_client import OllamaClient
from app.services.rule_resolver import resolve_rule, rule_config_hash
from app.services.scanner import scan_directory
from app.services.stale_cleanup import cleanup_stale_media


MEDIA_JOB_TYPES = {
    "extract_metadata",
    "analyze_image",
    "analyze_video",
    "reanalyze_video_summary",
    "reanalyze_media",
}
ANALYSIS_JOB_TYPES = {"analyze_image", "analyze_video", "reanalyze_video_summary"}
INTERRUPTED_ACTIVE_MEDIA_STATUSES = {
    "pending",
    "metadata_done",
    "analyzing",
    "needs_reanalysis",
    "embedding_pending",
}


@dataclass(frozen=True)
class WorkerPoolConfig:
    job_types: tuple[str, ...]
    concurrency: int
    name: str


class WorkerManager:
    def __init__(self, poll_seconds: float = 1.0, pools: list[WorkerPoolConfig] | None = None) -> None:
        self.poll_seconds = poll_seconds
        self._lifecycle_lock = threading.RLock()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._dispatcher_thread: threading.Thread | None = None
        self._worker_threads: list[threading.Thread] = []
        self._configure_pools(pools or self._default_pools())

    @staticmethod
    def _default_pools() -> list[WorkerPoolConfig]:
        return [
            WorkerPoolConfig(("scan_directory",), 1, "scan"),
            WorkerPoolConfig(("extract_metadata",), 6, "metadata"),
            WorkerPoolConfig(("analyze_image", "analyze_video", "reanalyze_video_summary"), 1, "vision"),
            WorkerPoolConfig(("reanalyze_media",), 1, "reanalyze"),
            WorkerPoolConfig(("cleanup_stale_media",), 1, "maintenance"),
        ]

    @staticmethod
    def _pools_from_settings(settings: Settings) -> list[WorkerPoolConfig]:
        return [
            WorkerPoolConfig(("scan_directory",), settings.scan_worker_concurrency, "scan"),
            WorkerPoolConfig(("extract_metadata",), settings.metadata_worker_concurrency, "metadata"),
            WorkerPoolConfig(
                ("analyze_image", "analyze_video", "reanalyze_video_summary"),
                settings.vision_worker_concurrency,
                "vision",
            ),
            WorkerPoolConfig(("reanalyze_media",), 1, "reanalyze"),
            WorkerPoolConfig(("cleanup_stale_media",), 1, "maintenance"),
        ]

    def _configure_pools(self, pools: list[WorkerPoolConfig]) -> None:
        self._queues: dict[str, Queue[str]] = {}
        self._queued_job_ids: set[str] = set()
        self._queued_lock = threading.Lock()
        self._job_type_to_queue: dict[str, Queue[str]] = {}
        self._pools = pools
        for pool in self._pools:
            queue: Queue[str] = Queue()
            self._queues[pool.name] = queue
            for job_type in pool.job_types:
                self._job_type_to_queue[job_type] = queue

    @classmethod
    def from_settings(cls, settings: Settings) -> "WorkerManager":
        return cls(poll_seconds=settings.worker_poll_seconds, pools=cls._pools_from_settings(settings))

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._dispatcher_thread and self._dispatcher_thread.is_alive():
                return
            self._recover_interrupted_completed_analysis_jobs()
            self._recover_interrupted_active_media_jobs()
            self._recover_interrupted_failed_media_jobs()
            self._stop.clear()
            self._dispatcher_thread = threading.Thread(
                target=self._dispatch_loop,
                args=(self._stop,),
                name="media-ai-dispatcher",
                daemon=True,
            )
            self._dispatcher_thread.start()
            for pool in self._pools:
                queue = self._queues[pool.name]
                for index in range(max(0, pool.concurrency)):
                    thread = threading.Thread(
                        target=self._worker_loop,
                        args=(queue, self._stop),
                        name=f"media-ai-{pool.name}-{index + 1}",
                        daemon=True,
                    )
                    self._worker_threads.append(thread)
                    thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_current_threads()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def is_paused(self) -> bool:
        return self._pause.is_set()

    def reconfigure_from_settings(self, settings: Settings) -> None:
        with self._lifecycle_lock:
            self._stop_current_threads()
            self.poll_seconds = settings.worker_poll_seconds
            self._stop = threading.Event()
            self._dispatcher_thread = None
            self._worker_threads = []
            self._configure_pools(self._pools_from_settings(settings))
            self.start()

    def _stop_current_threads(self) -> None:
        self._stop.set()
        if self._dispatcher_thread:
            self._dispatcher_thread.join(timeout=5)
        for thread in self._worker_threads:
            thread.join(timeout=5)

    def _dispatch_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if self.is_paused():
                stop_event.wait(self.poll_seconds)
                continue
            with suppress(Exception):
                self._dispatch_once()
            stop_event.wait(self.poll_seconds)

    def _dispatch_once(self) -> None:
        if self.is_paused():
            return
        with SessionLocal() as db:
            jobs = db.scalars(
                select(Job)
                .where(Job.status == "queued", Job.job_type.in_(self._job_type_to_queue.keys()))
                .order_by(Job.created_at.asc())
                .limit(100)
            ).all()
            for job in jobs:
                job_id = str(job.id)
                with self._queued_lock:
                    if job_id in self._queued_job_ids:
                        continue
                    self._queued_job_ids.add(job_id)
                self._job_type_to_queue[job.job_type].put(job_id)

    def _worker_loop(self, queue: Queue[str], stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if self.is_paused():
                stop_event.wait(self.poll_seconds)
                continue
            try:
                job_id = queue.get(timeout=self.poll_seconds)
            except Empty:
                continue
            try:
                if not self.is_paused():
                    self._run_job(job_id)
            finally:
                with self._queued_lock:
                    self._queued_job_ids.discard(job_id)
                queue.task_done()

    def _run_job(self, job_id: str) -> None:
        if self.is_paused():
            return
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is None or job.status != "queued":
                return
            job_pk = job.id
            mark_running(job)
            db.commit()
            try:
                self._execute_job(db, job)
                if _job_row_exists(db, job_pk):
                    mark_completed(job)
                    db.commit()
            except Exception as exc:
                db.rollback()
                job = db.scalar(select(Job).where(Job.id == job_pk))
                if job is not None:
                    mark_failed(job, str(exc))
                    self._mark_target_media_failed(db, job, str(exc))
                    db.commit()

    def _execute_job(self, db, job: Job) -> None:
        if job.job_type == "scan_directory":
            rule = db.get(DirectoryRule, job.target_id)
            if rule is None:
                raise RuntimeError("Directory rule does not exist")
            if not is_rule_effectively_enabled(rule, list(db.scalars(select(DirectoryRule)).all())):
                return
            payload = job.payload or {}
            mode = payload.get("mode", "incremental")
            run_ai = payload.get("run_ai", True) is not False
            discovered = scan_directory(db, rule, job.id, mode=mode, run_ai=run_ai)
            job.progress_current = discovered
            job.progress_total = discovered
            return

        if job.job_type == "extract_metadata":
            media = db.scalar(
                select(MediaFile)
                .options(joinedload(MediaFile.folder_rule))
                .where(MediaFile.id == job.target_id)
            )
            if media is None:
                raise RuntimeError("Media file does not exist")
            if not is_media_visible(db, media):
                return
            rules = db.scalars(select(DirectoryRule)).all()
            rule = resolve_rule(media.path, list(rules))
            if rule is None:
                return
            media.folder_rule_id = rule.id
            media.resolved_config_hash = rule_config_hash(rule)
            if media.media_type == "video":
                extract_video_metadata(db, media)
            else:
                extract_image_metadata(db, media)
            db.commit()
            run_ai = (job.payload or {}).get("run_ai", True) is not False
            if media.status == "metadata_done" and rule.enabled and run_ai and _job_row_exists(db, job.id):
                from app.services.job_service import create_job

                analyze_job_type = "analyze_video" if media.media_type == "video" else "analyze_image"
                create_job(db, job_type=analyze_job_type, target_id=media.id, target_path=media.path)
            return

        if job.job_type in {"analyze_image", "analyze_video", "reanalyze_video_summary"}:
            media = db.scalar(
                select(MediaFile)
                .options(joinedload(MediaFile.folder_rule))
                .where(MediaFile.id == job.target_id)
            )
            if media is None:
                raise RuntimeError("Media file does not exist")
            if not is_media_visible(db, media):
                return
            if job.job_type == "analyze_video":
                payload = job.payload or {}
                asyncio.run(
                    analyze_video(
                        db,
                        media,
                        OllamaClient(),
                        progress_callback=lambda stage, current, total: _update_job_progress(
                            db, job, stage, current, total
                        ),
                        resume_existing_segments=bool(payload.get("resume_segments")),
                    )
                )
            elif job.job_type == "reanalyze_video_summary":
                _update_job_progress(db, job, "final_summary", 0, 0)
                asyncio.run(regenerate_video_final_summary(db, media, OllamaClient()))
            else:
                asyncio.run(analyze_image(db, media, OllamaClient()))
            if _job_row_exists(db, job.id):
                _generate_embedding_after_analysis(db, media.id, job)
            return

        if job.job_type == "reanalyze_media":
            media = db.get(MediaFile, job.target_id)
            if media is None:
                raise RuntimeError("Media file does not exist")
            if not is_media_visible(db, media):
                return
            media.status = "needs_reanalysis"
            db.commit()
            from app.services.job_service import create_job

            if _job_row_exists(db, job.id):
                create_job(db, job_type="extract_metadata", target_id=media.id, target_path=media.path)
            return

        if job.job_type == "cleanup_stale_media":
            cleanup_stale_media(db, job)
            return

        raise RuntimeError(f"Unknown job type: {job.job_type}")

    @staticmethod
    def _recover_interrupted_completed_analysis_jobs() -> None:
        with SessionLocal() as db:
            rows = db.execute(
                select(Job, MediaFile)
                .join(MediaFile, Job.target_id == MediaFile.id)
                .where(
                    Job.status == "running",
                    Job.job_type.in_(ANALYSIS_JOB_TYPES),
                    MediaFile.status.in_(("embedding_pending", "done")),
                )
            ).all()
            for job, media in rows:
                if _media_has_default_embedding(db, media):
                    mark_completed(job)
                    media.status = "done"
                    media.error_message = None
                    db.add(media)
                    continue
                error = "Worker was interrupted before the AI summary vector was generated"
                mark_failed(job, error)
                _queue_reanalysis_for_media(db, [media], reason=error)
            if rows:
                db.commit()

    @staticmethod
    def _recover_interrupted_active_media_jobs() -> None:
        with SessionLocal() as db:
            rows = db.execute(
                select(Job, MediaFile)
                .join(MediaFile, Job.target_id == MediaFile.id)
                .where(
                    Job.status == "running",
                    Job.job_type.in_(MEDIA_JOB_TYPES),
                    MediaFile.status.in_(INTERRUPTED_ACTIVE_MEDIA_STATUSES),
                )
            ).all()
            for job, media in rows:
                if _media_has_default_embedding(db, media):
                    media.status = "done"
                    media.error_message = None
                    db.add(media)
                    _mark_superseded(
                        job,
                        "Worker was interrupted after media already had the current embedding",
                    )
                    continue
                error = f"Worker was interrupted while {job.job_type} was running"
                mark_failed(job, error)
                media.status = "failed"
                media.error_message = error
                db.add(media)
            if rows:
                db.commit()

    @staticmethod
    def _recover_interrupted_failed_media_jobs() -> None:
        with SessionLocal() as db:
            rows = db.execute(
                select(Job, MediaFile)
                .join(MediaFile, Job.target_id == MediaFile.id)
                .where(
                    Job.status == "running",
                    Job.job_type.in_(MEDIA_JOB_TYPES),
                    MediaFile.status == "failed",
                )
            ).all()
            for job, media in rows:
                mark_failed(
                    job,
                    media.error_message or "Worker was interrupted after media processing failed",
                )
            if rows:
                db.commit()

    @staticmethod
    def _mark_target_media_failed(db, job: Job, error: str) -> None:
        if job.job_type not in {
            "extract_metadata",
            "analyze_image",
            "analyze_video",
            "reanalyze_video_summary",
            "reanalyze_media",
        }:
            return
        if job.target_id is None:
            return
        media = db.get(MediaFile, job.target_id)
        if media is None or media.status == "missing":
            return
        media.status = "failed"
        media.error_message = error
        db.add(media)


def _update_job_progress(db, job: Job, stage: str, current: int, total: int) -> None:
    if not _job_row_exists(db, job.id):
        return
    job.progress_current = current
    job.progress_total = total
    payload = dict(job.payload or {})
    payload["stage"] = stage
    job.payload = payload
    db.add(job)
    db.commit()


def _generate_embedding_after_analysis(db, media_id: object, job: Job) -> None:
    _update_job_progress(db, job, "generate_embedding", job.progress_total, job.progress_total)
    if not _job_row_exists(db, job.id):
        return
    media = db.scalar(
        select(MediaFile)
        .options(joinedload(MediaFile.folder_rule), joinedload(MediaFile.ai_summary))
        .where(MediaFile.id == media_id)
    )
    if media is None:
        raise RuntimeError("Media file does not exist")
    if not is_media_visible(db, media):
        return
    asyncio.run(generate_embedding(db, media, OllamaClient()))


def _job_row_exists(db, job_id: object) -> bool:
    return db.scalar(select(Job.id).where(Job.id == job_id)) is not None


def _mark_superseded(job: Job, reason: str) -> None:
    job.status = "superseded"
    job.error_message = reason
    job.finished_at = datetime.now(timezone.utc)


def _media_has_default_embedding(db, media: MediaFile) -> bool:
    model_name = get_settings().default_embedding_model.strip()
    if not model_name:
        return db.scalar(select(MediaEmbedding.id).where(MediaEmbedding.media_id == media.id).limit(1)) is not None
    profile = db.scalar(select(EmbeddingProfile).where(EmbeddingProfile.model_name == model_name))
    if profile is None:
        return False
    return (
        db.scalar(
            select(MediaEmbedding.id)
            .where(MediaEmbedding.media_id == media.id, MediaEmbedding.profile_id == profile.id)
            .limit(1)
        )
        is not None
    )


def _queue_reanalysis_for_media(db, media_files: list[MediaFile], *, reason: str) -> None:
    if not media_files:
        return
    media_ids = [media.id for media in media_files]
    active_media_ids = set(
        db.scalars(
            select(Job.target_id).where(
                Job.target_id.in_(media_ids),
                Job.job_type.in_(MEDIA_JOB_TYPES),
                Job.status.in_(("queued", "running")),
            )
        ).all()
    )
    seen_media_ids: set[object] = set()
    for media in media_files:
        if media.id in seen_media_ids or media.status == "missing":
            continue
        seen_media_ids.add(media.id)
        media.status = "needs_reanalysis"
        media.error_message = reason
        db.add(media)
        if media.id not in active_media_ids:
            db.add(
                Job(
                    job_type="reanalyze_media",
                    target_id=media.id,
                    target_path=media.path,
                    payload={},
                )
            )
            active_media_ids.add(media.id)
