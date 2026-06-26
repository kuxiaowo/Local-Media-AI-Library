from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    database_url: str = "mysql+pymysql://media_ai:media_ai@localhost:3306/media_ai?charset=utf8mb4"

    ollama_base_url: str = "http://localhost:11434"
    ollama_timeout_seconds: int = 180
    ollama_keep_alive: str = "30m"

    cache_dir: Path = Path("./data/cache")
    thumbnail_dir: Path = Path("./data/thumbnails")
    frame_cache_dir: Path = Path("./data/video_frames")
    max_image_long_edge: int = 1280
    default_max_frames_per_video: int = 12
    default_frame_interval_seconds: int = 5
    default_video_frame_max_width: int = 1280
    default_video_batch_size: int = 6
    default_video_batch_overlap: int = 1
    default_video_frame_max_height: int | None = None
    ollama_num_ctx: int = 32768

    default_vision_model: str = "qwen2.5vl:7b"
    default_summary_model: str = "qwen3:8b"
    default_embedding_model: str = "nomic-embed-text"
    default_embedding_dimensions: int = 768

    worker_poll_seconds: float = 1.0
    scan_worker_concurrency: int = 1
    metadata_worker_concurrency: int = 6
    vision_worker_concurrency: int = 1
    embedding_worker_concurrency: int = 2
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ]
    )

    def ensure_dirs(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)
        self.frame_cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
