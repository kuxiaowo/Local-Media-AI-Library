from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path

DEFAULTS = {
    "provider": "ollama", "base_url": "http://localhost:11434", "model": "",
    "summary_model": "", "api_key": "", "embedding_provider": "ollama",
    "embedding_url": "http://localhost:11434", "embedding_model": "",
    "embedding_key": "", "codex_command": "codex", "video_budget": 1800,
    "image_concurrency": 1,
    "preview_image_concurrency": 2, "preview_video_concurrency": 1,
    "cpu_decode_concurrency": 4, "intel_decode_concurrency": 4,
    "nvidia_decode_concurrency": 4, "ai_request_concurrency": 4,
    "embedding_request_concurrency": 1, "max_cpu_cores": 0,
    "codex_effort": "", "codex_summary_effort": "",
    "request_timeout": 180, "frame_interval": 5, "scene_fps": 3,
    "video_decoder": "cpu", "video_frame_mode": "quality", "batch_size": 6,
    "batch_overlap": 1, "prompt": "用中文描述画面。前两句客观描述可见内容，随后结合目录、原始文件名、媒体备注和自身知识充分推理，完整描述照片内容、情境与叙事。",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS excluded_paths (path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS preview_cleanup (media_id TEXT PRIMARY KEY, due REAL NOT NULL);
CREATE TABLE IF NOT EXISTS credentials (service TEXT NOT NULL, url TEXT NOT NULL, secret TEXT NOT NULL, PRIMARY KEY(service,url));
CREATE TABLE IF NOT EXISTS roots (
 id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, background TEXT NOT NULL DEFAULT '',
 enabled INTEGER NOT NULL DEFAULT 1,
 visual_enhancement INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'pending', error TEXT, scanned REAL);
CREATE TABLE IF NOT EXISTS media (
 id TEXT PRIMARY KEY, root_id TEXT NOT NULL REFERENCES roots(id), path TEXT UNIQUE NOT NULL,
 parent TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, fsid TEXT,
 size INTEGER, mtime_ns INTEGER, hash TEXT, availability TEXT NOT NULL DEFAULT 'online',
 preview_state TEXT NOT NULL DEFAULT 'pending', analysis_state TEXT NOT NULL DEFAULT 'pending',
 vector_state TEXT NOT NULL DEFAULT 'pending', preview TEXT, metadata TEXT NOT NULL DEFAULT '{}',
 thumbnail TEXT, thumbnail_state TEXT NOT NULL DEFAULT 'pending',
 sort_time REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0,
 identity_pending INTEGER NOT NULL DEFAULT 0,
 error TEXT, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS media_sort ON media(availability, sort_time DESC, id);
CREATE INDEX IF NOT EXISTS media_root ON media(root_id, availability, sort_time DESC);
CREATE INDEX IF NOT EXISTS media_hash ON media(hash);
CREATE INDEX IF NOT EXISTS media_fsid ON media(fsid);
CREATE INDEX IF NOT EXISTS media_size_hashed ON media(size) WHERE hash IS NOT NULL;
CREATE TABLE IF NOT EXISTS vectors (
 media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE, profile TEXT NOT NULL,
 revision INTEGER NOT NULL, dim INTEGER NOT NULL, data BLOB NOT NULL,
 PRIMARY KEY(media_id, profile));
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT, config TEXT NOT NULL,
 created REAL NOT NULL, kind TEXT NOT NULL DEFAULT 'analysis');
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), media_id TEXT NOT NULL REFERENCES media(id),
 state TEXT NOT NULL DEFAULT 'queued', reason TEXT, source_hash TEXT,
 config TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}', elapsed REAL NOT NULL DEFAULT 0,
 error TEXT, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS task_queue ON tasks(state, updated);
CREATE INDEX IF NOT EXISTS task_media ON tasks(media_id,state,updated);
CREATE INDEX IF NOT EXISTS task_run ON tasks(run_id,state,updated);
CREATE TABLE IF NOT EXISTS video_frames (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
 task_id TEXT, kind TEXT NOT NULL, time REAL NOT NULL, end_time REAL,
 path TEXT NOT NULL UNIQUE, created REAL NOT NULL,
 source_hash TEXT, extraction_profile TEXT);
CREATE INDEX IF NOT EXISTS video_frames_media ON video_frames(media_id,time,id);
CREATE TABLE IF NOT EXISTS video_frame_segments (
 media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
 source_hash TEXT NOT NULL, extraction_profile TEXT NOT NULL,
 segment INTEGER NOT NULL, completed REAL NOT NULL,
 PRIMARY KEY(media_id,source_hash,extraction_profile,segment));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, created REAL, message TEXT);
CREATE TABLE IF NOT EXISTS worker_commands (
 id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', error TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS worker_status (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS vector_jobs (
 media_id TEXT NOT NULL REFERENCES media(id) ON DELETE CASCADE,
 profile TEXT NOT NULL, revision INTEGER NOT NULL, config TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', error TEXT, PRIMARY KEY(media_id, profile));
CREATE TABLE IF NOT EXISTS rebuilds (profile TEXT PRIMARY KEY, config TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS index_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
INSERT OR IGNORE INTO index_version VALUES (1,0);
CREATE TRIGGER IF NOT EXISTS vectors_insert AFTER INSERT ON vectors BEGIN UPDATE index_version SET version=version+1; END;
CREATE TRIGGER IF NOT EXISTS vectors_update AFTER UPDATE ON vectors BEGIN UPDATE index_version SET version=version+1; END;
CREATE TRIGGER IF NOT EXISTS vectors_delete AFTER DELETE ON vectors BEGIN UPDATE index_version SET version=version+1; END;
CREATE INDEX IF NOT EXISTS media_parent ON media(parent,availability,sort_time DESC);
CREATE INDEX IF NOT EXISTS media_analysis ON media(availability,analysis_state,sort_time DESC,id);
CREATE INDEX IF NOT EXISTS media_kind ON media(availability,kind,sort_time DESC,id);
"""


class Database:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "library.sqlite3"
        # WAL is persistent. Setting journal_mode on every request needlessly
        # competes with the separate analysis process for the database lock.
        with sqlite3.connect(self.path, timeout=30) as journal:
            journal.execute("PRAGMA journal_mode=WAL")
        with self.connect() as db:
            db.executescript(SCHEMA)
            legacy_concurrency = db.execute(
                "SELECT value FROM settings WHERE key='ai_concurrency'"
            ).fetchone()
            legacy_video_concurrency = db.execute(
                "SELECT value FROM settings WHERE key='video_decode_concurrency'"
            ).fetchone()
            has_image_concurrency = db.execute(
                "SELECT 1 FROM settings WHERE key='image_concurrency'"
            ).fetchone()
            root_columns = {row['name'] for row in db.execute('PRAGMA table_info(roots)')}
            if 'enabled' not in root_columns:
                db.execute('ALTER TABLE roots ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1')
            if 'visual_enhancement' not in root_columns:
                db.execute('ALTER TABLE roots ADD COLUMN visual_enhancement INTEGER NOT NULL DEFAULT 0')
            media_columns = {row['name'] for row in db.execute('PRAGMA table_info(media)')}
            if 'identity_pending' not in media_columns:
                db.execute('ALTER TABLE media ADD COLUMN identity_pending INTEGER NOT NULL DEFAULT 0')
            if 'thumbnail' not in media_columns:
                db.execute('ALTER TABLE media ADD COLUMN thumbnail TEXT')
            if 'thumbnail_state' not in media_columns:
                db.execute("ALTER TABLE media ADD COLUMN thumbnail_state TEXT NOT NULL DEFAULT 'pending'")
                # Existing standard previews are immediately usable in the grid.
                db.execute("UPDATE media SET thumbnail=preview,thumbnail_state='ready' WHERE preview_state='ready' AND preview IS NOT NULL")
            db.execute("CREATE INDEX IF NOT EXISTS media_preview_queue ON media(kind,updated,id) WHERE availability='online' AND preview_state='pending'")
            db.execute("CREATE INDEX IF NOT EXISTS media_thumbnail_queue ON media(kind,updated,id) WHERE availability='online' AND thumbnail_state='pending' AND preview_state='pending'")
            db.execute("CREATE INDEX IF NOT EXISTS media_hash_queue ON media(updated,id) WHERE availability='online' AND hash IS NULL AND preview_state<>'pending'")
            db.execute("CREATE INDEX IF NOT EXISTS media_pending_identity ON media(size) WHERE availability='online' AND identity_pending=1 AND hash IS NULL")
            frame_columns = {row['name'] for row in db.execute('PRAGMA table_info(video_frames)')}
            if 'source_hash' not in frame_columns:
                db.execute('ALTER TABLE video_frames ADD COLUMN source_hash TEXT')
            if 'extraction_profile' not in frame_columns:
                db.execute('ALTER TABLE video_frames ADD COLUMN extraction_profile TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS video_frames_source ON video_frames(media_id,source_hash,time,id)')
            db.execute(
                'UPDATE video_frames SET source_hash='
                '(SELECT hash FROM media WHERE media.id=video_frames.media_id) '
                'WHERE source_hash IS NULL'
            )
            for key, value in DEFAULTS.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, json.dumps(value)))
            if legacy_video_concurrency:
                try:
                    value = json.loads(legacy_video_concurrency['value'])
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        for key in ('cpu_decode_concurrency', 'intel_decode_concurrency',
                                    'nvidia_decode_concurrency'):
                            db.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(value), key))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            if legacy_concurrency and not has_image_concurrency:
                try:
                    value = json.loads(legacy_concurrency['value'])
                    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                        db.execute("UPDATE settings SET value=? WHERE key='image_concurrency'", (json.dumps(value),))
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            db.execute("DELETE FROM settings WHERE key='ai_concurrency'")
            db.execute("DELETE FROM settings WHERE key IN ('video_concurrency','video_decode_concurrency')")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('active_profile', '\"\"')")
            # Preserve frame references independently from disposable analysis runs.
            # Older databases only stored them inside task checkpoints.
            for task in db.execute("SELECT id,media_id,checkpoint,updated FROM tasks WHERE checkpoint<>'{}'"):
                try:
                    frames = json.loads(task['checkpoint']).get('frames', [])
                except (TypeError, json.JSONDecodeError):
                    continue
                for frame in frames:
                    path = frame.get('path')
                    stamp = frame.get('time')
                    if path is None or stamp is None:
                        continue
                    db.execute(
                        'INSERT OR IGNORE INTO video_frames(media_id,task_id,kind,time,end_time,path,created) VALUES (?,?,?,?,?,?,?)',
                        (task['media_id'], task['id'], frame.get('kind', 'legacy'), stamp,
                         frame.get('end_time'), path, task['updated']),
                    )

    @contextlib.contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount

    def settings(self, public=False):
        result = {r["key"]: json.loads(r["value"]) for r in self.rows("SELECT * FROM settings")}
        if public:
            for key in ("api_key", "embedding_key"):
                result[key + "_configured"] = bool(result.pop(key, ""))
        return result

    def set_setting(self, key, value):
        self.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def remember_credentials(self, config):
        with self.connect() as conn:
            for service, url_key, secret_key in [('description','base_url','api_key'),('embedding','embedding_url','embedding_key')]:
                conn.execute('INSERT OR REPLACE INTO credentials VALUES (?,?,?)', (service,config[url_key].rstrip('/'),config[secret_key]))

    def credential(self, service, url):
        current = self.settings()
        endpoint, key = ('base_url','api_key') if service=='description' else ('embedding_url','embedding_key')
        if current[endpoint].rstrip('/') == url.rstrip('/'):
            return current[key]
        row = self.one('SELECT secret FROM credentials WHERE service=? AND url=?', (service,url.rstrip('/')))
        if row is None:
            raise ValueError('旧任务对应的服务凭据不可用，请重新配置该服务')
        return row['secret']

    def event(self, message):
        self.execute("INSERT INTO events(created,message) VALUES (?,?)", (time.time(), message))

    def enabled_roots(self):
        """Return roots that are enabled and have no disabled managed ancestor."""
        roots = self.rows('SELECT * FROM roots')
        disabled = [Path(row['path']) for row in roots if not row['enabled']]
        return [row for row in roots
                if row['enabled'] and not any(parent in Path(row['path']).parents for parent in disabled)]

    def restart_recovery(self):
        with self.connect() as db:
            db.execute("UPDATE runs SET state='paused',reason='restart' WHERE state IN ('running','pausing')")
            db.execute("UPDATE tasks SET state='paused',reason='restart' WHERE state IN ('running','queued')")
            db.execute("UPDATE vector_jobs SET state='paused' WHERE state IN ('running','queued')")


def profile(config):
    return json.dumps([config["embedding_provider"], config["embedding_url"].rstrip('/'),
                       config["embedding_model"]], ensure_ascii=False)
