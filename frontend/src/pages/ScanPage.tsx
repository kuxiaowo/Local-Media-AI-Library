import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ImageOff, Play, RefreshCw, RotateCcw } from 'lucide-react';
import { API_BASE } from '../api/client';
import { getMediaQueue, getScanStatus, listJobs, retryJob, startScan } from '../api/scan';
import { StatusBadge } from '../components/StatusBadge';
import type { Job, MediaQueueItem, ScanMode } from '../types';

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
    mutationFn: (mode: ScanMode) => startScan({ mode }),
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

  const status = statusQuery.data;
  const queueItems = mediaQueueQuery.data?.items ?? [];
  const maintenanceJobs = (jobsQuery.data ?? []).filter((job) => maintenanceJobTypes.has(job.job_type));

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
            onClick={() => startMutation.mutate('incremental')}
            disabled={startMutation.isPending}
            title="只发现并处理新文件，不重算已有文件"
          >
            <Play className="h-4 w-4" />
            检测新文件
          </button>
          <button
            className="btn"
            onClick={() => startMutation.mutate('full')}
            disabled={startMutation.isPending}
            title="重新生成已扫描文件的元数据、AI 总结和 embedding"
          >
            <RefreshCw className="h-4 w-4" />
            全部重新生成
          </button>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="队列" value={status?.queued ?? 0} />
        <Metric label="运行中" value={status?.running ?? 0} />
        <Metric label="已完成任务" value={status?.completed ?? 0} />
        <Metric label="失败任务" value={status?.failed ?? 0} />
        <Metric label="媒体总数" value={status?.media_total ?? 0} />
        <Metric label="已分析" value={status?.media_done ?? 0} />
        <Metric label="媒体失败" value={status?.media_failed ?? 0} />
        <Metric label="缺失文件" value={status?.media_missing ?? 0} />
      </section>

      <section className="panel overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <div className="text-sm font-semibold">照片处理队列</div>
            <div className="mt-1 text-xs text-slate-500">
              当前共 {mediaQueueQuery.data?.total ?? 0} 张照片等待、处理中、失败或需要重分析
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
          <table className="min-w-full text-left text-sm">
            <thead className="border-b border-line bg-panel text-xs uppercase text-slate-500">
              <tr>
                <th className="px-4 py-2">照片</th>
                <th className="px-4 py-2">当前阶段</th>
                <th className="px-4 py-2">队列状态</th>
                <th className="px-4 py-2">媒体状态</th>
                <th className="px-4 py-2">错误</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {queueItems.map((item) => (
                <tr key={item.media_id} className="border-b border-line last:border-0">
                  <td className="px-4 py-3">
                    <div className="flex min-w-[260px] items-center gap-3">
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
                        <div className="max-w-xl truncate text-xs text-slate-500">{item.path}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 font-medium">{stageLabel(item)}</td>
                  <td className="px-4 py-3">
                    {item.job_status ? <StatusBadge status={item.job_status} /> : '-'}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.media_status} />
                  </td>
                  <td className="max-w-md truncate px-4 py-3 text-red-700">{item.error_message}</td>
                  <td className="px-4 py-3 text-right">
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
                    当前没有照片在队列中
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
                  <td className="max-w-md truncate px-4 py-3 text-red-700">{job.error_message}</td>
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

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="panel p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}

function stageLabel(item: MediaQueueItem) {
  if (item.job_type === 'extract_metadata') {
    return '提取元数据';
  }
  if (item.job_type === 'analyze_image') {
    return 'AI 分析图片';
  }
  if (item.job_type === 'generate_embedding') {
    return '生成搜索向量';
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
