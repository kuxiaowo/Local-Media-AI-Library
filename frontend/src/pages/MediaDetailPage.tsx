import { FormEvent, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { ExternalLink, FolderOpen, RotateCcw, Save } from 'lucide-react';
import { API_BASE } from '../api/client';
import {
  getMedia,
  openMediaLocation,
  reanalyzeMedia,
  reanalyzeVideoFinalSummary,
  updateMediaBackgroundContext,
} from '../api/media';
import { StatusBadge } from '../components/StatusBadge';

export function MediaDetailPage() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [backgroundForm, setBackgroundForm] = useState({
    background_context: '',
  });
  const query = useQuery({
    queryKey: ['media-detail', id],
    queryFn: () => getMedia(id!),
    enabled: Boolean(id),
  });
  const reanalyzeMutation = useMutation({
    mutationFn: () => reanalyzeMedia(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-detail', id] });
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const finalSummaryMutation = useMutation({
    mutationFn: () => reanalyzeVideoFinalSummary(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-detail', id] });
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const saveBackgroundMutation = useMutation({
    mutationFn: () =>
      updateMediaBackgroundContext(id!, {
        background_context: backgroundForm.background_context || null,
      }),
    onSuccess: (data) => {
      setBackgroundForm({
        background_context: data.background_context ?? '',
      });
      queryClient.invalidateQueries({ queryKey: ['media-detail', id] });
      queryClient.invalidateQueries({ queryKey: ['media'] });
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
    },
  });
  const openMutation = useMutation({ mutationFn: () => openMediaLocation(id!) });
  const media = query.data;

  useEffect(() => {
    if (!media) {
      return;
    }
    setBackgroundForm({
      background_context: media.background_context ?? '',
    });
  }, [media?.id, media?.background_context]);

  function submitBackground(event: FormEvent) {
    event.preventDefault();
    saveBackgroundMutation.mutate();
  }

  if (query.error) {
    return <div className="panel p-4 text-sm text-red-700">{query.error.message}</div>;
  }
  if (!media) {
    return <div className="panel p-4 text-sm text-slate-500">加载中</div>;
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold">
            {media.ai_summary?.title ?? media.path.split(/[\\/]/).pop()}
          </h1>
          <p className="truncate text-sm text-slate-500">{media.path}</p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => openMutation.mutate()}>
            <FolderOpen className="h-4 w-4" />
            位置
          </button>
          {media.media_type === 'video' ? (
            <>
              <button
                className="btn"
                onClick={() => reanalyzeMutation.mutate()}
                disabled={reanalyzeMutation.isPending}
                title="重新抽帧、分段分析、最终总结和搜索向量"
              >
                <RotateCcw className="h-4 w-4" />
                全部重新生成
              </button>
              <button
                className="btn"
                onClick={() => finalSummaryMutation.mutate()}
                disabled={finalSummaryMutation.isPending || !media.video_segments?.length}
                title="只基于已有视频片段重新生成最终总结和搜索向量"
              >
                <RotateCcw className="h-4 w-4" />
                重新生成最终总结
              </button>
            </>
          ) : (
            <button className="btn" onClick={() => reanalyzeMutation.mutate()} disabled={reanalyzeMutation.isPending}>
              <RotateCcw className="h-4 w-4" />
              重新生成
            </button>
          )}
        </div>
      </header>

      {(reanalyzeMutation.error || finalSummaryMutation.error || saveBackgroundMutation.error) && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {(reanalyzeMutation.error ?? finalSummaryMutation.error ?? saveBackgroundMutation.error)?.message}
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
        <section className="panel overflow-hidden">
          {media.media_type === 'video' ? (
            <video
              src={`${API_BASE}/api/media/${media.id}/preview`}
              className="max-h-[72vh] w-full bg-slate-950"
              controls
              preload="metadata"
            />
          ) : (
            <img
              src={`${API_BASE}/api/media/${media.id}/preview`}
              alt={media.ai_summary?.title ?? media.path}
              className="max-h-[72vh] w-full object-contain bg-slate-100"
            />
          )}
        </section>

        <section className="panel p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold">元数据</h2>
            <StatusBadge status={media.status} />
          </div>
          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-sm">
            <dt className="text-slate-500">类型</dt>
            <dd>{media.media_type === 'video' ? '视频' : '图片'}</dd>
            <dt className="text-slate-500">尺寸</dt>
            <dd>{media.width && media.height ? `${media.width} x ${media.height}` : '-'}</dd>
            {media.media_type === 'video' && (
              <>
                <dt className="text-slate-500">时长</dt>
                <dd>{formatDuration(media.duration_seconds)}</dd>
              </>
            )}
            <dt className="text-slate-500">大小</dt>
            <dd>{media.file_size ? `${(media.file_size / 1024 / 1024).toFixed(2)} MB` : '-'}</dd>
            <dt className="text-slate-500">拍摄时间</dt>
            <dd>{media.captured_at ? new Date(media.captured_at).toLocaleString() : '-'}</dd>
            <dt className="text-slate-500">时间来源</dt>
            <dd>{media.captured_at_source ?? '-'}</dd>
            <dt className="text-slate-500">置信度</dt>
            <dd>{media.captured_at_confidence ?? '-'}</dd>
          </dl>

          {media.error_message && (
            <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {media.error_message}
            </div>
          )}
        </section>
      </div>

      <form className="panel p-4" onSubmit={submitBackground}>
        <div className="mb-3">
          <h2 className="text-base font-semibold">当前媒体背景提示</h2>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            留空时使用目录背景补充；背景使用规则仍沿用目录规则。保存后重新生成，新的背景提示才会进入模型分析。
          </p>
        </div>
        <div className="max-w-3xl">
          <label>
            <span className="mb-1 block text-sm font-medium">背景补充</span>
            <textarea
              className="control min-h-28 w-full resize-y"
              value={backgroundForm.background_context}
              onChange={(event) =>
                setBackgroundForm({ ...backgroundForm, background_context: event.target.value })
              }
              placeholder="只对当前媒体生效"
            />
          </label>
        </div>
        {saveBackgroundMutation.isSuccess && (
          <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
            当前媒体背景提示已保存。
          </div>
        )}
        <button className="btn btn-primary mt-4" type="submit" disabled={saveBackgroundMutation.isPending}>
          <Save className="h-4 w-4" />
          保存背景提示
        </button>
      </form>

      <section className="panel p-4">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-base font-semibold">AI 摘要</h2>
          {media.ai_summary?.confidence && <StatusBadge status={media.ai_summary.confidence} />}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <h3 className="mb-1 text-sm font-semibold">一句话</h3>
            <p className="text-sm text-slate-700">{media.ai_summary?.short_summary ?? '-'}</p>
          </div>
          <div>
            <h3 className="mb-1 text-sm font-semibold">场景</h3>
            <p className="text-sm text-slate-700">{media.ai_summary?.scene ?? '-'}</p>
          </div>
          <div className="lg:col-span-2">
            <h3 className="mb-1 text-sm font-semibold">详细描述</h3>
            <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">
              {media.ai_summary?.detailed_summary ?? '-'}
            </p>
          </div>
          <JsonBlock title="关键词" value={media.ai_summary?.search_keywords} />
          {media.media_type === 'video' && <JsonBlock title="时间线" value={rawJsonValue(media.ai_summary?.raw_json, 'timeline')} />}
          <JsonBlock title="原始 JSON" value={media.ai_summary?.raw_json} />
        </div>
      </section>

      {media.media_type === 'video' && media.video_segments && media.video_segments.length > 0 && (
        <section className="panel p-4">
          <h2 className="mb-3 text-base font-semibold">视频片段</h2>
          <div className="space-y-3">
            {media.video_segments.map((segment) => {
              const frames = (media.video_frames ?? []).filter((frame) => frame.segment_id === segment.id);
              return (
                <article key={segment.id} className="rounded-md border border-slate-200 p-3">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <h3 className="text-sm font-semibold">片段 {segment.segment_index}</h3>
                    <span className="text-xs text-slate-500">
                      {formatTimestamp(segment.start_time_seconds ?? 0)} -{' '}
                      {formatTimestamp(segment.end_time_seconds ?? segment.start_time_seconds ?? 0)}
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">
                    {segment.current_segment_summary ?? '-'}
                  </p>
                  <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    <TextList title="重要观察" value={segment.important_observations} />
                    <TextList title="不确定点" value={segment.uncertain_points} />
                    <TextList title="标签" value={segment.current_segment_tags} />
                    <TextList title="重要物体/场景" value={segment.important_objects} />
                    <TextList title="新增线索" value={segment.new_objects_or_scenes} />
                  </div>
                  {frames.length > 0 && (
                    <div className="mt-3 grid gap-2 sm:grid-cols-3 xl:grid-cols-6">
                      {frames.map((frame) => (
                        <img
                          key={frame.id}
                          src={`${API_BASE}/api/media/${media.id}/frames/${frame.id}`}
                          alt={`片段 ${segment.segment_index} 关键帧 ${frame.frame_index ?? ''}`}
                          className="aspect-video w-full rounded-md bg-slate-100 object-cover"
                          loading="lazy"
                        />
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>
      )}

      {media.media_type === 'video' && media.video_frames && media.video_frames.length > 0 && (
        <section className="panel p-4">
          <h2 className="mb-3 text-base font-semibold">关键帧</h2>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {media.video_frames.map((frame) => (
              <div key={frame.id} className="overflow-hidden rounded-md border border-slate-200 bg-white">
                <img
                  src={`${API_BASE}/api/media/${media.id}/frames/${frame.id}`}
                  alt={frame.caption ?? `关键帧 ${formatTimestamp(frame.timestamp_seconds)}`}
                  className="aspect-video w-full bg-slate-100 object-cover"
                  loading="lazy"
                />
                <div className="space-y-2 p-3">
                  <div className="text-xs font-semibold text-slate-500">
                    {formatTimestamp(frame.timestamp_seconds)}
                  </div>
                  <p className="line-clamp-3 text-sm text-slate-700">{frame.caption ?? '-'}</p>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <a
        href={`${API_BASE}/api/media/${media.id}/preview`}
        target="_blank"
        rel="noreferrer"
        className="btn w-fit"
      >
        <ExternalLink className="h-4 w-4" />
        预览文件
      </a>
    </div>
  );
}

function formatDuration(seconds: number | null) {
  if (!seconds || seconds <= 0) {
    return '-';
  }
  const whole = Math.round(seconds);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`;
}

function formatTimestamp(seconds: number) {
  if (!Number.isFinite(seconds)) {
    return '0:00';
  }
  return formatDuration(seconds) === '-' ? '0:00' : formatDuration(seconds);
}

function TextList({ title, value }: { title: string; value: unknown }) {
  const items = toTextList(value);
  return (
    <div>
      <h4 className="mb-1 text-xs font-semibold text-slate-500">{title}</h4>
      <p className="text-sm text-slate-700">{items.length > 0 ? items.join('、') : '-'}</p>
    </div>
  );
}

function toTextList(value: unknown): string[] {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.map((item) => String(item).trim()).filter(Boolean);
  }
  if (typeof value === 'object') {
    return Object.values(value).map((item) => String(item).trim()).filter(Boolean);
  }
  const text = String(value).trim();
  return text ? [text] : [];
}

function rawJsonValue(value: unknown, key: string) {
  if (!value || typeof value !== 'object') {
    return null;
  }
  return (value as Record<string, unknown>)[key] ?? null;
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold">{title}</h3>
      <pre className="max-h-72 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
        {JSON.stringify(value ?? null, null, 2)}
      </pre>
    </div>
  );
}
