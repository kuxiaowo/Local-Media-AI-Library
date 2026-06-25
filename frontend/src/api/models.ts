import { apiRequest } from './client';
import type { OllamaModels, OllamaStatus } from '../types';

export function getOllamaStatus() {
  return apiRequest<OllamaStatus>('/api/models/status');
}

export function getOllamaModels() {
  return apiRequest<OllamaModels>('/api/models/ollama');
}
