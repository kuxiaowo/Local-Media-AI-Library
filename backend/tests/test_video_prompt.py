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


def test_video_segment_prompt_uses_directory_prompt_before_default() -> None:
    prompt = build_video_segment_user_prompt(
        previous_global_summary="",
        frame_infos=[],
        custom_segment_prompt="directory segment prompt",
        default_segment_prompt="global segment prompt",
    )

    assert "directory segment prompt" in prompt
    assert "global segment prompt" not in prompt
    assert "current_segment_summary" not in prompt
    assert "输入 JSON" in prompt


def test_video_segment_prompt_includes_source_filename() -> None:
    prompt = build_video_segment_user_prompt(
        previous_global_summary="",
        frame_infos=[],
        source_filename="source-video.mp4",
    )

    assert '"source_filename":"source-video.mp4"' in prompt
    assert "文件名线索" not in prompt


def test_default_video_segment_prompt_describes_timestamp_format() -> None:
    assert "timestamp_seconds 是视频内秒数" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT
    assert "timestamp 是同一时间的 HH:MM:SS 格式" in VIDEO_SEGMENT_ANALYSIS_USER_PROMPT


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


def test_video_final_summary_prompt_includes_source_filename() -> None:
    prompt = build_video_final_summary_user_prompt(
        duration_seconds=12.0,
        final_global_summary="final memory",
        segments=[],
        source_filename="source-video.mp4",
    )

    assert '"source_filename":"source-video.mp4"' in prompt
    assert "文件名线索" not in prompt


def test_default_video_final_prompt_describes_output_content() -> None:
    assert "请输出以下内容" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "视频整体内容" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "按时间顺序的主要事件" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "可能需要人工复核的不确定点" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "输出字段内容要求" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "short_summary：一到两句话概括视频整体内容" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "timeline：数组" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "start_time/end_time 使用 HH:MM:SS" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "uncertain_points：数组" in VIDEO_FINAL_SUMMARY_USER_PROMPT
    assert "confidence：high、medium 或 low" in VIDEO_FINAL_SUMMARY_USER_PROMPT
