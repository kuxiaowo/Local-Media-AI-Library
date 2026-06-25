from app.prompts.video_analysis import (
    VIDEO_FINAL_SUMMARY_USER_PROMPT,
    VIDEO_SEGMENT_ANALYSIS_USER_PROMPT,
    build_video_final_summary_user_prompt,
    build_video_segment_user_prompt,
)


def test_video_segment_prompt_includes_previous_state_and_frame_timestamps() -> None:
    prompt = build_video_segment_user_prompt(
        previous_global_summary="previous global summary",
        previous_timeline=[{"start_time": "00:00:00", "end_time": "00:00:05", "summary": "opening"}],
        frame_infos=[
            {
                "image_order": 1,
                "frame_index": 1,
                "timestamp_seconds": 5.0,
                "timestamp": "00:00:05",
            }
        ],
    )

    assert "previous global summary" in prompt
    assert "opening" in prompt
    assert "00:00:05" in prompt
    assert "输入 JSON" in prompt
    assert "current_frame_info" in prompt
    assert "按 schema 返回严格 JSON" in prompt


def test_default_video_segment_prompt_describes_structured_outputs() -> None:
    assert "片段摘要" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "标签" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "重要物体" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "OCR" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "滚动摘要和时间线" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT


def test_video_segment_prompt_uses_directory_prompt_before_default() -> None:
    prompt = build_video_segment_user_prompt(
        previous_global_summary="",
        previous_timeline=[],
        frame_infos=[],
        custom_segment_prompt="directory segment prompt",
        default_segment_prompt="global segment prompt",
    )

    assert "directory segment prompt" in prompt
    assert "global segment prompt" not in prompt


def test_video_final_summary_prompt_contains_all_segment_summaries() -> None:
    prompt = build_video_final_summary_user_prompt(
        duration_seconds=12.0,
        segments=[
            {
                "segment_index": 1,
                "start_time_seconds": 0.0,
                "end_time_seconds": 5.0,
                "current_segment_summary": "first segment summary",
                "current_segment_tags": ["desk"],
                "ocr_text": ["hello"],
            },
            {
                "segment_index": 2,
                "start_time_seconds": 5.0,
                "end_time_seconds": 10.0,
                "current_segment_summary": "second segment summary",
                "current_segment_tags": ["screen"],
                "ocr_text": ["world"],
            },
        ],
        rolling_global_summary="rolling summary",
        rolling_timeline=[],
        custom_final_prompt=None,
        default_final_prompt="global final prompt",
    )

    assert "global final prompt" in prompt
    assert "first segment summary" in prompt
    assert "second segment summary" in prompt
    assert "rolling summary" in prompt
    assert "输入 JSON" in prompt
    assert "按 schema 返回严格 JSON" in prompt
    assert "updated_global_summary" not in prompt


def test_default_video_final_prompt_describes_structured_outputs() -> None:
    assert "标题" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "简短摘要" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "详细摘要" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "时间线" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "整体场景" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "重要物体" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "动作/事件" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "可见文字" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "搜索关键词" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "置信度" in VIDEO_FINAL_SUMMARY_USER_PROMPT
