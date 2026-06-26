from __future__ import annotations

from app.models.db_models import Job
from app.services.job_service import mark_failed


def test_mark_failed_preserves_full_error_message() -> None:
    error = "Ollama returned invalid JSON\n" + ("x" * 6000)
    job = Job(job_type="analyze_video", payload={})

    mark_failed(job, error)

    assert job.status == "failed"
    assert job.error_message == error
