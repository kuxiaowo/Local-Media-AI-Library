from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass
from queue import Empty, Queue

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import Settings
from app.database import SessionLocal
from app.models.db_models import DirectoryRule, Job, MediaFile
from app.services.ai_analyzer import analyze_image, analyze_video
from app.services.embedding_service import generate_embedding
from app.services.job_service import mark_completed, mark_failed, mark_running
from app.services.metadata_extractor import extract_image_metadata, extract_video_metadata
from app.services.ollama_client import OllamaClient
from app.services.rule_resolver import resolve_rule, rule_config_hash
from app.services.scanner import scan_directory
from app.services.stale_cleanup import cleanup_stale_media


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
        self._dispatcher_thread: threading.Thread | None = None
        self._worker_threads: list[threading.Thread] = []
        self._configure_pools(pools or self._default_pools())

    @staticmethod
    def _default_pools() -> list[WorkerPoolConfig]:
        return [
            WorkerPoolConfig(("scan_directory",), 1, "scan"),
            WorkerPoolConfig(("extract_metadata",), 6, "metadata"),
            WorkerPoolConfig(("analyze_image", "analyze_video"), 1, "vision"),
            WorkerPoolConfig(("generate_embedding",), 2, "embedding"),
            WorkerPoolConfig(("reanalyze_media",), 1, "reanalyze"),
            WorkerPoolConfig(("cleanup_stale_media",), 1, "maintenance"),
        ]

    @staticmethod
    def _pools_from_settings(settings: Settings) -> list[WorkerPoolConfig]:
        return [
            WorkerPoolConfig(("scan_directory",), settings.scan_worker_concurrency, "scan"),
            WorkerPoolConfig(("extract_metadata",), settings.metadata_worker_concurrency, "metadata"),
            WorkerPoolConfig(("analyze_image", "analyze_video"), settings.vision_worker_concurrency, "vision"),
            WorkerPoolConfig(("generate_embedding",), settings.embedding_worker_concurrency, "embedding"),
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
            with suppress(Exception):
                self._dispatch_once()
            stop_event.wait(self.poll_seconds)

    def _dispatch_once(self) -> None:
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
            try:
                job_id = queue.get(timeout=self.poll_seconds)
            except Empty:
                continue
            try:
                self._run_job(job_id)
            finally:
                with self._queued_lock:
                    self._queued_job_ids.discard(job_id)
                queue.task_done()

    def _run_job(self, job_id: str) -> None:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job is None or job.status != "queued":
                return
            mark_running(job)
            db.commit()
            try:
                self._execute_job(db, job)
                mark_completed(job)
                db.commit()
            except Exception as exc:
                db.rollback()
                job = db.get(Job, job.id)
                if job is not None:
                    mark_failed(job, str(exc))
                    self._mark_target_media_failed(db, job, str(exc))
                    db.commit()

    def _execute_job(self, db, job: Job) -> None:
        if job.job_type == "scan_directory":
            rule = db.get(DirectoryRule, job.target_id)
            if rule is None:
                raise RuntimeError("Directory rule does not exist")
            mode = (job.payload or {}).get("mode", "incremental")
            discovered = scan_directory(db, rule, job.id, mode=mode)
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
            rules = db.scalars(select(DirectoryRule).where(DirectoryRule.enabled)).all()
            rule = resolve_rule(media.path, list(rules))
            if rule is None:
                media.status = "failed"
                media.error_message = "No enabled directory rule matched this media file"
                db.commit()
                raise RuntimeError(media.error_message)
            media.folder_rule_id = rule.id
            media.resolved_config_hash = rule_config_hash(rule)
            if media.media_type == "video":
                extract_video_metadata(db, media)
            else:
                extract_image_metadata(db, media)
            db.commit()
            if media.status == "metadata_done" and rule.enabled:
                from app.services.job_service import create_job

                analyze_job_type = "analyze_video" if media.media_type == "video" else "analyze_image"
                create_job(db, job_type=analyze_job_type, target_id=media.id, target_path=media.path)
            return

        if job.job_type in {"analyze_image", "analyze_video"}:
            media = db.scalar(
                select(MediaFile)
                .options(joinedload(MediaFile.folder_rule))
                .where(MediaFile.id == job.target_id)
            )
            if media is None:
                raise RuntimeError("Media file does not exist")
            if job.job_type == "analyze_video":
                asyncio.run(analyze_video(db, media, OllamaClient()))
            else:
                asyncio.run(analyze_image(db, media, OllamaClient()))
            from app.services.job_service import create_job

            create_job(db, job_type="generate_embedding", target_id=media.id, target_path=media.path)
            return

        if job.job_type == "generate_embedding":
            media = db.scalar(
                select(MediaFile)
                .options(joinedload(MediaFile.folder_rule), joinedload(MediaFile.ai_summary))
                .where(MediaFile.id == job.target_id)
            )
            if media is None:
                raise RuntimeError("Media file does not exist")
            asyncio.run(generate_embedding(db, media, OllamaClient()))
            return

        if job.job_type == "reanalyze_media":
            media = db.get(MediaFile, job.target_id)
            if media is None:
                raise RuntimeError("Media file does not exist")
            media.status = "needs_reanalysis"
            db.commit()
            from app.services.job_service import create_job

            create_job(db, job_type="extract_metadata", target_id=media.id, target_path=media.path)
            return

        if job.job_type == "cleanup_stale_media":
            cleanup_stale_media(db, job)
            return

        raise RuntimeError(f"Unknown job type: {job.job_type}")

    @staticmethod
    def _mark_target_media_failed(db, job: Job, error: str) -> None:
        if job.job_type not in {
            "extract_metadata",
            "analyze_image",
            "analyze_video",
            "generate_embedding",
            "reanalyze_media",
        }:
            return
        if job.target_id is None:
            return
        media = db.get(MediaFile, job.target_id)
        if media is None or media.status == "missing":
            return
        media.status = "failed"
        media.error_message = error[:4000]
        db.add(media)
