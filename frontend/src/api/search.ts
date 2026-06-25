import { apiRequest } from './client';
import type { SearchResponse } from '../types';

export function searchMedia(payload: {
  query: string;
  media_type: string;
  limit: number;
  candidate_k: number;
}) {
  return apiRequest<SearchResponse>('/api/search', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
