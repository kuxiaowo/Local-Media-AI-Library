import { API_BASE, apiRequest } from './client';
import type {
  ChatStreamEvent,
  ChatStreamPayload,
  SearchConversation,
  SearchConversationSummary,
  SearchMode,
  SearchResponse,
} from '../types';

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

export function listSearchConversations() {
  return apiRequest<SearchConversationSummary[]>('/api/search/conversations');
}

export function getSearchConversation(conversationId: string) {
  return apiRequest<SearchConversation>(`/api/search/conversations/${conversationId}`);
}

export async function streamSearchChat(
  payload: ChatStreamPayload,
  onEvent: (event: ChatStreamEvent) => void,
) {
  const response = await fetch(`${API_BASE}/api/search/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(response.statusText || '聊天请求失败');
  }
  if (!response.body) {
    throw new Error('浏览器不支持流式响应');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      const parsed = parseSseFrame(frame);
      if (parsed) {
        onEvent(parsed);
      }
    }
  }

  buffer += decoder.decode();
  const trailing = parseSseFrame(buffer);
  if (trailing) {
    onEvent(trailing);
  }
}

function parseSseFrame(frame: string): ChatStreamEvent | null {
  if (!frame.trim()) {
    return null;
  }

  let event = 'message';
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  try {
    return {
      event,
      data: JSON.parse(dataLines.join('\n')) as Record<string, unknown>,
    };
  } catch {
    return {
      event,
      data: { raw: dataLines.join('\n') },
    };
  }
}
