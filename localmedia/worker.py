from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

from .db import profile
from .analysis_prompts import VISUAL_OBSERVATION, PRESERVE_VISUAL_FINDINGS
from .frame_cache import extraction_profile
from .index import normalize, enqueue_rebuilds
from .media import ffmpeg, file_hash, video_decode_options, video_decoder_candidates
from .providers import Providers, ProviderError
from .tasks import Paused


class Worker:
    def __init__(self, db, tasks):
        self.db, self.tasks = db, tasks
        self.providers = Providers(db, self.quota_update)
        self.active = {}
        self.active_run = None
        self.active_media = None
        self.stopping = False
        self.quota_state = {'state': 'unknown', 'limits': None}
        self.resource_active = {
            'cpu_decode_concurrency': 0,
            'intel_decode_concurrency': 0,
            'nvidia_decode_concurrency': 0,
            'ai_request_concurrency': 0,
            'embedding_request_concurrency': 0,
        }
        self.resource_condition = asyncio.Condition()
        self.video_decoder_cursor = 0

    @contextlib.asynccontextmanager
    async def resource_slot(self, key):
        async with self.resource_condition:
            while self.resource_active[key] >= self.db.settings()[key]:
                try:
                    await asyncio.wait_for(self.resource_condition.wait(), timeout=.5)
                except asyncio.TimeoutError:
                    pass
            self.resource_active[key] += 1
        try:
            yield
        finally:
            async with self.resource_condition:
                self.resource_active[key] -= 1
                self.resource_condition.notify_all()

    def quota_update(self, payload):
        self.quota_state = {'state': 'ready', 'limits': payload}
        self.db.execute("INSERT OR REPLACE INTO worker_status VALUES ('quota',?)",
                        (json.dumps(self.quota_state),))

    def visual_references(self, config):
        references = config.get('visual_references') or []
        if not references:
            return '', []
        paths = []
        categories = []
        for reference in references:
            path = Path(reference['path'])
            if not path.is_file():
                raise ProviderError(f'视觉参考图不存在：{reference["category"]}')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != reference['sha256']:
                raise ProviderError(f'视觉参考图已改变，请新建分析任务：{reference["category"]}')
            categories.append(reference['category'])
            paths.append(str(path))
        instruction = (
            f'所附图片的前 {len(paths)} 张是视觉类别参考拼图，其后才是待分析媒体。'
            '参考类别按图片顺序为：' + json.dumps(categories, ensure_ascii=False) + '。'
            '参考图只用于视觉类别匹配，不代表目标中必然存在这些类别。'
            '对目标中外观明确匹配的物体，直接使用对应类别名，不得改用“小型成人用品”“未知装置”等模糊说法。'
            '相近类别必须依据可见结构区分：口罩不等于口球，头发遮眼不等于眼罩，蛋形设备不应自动归为啵啵贝。'
            '可直接确认“佩戴、贴附、连接、放置”；只有连续画面明确展示动作或状态变化时，才能判断“通电、震动、开启、运行”。'
            '没有匹配参考时应客观描述可见外观，不得强行归类。\n'
        )
        return instruction, paths

    async def quota_check(self):
        try:
            codex = await self.providers.get_codex()
            payload = await codex.rpc('account/rateLimits/read')
            self.quota_update(payload)
            return None
        except Exception:
            self.quota_state['state'] = 'unknown'
            return None

    async def stage(self, task, cp, operation, *, resource=None, request_timeout=True, label='单次处理'):
        # Resource queueing is deliberately outside the request/budget clock.
        # Waiting for a decoder or AI slot must not look like a hung request.
        if resource:
            is_embedding = resource == 'embedding_request_concurrency'
            is_ai = resource == 'ai_request_concurrency'
            cp['work_stage'] = ('embedding_queued' if is_embedding else
                                'ai_queued' if is_ai else 'extract_queued')
            self.tasks.checkpoint(task['id'], cp, 0)
            async with self.resource_slot(resource):
                cp['work_stage'] = ('embedding' if is_embedding else
                                    'ai' if is_ai else 'extracting')
                self.tasks.checkpoint(task['id'], cp, 0)
                try:
                    return await self.stage(task, cp, operation,
                                            request_timeout=request_timeout, label=label)
                finally:
                    cp['work_stage'] = 'processing'
                    self.tasks.checkpoint(task['id'], cp, 0)
        self.tasks.boundary(task['id'])
        config = json.loads(task['config'])
        if config['provider'] == 'codex' and config.get('task_kind', 'analysis') == 'analysis':
            await self.quota_check()
            self.tasks.boundary(task['id'])
        media = self.db.one('SELECT kind FROM media WHERE id=?', (task['media_id'],))
        row = self.db.one('SELECT elapsed FROM tasks WHERE id=?', (task['id'],))
        budget = self.db.settings()['video_budget'] if media['kind'] == 'video' else 0
        remaining = max(0.001, budget - row['elapsed']) if budget else math.inf
        timeout = min(config['request_timeout'], remaining) if request_timeout else remaining
        last = time.monotonic()
        unrecorded = 0.0
        future = asyncio.create_task(operation())
        try:
            deadline = last + timeout
            while True:
                done, _ = await asyncio.wait([future], timeout=min(0.5, max(0, deadline - time.monotonic())))
                now = time.monotonic()
                unrecorded += now - last
                last = now
                if unrecorded >= 3 or done:
                    self.tasks.checkpoint(task['id'], cp, unrecorded)
                    unrecorded = 0.0
                if done:
                    return future.result()
                if now >= deadline:
                    future.cancel()
                    await asyncio.gather(future, return_exceptions=True)
                    if not request_timeout or remaining <= config['request_timeout']:
                        self.db.execute("UPDATE tasks SET state='paused',reason='time_budget' WHERE id=?", (task['id'],))
                        raise Paused()
                    raise ProviderError(f'{label}超时，可重试未完成阶段')
        finally:
            if not future.done():
                future.cancel()
                await asyncio.gather(future, return_exceptions=True)
            self.tasks.checkpoint(task['id'], cp, unrecorded + time.monotonic() - last)

    async def process(self, args):
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            out, error = await proc.communicate()
            if proc.returncode:
                raise ProviderError('视频抽帧失败，请检查格式和 FFmpeg')
            return out, error
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    async def video_process(self, task, prefix, path, suffix, config, cp, codec):
        # Tasks created before this setting existed have no snapshot value;
        # let them adopt the current selection instead of silently staying on
        # CPU forever. New tasks retain normal snapshot semantics.
        configured = config.get('video_decoder') or self.db.settings().get('video_decoder', 'cpu')
        candidates = video_decoder_candidates(configured)
        combined = len(candidates) > 1
        if cp.get('video_decoder_fallback') and not combined:
            candidates = ['cpu']
        elif combined:
            # Distribute separate FFmpeg jobs; a single process still uses one
            # decoder because FFmpeg cannot split one stream across devices.
            offset = self.video_decoder_cursor % len(candidates)
            self.video_decoder_cursor += 1
            candidates = candidates[offset:] + candidates[:offset]
        if 'cpu' not in candidates:
            candidates.append('cpu')
        first = candidates[0]
        last_error = None
        for mode in candidates:
            command = [ffmpeg(), *prefix, *video_decode_options(mode, codec), '-i', path, *suffix]
            try:
                result = await self.stage(
                    task, cp, lambda: self.process(command),
                    resource=f'{mode}_decode_concurrency', request_timeout=False)
                usage = cp.setdefault('video_decoder_usage', {})
                usage[mode] = usage.get(mode, 0) + 1
                if mode != first:
                    self.db.event(f"{first} 解码失败，本次已改用 {mode}：{Path(path).name}")
                    if not combined and mode == 'cpu':
                        cp['video_decoder_fallback'] = True
                return result
            except ProviderError as error:
                last_error = error
        raise last_error

    async def video_codec(self, media, cp):
        if cp.get('video_codec'):
            return cp['video_codec']
        codec = json.loads(media['metadata']).get('codec')
        if not codec:
            output, _ = await self.process([
                ffmpeg('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=codec_name', '-of', 'default=nw=1:nk=1', media['path'],
            ])
            codec = output.decode('utf-8', 'replace').strip()
        cp['video_codec'] = codec
        return codec

    @staticmethod
    def frame_profile(config):
        return extraction_profile(config)

    def restore_cached_frames(self, task, cp, media, config):
        if cp.get('frame_cache_loaded'):
            return
        rows = self.db.rows(
            "SELECT kind,time,end_time,path,extraction_profile FROM video_frames "
            "WHERE media_id=? AND source_hash=? "
            "ORDER BY time,CASE kind WHEN 'interval' THEN 0 ELSE 1 END,created DESC,id DESC",
            (media['id'], task['source_hash']),
        )
        restored = []
        profile = self.frame_profile(config)
        for row in rows:
            if config.get('video_frame_mode', 'quality') == 'fast' and row['kind'] != 'interval':
                continue
            if row['kind'] == 'scene' and row['extraction_profile'] != profile:
                continue
            if not Path(row['path']).is_file():
                continue
            frame = {key: row[key] for key in ('time', 'path', 'kind')}
            if row['end_time'] is not None:
                frame['end_time'] = row['end_time']
            # Multiple deleted/rebuilt tasks can point at the same moment.
            # Reuse one physical image instead of sending duplicates to AI.
            if any(abs(frame['time'] - old['time']) <= .001 and frame['kind'] == old['kind']
                   for old in restored):
                continue
            restored.append(frame)
        existing = {(frame['kind'], frame['path']) for frame in cp['frames']}
        cp['frames'].extend(frame for frame in restored
                            if (frame['kind'], frame['path']) not in existing)
        cp['frame_cache_loaded'] = True
        cp['reused_frames'] = len(restored)
        self.tasks.checkpoint(task['id'], cp, 0)

    async def frames(self, task, cp, media, config):
        directory = self.db.directory / 'frames' / task['id']
        directory.mkdir(parents=True, exist_ok=True)
        meta = json.loads(media['metadata'])
        duration = meta.get('duration') or 0
        if duration <= 0:
            raise ProviderError('视频时长不可用，请先重试预览')
        cp.setdefault('frames', [])
        self.restore_cached_frames(task, cp, media, config)
        codec = await self.video_codec(media, cp)
        extraction_profile = self.frame_profile(config)
        def remember(frame, kind):
            self.db.execute(
                'INSERT OR IGNORE INTO video_frames(media_id,task_id,kind,time,end_time,path,created,source_hash,extraction_profile) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (media['id'], task['id'], kind, frame['time'], frame.get('end_time'), frame['path'], time.time(),
                 task['source_hash'], extraction_profile),
            )
        count = max(1, math.ceil(duration / config['frame_interval']))
        for number in range(cp.get('sample_next', 0), count):
            timestamp = number * config['frame_interval']
            cached = next((frame for frame in cp['frames']
                           if frame.get('kind') == 'interval' and abs(frame['time'] - timestamp) <= .001), None)
            if cached:
                cp['sample_next'] = number + 1
                self.tasks.checkpoint(task['id'], cp, 0)
                continue
            destination = directory / f'base-{number:08d}.jpg'
            try:
                await self.video_process(task,
                    ['-v', 'error', '-ss', str(timestamp)], media['path'],
                    ['-frames:v', '1', '-vf', 'scale=1280:1280:force_original_aspect_ratio=decrease', '-y', str(destination)],
                    config, cp, codec)
            except ProviderError:
                # Some truncated MP4 files advertise a container/stream
                # duration beyond their final decodable packet. Only tolerate
                # this at the last scheduled sample and only after at least one
                # earlier frame succeeded; failures in the middle still expose
                # real corruption instead of being silently swallowed.
                if number != count - 1 or not any(f.get('kind') == 'interval' for f in cp['frames']):
                    raise
                cp['decode_duration'] = timestamp
                cp['sample_next'] = number + 1
                self.db.event(f"视频尾部早于声明时长，已跳过无画面的末次采样：{Path(media['path']).name} @ {timestamp:g}s")
                self.tasks.checkpoint(task['id'], cp, 0)
                break
            if destination.is_file():
                frame = {'time': timestamp, 'path': str(destination), 'kind': 'interval'}
                cp['frames'].append(frame)
                remember(frame, 'interval')
            cp['sample_next'] = number + 1
            self.tasks.checkpoint(task['id'], cp, 0)
        frame_mode = config.get('video_frame_mode', 'quality')
        if frame_mode == 'fast' and not cp.get('scenes_done'):
            cp['scene_detection_skipped'] = True
            cp['scenes_done'] = True
            self.tasks.checkpoint(task['id'], cp, 0)
        if frame_mode == 'quality' and not cp.get('scenes_done'):
            decode_duration = min(duration, cp.get('decode_duration', duration))
            completed_segments = {
                row['segment'] for row in self.db.rows(
                    'SELECT segment FROM video_frame_segments '
                    'WHERE media_id=? AND source_hash=? AND extraction_profile=?',
                    (media['id'], task['source_hash'], extraction_profile),
                )
            }
            for segment in range(cp.get('scene_segment', 0), math.ceil(decode_duration / 60)):
                if segment in completed_segments:
                    cp['scene_segment'] = segment + 1
                    self.tasks.checkpoint(task['id'], cp, 0)
                    continue
                offset = segment * 60
                pattern = f'scene-{segment:08d}-%08d.jpg'
                scene_fps = config.get('scene_fps', 3)
                _, stderr = await self.video_process(task,
                    ['-hide_banner', '-ss', str(offset)], media['path'],
                    ['-t', str(min(60, decode_duration-offset)),
                     '-vf', f"fps={scene_fps},scale=480:480:force_original_aspect_ratio=decrease,select='eq(n,0)+gt(scene,0.35)',format=yuvj420p,showinfo",
                     '-fps_mode', 'vfr', '-an', '-y', str(directory / pattern)],
                    config, cp, codec)
                timestamps = re.findall(rb'pts_time:([\d.]+)', stderr)
                for number, timestamp in enumerate(timestamps, 1):
                    dest = directory / (pattern % number)
                    stamp = offset + float(timestamp)
                    if dest.is_file() and all(abs(stamp - f['time']) > .25 for f in cp['frames']):
                        frame = {'time': stamp, 'path': str(dest), 'kind': 'scene'}
                        cp['frames'].append(frame)
                        remember(frame, 'scene')
                cp['scene_segment'] = segment + 1
                self.db.execute(
                    'INSERT OR REPLACE INTO video_frame_segments(media_id,source_hash,extraction_profile,segment,completed) '
                    'VALUES (?,?,?,?,?)',
                    (media['id'], task['source_hash'], extraction_profile, segment, time.time()),
                )
                self.tasks.checkpoint(task['id'], cp, 0)
            cp['frames'].sort(key=lambda f: f['time'])
            cp['scenes_done'] = True
            self.tasks.checkpoint(task['id'], cp, 0)
        return cp['frames']

    async def analyze(self, task):
        config = json.loads(task['config'])
        cp = json.loads(task['checkpoint'])
        media = self.db.one('SELECT * FROM media WHERE id=?', (task['media_id'],))
        if config.get('source_revision') != media['revision']:
            raise ProviderError('描述或媒体已在任务创建后修改，已保留当前结果；请重新创建分析任务')
        if media['preview_state'] != 'ready':
            raise ProviderError('预览尚未就绪，请完成预览后重试')
        digest = await asyncio.to_thread(file_hash, media['path'])
        if digest != task['source_hash']:
            self.db.execute("UPDATE tasks SET state='paused',reason='source_changed' WHERE id=?", (task['id'],))
            raise Paused()
        context = json.dumps({
            '原始文件名': config.get('original_filename', media['name']),
            '文件所在目录': config.get('source_directory', media['parent']),
            '目录背景（从上级到下级）': config.get('background_chain',
                [{'background': config.get('background', '')}]),
            '照片或视频本身的备注（最高优先级）': config['note'],
        }, ensure_ascii=False)
        visual_instruction, visual_images = self.visual_references(config)
        visual_context = context + '\n' + visual_instruction
        if visual_images:
            visual_context += '\n' + VISUAL_OBSERVATION
        title_instruction = ('第一行使用“标题：”输出标题。若原始文件名去除扩展名后具有明确语义，'
                             '标题必须原样沿用该名称；只有原始文件名属于纯序号、哈希、相机默认编号、乱码或其他无意义名称时，才自动生成简洁中文标题。')
        photo_instruction = (title_instruction + '换行输出描述正文，不重复标题。正文不限制为四句：'
                             '前两句客观描述照片中直接可见的内容；从第三句开始充分结合画面线索、目录、文件名、背景备注与自身知识进行推理，'
                             '完整描述照片的内容、情境、人物关系、可能的前因后果和叙事，使文字连贯充实。')
        video_instruction = (title_instruction + '换行输出描述正文，不重复标题。按视频发展顺序完整叙述事件、人物关系、情境与前后因果，'
                             '结合抽帧、目录、文件名、背景备注和自身知识推理连续情节。')
        if 'description' not in cp:
            if media['kind'] == 'image':
                cp['description'] = await self.stage(task, cp, lambda: self.providers.describe(
                    config, visual_context + '\n' + photo_instruction, [*visual_images, media['preview']]),
                    resource='ai_request_concurrency', label='视觉描述')
            else:
                frames = await self.frames(task, cp, media, config)
                cp.setdefault('batches', [])
                size, overlap = config['batch_size'], config['batch_overlap']
                start = cp.get('next_frame', 0)
                while start < len(frames):
                    batch = frames[start:start + size]
                    prompt = visual_context + '\n按所附目标帧顺序分析，时间戳只对应参考图之后的目标帧。记录事件、时间范围与姿势变化；结合已有摘要更新连续性叙事。\n'
                    prompt += '时间戳（秒，静止画面含末次出现时间）：' + json.dumps([[f['time'], f.get('end_time', f['time'])] for f in batch])
                    prompt += '\n已有事件和连续性摘要：' + cp.get('continuity', '')
                    result = await self.stage(task, cp, lambda: self.providers.describe(
                        config, prompt, [*visual_images, *[f['path'] for f in batch]]),
                        resource='ai_request_concurrency', label='视频片段描述')
                    cp['batches'].append({'start': batch[0]['time'], 'end': batch[-1]['time'], 'description': result})
                    cp['continuity'] = result
                    start = len(frames) if start + size >= len(frames) else start + size - overlap
                    cp['next_frame'] = start
                    self.tasks.checkpoint(task['id'], cp, 0)
                cp.setdefault('summary_records', [b['description'] for b in cp['batches']])
                # Each reduction chunk is itself checkpointed; no fixed maximum video length.
                while sum(map(len, cp['summary_records'])) > 24000 or len(cp['summary_records']) > 8:
                    previous_size = sum(map(len, cp['summary_records']))
                    previous_count = len(cp['summary_records'])
                    cp.setdefault('reduced', [])
                    for index in range(len(cp['reduced']) * 6, len(cp['summary_records']), 6):
                        text = '\n'.join(cp['summary_records'][index:index + 6])
                        result = await self.stage(task, cp, lambda: self.providers.describe(config,
                            '将以下事件按时间顺序压缩汇总，保留时间、人物关系、因果线索和叙事发展。'
                            + PRESERVE_VISUAL_FINDINGS + '\n' + text, summary=True),
                            resource='ai_request_concurrency', label='视频事件汇总')
                        cp['reduced'].append(result)
                        self.tasks.checkpoint(task['id'], cp, 0)
                    cp['summary_records'] = cp.pop('reduced')
                    if len(cp['summary_records']) >= previous_count and sum(map(len, cp['summary_records'])) >= previous_size:
                        raise ProviderError('模型汇总未压缩事件记录，请调整汇总模型；已保存检查点')
                    self.tasks.checkpoint(task['id'], cp, 0)
                cp['description'] = await self.stage(task, cp, lambda: self.providers.describe(config,
                    context + '\n' + video_instruction + '\n' + PRESERVE_VISUAL_FINDINGS
                    + '\n' + '\n'.join(cp['summary_records']), summary=True),
                    resource='ai_request_concurrency', label='视频最终汇总')
            if not cp['description'].strip():
                raise ProviderError('模型返回空描述')
            self.tasks.checkpoint(task['id'], cp, 0)
        while True:
            # Description settings stay frozen, but a rebuilt embedding space may
            # have become active while this task was queued or awaiting the model.
            space = self.db.settings()['active_profile'] or profile(config)
            if 'vector' not in cp or cp.get('vector_profile', profile(config)) != space:
                provider, url, model = json.loads(space)
                embedding_config = {**config, 'embedding_provider': provider,
                                    'embedding_url': url, 'embedding_model': model}
                cp['vector'] = normalize(await self.stage(
                    task, cp, lambda: self.providers.embed(embedding_config, cp['description']),
                    resource='embedding_request_concurrency', label='向量生成')).tolist()
                cp['vector_profile'] = space
                self.tasks.checkpoint(task['id'], cp, 0)
            self.tasks.boundary(task['id'])
            if await asyncio.to_thread(file_hash, media['path']) != task['source_hash']:
                raise ProviderError('处理期间媒体内容改变，请重新分析')
            vector = normalize(cp['vector'])
            with self.db.connect() as conn:
                # Serialize the revision/profile check with description edits and
                # rebuild completion. No network calls run inside this transaction.
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT revision,hash FROM media WHERE id=?', (media['id'],)).fetchone()
                if not current or current['hash'] != task['source_hash']:
                    raise ProviderError('处理期间媒体内容改变')
                if current['revision'] != config.get('source_revision'):
                    raise ProviderError('描述或媒体已在任务创建后修改，已保留当前结果；请重新创建分析任务')
                active = json.loads(conn.execute("SELECT value FROM settings WHERE key='active_profile'").fetchone()['value'])
                if active and active != space:
                    continue
                revision = current['revision'] + 1
                conn.execute("UPDATE media SET description=?,revision=?,analysis_state='done',vector_state='ready' WHERE id=?",
                             (cp['description'], revision, media['id']))
                conn.execute('DELETE FROM vectors WHERE media_id=?', (media['id'],))
                conn.execute('DELETE FROM vector_jobs WHERE media_id=?', (media['id'],))
                conn.execute('INSERT INTO vectors VALUES (?,?,?,?,?)', (media['id'], space, revision, len(vector), vector.tobytes()))
                enqueue_rebuilds(conn, media['id'], revision)
                conn.execute("UPDATE tasks SET state='completed',reason=NULL,error=NULL WHERE id=?", (task['id'],))
                if not active:
                    conn.execute("UPDATE settings SET value=? WHERE key='active_profile'", (json.dumps(space),))
            return

    async def extract_frames(self, task):
        config = json.loads(task['config'])
        cp = json.loads(task['checkpoint'])
        media = self.db.one('SELECT * FROM media WHERE id=?', (task['media_id'],))
        if media['kind'] != 'video':
            raise ProviderError('仅抽帧任务只支持视频')
        if media['preview_state'] != 'ready':
            raise ProviderError('预览尚未就绪，请完成预览后重试')
        digest = await asyncio.to_thread(file_hash, media['path'])
        if digest != task['source_hash']:
            self.db.execute("UPDATE tasks SET state='paused',reason='source_changed' WHERE id=?", (task['id'],))
            raise Paused()
        await self.frames(task, cp, media, config)
        self.tasks.boundary(task['id'])
        if await asyncio.to_thread(file_hash, media['path']) != task['source_hash']:
            raise ProviderError('处理期间媒体内容改变，请重新抽帧')
        self.db.execute("UPDATE tasks SET state='completed',reason=NULL,error=NULL WHERE id=?", (task['id'],))

    def settle(self):
        for run in self.db.rows("SELECT id,state FROM runs WHERE state IN ('running','pausing')"):
            states = {r['state'] for r in self.db.rows('SELECT state FROM tasks WHERE run_id=?', (run['id'],))}
            if 'running' in states or 'queued' in states:
                continue
            state = 'paused' if 'paused' in states else 'failed' if 'failed' in states else 'completed'
            self.db.execute('UPDATE runs SET state=? WHERE id=?', (state, run['id']))

    async def vector_job(self, job):
        config = json.loads(job['config'])
        media = self.db.one('SELECT * FROM media WHERE id=?', (job['media_id'],))
        try:
            async with self.resource_slot('ai_request_concurrency'):
                vector = normalize(await self.providers.embed(config, media['description']))
            with self.db.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT revision FROM media WHERE id=?', (media['id'],)).fetchone()
                if current and current['revision'] == job['revision']:
                    conn.execute('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)', (media['id'], job['profile'], job['revision'], len(vector), vector.tobytes()))
                    active = json.loads(conn.execute("SELECT value FROM settings WHERE key='active_profile'").fetchone()['value'])
                    if not active:
                        conn.execute("UPDATE settings SET value=? WHERE key='active_profile'", (json.dumps(job['profile']),))
                        active = job['profile']
                    if conn.execute('SELECT 1 FROM vectors WHERE media_id=? AND profile=? AND revision=?',
                                    (media['id'], active, job['revision'])).fetchone():
                        conn.execute("UPDATE media SET vector_state='ready' WHERE id=?", (media['id'],))
                conn.execute('DELETE FROM vector_jobs WHERE media_id=? AND profile=? AND revision=?', (media['id'], job['profile'], job['revision']))
        except Exception as exc:
            self.db.execute("UPDATE vector_jobs SET state='failed',error=? WHERE media_id=? AND profile=? AND revision=?",
                             (str(exc) if isinstance(exc, ProviderError) else type(exc).__name__, job['media_id'], job['profile'], job['revision']))
            self.db.execute("UPDATE media SET vector_state='failed' WHERE id=? AND revision=? AND vector_state='pending'", (job['media_id'],job['revision']))
        self.finish_rebuilds()

    def finish_rebuilds(self):
        if not self.db.one("SELECT 1 FROM rebuilds WHERE state='running' LIMIT 1"):
            return
        with self.db.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for rebuild in conn.execute("SELECT * FROM rebuilds WHERE state='running'").fetchall():
                if conn.execute('SELECT 1 FROM vector_jobs WHERE profile=?', (rebuild['profile'],)).fetchone():
                    continue
                missing = conn.execute("SELECT m.id FROM media m WHERE m.description<>'' AND NOT EXISTS (SELECT 1 FROM vectors v WHERE v.media_id=m.id AND v.profile=? AND v.revision=m.revision) LIMIT 1", (rebuild['profile'],)).fetchone()
                if missing:
                    continue
                conn.execute("UPDATE settings SET value=? WHERE key='active_profile'", (json.dumps(rebuild['profile']),))
                conn.execute("UPDATE media SET vector_state='ready' WHERE description<>''")
                conn.execute("UPDATE rebuilds SET state='completed' WHERE profile=?", (rebuild['profile'],))

    async def execute(self, item, vector=False):
        try:
            if vector:
                await self.vector_job(item)
            elif item.get('run_kind') == 'frames':
                await self.extract_frames(item)
            else:
                await self.analyze(item)
        except (Paused, asyncio.CancelledError):
            if vector:
                self.db.execute("UPDATE vector_jobs SET state='paused' WHERE media_id=? AND state='running'", (item['media_id'],))
            else:
                self.db.execute("UPDATE tasks SET state='paused',reason=COALESCE(reason,'interrupt') WHERE id=? AND state='running'", (item['id'],))
        except Exception as exc:
            if not vector:
                self.db.execute("UPDATE tasks SET state='failed',error=? WHERE id=?", (str(exc) if isinstance(exc, ProviderError) else type(exc).__name__, item['id']))
        finally:
            self.active.pop(asyncio.current_task(), None)
            self.settle()

    async def cancel(self, media_ids=None, run_id=None):
        targets = [future for future, item in list(self.active.items())
                   if (media_ids is None or item['media_id'] in media_ids)
                   and (run_id is None or item.get('run_id') == run_id)]
        for future in targets:
            future.cancel()
        await asyncio.gather(*targets, return_exceptions=True)

    async def loop(self):
        while not self.stopping:
            settings = self.db.settings()
            queued_kinds = self.tasks.queued_kinds()
            active_images = sum(item['_phase'] == 'image' for item in self.active.values())
            while active_images < settings['image_concurrency'] and not self.stopping:
                task = self.tasks.claim('image')
                if not task:
                    break
                task['_phase'] = 'image'
                future = asyncio.create_task(self.execute(task))
                self.active[future] = task
                active_images += 1

            # Video pipelines have no outer/global concurrency ceiling. Every
            # video may enter the pipeline; actual work is independently
            # throttled by its selected decoder and by the AI request slots.
            while 'video' in queued_kinds and not self.stopping:
                task = self.tasks.claim('video')
                if not task:
                    break
                task['_phase'] = 'video'
                future = asyncio.create_task(self.execute(task))
                self.active[future] = task

            if not queued_kinds:
                active_vectors = sum(item['_phase'] == 'vector' for item in self.active.values())
                while active_vectors < settings['embedding_request_concurrency'] and not self.stopping:
                    task = self.db.one("SELECT * FROM vector_jobs WHERE state='queued' LIMIT 1")
                    if not task:
                        break
                    self.db.execute("UPDATE vector_jobs SET state='running' WHERE media_id=? AND profile=?", (task['media_id'],task['profile']))
                    task['_phase'] = 'vector'
                    future = asyncio.create_task(self.execute(task, True))
                    self.active[future] = task
                    active_vectors += 1
            self.finish_rebuilds()
            self.settle()
            await asyncio.sleep(.1)

    async def close(self):
        self.stopping = True
        await self.cancel()
        if self.providers.codex:
            await self.providers.codex.close()
