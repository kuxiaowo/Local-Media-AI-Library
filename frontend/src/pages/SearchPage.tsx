import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { searchMedia } from '../api/search';
import { listMediaDirectories } from '../api/media';
import { API_BASE } from '../api/client';
import type { SearchMode } from '../types';

const SEARCH_MODE_COPY: Record<SearchMode, { placeholder: string; examples: string[] }> = {
  vector: {
    placeholder: '学校展示、海边日落、穿红衣服的人、夜晚街景',
    examples: ['海边日落', '学校展示', '有蛋糕的照片', '雪山徒步视频'],
  },
  ai: {
    placeholder: '总结这个文件夹的照片、推荐去年夏天的照片、找最适合做封面的照片',
    examples: ['总结这个文件夹的照片', '推荐去年夏天的照片', '找出最适合做封面的照片', '这个目录里有哪些重复场景'],
  },
};

const searchMediaTypeValues = new Set(['any', 'image', 'video']);

export function SearchPage() {
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const submittedSearch = useMemo(() => readSearchParams(searchParams), [searchParams]);
  const [query, setQuery] = useState(submittedSearch.query);
  const [searchMode, setSearchMode] = useState<SearchMode>(submittedSearch.searchMode);
  const [mediaType, setMediaType] = useState(submittedSearch.mediaType);
  const [directoryPath, setDirectoryPath] = useState(submittedSearch.directoryPath);
  const [dateFrom, setDateFrom] = useState(submittedSearch.dateFrom);
  const [dateTo, setDateTo] = useState(submittedSearch.dateTo);
  const directoriesQuery = useQuery({
    queryKey: ['media-directories'],
    queryFn: listMediaDirectories,
  });
  const searchResultsQuery = useQuery({
    queryKey: ['search', submittedSearch],
    queryFn: () =>
      searchMedia({
        query: submittedSearch.query,
        mode: submittedSearch.searchMode,
        media_type: submittedSearch.mediaType,
        directory_path: submittedSearch.directoryPath || null,
        date_from: submittedSearch.dateFrom ? `${submittedSearch.dateFrom}T00:00:00` : null,
        date_to: submittedSearch.dateTo ? `${submittedSearch.dateTo}T23:59:59` : null,
        limit: 30,
        candidate_k: submittedSearch.searchMode === 'ai' ? 200 : 100,
      }),
    enabled: Boolean(submittedSearch.query.trim()),
    staleTime: 5 * 60 * 1000,
  });
  const modeCopy = SEARCH_MODE_COPY[searchMode];
  const detailReturnState = useMemo(
    () => ({
      returnTo: `${location.pathname}${location.search}`,
      returnLabel: '返回搜索',
    }),
    [location.pathname, location.search],
  );

  useEffect(() => {
    setQuery(submittedSearch.query);
    setSearchMode(submittedSearch.searchMode);
    setMediaType(submittedSearch.mediaType);
    setDirectoryPath(submittedSearch.directoryPath);
    setDateFrom(submittedSearch.dateFrom);
    setDateTo(submittedSearch.dateTo);
  }, [
    submittedSearch.query,
    submittedSearch.searchMode,
    submittedSearch.mediaType,
    submittedSearch.directoryPath,
    submittedSearch.dateFrom,
    submittedSearch.dateTo,
  ]);

  function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedQuery = query.trim();
    if (!trimmedQuery) {
      return;
    }

    const nextParams = new URLSearchParams();
    nextParams.set('q', trimmedQuery);
    if (searchMode !== 'vector') {
      nextParams.set('mode', searchMode);
    }
    if (mediaType !== 'any') {
      nextParams.set('media_type', mediaType);
    }
    if (directoryPath) {
      nextParams.set('directory_path', directoryPath);
    }
    if (dateFrom) {
      nextParams.set('date_from', dateFrom);
    }
    if (dateTo) {
      nextParams.set('date_to', dateTo);
    }
    setSearchParams(nextParams);
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-2xl font-semibold">AI 搜索</h1>
        <p className="text-sm text-slate-500">向量检索用于快速召回；AI 检索读取已有摘要并生成回答。</p>
      </header>

      <form className="panel space-y-3 p-3" onSubmit={submit}>
        <div className="flex flex-col gap-3 lg:flex-row">
          <input
            className="control min-w-0 flex-1"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={modeCopy.placeholder}
          />
          <select
            className="control lg:w-36"
            value={searchMode}
            onChange={(event) => setSearchMode(event.target.value as SearchMode)}
            title="搜索模式"
          >
            <option value="vector">向量检索</option>
            <option value="ai">AI 检索</option>
          </select>
          <button className="btn btn-primary lg:w-28" type="submit" disabled={searchResultsQuery.isFetching}>
            <Search className="h-4 w-4" />
            搜索
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <span>{searchMode === 'ai' ? 'AI 检索示例' : '向量检索示例'}</span>
          {modeCopy.examples.map((example) => (
            <button
              key={example}
              className="rounded-md border border-slate-200 bg-white px-2 py-1 text-slate-600 transition hover:border-signal hover:text-signal"
              type="button"
              onClick={() => setQuery(example)}
            >
              {example}
            </button>
          ))}
        </div>

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">媒体类型</span>
            <select className="control w-full" value={mediaType} onChange={(event) => setMediaType(event.target.value)}>
              <option value="any">全部</option>
              <option value="image">图片</option>
              <option value="video">视频</option>
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">目录</span>
            <select
              className="control w-full"
              value={directoryPath}
              onChange={(event) => setDirectoryPath(event.target.value)}
            >
              <option value="">全部目录</option>
              {(directoriesQuery.data ?? []).map((directory) => (
                <option key={directory.path} value={directory.path}>
                  {directory.name} · {directory.display_path}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">开始时间</span>
            <input className="control w-full" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          </label>
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">结束时间</span>
            <input className="control w-full" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
        </div>
      </form>

      {searchResultsQuery.error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {searchResultsQuery.error.message}
        </div>
      )}

      {searchResultsQuery.data?.answer && (
        <section className="panel p-4">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>{searchResultsQuery.data.mode === 'ai' ? 'AI 检索回答' : '搜索结果'}</span>
            {searchResultsQuery.data.ai_model && <span>模型：{searchResultsQuery.data.ai_model}</span>}
            {searchResultsQuery.data.scope_total !== null && <span>范围：{searchResultsQuery.data.scope_total} 项</span>}
          </div>
          <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{searchResultsQuery.data.answer}</p>
        </section>
      )}

      <section className="space-y-3">
        {(searchResultsQuery.data?.results ?? []).map((item) => (
          <Link
            to={`/media/${item.media_id}`}
            state={detailReturnState}
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
        {searchResultsQuery.data && searchResultsQuery.data.results.length === 0 && (
          <div className="panel p-6 text-center text-sm text-slate-500">没有匹配结果</div>
        )}
      </section>
    </div>
  );
}

function readSearchParams(params: URLSearchParams) {
  const mode = params.get('mode');
  return {
    query: params.get('q') ?? '',
    searchMode: mode === 'ai' ? 'ai' : ('vector' as SearchMode),
    mediaType: readSearchMediaType(params.get('media_type')),
    directoryPath: params.get('directory_path') ?? '',
    dateFrom: readDateParam(params.get('date_from')),
    dateTo: readDateParam(params.get('date_to')),
  };
}

function readSearchMediaType(value: string | null) {
  if (!value || !searchMediaTypeValues.has(value)) {
    return 'any';
  }
  return value;
}

function readDateParam(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return '';
  }
  return value;
}
