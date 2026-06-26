from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from app.config import get_settings


@dataclass(frozen=True)
class VideoInfo:
    width: int | None
    height: int | None
    duration_seconds: float | None
    creation_time: str | None
    raw_metadata: dict


@dataclass(frozen=True)
class ExtractedFrame:
    timestamp_seconds: float
    frame_path: str


def batch_frames(
    frames: list[ExtractedFrame],
    batch_size: int = 6,
    overlap: int = 0,
) -> list[list[ExtractedFrame]]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if overlap < 0:
        raise ValueError("overlap must be at least 0")
    normalized_overlap = min(overlap, batch_size - 1)
    step = batch_size - normalized_overlap
    return [frames[index : index + batch_size] for index in range(0, len(frames), step)]


def probe_video(video_path: str | Path) -> VideoInfo:
    path = Path(video_path)
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = _run(command, tool_name="ffprobe", timeout=60)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc

    streams = payload.get("streams") or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise RuntimeError(f"No video stream found: {path}")

    format_info = payload.get("format") or {}
    width = _int_or_none(video_stream.get("width"))
    height = _int_or_none(video_stream.get("height"))
    duration = _float_or_none(video_stream.get("duration")) or _float_or_none(format_info.get("duration"))
    creation_time = _first_creation_time(video_stream, format_info)

    return VideoInfo(
        width=width,
        height=height,
        duration_seconds=duration,
        creation_time=creation_time,
        raw_metadata=payload,
    )


def generate_video_thumbnail(video_path: str | Path, media_id: UUID, timestamp_seconds: float = 1.0) -> str:
    settings = get_settings()
    settings.thumbnail_dir.mkdir(parents=True, exist_ok=True)
    output_path = settings.thumbnail_dir / f"{media_id}.jpg"
    try:
        _extract_single_frame(video_path, output_path, timestamp_seconds, max_width=512)
    except RuntimeError:
        _extract_single_frame(video_path, output_path, 0.0, max_width=512)
    return str(output_path.resolve())


def extract_video_frames(
    video_path: str | Path,
    media_id: UUID,
    *,
    strategy: str,
    interval_seconds: int,
    max_frames: int,
    max_width: int,
    max_height: int | None = None,
    duration_seconds: float | None = None,
    run_id: str | None = None,
) -> list[ExtractedFrame]:
    if max_frames < 1:
        raise ValueError("max_frames must be at least 1")
    if interval_seconds < 1:
        raise ValueError("interval_seconds must be at least 1")
    if max_width < 1:
        raise ValueError("max_width must be at least 1")
    if max_height is not None and max_height < 1:
        raise ValueError("max_height must be at least 1")

    settings = get_settings()
    frame_root = settings.frame_cache_dir.resolve()
    output_dir = (frame_root / str(media_id) / (run_id or uuid4().hex)).resolve()
    try:
        output_dir.relative_to(frame_root)
    except ValueError as exc:
        raise RuntimeError(f"Frame cache path is outside {frame_root}") from exc
    output_dir.mkdir(parents=True, exist_ok=True)

    scenes: list[float] = []
    if strategy in {"scene", "hybrid"}:
        scenes = detect_scene_timestamps(video_path, max_candidates=max(max_frames * 3, max_frames))

    timestamps = choose_frame_timestamps(
        duration_seconds=duration_seconds,
        strategy=strategy,
        interval_seconds=interval_seconds,
        max_frames=max_frames,
        scene_timestamps=scenes,
    )
    if not timestamps:
        timestamps = [0.0]

    frames: list[ExtractedFrame] = []
    errors: list[str] = []
    for index, timestamp in enumerate(timestamps, start=1):
        output_path = output_dir / f"frame_{index:04d}.jpg"
        try:
            _extract_single_frame(video_path, output_path, timestamp, max_width=max_width, max_height=max_height)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        frames.append(ExtractedFrame(timestamp_seconds=timestamp, frame_path=str(output_path.resolve())))

    if not frames:
        detail = errors[0] if errors else "ffmpeg did not produce any frame"
        raise RuntimeError(f"Video frame extraction failed: {detail}")

    return frames


def detect_scene_timestamps(video_path: str | Path, *, max_candidates: int) -> list[float]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(Path(video_path)),
        "-vf",
        r"select=gt(scene\,0.35),showinfo",
        "-an",
        "-frames:v",
        str(max_candidates),
        "-f",
        "null",
        "-",
    ]
    try:
        result = _run(command, tool_name="ffmpeg", timeout=300)
    except RuntimeError:
        return []
    text = f"{result.stdout}\n{result.stderr}"
    timestamps = [float(match.group(1)) for match in re.finditer(r"pts_time:([0-9.]+)", text)]
    return _dedupe_timestamps(timestamps)


def choose_frame_timestamps(
    *,
    duration_seconds: float | None,
    strategy: str,
    interval_seconds: int,
    max_frames: int,
    scene_timestamps: list[float] | None = None,
) -> list[float]:
    duration = duration_seconds if duration_seconds and duration_seconds > 0 else None
    fixed = _fixed_interval_timestamps(duration, interval_seconds)
    scenes = _dedupe_timestamps(scene_timestamps or [])

    if strategy == "scene":
        candidates = scenes or fixed
    elif strategy == "hybrid":
        candidates = _dedupe_timestamps([*fixed, *scenes])
    else:
        candidates = fixed

    if duration is not None:
        candidates = [min(max(timestamp, 0.0), max(duration - 0.1, 0.0)) for timestamp in candidates]
    candidates = _dedupe_timestamps(candidates)
    return _limit_timestamps(candidates, max_frames)


def _extract_single_frame(
    video_path: str | Path,
    output_path: Path,
    timestamp_seconds: float,
    *,
    max_width: int | None = None,
    max_height: int | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(timestamp_seconds, 0.0):.3f}",
        "-i",
        str(Path(video_path)),
        "-frames:v",
        "1",
    ]
    scale_filter = build_scale_filter(max_width=max_width, max_height=max_height)
    if scale_filter:
        command.extend(["-vf", scale_filter])
    command.extend([
        "-q:v",
        "3",
        str(output_path),
    ])
    _run(command, tool_name="ffmpeg", timeout=120)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg did not create frame at {timestamp_seconds:.3f}s")


def build_scale_filter(*, max_width: int | None, max_height: int | None = None) -> str | None:
    if max_width is None or max_width <= 0:
        return None
    if max_height is not None and max_height > 0:
        return f"scale={int(max_width)}:{int(max_height)}"
    return f"scale=w='min(iw,{int(max_width)})':h=-2"


def _fixed_interval_timestamps(duration_seconds: float | None, interval_seconds: int) -> list[float]:
    if duration_seconds is None:
        return [0.0]
    if duration_seconds <= interval_seconds:
        return [0.0]
    timestamps: list[float] = []
    timestamp = 0.0
    while timestamp < duration_seconds:
        timestamps.append(round(timestamp, 3))
        timestamp += interval_seconds
    return timestamps or [0.0]


def _limit_timestamps(timestamps: list[float], max_frames: int) -> list[float]:
    if len(timestamps) <= max_frames:
        return timestamps
    if max_frames == 1:
        return [timestamps[0]]
    step = (len(timestamps) - 1) / (max_frames - 1)
    selected = [timestamps[round(index * step)] for index in range(max_frames)]
    return _dedupe_timestamps(selected)[:max_frames]


def _dedupe_timestamps(timestamps: list[float], min_gap: float = 0.25) -> list[float]:
    result: list[float] = []
    for timestamp in sorted(max(0.0, float(value)) for value in timestamps):
        if not result or abs(timestamp - result[-1]) >= min_gap:
            result.append(round(timestamp, 3))
    return result


def _first_creation_time(video_stream: dict, format_info: dict) -> str | None:
    for source in (video_stream, format_info):
        tags = source.get("tags") or {}
        for key, value in tags.items():
            if key.lower() == "creation_time" and value:
                return str(value)
    return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _run(command: list[str], *, tool_name: str, timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{tool_name} is not installed or not available in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{tool_name} timed out") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip().splitlines()
        message = detail[-1] if detail else str(exc)
        raise RuntimeError(f"{tool_name} failed: {message}") from exc
