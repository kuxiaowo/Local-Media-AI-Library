from datetime import timezone

from app.core.time_extract import extract_video_captured_time


def test_extract_video_captured_time_prefers_creation_time(tmp_path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")

    captured = extract_video_captured_time(video, "2025-06-01T12:34:56Z")

    assert captured.source == "video_creation_time"
    assert captured.confidence == "high"
    assert captured.captured_at.tzinfo == timezone.utc
    assert captured.captured_at.year == 2025
