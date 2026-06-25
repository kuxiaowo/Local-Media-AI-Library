import { apiRequest } from './client';
import type { Job, MediaQueueResponse, ScanMode, ScanStatus } from '../types';

export function startScan(params: { directoryRuleId?: string | null; mode?: ScanMode } = {}) {
  return apiRequest<Job[]>('/api/scan/start', {
    method: 'POST',
    body: JSON.stringify({
      directory_rule_id: params.directoryRuleId ?? null,
      mode: params.mode ?? 'incremental',
    }),
  });
}

export function getScanStatus() {
  return apiRequest<ScanStatus>('/api/scan/status');
}

export function getMediaQueue() {
  return apiRequest<MediaQueueResponse>('/api/scan/media-queue');
}

export function listJobs() {
  return apiRequest<Job[]>('/api/jobs');
}

export function retryJob(id: string) {
  return apiRequest<Job>(`/api/jobs/${id}/retry`, { method: 'POST' });
}
