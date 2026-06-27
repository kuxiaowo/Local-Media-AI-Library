import { Link } from 'react-router-dom';
import { ImageOff } from 'lucide-react';
import { API_BASE } from '../api/client';
import type { MediaFile } from '../types';
import { StatusBadge } from './StatusBadge';

type DetailReturnState = {
  returnTo: string;
  returnLabel: string;
};

export function MediaGrid({
  items,
  detailReturnState,
}: {
  items: MediaFile[];
  detailReturnState?: DetailReturnState;
}) {
  if (items.length === 0) {
    return (
      <div className="panel flex h-64 items-center justify-center text-sm text-slate-500">
        暂无媒体
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
      {items.map((media) => {
        const originalFileName = fileName(media.path);
        return (
          <Link
            to={`/media/${media.id}`}
            state={detailReturnState}
            key={media.id}
            className="panel overflow-hidden transition hover:border-signal"
          >
            <div className="aspect-[4/3] bg-slate-100">
              {media.thumbnail_path ? (
                <img
                  src={`${API_BASE}/api/media/${media.id}/thumbnail`}
                  alt={media.ai_summary?.title ?? media.path}
                  className="h-full w-full object-cover"
                  loading="lazy"
                />
              ) : (
                <div className="flex h-full items-center justify-center text-slate-400">
                  <ImageOff className="h-8 w-8" />
                </div>
              )}
            </div>
            <div className="space-y-2 p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 truncate text-sm font-semibold">
                  {media.ai_summary?.title ?? originalFileName}
                </div>
                <StatusBadge status={media.status} />
              </div>
              <div className="truncate text-[11px] leading-4 text-slate-400" title={originalFileName}>
                原始文件名：{originalFileName}
              </div>
              <div className="text-xs text-slate-500">
                {media.captured_at ? new Date(media.captured_at).toLocaleString() : '时间未知'}
              </div>
              <p className="line-clamp-2 min-h-10 text-sm text-slate-700">
                {media.ai_summary?.short_summary ?? media.error_message ?? '暂无摘要'}
              </p>
            </div>
          </Link>
        );
      })}
    </div>
  );
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() || path;
}
