import { FormEvent, ReactNode, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Braces, ChevronDown, FileJson, ImageIcon, Lock, Pencil, RefreshCw, Save, Trash2, Video } from 'lucide-react';
import { getOllamaModels, getOllamaStatus } from '../api/models';
import {
  cleanupStaleMedia,
  getDefaultAnalysisPrompt,
  getDefaultAnalysisSystemPrompt,
  getDefaultBackgroundContextPrompt,
  getDefaultVideoFinalSummarySystemPrompt,
  getDefaultVideoFinalSummaryPrompt,
  getDefaultVideoSegmentSystemPrompt,
  getDefaultVideoSegmentPrompt,
  getRuntimeSettings,
  resetDefaultAnalysisPrompt,
  resetDefaultAnalysisSystemPrompt,
  resetDefaultBackgroundContextPrompt,
  resetDefaultVideoFinalSummarySystemPrompt,
  resetDefaultVideoFinalSummaryPrompt,
  resetDefaultVideoSegmentSystemPrompt,
  resetDefaultVideoSegmentPrompt,
  updateDefaultAnalysisPrompt,
  updateDefaultAnalysisSystemPrompt,
  updateDefaultBackgroundContextPrompt,
  updateDefaultVideoFinalSummarySystemPrompt,
  updateDefaultVideoFinalSummaryPrompt,
  updateDefaultVideoSegmentSystemPrompt,
  updateDefaultVideoSegmentPrompt,
  updateRuntimeSettings,
} from '../api/settings';
import type { RuntimeSettings } from '../types';

const emptyRuntimeSettings: RuntimeSettings = {
  default_embedding_model: 'nomic-embed-text',
  max_image_long_edge: 1280,
  scan_worker_concurrency: 1,
  metadata_worker_concurrency: 6,
  vision_worker_concurrency: 1,
  embedding_worker_concurrency: 2,
};

type ReadOnlyPromptBlock = {
  title: string;
  description: string;
  kind: 'fixed' | 'dynamic';
  content: string;
};

const imagePromptBlocksBeforeBackground: ReadOnlyPromptBlock[] = [
  {
    title: '动态追加：目录背景补充',
    description: '只有目录规则里填写了背景补充时才会追加。背景内容来自目录设置，不是系统设置页的全局文本。',
    kind: 'dynamic',
    content: `目录背景补充：
<目录规则中的背景补充>`,
  },
  {
    title: '动态追加：媒体背景使用规则',
    description: '随目录背景补充一起追加。优先使用目录规则里的覆盖值；没有覆盖时使用“媒体背景使用规则”的全局默认值。',
    kind: 'dynamic',
    content: `背景使用规则：
<目录覆盖值或全局媒体背景使用规则>`,
  },
];

const imagePromptBlocksAfterBackground: ReadOnlyPromptBlock[] = [
  {
    title: '固定追加：输出要求',
    description: '代码在 user prompt 末尾固定追加，避免目录自定义提示词漏掉 JSON 和中文输出约束。',
    kind: 'fixed',
    content: '固定输出要求：必须返回符合指定 schema 的有效 JSON，所有用户可见文本必须使用简体中文。',
  },
  {
    title: '动态输入：当前图片',
    description: '图片不会塞进文本 prompt，而是通过 Ollama /api/generate 的 images 数组随同请求发送。',
    kind: 'dynamic',
    content: `images: [
  "<当前图片按 max_image_long_edge 缩放后的 base64>"
]`,
  },
  {
    title: '固定约束：JSON Schema',
    description: '通过 Ollama format 字段传入。字段名必须保持英文，后端按这些字段写入 media_ai_summaries。',
    kind: 'fixed',
    content:
      'format: IMAGE_ANALYSIS_SCHEMA，必须返回 title, short_summary, detailed_summary, scene, objects, people, actions, text_visible, location_guess, time_clues, mood, search_keywords, confidence.',
  },
];

const segmentPromptBlocks: ReadOnlyPromptBlock[] = [
  {
    title: '固定追加：批次说明与上下文定义',
    description: '代码在可编辑 user prompt 后面固定追加，说明当前请求正在处理一个按时间排序的关键帧批次，并解释上一轮上下文的含义。',
    kind: 'fixed',
    content: `你正在分析视频中的一个关键帧批次。帧已经按时间顺序排列。

字段含义：
- previous_global_summary：从视频开头到上一批关键帧为止，已经形成的滚动整体摘要；第一批为空。
- previous_timeline：从视频开头到上一批关键帧为止，已经形成的滚动时间线；第一批为空数组。
- current_frame_info：当前批次每张关键帧的图片顺序、全局帧序号和时间戳。

使用规则：
- previous_global_summary 和 previous_timeline 只用于理解当前片段的前后关系，不能当作当前画面的新证据。
- current_segment_summary 必须以当前批次图片为事实来源，只描述当前批次能支持的内容。
- updated_global_summary 和 updated_timeline 是把上一轮上下文与当前片段合并后的新滚动状态，会提供给下一批继续使用。

下面的 JSON 包含此前上下文，以及当前批次每帧的序号和时间戳元数据。`,
  },
  {
    title: '动态注入：上一批上下文与当前帧信息',
    description: '每个批次运行时生成。第一批 previous_global_summary 为空，后续批次使用上一批的 updated_global_summary。',
    kind: 'dynamic',
    content: `{
  "previous_global_summary": "<上一批 updated_global_summary；第一批为空字符串>",
  "previous_timeline": [
    {
      "start_time": "00:00:00",
      "end_time": "00:00:25",
      "summary": "<上一批已经确认的时间线片段>"
    }
  ],
  "current_frame_info": [
    {
      "image_order": 1,
      "frame_index": 7,
      "timestamp_seconds": 30.0,
      "timestamp": "00:00:30"
    }
  ]
}`,
  },
  {
    title: '固定追加：当前片段和滚动摘要约束',
    description: '保证模型不要把上一批摘要当成当前画面事实，同时要求继续更新全局摘要和时间线。',
    kind: 'fixed',
    content:
      '只返回严格 JSON。current_segment_summary 必须只描述当前批次关键帧中可见、可判断，或由当前批次连续性支持的内容。不要把 previous_global_summary / previous_timeline 中出现但当前批次没有画面支持的细节写进 current_segment_summary。updated_global_summary 必须描述从视频开头到当前批次为止的整体内容。updated_timeline 必须保留此前时间线，并加入当前片段。',
  },
  {
    title: '固定追加：必须返回字段',
    description: '字段名必须保持英文，后端按这些字段解析并写入 video_segment_summaries。',
    kind: 'fixed',
    content:
      '必须返回字段：current_segment_summary, current_segment_tags, important_objects, ocr_text, new_objects_or_scenes, updated_global_summary, updated_timeline, confidence.',
  },
  {
    title: '动态追加：目录背景补充',
    description: '只有目录规则里填写了背景补充时才会追加；背景使用规则来自目录覆盖或“媒体背景使用规则”的全局默认值。',
    kind: 'dynamic',
    content: `目录背景补充，仅作理解用途和关键词参考；如果与画面冲突，以画面为准：
<目录背景补充>

背景使用规则：
<目录覆盖值或全局媒体背景使用规则>`,
  },
  {
    title: '动态输入：当前批次图片',
    description: '关键帧图片不会塞进文本 prompt，而是通过 Ollama /api/chat 的 images 数组随同一次请求发送。',
    kind: 'dynamic',
    content: `images: [
  "<当前批次第 1 张关键帧 base64>",
  "<当前批次第 2 张关键帧 base64>",
  "..."
]`,
  },
];

const finalSummaryPromptBlocks: ReadOnlyPromptBlock[] = [
  {
    title: '固定追加：分段结果说明',
    description: '代码在可编辑 user prompt 后面固定追加，明确最终总结的主要依据是 segments。',
    kind: 'fixed',
    content: `下面的 JSON 包含分段级视频识别结果。请以 segments 作为主要信息来源。

rolling_global_summary 是最后一批结束时的滚动整体摘要，rolling_timeline 是最后一批结束时的滚动时间线。它们只能作为快速理解整体脉络的参考，不能替代 segments 中每个片段的具体证据。`,
  },
  {
    title: '动态注入：全部 segment 结果',
    description: '所有分段识别完成后生成。这里不会再包含关键帧图片，只包含每段保存下来的文本和结构化结果。',
    kind: 'dynamic',
    content: `{
  "duration_seconds": 123.45,
  "rolling_global_summary": "<最后一批 updated_global_summary>",
  "rolling_timeline": [
    {
      "start_time": "00:00:00",
      "end_time": "00:00:25",
      "summary": "<滚动时间线片段>"
    }
  ],
  "segments": [
    {
      "segment_index": 1,
      "start_time_seconds": 0.0,
      "end_time_seconds": 25.0,
      "current_segment_summary": "<第 1 批片段摘要>",
      "current_segment_tags": ["<标签>"],
      "important_objects": ["<重要物体>"],
      "ocr_text": ["<画面可见文字>"],
      "new_objects_or_scenes": ["<新场景或新物体>"],
      "confidence": 0.82
    }
  ]
}`,
  },
  {
    title: '固定追加：最终摘要约束',
    description: '保证最终输出是可解析 JSON。',
    kind: 'fixed',
    content: '只返回严格 JSON。timeline 必须覆盖视频中的重要片段。',
  },
  {
    title: '固定追加：必须返回字段',
    description: '字段名必须保持英文，后端按这些字段写入 media_ai_summaries 并生成搜索文本。',
    kind: 'fixed',
    content:
      '必须返回字段：title, short_summary, detailed_summary, timeline, scene, objects, actions, text_visible, search_keywords, confidence.',
  },
  {
    title: '动态追加：目录背景补充',
    description: '只有目录规则里填写了背景补充时才会追加；背景使用规则来自目录覆盖或“媒体背景使用规则”的全局默认值。',
    kind: 'dynamic',
    content: `目录背景补充，仅作理解用途和关键词参考；如果与分段内容冲突，以分段内容为准：
<目录背景补充>

背景使用规则：
<目录覆盖值或全局媒体背景使用规则>`,
  },
];

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [runtimeForm, setRuntimeForm] = useState<RuntimeSettings>(emptyRuntimeSettings);
  const [defaultSystemPromptForm, setDefaultSystemPromptForm] = useState('');
  const [defaultPromptForm, setDefaultPromptForm] = useState('');
  const [defaultBackgroundPromptForm, setDefaultBackgroundPromptForm] = useState('');
  const [defaultVideoSegmentSystemPromptForm, setDefaultVideoSegmentSystemPromptForm] = useState('');
  const [defaultVideoSegmentPromptForm, setDefaultVideoSegmentPromptForm] = useState('');
  const [defaultVideoFinalSystemPromptForm, setDefaultVideoFinalSystemPromptForm] = useState('');
  const [defaultVideoFinalPromptForm, setDefaultVideoFinalPromptForm] = useState('');
  const [imagePromptsOpen, setImagePromptsOpen] = useState(true);
  const [videoPromptsOpen, setVideoPromptsOpen] = useState(true);
  const statusQuery = useQuery({ queryKey: ['ollama-status'], queryFn: getOllamaStatus });
  const modelsQuery = useQuery({ queryKey: ['ollama-models'], queryFn: getOllamaModels });
  const runtimeQuery = useQuery({ queryKey: ['runtime-settings'], queryFn: getRuntimeSettings });
  const modelOptions = modelsQuery.data?.models ?? [];
  const defaultPromptQuery = useQuery({
    queryKey: ['default-analysis-prompt'],
    queryFn: getDefaultAnalysisPrompt,
  });
  const defaultSystemPromptQuery = useQuery({
    queryKey: ['default-analysis-system-prompt'],
    queryFn: getDefaultAnalysisSystemPrompt,
  });
  const defaultBackgroundPromptQuery = useQuery({
    queryKey: ['default-background-context-prompt'],
    queryFn: getDefaultBackgroundContextPrompt,
  });
  const defaultVideoSegmentSystemPromptQuery = useQuery({
    queryKey: ['default-video-segment-system-prompt'],
    queryFn: getDefaultVideoSegmentSystemPrompt,
  });
  const defaultVideoSegmentPromptQuery = useQuery({
    queryKey: ['default-video-segment-prompt'],
    queryFn: getDefaultVideoSegmentPrompt,
  });
  const defaultVideoFinalSystemPromptQuery = useQuery({
    queryKey: ['default-video-final-summary-system-prompt'],
    queryFn: getDefaultVideoFinalSummarySystemPrompt,
  });
  const defaultVideoFinalPromptQuery = useQuery({
    queryKey: ['default-video-final-summary-prompt'],
    queryFn: getDefaultVideoFinalSummaryPrompt,
  });
  const saveRuntimeMutation = useMutation({
    mutationFn: updateRuntimeSettings,
    onSuccess: (data) => {
      setRuntimeForm(data);
      queryClient.invalidateQueries({ queryKey: ['runtime-settings'] });
    },
  });
  const saveDefaultPromptMutation = useMutation({
    mutationFn: updateDefaultAnalysisPrompt,
    onSuccess: (data) => {
      setDefaultPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-analysis-prompt'] });
    },
  });
  const resetDefaultPromptMutation = useMutation({
    mutationFn: resetDefaultAnalysisPrompt,
    onSuccess: (data) => {
      setDefaultPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-analysis-prompt'] });
    },
  });
  const saveDefaultSystemPromptMutation = useMutation({
    mutationFn: updateDefaultAnalysisSystemPrompt,
    onSuccess: (data) => {
      setDefaultSystemPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-analysis-system-prompt'] });
    },
  });
  const resetDefaultSystemPromptMutation = useMutation({
    mutationFn: resetDefaultAnalysisSystemPrompt,
    onSuccess: (data) => {
      setDefaultSystemPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-analysis-system-prompt'] });
    },
  });
  const saveDefaultBackgroundPromptMutation = useMutation({
    mutationFn: updateDefaultBackgroundContextPrompt,
    onSuccess: (data) => {
      setDefaultBackgroundPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-background-context-prompt'] });
    },
  });
  const resetDefaultBackgroundPromptMutation = useMutation({
    mutationFn: resetDefaultBackgroundContextPrompt,
    onSuccess: (data) => {
      setDefaultBackgroundPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-background-context-prompt'] });
    },
  });
  const saveDefaultVideoSegmentSystemPromptMutation = useMutation({
    mutationFn: updateDefaultVideoSegmentSystemPrompt,
    onSuccess: (data) => {
      setDefaultVideoSegmentSystemPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-segment-system-prompt'] });
    },
  });
  const resetDefaultVideoSegmentSystemPromptMutation = useMutation({
    mutationFn: resetDefaultVideoSegmentSystemPrompt,
    onSuccess: (data) => {
      setDefaultVideoSegmentSystemPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-segment-system-prompt'] });
    },
  });
  const saveDefaultVideoSegmentPromptMutation = useMutation({
    mutationFn: updateDefaultVideoSegmentPrompt,
    onSuccess: (data) => {
      setDefaultVideoSegmentPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-segment-prompt'] });
    },
  });
  const resetDefaultVideoSegmentPromptMutation = useMutation({
    mutationFn: resetDefaultVideoSegmentPrompt,
    onSuccess: (data) => {
      setDefaultVideoSegmentPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-segment-prompt'] });
    },
  });
  const saveDefaultVideoFinalSystemPromptMutation = useMutation({
    mutationFn: updateDefaultVideoFinalSummarySystemPrompt,
    onSuccess: (data) => {
      setDefaultVideoFinalSystemPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-final-summary-system-prompt'] });
    },
  });
  const resetDefaultVideoFinalSystemPromptMutation = useMutation({
    mutationFn: resetDefaultVideoFinalSummarySystemPrompt,
    onSuccess: (data) => {
      setDefaultVideoFinalSystemPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-final-summary-system-prompt'] });
    },
  });
  const saveDefaultVideoFinalPromptMutation = useMutation({
    mutationFn: updateDefaultVideoFinalSummaryPrompt,
    onSuccess: (data) => {
      setDefaultVideoFinalPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-final-summary-prompt'] });
    },
  });
  const resetDefaultVideoFinalPromptMutation = useMutation({
    mutationFn: resetDefaultVideoFinalSummaryPrompt,
    onSuccess: (data) => {
      setDefaultVideoFinalPromptForm(data.prompt);
      queryClient.invalidateQueries({ queryKey: ['default-video-final-summary-prompt'] });
    },
  });
  const cleanupMutation = useMutation({
    mutationFn: cleanupStaleMedia,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] });
      queryClient.invalidateQueries({ queryKey: ['media-queue'] });
      queryClient.invalidateQueries({ queryKey: ['scan-status'] });
      queryClient.invalidateQueries({ queryKey: ['media'] });
      queryClient.invalidateQueries({ queryKey: ['media-directories'] });
    },
  });

  useEffect(() => {
    if (runtimeQuery.data) {
      setRuntimeForm(runtimeQuery.data);
    }
  }, [runtimeQuery.data]);

  useEffect(() => {
    if (defaultPromptQuery.data) {
      setDefaultPromptForm(defaultPromptQuery.data.prompt);
    }
  }, [defaultPromptQuery.data]);

  useEffect(() => {
    if (defaultSystemPromptQuery.data) {
      setDefaultSystemPromptForm(defaultSystemPromptQuery.data.prompt);
    }
  }, [defaultSystemPromptQuery.data]);

  useEffect(() => {
    if (defaultBackgroundPromptQuery.data) {
      setDefaultBackgroundPromptForm(defaultBackgroundPromptQuery.data.prompt);
    }
  }, [defaultBackgroundPromptQuery.data]);

  useEffect(() => {
    if (defaultVideoSegmentSystemPromptQuery.data) {
      setDefaultVideoSegmentSystemPromptForm(defaultVideoSegmentSystemPromptQuery.data.prompt);
    }
  }, [defaultVideoSegmentSystemPromptQuery.data]);

  useEffect(() => {
    if (defaultVideoSegmentPromptQuery.data) {
      setDefaultVideoSegmentPromptForm(defaultVideoSegmentPromptQuery.data.prompt);
    }
  }, [defaultVideoSegmentPromptQuery.data]);

  useEffect(() => {
    if (defaultVideoFinalSystemPromptQuery.data) {
      setDefaultVideoFinalSystemPromptForm(defaultVideoFinalSystemPromptQuery.data.prompt);
    }
  }, [defaultVideoFinalSystemPromptQuery.data]);

  useEffect(() => {
    if (defaultVideoFinalPromptQuery.data) {
      setDefaultVideoFinalPromptForm(defaultVideoFinalPromptQuery.data.prompt);
    }
  }, [defaultVideoFinalPromptQuery.data]);

  function submitRuntime(event: FormEvent) {
    event.preventDefault();
    saveRuntimeMutation.mutate(runtimeForm);
  }

  function submitDefaultPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultPromptMutation.mutate({ prompt: defaultPromptForm });
  }

  function submitDefaultSystemPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultSystemPromptMutation.mutate({ prompt: defaultSystemPromptForm });
  }

  function submitDefaultBackgroundPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultBackgroundPromptMutation.mutate({ prompt: defaultBackgroundPromptForm });
  }

  function submitDefaultVideoSegmentSystemPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultVideoSegmentSystemPromptMutation.mutate({ prompt: defaultVideoSegmentSystemPromptForm });
  }

  function submitDefaultVideoSegmentPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultVideoSegmentPromptMutation.mutate({ prompt: defaultVideoSegmentPromptForm });
  }

  function submitDefaultVideoFinalSystemPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultVideoFinalSystemPromptMutation.mutate({ prompt: defaultVideoFinalSystemPromptForm });
  }

  function submitDefaultVideoFinalPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultVideoFinalPromptMutation.mutate({ prompt: defaultVideoFinalPromptForm });
  }

  const embeddingModelChanged =
    Boolean(runtimeQuery.data) &&
    runtimeForm.default_embedding_model.trim() !== runtimeQuery.data!.default_embedding_model;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">设置</h1>
          <p className="text-sm text-slate-500">Ollama 连接、本机模型和后台并发</p>
        </div>
        <button
          className="icon-btn"
          title="刷新"
          onClick={() => {
            statusQuery.refetch();
            modelsQuery.refetch();
            runtimeQuery.refetch();
            defaultPromptQuery.refetch();
            defaultSystemPromptQuery.refetch();
            defaultBackgroundPromptQuery.refetch();
            defaultVideoSegmentSystemPromptQuery.refetch();
            defaultVideoSegmentPromptQuery.refetch();
            defaultVideoFinalSystemPromptQuery.refetch();
            defaultVideoFinalPromptQuery.refetch();
          }}
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      </header>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="panel p-4">
          <h2 className="mb-3 text-base font-semibold">Ollama 连接</h2>
          <dl className="grid grid-cols-[100px_1fr] gap-2 text-sm">
            <dt className="text-slate-500">状态</dt>
            <dd>{statusQuery.data?.ok ? 'ok' : 'failed'}</dd>
            <dt className="text-slate-500">地址</dt>
            <dd>{statusQuery.data?.base_url ?? '-'}</dd>
            <dt className="text-slate-500">错误</dt>
            <dd className="text-red-700">{statusQuery.data?.error ?? '-'}</dd>
          </dl>
        </div>
        <div className="panel p-4">
          <h2 className="mb-3 text-base font-semibold">本机模型</h2>
          <div className="flex flex-wrap gap-2">
            {(modelsQuery.data?.models ?? []).map((model) => (
              <span key={model} className="rounded-md border border-line bg-panel px-2 py-1 text-sm">
                {model}
              </span>
            ))}
            {modelsQuery.data?.models.length === 0 && (
              <span className="text-sm text-slate-500">未发现模型</span>
            )}
          </div>
        </div>
      </section>

      <section className="panel overflow-hidden">
        <div className="border-b border-line bg-white px-4 py-4">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-signal/20 bg-signal/10 text-signal">
              <FileJson className="h-4 w-4" />
            </span>
            <div>
              <h2 className="text-base font-semibold">媒体背景使用规则</h2>
              <p className="mt-1 text-sm leading-6 text-slate-500">
                图片和视频共用。只有目录规则填写了“背景补充”时才会随背景内容一起追加；目录规则可以覆盖，没有覆盖时使用这里的默认值。
              </p>
            </div>
          </div>
        </div>
        <div className="bg-slate-50 px-4 py-4">
          <EditablePromptBlock
            title="User prompt 条件追加：背景使用规则"
            description="约束模型如何使用目录背景补充，避免把未被画面或分段内容支持的背景信息当作事实。"
            value={defaultBackgroundPromptForm}
            onChange={setDefaultBackgroundPromptForm}
            onSubmit={submitDefaultBackgroundPrompt}
            onReset={() => resetDefaultBackgroundPromptMutation.mutate()}
            isSaving={saveDefaultBackgroundPromptMutation.isPending}
            isResetting={resetDefaultBackgroundPromptMutation.isPending}
            error={
              defaultBackgroundPromptQuery.error ??
              saveDefaultBackgroundPromptMutation.error ??
              resetDefaultBackgroundPromptMutation.error
            }
            saveSuccess={saveDefaultBackgroundPromptMutation.isSuccess}
            resetSuccess={resetDefaultBackgroundPromptMutation.isSuccess}
            textareaClassName="min-h-36"
            resetTitle="恢复代码内置的背景使用规则"
          />
        </div>
      </section>

      <CollapsiblePromptGroup
        title="照片提示词"
        isOpen={imagePromptsOpen}
        onToggle={() => setImagePromptsOpen((current) => !current)}
      >
        <ImagePromptAssemblyCard
          systemPromptBlock={{
            title: 'System prompt 可编辑：图片分析系统提示词',
            description:
              '通过 Ollama /api/generate 的 system 字段发送，控制图片识别的角色、安全边界、语言和 JSON 基础约束。',
            value: defaultSystemPromptForm,
            onChange: setDefaultSystemPromptForm,
            onSubmit: submitDefaultSystemPrompt,
            onReset: () => resetDefaultSystemPromptMutation.mutate(),
            isSaving: saveDefaultSystemPromptMutation.isPending,
            isResetting: resetDefaultSystemPromptMutation.isPending,
            error:
              defaultSystemPromptQuery.error ??
              saveDefaultSystemPromptMutation.error ??
              resetDefaultSystemPromptMutation.error,
            saveSuccess: saveDefaultSystemPromptMutation.isSuccess,
            resetSuccess: resetDefaultSystemPromptMutation.isSuccess,
            textareaClassName: 'min-h-48',
          }}
          imagePromptBlock={{
            title: 'User prompt 可编辑开头：图片分析提示词',
            description:
              '这段是图片识别 prompt 的主体。目录规则可以覆盖；没有目录覆盖时，会使用这里的默认值。',
            value: defaultPromptForm,
            onChange: setDefaultPromptForm,
            onSubmit: submitDefaultPrompt,
            onReset: () => resetDefaultPromptMutation.mutate(),
            isSaving: saveDefaultPromptMutation.isPending,
            isResetting: resetDefaultPromptMutation.isPending,
            error: defaultPromptQuery.error ?? saveDefaultPromptMutation.error ?? resetDefaultPromptMutation.error,
            saveSuccess: saveDefaultPromptMutation.isSuccess,
            resetSuccess: resetDefaultPromptMutation.isSuccess,
            textareaClassName: 'min-h-96',
          }}
        />
      </CollapsiblePromptGroup>

      <CollapsiblePromptGroup
        title="视频提示词"
        isOpen={videoPromptsOpen}
        onToggle={() => setVideoPromptsOpen((current) => !current)}
      >
        <VideoPromptAssemblyCard
          title="分段识别请求拼接"
          description="每一批关键帧调用视觉模型。上一批滚动摘要只作为背景，当前片段描述必须以当前批次帧为依据。"
          systemBlock={{
            title: 'System message：视频分段识别',
            description: '作为 Ollama /api/chat 的 system message 发送，控制视觉模型的角色、语言和 JSON 约束。',
            value: defaultVideoSegmentSystemPromptForm,
            onChange: setDefaultVideoSegmentSystemPromptForm,
            onSubmit: submitDefaultVideoSegmentSystemPrompt,
            onReset: () => resetDefaultVideoSegmentSystemPromptMutation.mutate(),
            isSaving: saveDefaultVideoSegmentSystemPromptMutation.isPending,
            isResetting: resetDefaultVideoSegmentSystemPromptMutation.isPending,
            error:
              defaultVideoSegmentSystemPromptQuery.error ??
              saveDefaultVideoSegmentSystemPromptMutation.error ??
              resetDefaultVideoSegmentSystemPromptMutation.error,
            saveSuccess: saveDefaultVideoSegmentSystemPromptMutation.isSuccess,
            resetSuccess: resetDefaultVideoSegmentSystemPromptMutation.isSuccess,
            textareaClassName: 'min-h-48',
          }}
          userBlock={{
            title: 'User message 可编辑开头：分段识别提示词',
            description: '这段是 user prompt 的开头。后面的上下文 JSON、字段要求、背景补充由代码按当前视频实时追加。',
            value: defaultVideoSegmentPromptForm,
            onChange: setDefaultVideoSegmentPromptForm,
            onSubmit: submitDefaultVideoSegmentPrompt,
            onReset: () => resetDefaultVideoSegmentPromptMutation.mutate(),
            isSaving: saveDefaultVideoSegmentPromptMutation.isPending,
            isResetting: resetDefaultVideoSegmentPromptMutation.isPending,
            error:
              defaultVideoSegmentPromptQuery.error ??
              saveDefaultVideoSegmentPromptMutation.error ??
              resetDefaultVideoSegmentPromptMutation.error,
            saveSuccess: saveDefaultVideoSegmentPromptMutation.isSuccess,
            resetSuccess: resetDefaultVideoSegmentPromptMutation.isSuccess,
            textareaClassName: 'min-h-72',
          }}
          fixedBlocks={segmentPromptBlocks}
        />

        <VideoPromptAssemblyCard
          title="最终总结请求拼接"
          description="所有 segment 完成后调用总结模型，只投喂文本化的分段结果，不再投喂关键帧图片。"
          systemBlock={{
            title: 'System message：视频最终总结',
            description: '作为 Ollama /api/chat 的 system message 发送，控制总结模型只根据分段结果做最终归并。',
            value: defaultVideoFinalSystemPromptForm,
            onChange: setDefaultVideoFinalSystemPromptForm,
            onSubmit: submitDefaultVideoFinalSystemPrompt,
            onReset: () => resetDefaultVideoFinalSystemPromptMutation.mutate(),
            isSaving: saveDefaultVideoFinalSystemPromptMutation.isPending,
            isResetting: resetDefaultVideoFinalSystemPromptMutation.isPending,
            error:
              defaultVideoFinalSystemPromptQuery.error ??
              saveDefaultVideoFinalSystemPromptMutation.error ??
              resetDefaultVideoFinalSystemPromptMutation.error,
            saveSuccess: saveDefaultVideoFinalSystemPromptMutation.isSuccess,
            resetSuccess: resetDefaultVideoFinalSystemPromptMutation.isSuccess,
            textareaClassName: 'min-h-48',
          }}
          userBlock={{
            title: 'User message 可编辑开头：最终总结提示词',
            description: '这段是最终总结 user prompt 的开头。后面的 segments JSON 和字段要求由代码生成。',
            value: defaultVideoFinalPromptForm,
            onChange: setDefaultVideoFinalPromptForm,
            onSubmit: submitDefaultVideoFinalPrompt,
            onReset: () => resetDefaultVideoFinalPromptMutation.mutate(),
            isSaving: saveDefaultVideoFinalPromptMutation.isPending,
            isResetting: resetDefaultVideoFinalPromptMutation.isPending,
            error:
              defaultVideoFinalPromptQuery.error ??
              saveDefaultVideoFinalPromptMutation.error ??
              resetDefaultVideoFinalPromptMutation.error,
            saveSuccess: saveDefaultVideoFinalPromptMutation.isSuccess,
            resetSuccess: resetDefaultVideoFinalPromptMutation.isSuccess,
            textareaClassName: 'min-h-56',
          }}
          fixedBlocks={finalSummaryPromptBlocks}
        />
      </CollapsiblePromptGroup>

      <form className="panel p-4" onSubmit={submitRuntime}>
        <div className="mb-3">
          <h2 className="text-base font-semibold">运行设置</h2>
          <p className="text-sm text-slate-500">
            Embedding 模型用于生成和搜索向量；图片最长边控制发给视觉模型的缩放尺寸；后台并发保存后会立即重载 worker 池。
          </p>
        </div>
        <div className="mb-4 grid gap-4 lg:grid-cols-2">
          <div>
            <ModelSelector
              id="global-embedding-model-options"
              label="全局 Embedding 模型"
              value={runtimeForm.default_embedding_model}
              models={modelOptions}
              onChange={(value) => setRuntimeForm({ ...runtimeForm, default_embedding_model: value })}
            />
            <p className="mt-1 text-xs leading-5 text-slate-500">
              所有目录共用这个 embedding 模型。更换模型后，已有向量不会自动重算；请到“扫描任务”点击“全部重新生成”，否则搜索只会覆盖已经用新模型生成过向量的媒体。
            </p>
            {embeddingModelChanged && (
              <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                你正在更换全局 Embedding 模型。保存后请重新生成媒体向量，否则旧模型生成的向量不会参与新模型搜索。
              </div>
            )}
          </div>
          <div>
            <NumberField
              label="图片最长边像素"
              min={256}
              max={4096}
              value={runtimeForm.max_image_long_edge}
              onChange={(value) => setRuntimeForm({ ...runtimeForm, max_image_long_edge: value })}
            />
            <p className="mt-1 text-xs leading-5 text-slate-500">
              图片分析前会把原图缩放到最长边不超过这个值，再发送给 Ollama。调大可能提高细节识别，但会增加耗时和显存压力；已分析图片不会自动重算。
            </p>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <NumberField
            label="扫描目录"
            min={1}
            max={8}
            value={runtimeForm.scan_worker_concurrency}
            onChange={(value) => setRuntimeForm({ ...runtimeForm, scan_worker_concurrency: value })}
          />
          <NumberField
            label="提取元数据"
            min={1}
            max={32}
            value={runtimeForm.metadata_worker_concurrency}
            onChange={(value) => setRuntimeForm({ ...runtimeForm, metadata_worker_concurrency: value })}
          />
          <NumberField
            label="图片分析"
            min={1}
            max={4}
            value={runtimeForm.vision_worker_concurrency}
            onChange={(value) => setRuntimeForm({ ...runtimeForm, vision_worker_concurrency: value })}
          />
          <NumberField
            label="生成 Embedding"
            min={1}
            max={16}
            value={runtimeForm.embedding_worker_concurrency}
            onChange={(value) => setRuntimeForm({ ...runtimeForm, embedding_worker_concurrency: value })}
          />
        </div>

        {(runtimeQuery.error || saveRuntimeMutation.error) && (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {(runtimeQuery.error ?? saveRuntimeMutation.error)?.message}
          </div>
        )}
        {saveRuntimeMutation.isSuccess && (
          <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
            运行设置已保存。
          </div>
        )}

        <button className="btn btn-primary mt-5" type="submit" disabled={saveRuntimeMutation.isPending}>
          <Save className="h-4 w-4" />
          保存运行设置
        </button>
      </form>

      <section className="panel p-4">
        <div className="mb-3">
          <h2 className="text-base font-semibold">数据维护</h2>
          <p className="text-sm text-slate-500">
            清除数据库中过期的媒体索引：原始文件不存在，或记录关联的根目录已经不存在。这个操作不会删除任何真实照片。
          </p>
        </div>

        {cleanupMutation.error && (
          <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {cleanupMutation.error.message}
          </div>
        )}
        {cleanupMutation.data && (
          <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
            已创建清理任务：{cleanupMutation.data.id}。可在“扫描”页面查看进度。
          </div>
        )}

        <button
          className="btn"
          type="button"
          disabled={cleanupMutation.isPending}
          onClick={() => cleanupMutation.mutate()}
          title="核对文件是否还存在，删除已失效的数据库索引"
        >
          <Trash2 className="h-4 w-4" />
          清除过期数据
        </button>
      </section>
    </div>
  );
}

function ModelSelector({
  id,
  label,
  value,
  models,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  models: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label>
      <span className="mb-1 block text-sm font-medium">{label}</span>
      <input
        className="control w-full"
        list={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder="选择或手动输入模型名"
        required
      />
      <datalist id={id}>
        {models.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
    </label>
  );
}

function CollapsiblePromptGroup({
  title,
  isOpen,
  onToggle,
  children,
}: {
  title: string;
  isOpen: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const isVideo = title.includes('视频');
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-slate-50">
      <button
        className="flex w-full items-center justify-between gap-3 border-b border-line bg-white px-4 py-3 text-left text-base font-semibold transition hover:bg-slate-50"
        type="button"
        aria-expanded={isOpen}
        onClick={onToggle}
      >
        <span className="flex min-w-0 items-center gap-2">
          {isVideo ? <Video className="h-4 w-4 text-signal" /> : <ImageIcon className="h-4 w-4 text-signal" />}
          <span>{title}</span>
        </span>
        <ChevronDown className={`h-4 w-4 shrink-0 transition-transform duration-300 ${isOpen ? '' : '-rotate-90'}`} />
      </button>
      <div
        className={`grid transition-[grid-template-rows,opacity] duration-300 ease-out ${
          isOpen ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0'
        }`}
      >
        <div className="overflow-hidden">
          <div className="space-y-4 border-t border-white px-4 py-4">{children}</div>
        </div>
      </div>
    </section>
  );
}

type EditablePromptBlockProps = {
  title: string;
  description: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onReset: () => void;
  isSaving: boolean;
  isResetting: boolean;
  error?: { message?: string } | null;
  saveSuccess: boolean;
  resetSuccess: boolean;
  textareaClassName?: string;
  resetTitle?: string;
};

function ImagePromptAssemblyCard({
  systemPromptBlock,
  imagePromptBlock,
}: {
  systemPromptBlock: EditablePromptBlockProps;
  imagePromptBlock: EditablePromptBlockProps;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="border-b border-line bg-white px-4 py-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-signal/20 bg-signal/10 text-signal">
            <FileJson className="h-4 w-4" />
          </span>
          <div>
            <h2 className="text-base font-semibold">图片识别请求拼接</h2>
            <p className="mt-1 text-sm leading-6 text-slate-500">
              单张图片调用视觉模型。当前请求使用 Ollama /api/generate 的 system、prompt、images 和 format 字段。
            </p>
          </div>
        </div>
      </div>
      <div className="bg-slate-50 px-4 py-4">
        <div className="space-y-3 border-l-2 border-slate-200 pl-4">
          <EditablePromptBlock {...systemPromptBlock} resetTitle="恢复代码内置的默认图片 system prompt" />
          <EditablePromptBlock {...imagePromptBlock} resetTitle="恢复代码内置的默认图片分析提示词" />
          {imagePromptBlocksBeforeBackground.map((block) => (
            <ReadOnlyPromptBlock key={block.title} block={block} />
          ))}
          {imagePromptBlocksAfterBackground.map((block) => (
            <ReadOnlyPromptBlock key={block.title} block={block} />
          ))}
        </div>
      </div>
    </section>
  );
}

function VideoPromptAssemblyCard({
  title,
  description,
  systemBlock,
  userBlock,
  fixedBlocks,
}: {
  title: string;
  description: string;
  systemBlock: EditablePromptBlockProps;
  userBlock: EditablePromptBlockProps;
  fixedBlocks: ReadOnlyPromptBlock[];
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="border-b border-line bg-white px-4 py-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-signal/20 bg-signal/10 text-signal">
            <FileJson className="h-4 w-4" />
          </span>
          <div>
            <h2 className="text-base font-semibold">{title}</h2>
            <p className="mt-1 text-sm leading-6 text-slate-500">{description}</p>
          </div>
        </div>
      </div>
      <div className="bg-slate-50 px-4 py-4">
        <div className="space-y-3 border-l-2 border-slate-200 pl-4">
          <EditablePromptBlock {...systemBlock} />
          <EditablePromptBlock {...userBlock} />
          {fixedBlocks.map((block) => (
            <ReadOnlyPromptBlock key={block.title} block={block} />
          ))}
        </div>
      </div>
    </section>
  );
}

function EditablePromptBlock({
  title,
  description,
  value,
  onChange,
  onSubmit,
  onReset,
  isSaving,
  isResetting,
  error,
  saveSuccess,
  resetSuccess,
  textareaClassName = 'min-h-64',
  resetTitle = '恢复代码内置的完整 JSON 结构提示词',
}: EditablePromptBlockProps) {
  return (
    <form className="rounded-md border border-line bg-white p-4 shadow-sm" onSubmit={onSubmit}>
      <div className="mb-3">
        <div className="flex items-center gap-2">
          <Pencil className="h-4 w-4 text-signal" />
          <h3 className="text-sm font-semibold">{title}</h3>
          <span className="rounded-full border border-signal/20 bg-signal/10 px-2 py-0.5 text-xs font-medium text-signal">
            可编辑
          </span>
        </div>
        <p className="mt-1 text-sm leading-6 text-slate-500">{description}</p>
      </div>

      <textarea
        className={`control w-full resize-y font-mono text-xs leading-5 ${textareaClassName}`}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required
      />

      {error && (
        <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error.message}
        </div>
      )}
      {saveSuccess && (
        <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          默认提示词已保存。
        </div>
      )}
      {resetSuccess && (
        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          已恢复内置默认提示词。
        </div>
      )}

      <div className="mt-5 flex flex-wrap gap-2">
        <button className="btn btn-primary" type="submit" disabled={isSaving}>
          <Save className="h-4 w-4" />
          保存
        </button>
        <button
          className="btn"
          type="button"
          disabled={isResetting}
          onClick={onReset}
          title={resetTitle}
        >
          <RefreshCw className="h-4 w-4" />
          恢复内置默认
        </button>
      </div>
    </form>
  );
}

function ReadOnlyPromptBlock({ block }: { block: ReadOnlyPromptBlock }) {
  const isDynamic = block.kind === 'dynamic';
  return (
    <div className="rounded-md border border-slate-200 bg-slate-100 p-4 text-slate-700">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {isDynamic ? <Braces className="h-4 w-4 text-slate-500" /> : <Lock className="h-4 w-4 text-slate-500" />}
        <h3 className="text-sm font-semibold">{block.title}</h3>
        <span className="rounded-full border border-slate-300 bg-white/70 px-2 py-0.5 text-xs font-medium text-slate-600">
          {isDynamic ? '运行时生成' : '代码固定'}
        </span>
      </div>
      <p className="mb-3 text-sm leading-6 text-slate-500">{block.description}</p>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-200 bg-slate-200/70 p-3 font-mono text-xs leading-5 text-slate-700">
        {block.content}
      </pre>
    </div>
  );
}

function NumberField({
  label,
  min,
  max,
  value,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span className="mb-1 block text-sm font-medium">{label}</span>
      <input
        className="control w-full"
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
