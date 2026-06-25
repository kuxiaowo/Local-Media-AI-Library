from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.db_models import Job
from app.models.schemas import JobRead
from app.services.job_service import create_job
from app.services.prompt_settings import (
    get_default_analysis_prompt,
    reset_default_analysis_prompt,
    update_default_analysis_prompt,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class RuntimeSettings(BaseModel):
    default_embedding_model: str = Field(min_length=1)
    scan_worker_concurrency: int = Field(ge=1, le=8)
    metadata_worker_concurrency: int = Field(ge=1, le=32)
    vision_worker_concurrency: int = Field(ge=1, le=4)
    embedding_worker_concurrency: int = Field(ge=1, le=16)

    @field_validator("default_embedding_model")
    @classmethod
    def normalize_embedding_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Embedding model is required")
        return normalized


class AnalysisPromptSettings(BaseModel):
    prompt: str = Field(min_length=1)


@router.get("/defaults")
def defaults() -> dict[str, object]:
    settings = get_settings()
    return {
        "default_vision_model": settings.default_vision_model,
        "default_summary_model": settings.default_summary_model,
        "default_embedding_model": settings.default_embedding_model,
        "default_embedding_dimensions": settings.default_embedding_dimensions,
        "max_image_long_edge": settings.max_image_long_edge,
    }


@router.get("/runtime", response_model=RuntimeSettings)
def runtime_settings() -> RuntimeSettings:
    settings = get_settings()
    return RuntimeSettings(
        default_embedding_model=settings.default_embedding_model,
        scan_worker_concurrency=settings.scan_worker_concurrency,
        metadata_worker_concurrency=settings.metadata_worker_concurrency,
        vision_worker_concurrency=settings.vision_worker_concurrency,
        embedding_worker_concurrency=settings.embedding_worker_concurrency,
    )


@router.put("/runtime", response_model=RuntimeSettings)
def update_runtime_settings(payload: RuntimeSettings, request: Request) -> RuntimeSettings:
    _update_env_values(
        Path(".env"),
        {
            "DEFAULT_EMBEDDING_MODEL": payload.default_embedding_model.strip(),
            "SCAN_WORKER_CONCURRENCY": str(payload.scan_worker_concurrency),
            "METADATA_WORKER_CONCURRENCY": str(payload.metadata_worker_concurrency),
            "VISION_WORKER_CONCURRENCY": str(payload.vision_worker_concurrency),
            "EMBEDDING_WORKER_CONCURRENCY": str(payload.embedding_worker_concurrency),
        },
    )
    get_settings.cache_clear()
    settings = get_settings()
    worker_manager = getattr(request.app.state, "worker_manager", None)
    if worker_manager is not None:
        worker_manager.reconfigure_from_settings(settings)
    return runtime_settings()


@router.get("/default-analysis-prompt", response_model=AnalysisPromptSettings)
def default_analysis_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=get_default_analysis_prompt())


@router.put("/default-analysis-prompt", response_model=AnalysisPromptSettings)
def save_default_analysis_prompt(payload: AnalysisPromptSettings) -> AnalysisPromptSettings:
    try:
        prompt = update_default_analysis_prompt(payload.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisPromptSettings(prompt=prompt)


@router.post("/default-analysis-prompt/reset", response_model=AnalysisPromptSettings)
def reset_analysis_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=reset_default_analysis_prompt())


@router.post("/cleanup-stale-media", response_model=JobRead)
def start_cleanup_stale_media(db: Session = Depends(get_db)) -> Job:
    return create_job(
        db,
        job_type="cleanup_stale_media",
        target_path="stale media cleanup",
        payload={"mode": "delete_missing_files_and_missing_roots"},
    )


def _update_env_values(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            next_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={value}")

    path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
