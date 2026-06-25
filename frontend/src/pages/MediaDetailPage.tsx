import { useMutation, useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { ExternalLink, FolderOpen, RotateCcw } from 'lucide-react';
import { API_BASE } from '../api/client';
import { getMedia, openMediaLocation, reanalyzeMedia } from '../api/media';
import { StatusBadge } from '../components/StatusBadge';

export function MediaDetailPage() {
  const { id } = useParams<{ id: string }>();
  const query = useQuery({
    queryKey: ['media-detail', id],
    queryFn: () => getMedia(id!),
    enabled: Boolean(id),
  });
  const reanalyzeMutation = useMutation({ mutationFn: () => reanalyzeMedia(id!) });
  const openMutation = useMutation({ mutationFn: () => openMediaLocation(id!) });
  const media = query.data;

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
          <button className="btn" onClick={() => reanalyzeMutation.mutate()}>
            <RotateCcw className="h-4 w-4" />
            重分析
          </button>
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
        <section className="panel overflow-hidden">
          <img
            src={`${API_BASE}/api/media/${media.id}/preview`}
            alt={media.ai_summary?.title ?? media.path}
            className="max-h-[72vh] w-full object-contain bg-slate-100"
          />
        </section>

        <section className="panel p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold">元数据</h2>
            <StatusBadge status={media.status} />
          </div>
          <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-sm">
            <dt className="text-slate-500">尺寸</dt>
            <dd>{media.width && media.height ? `${media.width} x ${media.height}` : '-'}</dd>
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
          <JsonBlock title="原始 JSON" value={media.ai_summary?.raw_json} />
        </div>
      </section>

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
