from app.prompts.video_analysis import (
    VIDEO_FINAL_SUMMARY_USER_PROMPT,
    VIDEO_SEGMENT_ANALYSIS_USER_PROMPT,
    build_video_final_summary_user_prompt,
    build_video_segment_user_prompt,
)


def test_video_segment_prompt_includes_previous_summary_and_frame_timestamps() -> None:
    prompt = build_video_segment_user_prompt(
        previous_global_summary="前一批全局记忆",
        frame_infos=[
            {
                "image_order": 1,
                "frame_index": 1,
                "timestamp_seconds": 5.0,
                "timestamp": "00:00:05",
            }
        ],
    )

    assert "前一批全局记忆" in prompt
    assert "00:00:05" in prompt
    assert "输入 JSON" in prompt
    assert "current_frame_info" in prompt
    assert "current_segment_summary" in prompt
    assert "updated_global_summary" in prompt
    assert "events" not in prompt
    assert "ocr_text" not in prompt


def test_default_video_segment_prompt_describes_recursive_outputs() -> None:
    assert "previous_global_summary" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "current_segment_summary" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "important_observations" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "updated_global_summary" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "uncertain_points" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "OCR" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "逐事件列表" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT


def test_video_segment_prompt_uses_directory_prompt_before_default() -> None:
    prompt = build_video_segment_user_prompt(
        previous_global_summary="",
        frame_infos=[],
        custom_segment_prompt="directory segment prompt",
        default_segment_prompt="global segment prompt",
    )

    assert "directory segment prompt" in prompt
    assert "global segment prompt" not in prompt
    assert "current_segment_summary" in prompt


def test_video_final_summary_prompt_contains_all_segment_summaries() -> None:
    prompt = build_video_final_summary_user_prompt(
        duration_seconds=12.0,
        final_global_summary="最终全局记忆",
        segments=[
            {
                "segment_index": 1,
                "start_time_seconds": 0.0,
                "end_time_seconds": 5.0,
                "current_segment_summary": "first segment summary",
                "important_observations": ["desk"],
                "uncertain_points": [],
            },
            {
                "segment_index": 2,
                "start_time_seconds": 5.0,
                "end_time_seconds": 10.0,
                "current_segment_summary": "second segment summary",
                "important_observations": ["screen"],
                "uncertain_points": ["unclear action"],
            },
        ],
        custom_final_prompt=None,
        default_final_prompt="global final prompt",
    )

    assert "global final prompt" in prompt
    assert "最终全局记忆" in prompt
    assert "first segment summary" in prompt
    assert "second segment summary" in prompt
    assert "输入 JSON" in prompt
    assert "rolling_global_summary" not in prompt
    assert "updated_timeline" not in prompt
    assert "events" not in prompt
    assert "ocr_text" not in prompt


def test_default_video_final_prompt_describes_structured_outputs() -> None:
    assert "标题" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "简短摘要" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "详细摘要" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "时间线" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "整体场景" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "重要物体" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "不确定点" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "搜索关键词" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "置信度" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "必须返回字段：title" in VIDEO_FINAL_SUMMARY_USER_PROMPT
