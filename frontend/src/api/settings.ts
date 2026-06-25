import { apiRequest } from './client';
import type { AnalysisPromptSettings, Job, RuntimeSettings } from '../types';

export function getRuntimeSettings() {
  return apiRequest<RuntimeSettings>('/api/settings/runtime');
}

export function updateRuntimeSettings(payload: RuntimeSettings) {
  return apiRequest<RuntimeSettings>('/api/settings/runtime', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function getDefaultAnalysisPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-analysis-prompt');
}

export function updateDefaultAnalysisPrompt(payload: AnalysisPromptSettings) {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-analysis-prompt', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function resetDefaultAnalysisPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-analysis-prompt/reset', {
    method: 'POST',
  });
}

export function cleanupStaleMedia() {
  return apiRequest<Job>('/api/settings/cleanup-stale-media', { method: 'POST' });
}
