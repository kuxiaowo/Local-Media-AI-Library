from __future__ import annotations

import json
import math
from pathlib import Path


def extraction_profile(config):
    return json.dumps({
        'scene_fps': config.get('scene_fps', 3),
        'scene_threshold': 0.35,
        'scene_scale': 480,
    }, sort_keys=True, separators=(',', ':'))


def complete(db, media, config):
    """Return whether the reusable cache fully covers this video/configuration."""
    source_hash = media.get('hash')
    try:
        duration = float(json.loads(media.get('metadata') or '{}').get('duration') or 0)
        interval = float(config['frame_interval'])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return False
    if not source_hash or duration <= 0 or interval <= 0:
        return False

    interval_frames = db.rows(
        "SELECT time,path FROM video_frames "
        "WHERE media_id=? AND source_hash=? AND kind='interval' ORDER BY time",
        (media['id'], source_hash),
    )
    available_times = [float(row['time']) for row in interval_frames if Path(row['path']).is_file()]
    expected_samples = max(1, math.ceil(duration / interval))
    if any(not any(abs(stamp - number * interval) <= .001 for stamp in available_times)
           for number in range(expected_samples)):
        return False

    if config.get('video_frame_mode', 'quality') == 'fast':
        return True

    profile = extraction_profile(config)
    segments = {
        int(row['segment']) for row in db.rows(
            'SELECT segment FROM video_frame_segments '
            'WHERE media_id=? AND source_hash=? AND extraction_profile=?',
            (media['id'], source_hash, profile),
        )
    }
    expected_segments = math.ceil(duration / 60)
    if any(segment not in segments for segment in range(expected_segments)):
        return False

    scene_frames = db.rows(
        "SELECT time,path FROM video_frames "
        "WHERE media_id=? AND source_hash=? AND kind='scene' AND extraction_profile=?",
        (media['id'], source_hash, profile),
    )
    available_scene_times = [float(row['time']) for row in scene_frames if Path(row['path']).is_file()]
    return all(any(segment * 60 <= stamp < min(duration, (segment + 1) * 60)
                   for stamp in available_scene_times)
               for segment in range(expected_segments))
