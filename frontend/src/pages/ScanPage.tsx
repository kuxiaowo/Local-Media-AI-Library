import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ImageOff, Pause, Play, RefreshCw, RotateCcw, Trash2 } from 'lucide-react';
import { API_BASE } from '../api/client';
import {
  clearJobs,
  getMediaQueue,
  getScanStatus,
  listJobs,
  pauseScanTasks,
  resumeScanTasks,
  retryJob,
  startScan,
} from '../api/scan';
import { StatusBadge } from '../components/StatusBadge';
import type { Job, MediaQueueItem } from '../types';

const maintenanceJobTypes = new Set(['cleanup_stale_media']);

export function ScanPage() {
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    queryKey: ['scan-status'],
    queryFn: getScanStatus,
    refetchInterval: 1500,
  });
  const mediaQueueQuery = useQuery({
    queryKey: ['media-queue'],
    queryFn: getMediaQueue,
    refetchInterval: 1500,
  });
  const jobsQuery = useQuery({
    queryKey: ['jobs'],
    queryFn: listJobs,
    refetchInterval: 1500,
  });
  const startMutation = useMutation({
    mutationFn: () => startScan({ mode: 'incremental' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['scan-status'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const retryMutation = useMutation({
    mutationFn: retryJob,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['scan-status'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const clearMutation = useMutation({
    mutationFn: clearJobs,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['scan-status'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const pauseMutation = useMutation({
    mutationFn: pauseScanTasks,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['scan-status'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });
  const resumeMutation = useMutation({
    mutationFn: resumeScanTasks,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['scan-status'] });
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
    },
  });

  const status = statusQuery.data;
  const isPaused = status?.paused ?? false;
  const isPauseTogglePending = pauseMutation.isPending || resumeMutation.isPending;
  const queueItems = mediaQueueQuery.data?.items ?? [];
  const maintenanceJobs = (jobsQuery.data ?? []).filter((job) => maintenanceJobTypes.has(job.job_type));
  const jobCount =
    (status?.queued ?? 0) + (status?.running ?? 0) + (status?.failed ?? 0) + (status?.completed ?? 0);

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">扫描任务</h1>
          <p className="text-sm text-slate-500">按照片查看当前处理队列、失败重试和处理统计</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn btn-primary"
            onClick={() => startMutation.mutate()}
            disabled={startMutation.isPending || isPaused}
            title={isPaused ? '任务已暂停，继续后再检测新文件' : '只发现并处理新文件，不重算已有文件'}
          >
            <Play className="h-4 w-4" />
            检测新文件
          </button>
          <button
            className={isPaused ? 'btn btn-primary' : 'btn'}
            onClick={() => {
              if (isPaused) {
                resumeMutation.mutate();
              } else {
                pauseMutation.mutate();
              }
            }}
            disabled={isPauseTogglePending}
            title={isPaused ? '继续派发排队中的任务' : '暂停派发新任务，已运行的任务会完成当前处理'}
          >
            {isPaused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
            {isPaused ? '继续任务' : '暂停所有任务'}
          </button>
          <button
            className="btn"
            onClick={() => {
              if (window.confirm('确定去除所有任务？这不会删除媒体文件或分析结果。')) {
                clearMutation.mutate();
              }
            }}
            disabled={clearMutation.isPending || jobCount === 0}
            title="清空当前任务队列和任务历史，不删除媒体文件"
          >
            <Trash2 className="h-4 w-4" />
            去除所有任务
          </button>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="任务派发" value={isPaused ? '已暂停' : '运行中'} />
        <Metric label="队列" value={status?.queued ?? 0} />
        <Metric label="运行中" value={status?.running ?? 0} />
        <Metric label="已完成任务" value={status?.completed ?? 0} />
        <Metric label="失败任务" value={status?.failed ?? 0} />
        <Metric label="媒体总数" value={status?.media_total ?? 0} />
        <Metric label="已完成" value={status?.media_done ?? 0} />
        <Metric label="媒体失败" value={status?.media_failed ?? 0} />
        <Metric label="缺失文件" value={status?.media_missing ?? 0} />
      </section>

      <section className="panel overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <div className="text-sm font-semibold">媒体处理任务</div>
            <div className="mt-1 text-xs text-slate-500">
              当前共 {mediaQueueQuery.data?.total ?? 0} 个媒体任务正在排队、运行或失败待重试
            </div>
          </div>
          <button className="icon-btn" title="刷新队列" onClick={() => mediaQueueQuery.refetch()}>
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>

        {mediaQueueQuery.error && (
          <div className="m-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {mediaQueueQuery.error.message}
          </div>
        )}

        <div className="overflow-auto">
          <table className="w-full min-w-[1120px] table-fixed text-left text-sm">
            <colgroup>
              <col className="w-[38%]" />
              <col className="w-40" />
              <col className="w-32" />
              <col className="w-32" />
              <col />
              <col className="w-16" />
            </colgroup>
            <thead className="border-b border-line bg-panel text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">媒体</th>
                <th className="px-4 py-2">当前阶段</th>
                <th className="px-4 py-2">队列状态</th>
                <th className="px-4 py-2">媒体状态</th>
                <th className="px-4 py-2">错误</th>
                <th className="px-3 py-2" aria-label="操作"></th>
              </tr>
            </thead>
            <tbody>
              {queueItems.map((item) => (
                <tr key={item.media_id} className="border-b border-line last:border-0">
                  <td className="px-4 py-3 align-middle">
                    <div className="flex min-w-0 items-center gap-3">
                      <div className="h-14 w-14 shrink-0 overflow-hidden rounded-md border border-line bg-slate-100">
                        {item.thumbnail_url ? (
                          <img
                            className="h-full w-full object-cover"
                            src={`${API_BASE}${item.thumbnail_url}`}
                            alt={item.path}
                            loading="lazy"
                          />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center text-slate-400">
                            <ImageOff className="h-5 w-5" />
                          </div>
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="truncate font-medium">{fileName(item.path)}</div>
                        <div className="truncate text-xs text-slate-500">{item.path}</div>
                      </div>
                    </div>
                  </td>
                  <td className="whitespace-normal break-words px-4 py-3 align-middle font-medium text-slate-800">
                    {stageLabel(item)}
                  </td>
                  <td className="px-4 py-3 align-middle">
                    {item.job_status ? <StatusBadge status={item.job_status} /> : '-'}
                  </td>
                  <td className="px-4 py-3 align-middle">
                    <StatusBadge status={item.media_status} />
                  </td>
                  <td className="min-w-0 px-4 py-3 align-middle">
                    <ExpandableError message={item.error_message} />
                  </td>
                  <td className="px-3 py-3 text-right align-middle">
                    {item.job_status === 'failed' && item.job_id && (
                      <button
                        className="icon-btn"
                        title="重试当前照片失败任务"
                        onClick={() => retryMutation.mutate(item.job_id!)}
                      >
                        <RotateCcw className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!mediaQueueQuery.error && queueItems.length === 0 && (
                <tr>
                  <td className="px-4 py-10 text-center text-sm text-slate-500" colSpan={6}>
                    当前没有媒体处理任务
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <div className="text-sm font-semibold">维护任务</div>
            <div className="mt-1 text-xs text-slate-500">
              清除过期数据等不绑定单张照片的后台任务会显示在这里
            </div>
          </div>
          <button className="icon-btn" title="刷新维护任务" onClick={() => jobsQuery.refetch()}>
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>

        {jobsQuery.error && (
          <div className="m-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {jobsQuery.error.message}
          </div>
        )}

        <div className="overflow-auto">
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-line bg-panel text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">任务</th>
                <th className="px-4 py-2">状态</th>
                <th className="px-4 py-2">进度</th>
                <th className="px-4 py-2">结果</th>
                <th className="px-4 py-2">创建时间</th>
                <th className="px-4 py-2">错误</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {maintenanceJobs.map((job) => (
                <tr key={job.id} className="border-b border-line last:border-0">
                  <td className="px-4 py-3 font-medium">{jobLabel(job)}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={job.status} />
                  </td>
                  <td className="px-4 py-3 text-slate-700">{jobProgress(job)}</td>
                  <td className="px-4 py-3 text-slate-700">{jobResult(job)}</td>
                  <td className="px-4 py-3 text-slate-500">{new Date(job.created_at).toLocaleString()}</td>
                  <td className="px-4 py-3">
                    <ExpandableError message={job.error_message} />
                  </td>
                  <td className="px-4 py-3 text-right">
                    {job.status === 'failed' && (
                      <button
                        className="icon-btn"
                        title="重试维护任务"
                        onClick={() => retryMutation.mutate(job.id)}
                      >
                        <RotateCcw className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {!jobsQuery.error && maintenanceJobs.length === 0 && (
                <tr>
                  <td className="px-4 py-10 text-center text-sm text-slate-500" colSpan={7}>
                    暂无维护任务
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="panel p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function ExpandableError({ message }: { message: string | null }) {
  if (!message) {
    return <span className="text-slate-400">-</span>;
  }

  const shouldCollapse = message.length > 80 || message.includes('\n');
  if (!shouldCollapse) {
    return <span className="block max-w-full break-words text-red-700">{message}</span>;
  }

  return (
    <details className="group min-w-0 max-w-full text-red-700">
      <summary className="flex min-w-0 cursor-pointer list-none items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate">{message}</span>
        <span className="shrink-0 text-xs text-red-500 group-open:hidden">展开</span>
        <span className="hidden shrink-0 text-xs text-red-500 group-open:inline">收起</span>
      </summary>
      <pre className="mt-2 max-w-full whitespace-pre-wrap break-words rounded-md border border-red-200 bg-red-50 p-3 font-sans text-xs leading-5">
        {message}
      </pre>
    </details>
  );
}

function stageLabel(item: MediaQueueItem) {
  const stage = typeof item.job_payload?.stage === 'string' ? item.job_payload.stage : null;
  if (item.job_type === 'extract_metadata') {
    return '提取元数据';
  }
  if (item.job_type === 'analyze_image') {
    return 'AI 分析图片';
  }
  if (item.job_type === 'analyze_video') {
    if (stage === 'extract_frames') {
      return 'AI 分析视频：抽帧';
    }
    if (stage === 'analyze_segments') {
      return `AI 分析视频：分段识别${batchSuffix(item)}`;
    }
    if (stage === 'final_summary') {
      return 'AI 分析视频：生成最终总结';
    }
    if (stage === 'generate_embedding' || stage === 'queue_embedding') {
      return 'AI 分析视频：生成搜索向量';
    }
    return 'AI 分析视频';
  }
  if (item.job_type === 'reanalyze_video_summary') {
    return '重新生成视频最终总结';
  }
  if (item.job_type === 'reanalyze_media') {
    return '准备重新分析';
  }
  if (item.media_status === 'pending') {
    return '等待提取元数据';
  }
  if (item.media_status === 'metadata_done') {
    return '等待 AI 分析';
  }
  if (item.media_status === 'embedding_pending') {
    return 'AI 总结完成，等待生成搜索向量';
  }
  if (item.media_status === 'analyzing') {
    return 'AI 分析图片';
  }
  if (item.media_status === 'needs_reanalysis') {
    return '等待重新分析';
  }
  if (item.media_status === 'failed') {
    return '处理失败';
  }
  return '等待处理';
}

function batchSuffix(item: MediaQueueItem) {
  if (item.job_progress_total <= 0 || item.job_progress_current <= 0) {
    return '';
  }
  return `（第 ${item.job_progress_current}/${item.job_progress_total} 批）`;
}

function fileName(path: string) {
  return path.split(/[\\/]/).pop() || path;
}

function jobLabel(job: Job) {
  if (job.job_type === 'cleanup_stale_media') {
    return '清除过期数据';
  }
  return job.job_type;
}

function jobProgress(job: Job) {
  if (job.progress_total > 0) {
    return `${job.progress_current} / ${job.progress_total}`;
  }
  return '-';
}

function jobResult(job: Job) {
  const checked = typeof job.payload?.checked === 'number' ? job.payload.checked : null;
  const deleted = typeof job.payload?.deleted === 'number' ? job.payload.deleted : null;
  if (checked !== null || deleted !== null) {
    return `检查 ${checked ?? 0}，删除 ${deleted ?? 0}`;
  }
  return '-';
}
