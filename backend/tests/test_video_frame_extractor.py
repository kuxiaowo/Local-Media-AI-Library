from app.services.video_frame_extractor import ExtractedFrame, batch_frames, build_scale_filter, choose_frame_timestamps


def test_choose_fixed_interval_timestamps_are_limited() -> None:
    timestamps = choose_frame_timestamps(
        duration_seconds=60,
        strategy="fixed_interval",
        interval_seconds=5,
        max_frames=4,
    )
    assert timestamps == [0.0, 20.0, 35.0, 55.0]


def test_choose_scene_timestamps_falls_back_to_fixed_interval() -> None:
    timestamps = choose_frame_timestamps(
        duration_seconds=12,
        strategy="scene",
        interval_seconds=5,
        max_frames=12,
        scene_timestamps=[],
    )
    assert timestamps == [0.0, 5.0, 10.0]


def test_choose_hybrid_merges_scene_and_fixed_timestamps() -> None:
    timestamps = choose_frame_timestamps(
        duration_seconds=20,
        strategy="hybrid",
        interval_seconds=10,
        max_frames=6,
        scene_timestamps=[3.0, 10.1, 14.0],
    )
    assert timestamps == [0.0, 3.0, 10.0, 14.0]


def test_batch_frames_groups_by_batch_size() -> None:
    frames = [ExtractedFrame(timestamp_seconds=float(index), frame_path=f"frame_{index}.jpg") for index in range(5)]
    batches = batch_frames(frames, batch_size=2)
    assert [[frame.frame_path for frame in batch] for batch in batches] == [
        ["frame_0.jpg", "frame_1.jpg"],
        ["frame_2.jpg", "frame_3.jpg"],
        ["frame_4.jpg"],
    ]


def test_batch_frames_uses_overlap() -> None:
    frames = [ExtractedFrame(timestamp_seconds=float(index), frame_path=f"frame_{index}.jpg") for index in range(10)]
    batches = batch_frames(frames, batch_size=4, overlap=1)
    assert [[frame.frame_path for frame in batch] for batch in batches] == [
        ["frame_0.jpg", "frame_1.jpg", "frame_2.jpg", "frame_3.jpg"],
        ["frame_3.jpg", "frame_4.jpg", "frame_5.jpg", "frame_6.jpg"],
        ["frame_6.jpg", "frame_7.jpg", "frame_8.jpg", "frame_9.jpg"],
        ["frame_9.jpg"],
    ]


def test_batch_frames_caps_overlap_when_batch_size_is_one() -> None:
    frames = [ExtractedFrame(timestamp_seconds=float(index), frame_path=f"frame_{index}.jpg") for index in range(3)]
    batches = batch_frames(frames, batch_size=1, overlap=1)
    assert [[frame.frame_path for frame in batch] for batch in batches] == [
        ["frame_0.jpg"],
        ["frame_1.jpg"],
        ["frame_2.jpg"],
    ]


def test_build_scale_filter_keeps_ratio_when_height_is_empty() -> None:
    assert build_scale_filter(max_width=1280, max_height=None) == "scale=w='min(iw,1280)':h=-2"


def test_build_scale_filter_uses_fixed_size_when_height_is_set() -> None:
    assert build_scale_filter(max_width=640, max_height=360) == "scale=640:360"
