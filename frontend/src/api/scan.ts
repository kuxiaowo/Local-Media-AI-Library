import { apiRequest } from './client';
import type { GenerateAiRecordsMode, Job, MediaQueueResponse, ScanMode, ScanStatus } from '../types';

export function startScan(
  params: { directoryRuleId?: string | null; mode?: ScanMode; runAi?: boolean } = {},
) {
  return apiRequest<Job[]>('/api/scan/start', {
    method: 'POST',
    body: JSON.stringify({
      directory_rule_id: params.directoryRuleId ?? null,
      mode: params.mode ?? 'incremental',
      run_ai: params.runAi ?? true,
    }),
  });
}

export function generateAiRecords(
  params: { directoryRuleId?: string | null; mode?: GenerateAiRecordsMode } = {},
) {
  return apiRequest<Job[]>('/api/scan/generate-ai-records', {
    method: 'POST',
    body: JSON.stringify({
      directory_rule_id: params.directoryRuleId ?? null,
      mode: params.mode ?? 'missing',
    }),
  });
}

export function getScanStatus() {
  return apiRequest<ScanStatus>('/api/scan/status');
}

export function getMediaQueue() {
  return apiRequest<MediaQueueResponse>('/api/scan/media-queue');
}

export function pauseScanTasks() {
  return apiRequest<{ paused: boolean }>('/api/scan/pause', { method: 'POST' });
}

export function resumeScanTasks() {
  return apiRequest<{ paused: boolean }>('/api/scan/resume', { method: 'POST' });
}

export function listJobs() {
  return apiRequest<Job[]>('/api/jobs');
}

export function clearJobs() {
  return apiRequest<{ deleted: number }>('/api/jobs', { method: 'DELETE' });
}

export function retryJob(id: string) {
  return apiRequest<Job>(`/api/jobs/${id}/retry`, { method: 'POST' });
}
