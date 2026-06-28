export interface DirectoryRule {
  id: string;
  path: string;
  normalized_path: string;
  recursive: boolean;
  vision_model: string;
  summary_model: string;
  custom_analysis_prompt: string | null;
  background_context: string | null;
  background_context_prompt: string | null;
  video_segment_prompt: string | null;
  video_final_summary_prompt: string | null;
  video_frame_strategy: 'fixed_interval' | 'scene' | 'hybrid';
  frame_interval_seconds: number;
  max_frames_per_video: number;
  video_frame_max_width: number;
  video_frame_max_height: number | null;
  video_batch_size: number;
  video_batch_overlap: number;
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
  background_context_prompt: string | null;
  video_segment_prompt: string | null;
  video_final_summary_prompt: string | null;
  video_frame_strategy: 'fixed_interval' | 'scene' | 'hybrid';
  frame_interval_seconds: number;
  max_frames_per_video: number;
  video_frame_max_width: number;
  video_frame_max_height: number | null;
  video_batch_size: number;
  video_batch_overlap: number;
  analysis_detail: string;
  enabled: boolean;
}

export interface DirectoryRuleDefaults {
  recursive: boolean;
  vision_model: string;
  summary_model: string;
  video_frame_strategy: 'fixed_interval' | 'scene' | 'hybrid';
  frame_interval_seconds: number;
  max_frames_per_video: number;
  video_frame_max_width: number;
  video_frame_max_height: number | null;
  video_batch_size: number;
  video_batch_overlap: number;
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

export interface VideoFrameSummary {
  id: string;
  segment_id: string | null;
  frame_index: number | null;
  timestamp_seconds: number;
  frame_path: string;
  model_used: string | null;
  caption: string | null;
  objects: unknown;
  people: unknown;
  actions: unknown;
  text_visible: unknown;
  raw_json: unknown;
  created_at: string;
}

export interface VideoSegmentSummary {
  id: string;
  segment_index: number;
  start_time_seconds: number | null;
  end_time_seconds: number | null;
  frame_paths: unknown;
  current_segment_summary: string | null;
  important_observations: unknown;
  current_segment_tags: unknown;
  important_objects: unknown;
  new_objects_or_scenes: unknown;
  updated_global_summary: string | null;
  uncertain_points: unknown;
  confidence: number | null;
  raw_json: unknown;
  created_at: string;
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
  duration_seconds: number | null;
  captured_at: string | null;
  captured_at_source: string | null;
  captured_at_confidence: string | null;
  file_created_at: string | null;
  file_modified_at: string | null;
  background_context: string | null;
  status: string;
  error_message: string | null;
  thumbnail_path: string | null;
  created_at: string;
  updated_at: string;
  ai_summary: MediaSummary | null;
  video_frames?: VideoFrameSummary[];
  video_segments?: VideoSegmentSummary[];
}

export interface MediaListResponse {
  items: MediaFile[];
  total: number;
  offset: number;
  limit: number;
}

export interface MediaBackgroundContextPayload {
  background_context: string | null;
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
  paused: boolean;
  media_total: number;
  media_done: number;
  media_failed: number;
  media_missing: number;
}

export type ScanMode = 'incremental' | 'full';
export type GenerateAiRecordsMode = 'missing' | 'all_known';

export interface MediaQueueItem {
  media_id: string;
  path: string;
  thumbnail_url: string | null;
  media_status: string;
  job_id: string | null;
  job_type: string | null;
  job_status: string | null;
  job_progress_current: number;
  job_progress_total: number;
  job_payload: Record<string, unknown> | null;
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
  default_ai_search_model: string;
  max_image_long_edge: number;
  scan_worker_concurrency: number;
  metadata_worker_concurrency: number;
  vision_worker_concurrency: number;
}

export interface AnalysisPromptSettings {
  prompt: string;
}

export interface DirectoryPickerResponse {
  path: string | null;
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

export type SearchMode = 'vector' | 'ai';

export interface SearchResponse {
  query: string;
  mode: SearchMode;
  parsed_filters: {
    media_type: string;
    date_from: string | null;
    date_to: string | null;
    semantic_query: string;
  };
  results: SearchResultItem[];
  answer: string | null;
  ai_model: string | null;
  scope_total: number | null;
}

export interface TextAssistantBlock {
  type: 'text';
  text: string;
}

export interface MediaGridAssistantBlock {
  type: 'media_grid';
  title?: string | null;
  items: SearchResultItem[];
}

export type AssistantBlock = TextAssistantBlock | MediaGridAssistantBlock;

export interface SearchMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  blocks: AssistantBlock[] | null;
  tool_events: Array<Record<string, unknown>> | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface SearchConversationSummary {
  id: string;
  title: string | null;
  last_message_at: string;
  created_at: string;
  updated_at: string;
}

export interface SearchConversation extends SearchConversationSummary {
  messages: SearchMessage[];
}

export interface ChatStreamPayload {
  conversation_id?: string | null;
  message: string;
  media_type: 'image' | 'video' | 'any';
  directory_path?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  limit: number;
  candidate_k: number;
}

export interface ChatStreamEvent {
  event: string;
  data: Record<string, unknown>;
}
