export interface DirectoryRule {
  id: string;
  path: string;
  normalized_path: string;
  recursive: boolean;
  vision_model: string;
  summary_model: string;
  custom_analysis_prompt: string | null;
  background_context: string | null;
  video_frame_strategy: 'fixed_interval' | 'scene' | 'hybrid';
  frame_interval_seconds: number;
  max_frames_per_video: number;
  analysis_detail: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface DirectoryRulePayload {
  path: string;
  recursive: boolean;
  vision_model: string;
  summary_model: string;
  custom_analysis_prompt: string | null;
  background_context: string | null;
  video_frame_strategy: 'fixed_interval' | 'scene' | 'hybrid';
  frame_interval_seconds: number;
  max_frames_per_video: number;
  analysis_detail: string;
  enabled: boolean;
}

export interface MediaSummary {
  model_used: string;
  title: string | null;
  short_summary: string | null;
  detailed_summary: string | null;
  scene: string | null;
  objects: unknown;
  people: unknown;
  actions: unknown;
  text_visible: unknown;
  location_guess: string | null;
  time_clues: string | null;
  mood: string | null;
  search_keywords: unknown;
  searchable_text: string;
  raw_json: unknown;
  confidence: string | null;
  created_at: string;
  updated_at: string;
}

export interface MediaFile {
  id: string;
  path: string;
  normalized_path: string;
  root_path: string | null;
  parent_dir: string | null;
  media_type: string;
  mime_type: string | null;
  file_size: number | null;
  file_hash: string | null;
  width: number | null;
  height: number | null;
  captured_at: string | null;
  captured_at_source: string | null;
  captured_at_confidence: string | null;
  file_created_at: string | null;
  file_modified_at: string | null;
  status: string;
  error_message: string | null;
  thumbnail_path: string | null;
  created_at: string;
  updated_at: string;
  ai_summary: MediaSummary | null;
}

export interface MediaListResponse {
  items: MediaFile[];
  total: number;
  offset: number;
  limit: number;
}

export interface MediaDirectory {
  path: string;
  display_path: string;
  name: string;
  direct_media_count: number;
}

export interface Job {
  id: string;
  job_type: string;
  status: string;
  target_id: string | null;
  target_path: string | null;
  progress_current: number;
  progress_total: number;
  error_message: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ScanStatus {
  queued: number;
  running: number;
  failed: number;
  completed: number;
  media_total: number;
  media_done: number;
  media_failed: number;
  media_missing: number;
}

export type ScanMode = 'incremental' | 'full';

export interface MediaQueueItem {
  media_id: string;
  path: string;
  thumbnail_url: string | null;
  media_status: string;
  job_id: string | null;
  job_type: string | null;
  job_status: string | null;
  error_message: string | null;
  updated_at: string;
  job_created_at: string | null;
  job_started_at: string | null;
}

export interface MediaQueueResponse {
  items: MediaQueueItem[];
  total: number;
}

export interface OllamaStatus {
  ok: boolean;
  base_url: string;
  error: string | null;
}

export interface OllamaModels {
  models: string[];
}

export interface RuntimeSettings {
  default_embedding_model: string;
  scan_worker_concurrency: number;
  metadata_worker_concurrency: number;
  vision_worker_concurrency: number;
  embedding_worker_concurrency: number;
}

export interface AnalysisPromptSettings {
  prompt: string;
}

export interface SearchResultItem {
  media_id: string;
  path: string;
  thumbnail_url: string;
  media_type: string;
  captured_at: string | null;
  title: string | null;
  short_summary: string | null;
  match_reason: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  parsed_filters: {
    media_type: string;
    date_from: string | null;
    date_to: string | null;
    semantic_query: string;
  };
  results: SearchResultItem[];
}
