import { apiRequest } from './client';
import type { SearchMode, SearchResponse } from '../types';

export function searchMedia(payload: {
  query: string;
  mode: SearchMode;
  media_type: string;
  directory_path?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  limit: number;
  candidate_k: number;
}) {
  return apiRequest<SearchResponse>('/api/search', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
