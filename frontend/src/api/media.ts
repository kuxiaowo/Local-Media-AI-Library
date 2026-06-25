import { apiRequest } from './client';
import type { MediaDirectory, MediaFile, MediaListResponse } from '../types';

export function listMedia(params: {
  offset: number;
  limit: number;
  mediaType: string;
  status: string;
  directoryPath?: string | null;
}) {
  const query = new URLSearchParams({
    offset: String(params.offset),
    limit: String(params.limit),
    media_type: params.mediaType,
    status: params.status,
  });
  if (params.directoryPath) {
    query.set('directory_path', params.directoryPath);
  }
  return apiRequest<MediaListResponse>(`/api/media?${query}`);
}

export function listMediaDirectories() {
  return apiRequest<MediaDirectory[]>('/api/media/directories');
}

export function getMedia(id: string) {
  return apiRequest<MediaFile>(`/api/media/${id}`);
}

export function reanalyzeMedia(id: string) {
  return apiRequest(`/api/media/${id}/reanalyze`, { method: 'POST' });
}

export function openMediaLocation(id: string) {
  return apiRequest(`/api/media/${id}/open-location`, { method: 'POST' });
}
