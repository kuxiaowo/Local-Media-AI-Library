import { apiRequest } from './client';
import type { AnalysisPromptSettings, DirectoryPickerResponse, Job, RuntimeSettings } from '../types';

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

export function getDefaultAnalysisSystemPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-analysis-system-prompt');
}

export function updateDefaultAnalysisSystemPrompt(payload: AnalysisPromptSettings) {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-analysis-system-prompt', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function resetDefaultAnalysisSystemPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-analysis-system-prompt/reset', {
    method: 'POST',
  });
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

export function getDefaultBackgroundContextPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-background-context-prompt');
}

export function updateDefaultBackgroundContextPrompt(payload: AnalysisPromptSettings) {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-background-context-prompt', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function resetDefaultBackgroundContextPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-background-context-prompt/reset', {
    method: 'POST',
  });
}

export function getDefaultVideoSegmentPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-segment-prompt');
}

export function getDefaultVideoSegmentSystemPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-segment-system-prompt');
}

export function updateDefaultVideoSegmentSystemPrompt(payload: AnalysisPromptSettings) {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-segment-system-prompt', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function resetDefaultVideoSegmentSystemPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-segment-system-prompt/reset', {
    method: 'POST',
  });
}

export function updateDefaultVideoSegmentPrompt(payload: AnalysisPromptSettings) {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-segment-prompt', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function resetDefaultVideoSegmentPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-segment-prompt/reset', {
    method: 'POST',
  });
}

export function getDefaultVideoFinalSummaryPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-final-summary-prompt');
}

export function getDefaultVideoFinalSummarySystemPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-final-summary-system-prompt');
}

export function updateDefaultVideoFinalSummarySystemPrompt(payload: AnalysisPromptSettings) {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-final-summary-system-prompt', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function resetDefaultVideoFinalSummarySystemPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-final-summary-system-prompt/reset', {
    method: 'POST',
  });
}

export function updateDefaultVideoFinalSummaryPrompt(payload: AnalysisPromptSettings) {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-final-summary-prompt', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function resetDefaultVideoFinalSummaryPrompt() {
  return apiRequest<AnalysisPromptSettings>('/api/settings/default-video-final-summary-prompt/reset', {
    method: 'POST',
  });
}

export function cleanupStaleMedia() {
  return apiRequest<Job>('/api/settings/cleanup-stale-media', { method: 'POST' });
}

export function browseDirectory(initialPath?: string | null) {
  return apiRequest<DirectoryPickerResponse>('/api/settings/browse-directory', {
    method: 'POST',
    body: JSON.stringify({ initial_path: initialPath || null }),
  });
}
