import { FormEvent, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { Link } from 'react-router-dom';
import { searchMedia } from '../api/search';
import { API_BASE } from '../api/client';

export function SearchPage() {
  const [query, setQuery] = useState('');
  const [mediaType, setMediaType] = useState('any');
  const mutation = useMutation({
    mutationFn: () =>
      searchMedia({
        query,
        media_type: mediaType,
        limit: 30,
        candidate_k: 100,
      }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) {
      mutation.mutate();
    }
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">AI 搜索</h1>
        <p className="text-sm text-slate-500">SQL 过滤、向量召回和轻量排序</p>
      </header>

      <form className="panel flex flex-col gap-3 p-3 sm:flex-row" onSubmit={submit}>
        <input
          className="control min-w-0 flex-1"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="学校展示、海边日落、白板文字"
        />
        <select
          className="control"
          value={mediaType}
          onChange={(event) => setMediaType(event.target.value)}
        >
          <option value="any">全部</option>
          <option value="image">图片</option>
          <option value="video">视频</option>
        </select>
        <button className="btn btn-primary" type="submit" disabled={mutation.isPending}>
          <Search className="h-4 w-4" />
          搜索
        </button>
      </form>

      {mutation.error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {mutation.error.message}
        </div>
      )}

      <section className="space-y-3">
        {(mutation.data?.results ?? []).map((item) => (
          <Link
            to={`/media/${item.media_id}`}
            key={item.media_id}
            className="panel grid gap-3 p-3 transition hover:border-signal sm:grid-cols-[160px_1fr]"
          >
            <img
              src={`${API_BASE}${item.thumbnail_url}`}
              alt={item.title ?? item.path}
              className="aspect-[4/3] w-full rounded-md bg-slate-100 object-cover"
              loading="lazy"
            />
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate text-base font-semibold">{item.title ?? item.path}</h2>
                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs">
                  {item.score.toFixed(3)}
                </span>
              </div>
              <p className="mt-1 text-sm text-slate-600">{item.short_summary}</p>
              <p className="mt-2 text-sm text-accent">{item.match_reason}</p>
              <p className="mt-2 truncate text-xs text-slate-500">{item.path}</p>
            </div>
          </Link>
        ))}
        {mutation.data && mutation.data.results.length === 0 && (
          <div className="panel p-6 text-center text-sm text-slate-500">没有匹配结果</div>
        )}
      </section>
    </div>
  );
}
