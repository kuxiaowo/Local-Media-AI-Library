from pathlib import Path
import os
import subprocess

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
    get_default_analysis_system_prompt,
    get_default_background_context_prompt,
    get_default_video_final_summary_system_prompt,
    get_default_video_final_summary_prompt,
    get_default_video_segment_system_prompt,
    get_default_video_segment_prompt,
    reset_default_analysis_prompt,
    reset_default_analysis_system_prompt,
    reset_default_background_context_prompt,
    reset_default_video_final_summary_system_prompt,
    reset_default_video_final_summary_prompt,
    reset_default_video_segment_system_prompt,
    reset_default_video_segment_prompt,
    update_default_analysis_prompt,
    update_default_analysis_system_prompt,
    update_default_background_context_prompt,
    update_default_video_final_summary_system_prompt,
    update_default_video_final_summary_prompt,
    update_default_video_segment_system_prompt,
    update_default_video_segment_prompt,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class RuntimeSettings(BaseModel):
    default_embedding_model: str = Field(min_length=1)
    default_ai_search_model: str = Field(min_length=1)
    max_image_long_edge: int = Field(ge=256, le=4096)
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

    @field_validator("default_ai_search_model")
    @classmethod
    def normalize_ai_search_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("AI search model is required")
        return normalized


class AnalysisPromptSettings(BaseModel):
    prompt: str = Field(min_length=1)


class DirectoryPickerRequest(BaseModel):
    initial_path: str | None = None


class DirectoryPickerResponse(BaseModel):
    path: str | None = None


@router.get("/defaults")
def defaults() -> dict[str, object]:
    settings = get_settings()
    return {
        "default_vision_model": settings.default_vision_model,
        "default_summary_model": settings.default_summary_model,
        "default_ai_search_model": settings.default_ai_search_model,
        "default_embedding_model": settings.default_embedding_model,
        "default_embedding_dimensions": settings.default_embedding_dimensions,
        "max_image_long_edge": settings.max_image_long_edge,
    }


@router.get("/runtime", response_model=RuntimeSettings)
def runtime_settings() -> RuntimeSettings:
    settings = get_settings()
    return RuntimeSettings(
        default_embedding_model=settings.default_embedding_model,
        default_ai_search_model=settings.default_ai_search_model,
        max_image_long_edge=settings.max_image_long_edge,
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
            "DEFAULT_AI_SEARCH_MODEL": payload.default_ai_search_model.strip(),
            "MAX_IMAGE_LONG_EDGE": str(payload.max_image_long_edge),
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


@router.get("/default-analysis-system-prompt", response_model=AnalysisPromptSettings)
def default_analysis_system_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=get_default_analysis_system_prompt())


@router.put("/default-analysis-system-prompt", response_model=AnalysisPromptSettings)
def save_default_analysis_system_prompt(payload: AnalysisPromptSettings) -> AnalysisPromptSettings:
    try:
        prompt = update_default_analysis_system_prompt(payload.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisPromptSettings(prompt=prompt)


@router.post("/default-analysis-system-prompt/reset", response_model=AnalysisPromptSettings)
def reset_analysis_system_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=reset_default_analysis_system_prompt())


@router.get("/default-background-context-prompt", response_model=AnalysisPromptSettings)
def default_background_context_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=get_default_background_context_prompt())


@router.put("/default-background-context-prompt", response_model=AnalysisPromptSettings)
def save_default_background_context_prompt(payload: AnalysisPromptSettings) -> AnalysisPromptSettings:
    try:
        prompt = update_default_background_context_prompt(payload.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisPromptSettings(prompt=prompt)


@router.post("/default-background-context-prompt/reset", response_model=AnalysisPromptSettings)
def reset_background_context_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=reset_default_background_context_prompt())


@router.get("/default-video-segment-prompt", response_model=AnalysisPromptSettings)
def default_video_segment_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=get_default_video_segment_prompt())


@router.get("/default-video-segment-system-prompt", response_model=AnalysisPromptSettings)
def default_video_segment_system_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=get_default_video_segment_system_prompt())


@router.put("/default-video-segment-system-prompt", response_model=AnalysisPromptSettings)
def save_default_video_segment_system_prompt(payload: AnalysisPromptSettings) -> AnalysisPromptSettings:
    try:
        prompt = update_default_video_segment_system_prompt(payload.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisPromptSettings(prompt=prompt)


@router.post("/default-video-segment-system-prompt/reset", response_model=AnalysisPromptSettings)
def reset_video_segment_system_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=reset_default_video_segment_system_prompt())


@router.put("/default-video-segment-prompt", response_model=AnalysisPromptSettings)
def save_default_video_segment_prompt(payload: AnalysisPromptSettings) -> AnalysisPromptSettings:
    try:
        prompt = update_default_video_segment_prompt(payload.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisPromptSettings(prompt=prompt)


@router.post("/default-video-segment-prompt/reset", response_model=AnalysisPromptSettings)
def reset_video_segment_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=reset_default_video_segment_prompt())


@router.get("/default-video-final-summary-prompt", response_model=AnalysisPromptSettings)
def default_video_final_summary_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=get_default_video_final_summary_prompt())


@router.get("/default-video-final-summary-system-prompt", response_model=AnalysisPromptSettings)
def default_video_final_summary_system_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=get_default_video_final_summary_system_prompt())


@router.put("/default-video-final-summary-system-prompt", response_model=AnalysisPromptSettings)
def save_default_video_final_summary_system_prompt(payload: AnalysisPromptSettings) -> AnalysisPromptSettings:
    try:
        prompt = update_default_video_final_summary_system_prompt(payload.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisPromptSettings(prompt=prompt)


@router.post("/default-video-final-summary-system-prompt/reset", response_model=AnalysisPromptSettings)
def reset_video_final_summary_system_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=reset_default_video_final_summary_system_prompt())


@router.put("/default-video-final-summary-prompt", response_model=AnalysisPromptSettings)
def save_default_video_final_summary_prompt(payload: AnalysisPromptSettings) -> AnalysisPromptSettings:
    try:
        prompt = update_default_video_final_summary_prompt(payload.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AnalysisPromptSettings(prompt=prompt)


@router.post("/default-video-final-summary-prompt/reset", response_model=AnalysisPromptSettings)
def reset_video_final_summary_prompt() -> AnalysisPromptSettings:
    return AnalysisPromptSettings(prompt=reset_default_video_final_summary_prompt())


@router.post("/cleanup-stale-media", response_model=JobRead)
def start_cleanup_stale_media(db: Session = Depends(get_db)) -> Job:
    return create_job(
        db,
        job_type="cleanup_stale_media",
        target_path="stale media cleanup",
        payload={"mode": "delete_missing_files_and_missing_roots"},
    )


@router.post("/browse-directory", response_model=DirectoryPickerResponse)
def browse_directory(payload: DirectoryPickerRequest) -> DirectoryPickerResponse:
    try:
        selected = _pick_directory(payload.initial_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return DirectoryPickerResponse(path=selected)


def _pick_directory(initial_path: str | None) -> str | None:
    try:
        return _pick_directory_tk(initial_path)
    except RuntimeError:
        if os.name != "nt":
            raise
        return _pick_directory_windows(initial_path)


def _pick_directory_windows(initial_path: str | None) -> str | None:
    script = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择媒体库文件夹'
$dialog.ShowNewFolderButton = $true
$initialPath = $env:LOCAL_MEDIA_AI_INITIAL_PATH
if ($initialPath -and [System.IO.Directory]::Exists($initialPath)) {
    $dialog.SelectedPath = $initialPath
}
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dialog.SelectedPath
}
"""
    env = os.environ.copy()
    env["LOCAL_MEDIA_AI_INITIAL_PATH"] = initial_path or ""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            check=False,
            encoding="utf-8",
            env=env,
        )
    except OSError as exc:
        raise RuntimeError(f"Unable to open folder picker: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Unable to open folder picker: {detail}")
    selected = result.stdout.strip()
    return selected or None


def _pick_directory_tk(initial_path: str | None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError(f"Folder picker is not available: {exc}") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()
    root.lift()
    root.focus_force()
    try:
        selected = filedialog.askdirectory(
            parent=root,
            title="选择媒体库文件夹",
            initialdir=initial_path if initial_path and Path(initial_path).is_dir() else None,
        )
    finally:
        root.destroy()
    return selected or None


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
