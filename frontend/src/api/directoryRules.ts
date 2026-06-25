import { apiRequest } from './client';
import type { DirectoryRule, DirectoryRulePayload } from '../types';

export function listDirectoryRules() {
  return apiRequest<DirectoryRule[]>('/api/directory-rules');
}

export function createDirectoryRule(payload: DirectoryRulePayload) {
  return apiRequest<DirectoryRule>('/api/directory-rules', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function updateDirectoryRule(id: string, payload: Partial<DirectoryRulePayload>) {
  return apiRequest<DirectoryRule>(`/api/directory-rules/${id}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function deleteDirectoryRule(id: string) {
  return apiRequest<void>(`/api/directory-rules/${id}`, {
    method: 'DELETE',
  });
}
