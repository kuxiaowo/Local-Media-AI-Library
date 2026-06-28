import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Image as ImageIcon, Loader2, MessageSquare, Plus, Send, User, Video, Wrench } from 'lucide-react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { getSearchConversation, listSearchConversations, streamSearchChat } from '../api/search';
import { listMediaDirectories } from '../api/media';
import { API_BASE } from '../api/client';
import type {
  AssistantBlock,
  ChatStreamEvent,
  MediaGridAssistantBlock,
  SearchMessage,
  SearchResultItem,
  TextAssistantBlock,
} from '../types';

type ChatBlock = AssistantBlock & { block_id?: string };
type ChatMessage = Omit<SearchMessage, 'blocks'> & {
  blocks: ChatBlock[] | null;
  pending?: boolean;
};

const searchMediaTypeValues = new Set(['any', 'image', 'video']);

export function AgentPage() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeConversationId = searchParams.get('c');
  const initialQuery = searchParams.get('q') ?? '';
  const [input, setInput] = useState(initialQuery);
  const [mediaType, setMediaType] = useState(readSearchMediaType(searchParams.get('media_type')));
  const [directoryPath, setDirectoryPath] = useState(searchParams.get('directory_path') ?? '');
  const [dateFrom, setDateFrom] = useState(readDateParam(searchParams.get('date_from')));
  const [dateTo, setDateTo] = useState(readDateParam(searchParams.get('date_to')));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const directoriesQuery = useQuery({
    queryKey: ['media-directories'],
    queryFn: listMediaDirectories,
  });
  const conversationsQuery = useQuery({
    queryKey: ['search-conversations'],
    queryFn: listSearchConversations,
  });
  const conversationQuery = useQuery({
    queryKey: ['search-conversation', activeConversationId],
    queryFn: () => getSearchConversation(activeConversationId!),
    enabled: Boolean(activeConversationId),
  });

  useEffect(() => {
    if (!isStreaming) {
      setMessages((conversationQuery.data?.messages ?? []) as ChatMessage[]);
    }
  }, [conversationQuery.data, isStreaming]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, [messages]);

  const detailReturnState = useMemo(
    () => ({
      returnTo: `${location.pathname}${location.search}`,
      returnLabel: '返回 Agent',
    }),
    [location.pathname, location.search],
  );

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = input.trim();
    if (!message || isStreaming) {
      return;
    }

    setInput('');
    setStreamError(null);
    setIsStreaming(true);
    try {
      await streamSearchChat(
        {
          conversation_id: activeConversationId,
          message,
          media_type: mediaType,
          directory_path: directoryPath || null,
          date_from: dateFrom ? `${dateFrom}T00:00:00` : null,
          date_to: dateTo ? `${dateTo}T23:59:59` : null,
          limit: 30,
          candidate_k: 200,
        },
        handleStreamEvent,
      );
      await queryClient.invalidateQueries({ queryKey: ['search-conversations'] });
      if (activeConversationId) {
        await queryClient.invalidateQueries({ queryKey: ['search-conversation', activeConversationId] });
      }
    } catch (error) {
      const messageText = error instanceof Error ? error.message : String(error);
      setStreamError(messageText);
      setMessages((current) => [
        ...removePendingAssistant(current),
        makeLocalAssistantMessage([{ type: 'text', text: `检索失败：${messageText}` }], messageText),
      ]);
    } finally {
      setIsStreaming(false);
    }
  }

  function handleStreamEvent(event: ChatStreamEvent) {
    if (event.event === 'conversation') {
      const conversationId = stringValue(event.data.conversation_id);
      if (conversationId && conversationId !== activeConversationId) {
        setSearchParams((current) => {
          const next = new URLSearchParams(current);
          next.set('c', conversationId);
          next.delete('q');
          return next;
        });
      }
      return;
    }

    if (event.event === 'user_message') {
      const message = event.data.message as SearchMessage | undefined;
      if (message) {
        setMessages((current) => [...removePendingAssistant(current), message as ChatMessage]);
      }
      return;
    }

    if (event.event === 'tool_call' || event.event === 'tool_result') {
      setMessages((current) => appendToolEvent(ensurePendingAssistant(current), event));
      return;
    }

    if (event.event === 'text_start') {
      const blockId = stringValue(event.data.block_id);
      setMessages((current) => appendTextBlock(ensurePendingAssistant(current), blockId));
      return;
    }

    if (event.event === 'text_delta') {
      const blockId = stringValue(event.data.block_id);
      const text = stringValue(event.data.text);
      setMessages((current) => appendTextDelta(ensurePendingAssistant(current), blockId, text));
      return;
    }

    if (event.event === 'media_block') {
      const block = mediaBlockFromEvent(event.data);
      if (block) {
        setMessages((current) => appendMediaBlock(ensurePendingAssistant(current), block));
      }
      return;
    }

    if (event.event === 'done') {
      const message = event.data.message as SearchMessage | undefined;
      if (message) {
        setMessages((current) => [...removePendingAssistant(current), message as ChatMessage]);
      }
      return;
    }

    if (event.event === 'error') {
      const messageText = stringValue(event.data.message) || '检索失败';
      setStreamError(messageText);
      setMessages((current) => [
        ...removePendingAssistant(current),
        makeLocalAssistantMessage([{ type: 'text', text: `检索失败：${messageText}` }], messageText),
      ]);
    }
  }

  function newConversation() {
    setSearchParams(new URLSearchParams());
    setMessages([]);
    setInput('');
    setStreamError(null);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
      <aside className="panel h-fit p-3">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h1 className="text-base font-semibold">Agent</h1>
            <p className="text-xs text-slate-500">对话式媒体助手</p>
          </div>
          <button className="btn btn-secondary h-9 px-2" type="button" onClick={newConversation} title="新对话">
            <Plus className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-1">
          {(conversationsQuery.data ?? []).map((conversation) => (
            <button
              key={conversation.id}
              className={[
                'w-full rounded-md px-2 py-2 text-left text-sm transition',
                conversation.id === activeConversationId
                  ? 'bg-accent text-white'
                  : 'text-slate-700 hover:bg-slate-100',
              ].join(' ')}
              type="button"
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.set('c', conversation.id);
                next.delete('q');
                setSearchParams(next);
              }}
            >
              <div className="truncate font-medium">{conversation.title || '未命名对话'}</div>
              <div className={conversation.id === activeConversationId ? 'text-xs text-white/75' : 'text-xs text-slate-400'}>
                {formatDateTime(conversation.last_message_at)}
              </div>
            </button>
          ))}
          {conversationsQuery.data?.length === 0 && (
            <div className="rounded-md border border-dashed border-line p-3 text-xs text-slate-500">暂无历史对话</div>
          )}
        </div>
      </aside>

      <section className="min-w-0 space-y-3">
        <div className="panel grid gap-3 p-3 md:grid-cols-2 xl:grid-cols-4">
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">媒体类型</span>
            <select className="control w-full" value={mediaType} onChange={(event) => setMediaType(readSearchMediaType(event.target.value))}>
              <option value="any">全部</option>
              <option value="image">图片</option>
              <option value="video">视频</option>
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">目录</span>
            <select className="control w-full" value={directoryPath} onChange={(event) => setDirectoryPath(event.target.value)}>
              <option value="">全部目录</option>
              {(directoriesQuery.data ?? []).map((directory) => (
                <option key={directory.path} value={directory.path}>
                  {directory.name} · {directory.display_path}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">开始时间</span>
            <input className="control w-full" type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          </label>
          <label>
            <span className="mb-1 block text-xs font-medium text-slate-500">结束时间</span>
            <input className="control w-full" type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
        </div>

        <div className="panel flex min-h-[560px] flex-col overflow-hidden">
          <div className="border-b border-line px-4 py-3">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <MessageSquare className="h-4 w-4 text-accent" />
              {conversationQuery.data?.title || '新对话'}
              {isStreaming && <Loader2 className="h-4 w-4 animate-spin text-slate-400" />}
            </div>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {messages.length === 0 && !conversationQuery.isFetching && (
              <div className="mx-auto max-w-2xl rounded-md border border-dashed border-line p-6 text-center text-sm text-slate-500">
                输入自然语言需求。Agent 会先理解意图，再决定查看目录、全局检索或分批阅读摘要。
              </div>
            )}
            {messages.map((message) => (
              <ChatMessageView
                key={message.id}
                message={message}
                detailReturnState={detailReturnState}
              />
            ))}
            {streamError && <div className="text-sm text-red-600">{streamError}</div>}
            <div ref={bottomRef} />
          </div>

          <form className="border-t border-line p-3" onSubmit={submit}>
            <div className="flex gap-2">
              <textarea
                className="control min-h-12 flex-1 resize-none"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder="例如：找几张适合做封面的照片；总结这个目录里重复出现的场景；继续缩小到去年夏天"
              />
              <button className="btn btn-primary h-12 px-4" type="submit" disabled={isStreaming || !input.trim()}>
                {isStreaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                发送
              </button>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
}

function ChatMessageView({
  message,
  detailReturnState,
}: {
  message: ChatMessage;
  detailReturnState: { returnTo: string; returnLabel: string };
}) {
  const isUser = message.role === 'user';
  const blocks = message.blocks ?? [{ type: 'text', text: message.content }];
  return (
    <div className={['flex gap-3', isUser ? 'justify-end' : 'justify-start'].join(' ')}>
      {!isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-accent text-white">
          <Bot className="h-4 w-4" />
        </div>
      )}
      <div className={isUser ? 'max-w-2xl rounded-md bg-accent px-3 py-2 text-sm text-white' : 'min-w-0 flex-1 space-y-3'}>
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <>
            {message.tool_events && message.tool_events.length > 0 && <ToolEvents events={message.tool_events} />}
            {blocks.map((block, index) => {
              if (block.type === 'media_grid') {
                return (
                  <MediaBlock
                    block={block}
                    detailReturnState={detailReturnState}
                    key={`${message.id}-media-${index}`}
                  />
                );
              }
              return (
                <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700" key={`${message.id}-text-${index}`}>
                  {(block as TextAssistantBlock).text}
                </p>
              );
            })}
          </>
        )}
      </div>
      {isUser && (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-slate-200 text-slate-700">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  );
}

function ToolEvents({ events }: { events: Array<Record<string, unknown>> }) {
  return (
    <div className="space-y-1 rounded-md border border-line bg-slate-50 p-2">
      {events.map((event, index) => (
        <div className="flex items-start gap-2 text-xs text-slate-500" key={index}>
          <Wrench className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {stringValue(event.tool) || '工具'}：{stringValue(event.reason) || stringValue(event.summary) || stringValue(event.event)}
          </span>
        </div>
      ))}
    </div>
  );
}

function MediaBlock({
  block,
  detailReturnState,
}: {
  block: MediaGridAssistantBlock;
  detailReturnState: { returnTo: string; returnLabel: string };
}) {
  return (
    <div className="space-y-2">
      {block.title && <h2 className="text-sm font-semibold text-ink">{block.title}</h2>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {block.items.map((item) => (
          <Link
            to={`/media/${item.media_id}`}
            state={detailReturnState}
            key={item.media_id}
            className="rounded-md border border-line bg-white p-2 transition hover:border-signal"
          >
            <div className="relative">
              <img
                src={`${API_BASE}${item.thumbnail_url}`}
                alt={item.title ?? item.path}
                className="aspect-[4/3] w-full rounded-md bg-slate-100 object-cover"
                loading="lazy"
              />
              <div className="absolute left-2 top-2 rounded-md bg-black/60 px-1.5 py-0.5 text-xs text-white">
                {item.media_type === 'video' ? <Video className="h-3.5 w-3.5" /> : <ImageIcon className="h-3.5 w-3.5" />}
              </div>
            </div>
            <div className="mt-2 min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="truncate text-sm font-semibold text-ink">{item.title ?? item.path}</h3>
                <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">{item.score.toFixed(3)}</span>
              </div>
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-600">{item.short_summary}</p>
              <p className="mt-1 line-clamp-2 text-xs leading-5 text-accent">{item.match_reason}</p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function ensurePendingAssistant(messages: ChatMessage[]) {
  if (messages.some((message) => message.pending)) {
    return messages;
  }
  return [...messages, makeLocalAssistantMessage([], '', true)];
}

function removePendingAssistant(messages: ChatMessage[]) {
  return messages.filter((message) => !message.pending);
}

function appendToolEvent(messages: ChatMessage[], event: ChatStreamEvent) {
  return messages.map((message) =>
    message.pending
      ? {
          ...message,
          tool_events: [...(message.tool_events ?? []), { event: event.event, ...event.data }],
        }
      : message,
  );
}

function appendTextBlock(messages: ChatMessage[], blockId: string) {
  return messages.map((message) => {
    if (!message.pending) {
      return message;
    }
    const blocks = [...(message.blocks ?? [])];
    if (!blocks.some((block) => block.block_id === blockId)) {
      blocks.push({ type: 'text', text: '', block_id: blockId });
    }
    return { ...message, blocks };
  });
}

function appendTextDelta(messages: ChatMessage[], blockId: string, text: string) {
  return messages.map((message) => {
    if (!message.pending) {
      return message;
    }
    const blocks = [...(message.blocks ?? [])];
    const index = blocks.findIndex((block) => block.block_id === blockId);
    if (index === -1) {
      blocks.push({ type: 'text', text, block_id: blockId });
    } else {
      const block = blocks[index] as TextAssistantBlock & { block_id?: string };
      blocks[index] = { ...block, text: `${block.text}${text}` };
    }
    return { ...message, blocks, content: `${message.content}${text}` };
  });
}

function appendMediaBlock(messages: ChatMessage[], block: MediaGridAssistantBlock & { block_id?: string }) {
  return messages.map((message) => {
    if (!message.pending) {
      return message;
    }
    return { ...message, blocks: [...(message.blocks ?? []), block] };
  });
}

function mediaBlockFromEvent(data: Record<string, unknown>): (MediaGridAssistantBlock & { block_id?: string }) | null {
  if (!Array.isArray(data.items)) {
    return null;
  }
  return {
    type: 'media_grid',
    block_id: stringValue(data.block_id),
    title: stringValue(data.title) || null,
    items: data.items as SearchResultItem[],
  };
}

function makeLocalAssistantMessage(blocks: ChatBlock[], content: string, pending = false): ChatMessage {
  const now = new Date().toISOString();
  return {
    id: pending ? 'streaming-assistant' : `local-${now}`,
    conversation_id: '',
    role: 'assistant',
    content,
    blocks,
    tool_events: [],
    error_message: null,
    created_at: now,
    updated_at: now,
    pending,
  };
}

function readSearchMediaType(value: string | null) {
  if (!value || !searchMediaTypeValues.has(value)) {
    return 'any' as const;
  }
  return value as 'any' | 'image' | 'video';
}

function readDateParam(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return '';
  }
  return value;
}

function stringValue(value: unknown) {
  return typeof value === 'string' ? value : value == null ? '' : String(value);
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}
