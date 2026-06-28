import { useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Folder,
  FolderOpen,
  FolderPlus,
  Play,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
} from 'lucide-react';
import {
  createDirectoryRule,
  deleteDirectoryRule,
  listDirectoryRules,
  updateDirectoryRule,
} from '../api/directoryRules';
import { getOllamaModels } from '../api/models';
import { generateAiRecords, startScan } from '../api/scan';
import {
  browseDirectory,
  getDefaultAnalysisPrompt,
  getDefaultBackgroundContextPrompt,
  getDefaultVideoFinalSummaryPrompt,
  getDefaultVideoSegmentPrompt,
  getDirectoryRuleDefaults,
} from '../api/settings';
import type { DirectoryRule, DirectoryRuleDefaults, DirectoryRulePayload } from '../types';

const fallbackDirectoryRuleDefaults: DirectoryRuleDefaults = {
  recursive: true,
  vision_model: 'qwen2.5vl:7b',
  summary_model: 'qwen3:8b',
  video_frame_strategy: 'hybrid',
  frame_interval_seconds: 5,
  max_frames_per_video: 12,
  video_frame_max_width: 1280,
  video_frame_max_height: null,
  video_batch_size: 6,
  video_batch_overlap: 1,
  analysis_detail: 'normal',
  enabled: true,
};

type PromptDefaults = {
  custom_analysis_prompt?: string;
  background_context_prompt?: string;
  video_segment_prompt?: string;
  video_final_summary_prompt?: string;
};

type ToggleEnabledVariables = {
  id: string;
  enabled: boolean;
  previousRules?: DirectoryRule[];
  previousSelectedId: string | null;
  previousForm: DirectoryRulePayload;
};

type ExpandedDirectoryAction = 'scan' | 'ai' | 'oneClick' | null;

function createDefaultPayload(
  defaults: DirectoryRuleDefaults = fallbackDirectoryRuleDefaults,
  prompts: PromptDefaults = {},
): DirectoryRulePayload {
  return {
    path: '',
    recursive: defaults.recursive,
    vision_model: defaults.vision_model,
    summary_model: defaults.summary_model,
    custom_analysis_prompt: prompts.custom_analysis_prompt ?? '',
    background_context: '',
    background_context_prompt: prompts.background_context_prompt ?? '',
    video_segment_prompt: prompts.video_segment_prompt ?? '',
    video_final_summary_prompt: prompts.video_final_summary_prompt ?? '',
    video_frame_strategy: defaults.video_frame_strategy,
    frame_interval_seconds: defaults.frame_interval_seconds,
    max_frames_per_video: defaults.max_frames_per_video,
    video_frame_max_width: defaults.video_frame_max_width,
    video_frame_max_height: defaults.video_frame_max_height,
    video_batch_size: defaults.video_batch_size,
    video_batch_overlap: defaults.video_batch_overlap,
    analysis_detail: defaults.analysis_detail,
    enabled: defaults.enabled,
  };
}

type DirectoryRuleTreeNode = DirectoryRule & {
  children: DirectoryRuleTreeNode[];
  parentPath: string | null;
  parentDisplayPath: string | null;
};

export function LibrarySettingsPage() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<DirectoryRulePayload>(() => createDefaultPayload());
  const [appliedSettingsTarget, setAppliedSettingsTarget] = useState<string | null>(null);
  const [expandedAction, setExpandedAction] = useState<ExpandedDirectoryAction>(null);
  const rulesQuery = useQuery({ queryKey: ['directory-rules'], queryFn: listDirectoryRules });
  const modelsQuery = useQuery({ queryKey: ['ollama-models'], queryFn: getOllamaModels });
  const directoryDefaultsQuery = useQuery({
    queryKey: ['directory-rule-defaults'],
    queryFn: getDirectoryRuleDefaults,
  });
  const defaultPromptQuery = useQuery({
    queryKey: ['default-analysis-prompt'],
    queryFn: getDefaultAnalysisPrompt,
  });
  const defaultBackgroundPromptQuery = useQuery({
    queryKey: ['default-background-context-prompt'],
    queryFn: getDefaultBackgroundContextPrompt,
  });
  const defaultVideoSegmentPromptQuery = useQuery({
    queryKey: ['default-video-segment-prompt'],
    queryFn: getDefaultVideoSegmentPrompt,
  });
  const defaultVideoFinalPromptQuery = useQuery({
    queryKey: ['default-video-final-summary-prompt'],
    queryFn: getDefaultVideoFinalSummaryPrompt,
  });
  const modelOptions = modelsQuery.data?.models ?? [];
  const directoryDefaults = directoryDefaultsQuery.data ?? fallbackDirectoryRuleDefaults;
  const defaultAnalysisPrompt = defaultPromptQuery.data?.prompt ?? '';
  const defaultBackgroundContextPrompt = defaultBackgroundPromptQuery.data?.prompt ?? '';
  const defaultVideoSegmentPrompt = defaultVideoSegmentPromptQuery.data?.prompt ?? '';
  const defaultVideoFinalSummaryPrompt = defaultVideoFinalPromptQuery.data?.prompt ?? '';
  const promptDefaults = useMemo<PromptDefaults>(
    () => ({
      custom_analysis_prompt: defaultAnalysisPrompt,
      background_context_prompt: defaultBackgroundContextPrompt,
      video_segment_prompt: defaultVideoSegmentPrompt,
      video_final_summary_prompt: defaultVideoFinalSummaryPrompt,
    }),
    [
      defaultAnalysisPrompt,
      defaultBackgroundContextPrompt,
      defaultVideoFinalSummaryPrompt,
      defaultVideoSegmentPrompt,
    ],
  );
  const requestedPath = searchParams.get('path');
  const requestedNormalizedPath = searchParams.get('normalized_path');
  const settingsTargetKey = `${requestedNormalizedPath ?? ''}|${requestedPath ?? ''}`;

  const selected = useMemo(
    () => rulesQuery.data?.find((rule) => rule.id === selectedId) ?? null,
    [rulesQuery.data, selectedId],
  );
  const ruleTree = useMemo(() => buildDirectoryRuleTree(rulesQuery.data ?? []), [rulesQuery.data]);
  const [collapsedRulePaths, setCollapsedRulePaths] = useState<Set<string>>(() => new Set());
  const knownCollapsibleRulePaths = useRef<Set<string>>(new Set());

  useEffect(() => {
    const collapsiblePaths = collectCollapsibleRulePaths(ruleTree);
    const newPaths = collapsiblePaths.filter((path) => !knownCollapsibleRulePaths.current.has(path));
    if (newPaths.length === 0) {
      return;
    }
    for (const path of newPaths) {
      knownCollapsibleRulePaths.current.add(path);
    }
    setCollapsedRulePaths((current) => {
      const next = new Set(current);
      for (const path of newPaths) {
        next.add(path);
      }
      return next;
    });
  }, [ruleTree]);

  function invalidateProcessingQueries() {
    queryClient.invalidateQueries({ queryKey: ['directory-rules'] });
    queryClient.invalidateQueries({ queryKey: ['media-directories'] });
    queryClient.invalidateQueries({ queryKey: ['media-queue'] });
    queryClient.invalidateQueries({ queryKey: ['scan-status'] });
    queryClient.invalidateQueries({ queryKey: ['jobs'] });
    queryClient.invalidateQueries({ queryKey: ['media'] });
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = normalizedDirectoryRulePayload(form);
      return selected ? updateDirectoryRule(selected.id, payload) : createDirectoryRule(payload);
    },
    onSuccess: (rule) => {
      queryClient.invalidateQueries({ queryKey: ['directory-rules'] });
      setSelectedId(rule.id);
      setForm((current) => ({ ...current, path: rule.path }));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteDirectoryRule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['directory-rules'] });
      setSelectedId(null);
      setForm(createDefaultPayload(directoryDefaults, promptDefaults));
    },
  });

  const scanMutation = useMutation({
    mutationFn: (id: string) => startScan({ directoryRuleId: id, mode: 'incremental', runAi: false }),
    onSuccess: invalidateProcessingQueries,
  });
  const fullScanMutation = useMutation({
    mutationFn: (id: string) => startScan({ directoryRuleId: id, mode: 'full', runAi: false }),
    onSuccess: invalidateProcessingQueries,
  });
  const generateAiMutation = useMutation({
    mutationFn: (id: string) => generateAiRecords({ directoryRuleId: id, mode: 'missing' }),
    onSuccess: invalidateProcessingQueries,
  });
  const regenerateAiMutation = useMutation({
    mutationFn: (id: string) => generateAiRecords({ directoryRuleId: id, mode: 'all_known' }),
    onSuccess: invalidateProcessingQueries,
  });
  const oneClickGenerateMutation = useMutation({
    mutationFn: async (id: string) => {
      const [scanJobs, aiJobs] = await Promise.all([
        startScan({ directoryRuleId: id, mode: 'incremental', runAi: true }),
        generateAiRecords({ directoryRuleId: id, mode: 'missing' }),
      ]);
      return [...scanJobs, ...aiJobs];
    },
    onSuccess: invalidateProcessingQueries,
  });
  const fullOneClickGenerateMutation = useMutation({
    mutationFn: (id: string) => startScan({ directoryRuleId: id, mode: 'full', runAi: true }),
    onSuccess: invalidateProcessingQueries,
  });
  const browseMutation = useMutation({
    mutationFn: () => browseDirectory(form.path),
    onSuccess: (data) => {
      if (data.path) {
        setForm((current) => ({ ...current, path: data.path! }));
      }
    },
  });
  const toggleEnabledMutation = useMutation({
    mutationFn: ({ id, enabled }: ToggleEnabledVariables) =>
      updateDirectoryRule(id, { enabled }),
    onError: (_error, variables) => {
      if (variables.previousRules) {
        queryClient.setQueryData<DirectoryRule[]>(['directory-rules'], variables.previousRules);
      }
      setSelectedId(variables.previousSelectedId);
      setForm(variables.previousForm);
    },
    onSuccess: (updatedRule) => {
      queryClient.setQueryData<DirectoryRule[]>(['directory-rules'], (rules) =>
        rules?.map((rule) => {
          if (rule.id === updatedRule.id) {
            return updatedRule;
          }
          if (!updatedRule.enabled && directoryRuleIsDescendant(rule, updatedRule)) {
            return { ...rule, enabled: false };
          }
          return rule;
        }),
      );
      if (selectedId === updatedRule.id) {
        setForm((current) => ({ ...current, enabled: updatedRule.enabled }));
      }
    },
    onSettled: () => {
      invalidateProcessingQueries();
    },
  });

  function selectRule(rule: DirectoryRule) {
    setSelectedId(rule.id);
    setForm({
      path: normalizeDisplayPath(rule.path),
      recursive: rule.recursive,
      vision_model: rule.vision_model,
      summary_model: rule.summary_model,
      custom_analysis_prompt: rule.custom_analysis_prompt?.trim() || defaultAnalysisPrompt,
      background_context: rule.background_context ?? '',
      background_context_prompt: rule.background_context_prompt?.trim() || defaultBackgroundContextPrompt,
      video_segment_prompt: rule.video_segment_prompt?.trim() || defaultVideoSegmentPrompt,
      video_final_summary_prompt: rule.video_final_summary_prompt?.trim() || defaultVideoFinalSummaryPrompt,
      video_frame_strategy: rule.video_frame_strategy,
      frame_interval_seconds: rule.frame_interval_seconds,
      max_frames_per_video: rule.max_frames_per_video,
      video_frame_max_width: rule.video_frame_max_width,
      video_frame_max_height: rule.video_frame_max_height,
      video_batch_size: rule.video_batch_size,
      video_batch_overlap: rule.video_batch_overlap,
      analysis_detail: rule.analysis_detail,
      enabled: rule.enabled,
    });
  }

  useEffect(() => {
    if (
      !rulesQuery.data ||
      !directoryDefaultsQuery.data ||
      settingsTargetKey === '|' ||
      appliedSettingsTarget === settingsTargetKey
    ) {
      return;
    }

    const targetPath = requestedNormalizedPath ?? requestedPath;
    const matchedRule = targetPath
      ? rulesQuery.data.find(
          (rule) => normalizeDirectoryPath(rule.normalized_path) === normalizeDirectoryPath(targetPath),
        )
      : null;

    if (matchedRule) {
      selectRule(matchedRule);
    } else if (requestedPath) {
      setSelectedId(null);
      setForm({
        ...createDefaultPayload(directoryDefaults, promptDefaults),
        path: normalizeDisplayPath(requestedPath),
      });
    }
    setAppliedSettingsTarget(settingsTargetKey);
  }, [
    appliedSettingsTarget,
    directoryDefaults,
    directoryDefaultsQuery.data,
    promptDefaults,
    requestedNormalizedPath,
    requestedPath,
    rulesQuery.data,
    settingsTargetKey,
  ]);

  useEffect(() => {
    if (defaultAnalysisPrompt && !form.custom_analysis_prompt?.trim()) {
      setForm((current) => ({ ...current, custom_analysis_prompt: defaultAnalysisPrompt }));
    }
  }, [defaultAnalysisPrompt, form.custom_analysis_prompt]);

  useEffect(() => {
    if (defaultBackgroundContextPrompt && !form.background_context_prompt?.trim()) {
      setForm((current) => ({ ...current, background_context_prompt: defaultBackgroundContextPrompt }));
    }
  }, [defaultBackgroundContextPrompt, form.background_context_prompt]);

  useEffect(() => {
    if (defaultVideoSegmentPrompt && !form.video_segment_prompt?.trim()) {
      setForm((current) => ({ ...current, video_segment_prompt: defaultVideoSegmentPrompt }));
    }
  }, [defaultVideoSegmentPrompt, form.video_segment_prompt]);

  useEffect(() => {
    if (defaultVideoFinalSummaryPrompt && !form.video_final_summary_prompt?.trim()) {
      setForm((current) => ({ ...current, video_final_summary_prompt: defaultVideoFinalSummaryPrompt }));
    }
  }, [defaultVideoFinalSummaryPrompt, form.video_final_summary_prompt]);

  function submit(event: FormEvent) {
    event.preventDefault();
    saveMutation.mutate();
  }

  function toggleRuleCollapsed(path: string) {
    setCollapsedRulePaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  function toggleRuleEnabled(rule: DirectoryRule, enabled: boolean) {
    const previousRules = queryClient.getQueryData<DirectoryRule[]>(['directory-rules']);
    const previousSelectedId = selectedId;
    const previousForm = form;
    const selectedRule = previousRules?.find((item) => item.id === selectedId) ?? null;
    if (!enabled && selectedRule && selectedRule.id !== rule.id && directoryRuleIsDescendant(selectedRule, rule)) {
      selectRule({ ...rule, enabled });
    } else if (selectedId === rule.id) {
      setForm((current) => ({ ...current, enabled }));
    }
    queryClient.setQueryData<DirectoryRule[]>(['directory-rules'], (rules) =>
      rules?.map((item) => {
        if (item.id === rule.id) {
          return { ...item, enabled };
        }
        if (!enabled && directoryRuleIsDescendant(item, rule)) {
          return { ...item, enabled: false };
        }
        return item;
      }),
    );
    toggleEnabledMutation.mutate({ id: rule.id, enabled, previousRules, previousSelectedId, previousForm });
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">媒体库设置</h1>
          <p className="text-sm text-slate-500">目录规则、模型和扫描入口</p>
        </div>
        <button
          className="btn"
          onClick={() => {
            setSelectedId(null);
            setForm(createDefaultPayload(directoryDefaults, promptDefaults));
          }}
          title="新增目录规则"
        >
          <FolderPlus className="h-4 w-4" />
          新增
        </button>
      </header>

      <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
        <section className="panel overflow-hidden">
          <div className="border-b border-line px-4 py-3 text-sm font-semibold">目录</div>
          <div className="max-h-[calc(100vh-180px)] overflow-auto p-2">
            <DirectoryRuleTree
              nodes={ruleTree}
              selectedId={selectedId}
              collapsedPaths={collapsedRulePaths}
              toggleDisabled={toggleEnabledMutation.isPending}
              onSelect={selectRule}
              onToggleCollapsed={toggleRuleCollapsed}
              onToggleEnabled={toggleRuleEnabled}
            />
            {rulesQuery.data?.length === 0 && (
              <div className="p-6 text-center text-sm text-slate-500">暂无目录规则</div>
            )}
          </div>
        </section>

        <form className="panel p-4" onSubmit={submit}>
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="lg:col-span-2">
              <label htmlFor="directory-path" className="mb-1 block text-sm font-medium">
                路径
              </label>
              <div className="flex gap-2">
                <input
                  id="directory-path"
                  className="control min-w-0 flex-1"
                  value={form.path}
                  onChange={(event) => setForm({ ...form, path: event.target.value })}
                  placeholder="D:/Photos"
                  required
                />
                <button
                  className="btn shrink-0"
                  type="button"
                  onClick={() => browseMutation.mutate()}
                  disabled={browseMutation.isPending}
                  title="打开系统文件夹选择器"
                >
                  <FolderOpen className="h-4 w-4" />
                  浏览
                </button>
              </div>
            </div>

            <ModelSelector
              id="vision-model-options"
              label="视觉模型"
              value={form.vision_model}
              models={modelOptions}
              onChange={(value) => setForm({ ...form, vision_model: value })}
            />
            <ModelSelector
              id="summary-model-options"
              label="总结模型"
              value={form.summary_model}
              models={modelOptions}
              onChange={(value) => setForm({ ...form, summary_model: value })}
            />

            <label className="lg:col-span-2">
              <span className="mb-1 block text-sm font-medium">背景补充</span>
              <textarea
                className="control min-h-24 w-full resize-y"
                value={form.background_context ?? ''}
                onChange={(event) => setForm({ ...form, background_context: event.target.value })}
                placeholder="例如：这个目录主要是某次旅行、某个项目素材、角色设定参考、家庭相册或商品拍摄。模型会把它作为理解背景，不会当作图片事实强行写入。"
              />
              <p className="mt-1 text-xs leading-5 text-slate-500">
                用来告诉模型这个目录照片的大致拍摄背景、用途或命名习惯；画面可见内容仍优先。
              </p>
            </label>

            <div className="lg:col-span-2">
              <div className="mb-1 flex items-center justify-between gap-3">
                <label htmlFor="background-context-prompt" className="block text-sm font-medium">
                  背景使用规则
                </label>
                <button
                  className="btn h-8"
                  type="button"
                  onClick={() =>
                    setForm({ ...form, background_context_prompt: defaultBackgroundContextPrompt })
                  }
                  disabled={!defaultBackgroundContextPrompt}
                  title="恢复为设置页中的默认背景使用规则"
                >
                  <RefreshCw className="h-4 w-4" />
                  恢复默认
                </button>
              </div>
              <textarea
                id="background-context-prompt"
                className="control min-h-28 w-full resize-y"
                value={form.background_context_prompt ?? ''}
                onChange={(event) => setForm({ ...form, background_context_prompt: event.target.value })}
              />
              <p className="mt-1 text-xs leading-5 text-slate-500">
                只有“背景补充”非空时才会随媒体分析 prompt 一起发送；为空时这段规则不会进入提示词。
              </p>
            </div>

            <div className="lg:col-span-2">
              <div className="mb-1 flex items-center justify-between gap-3">
                <label htmlFor="analysis-prompt" className="block text-sm font-medium">
                  分析提示词
                </label>
                <button
                  className="btn h-8"
                  type="button"
                  onClick={() => setForm({ ...form, custom_analysis_prompt: defaultAnalysisPrompt })}
                  disabled={!defaultAnalysisPrompt}
                  title="恢复为设置页中的默认分析提示词"
                >
                  <RefreshCw className="h-4 w-4" />
                  恢复默认
                </button>
              </div>
              <textarea
                id="analysis-prompt"
                className="control min-h-44 w-full resize-y"
                value={form.custom_analysis_prompt ?? ''}
                onChange={(event) => setForm({ ...form, custom_analysis_prompt: event.target.value })}
                required
              />
              <p className="mt-1 text-xs leading-5 text-slate-500">
                这是这个目录实际使用的图片分析提示词。系统仍会额外强制结构化 JSON 和中文输出，避免破坏扫描流程。
              </p>
            </div>

            <label>
              <span className="mb-1 block text-sm font-medium">抽帧策略</span>
              <select
                className="control w-full"
                value={form.video_frame_strategy}
                onChange={(event) =>
                  setForm({
                    ...form,
                    video_frame_strategy: event.target.value as DirectoryRulePayload['video_frame_strategy'],
                  })
                }
              >
                <option value="hybrid">hybrid</option>
                <option value="fixed_interval">fixed_interval</option>
                <option value="scene">scene</option>
              </select>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                视频分析预留项：fixed_interval 按固定秒数取帧，scene 按画面变化取帧，hybrid
                先按场景变化取帧、不足时再按固定间隔补齐。当前图片扫描不受这个设置影响。
              </p>
            </label>

            <NumberField
              label="抽帧间隔秒"
              value={form.frame_interval_seconds}
              onChange={(value) => setForm({ ...form, frame_interval_seconds: value })}
            />
            <NumberField
              label="最大帧数"
              value={form.max_frames_per_video}
              max={500}
              onChange={(value) => setForm({ ...form, max_frames_per_video: value })}
            />
            <NumberField
              label="关键帧最大宽度"
              value={form.video_frame_max_width}
              min={160}
              max={4096}
              onChange={(value) => setForm({ ...form, video_frame_max_width: value })}
            />
            <OptionalNumberField
              label="关键帧高度"
              value={form.video_frame_max_height}
              min={160}
              max={4096}
              placeholder="留空保持比例"
              onChange={(value) => setForm({ ...form, video_frame_max_height: value })}
            />
            <NumberField
              label="每批帧数"
              value={form.video_batch_size}
              min={1}
              max={24}
              onChange={(value) => setForm({ ...form, video_batch_size: value })}
            />
            <NumberField
              label="相邻批次重叠帧数"
              value={form.video_batch_overlap}
              min={0}
              max={23}
              onChange={(value) => setForm({ ...form, video_batch_overlap: value })}
            />
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.recursive}
                onChange={(event) => setForm({ ...form, recursive: event.target.checked })}
              />
              递归扫描
            </label>
          </div>

          <div className="mt-4 grid gap-4">
            <div>
              <div className="mb-1 flex items-center justify-between gap-3">
                <label htmlFor="video-segment-prompt" className="block text-sm font-medium">
                  视频分段识别提示词
                </label>
                <button
                  className="btn h-8"
                  type="button"
                  onClick={() => setForm({ ...form, video_segment_prompt: defaultVideoSegmentPrompt })}
                  disabled={!defaultVideoSegmentPrompt}
                  title="恢复为设置页中的默认视频分段识别提示词"
                >
                  <RefreshCw className="h-4 w-4" />
                  恢复默认
                </button>
              </div>
              <textarea
                id="video-segment-prompt"
                className="control min-h-44 w-full resize-y font-mono text-xs leading-5"
                value={form.video_segment_prompt ?? ''}
                onChange={(event) => setForm({ ...form, video_segment_prompt: event.target.value })}
              />
              <p className="mt-1 text-xs leading-5 text-slate-500">
                用于每批关键帧递推摘要。系统会额外附加上一批全局记忆、当前帧序号和时间戳，并强制返回片段摘要 JSON。
              </p>
            </div>

            <div>
              <div className="mb-1 flex items-center justify-between gap-3">
                <label htmlFor="video-final-summary-prompt" className="block text-sm font-medium">
                  视频最终总结提示词
                </label>
                <button
                  className="btn h-8"
                  type="button"
                  onClick={() =>
                    setForm({ ...form, video_final_summary_prompt: defaultVideoFinalSummaryPrompt })
                  }
                  disabled={!defaultVideoFinalSummaryPrompt}
                  title="恢复为设置页中的默认视频最终总结提示词"
                >
                  <RefreshCw className="h-4 w-4" />
                  恢复默认
                </button>
              </div>
              <textarea
                id="video-final-summary-prompt"
                className="control min-h-44 w-full resize-y font-mono text-xs leading-5"
                value={form.video_final_summary_prompt ?? ''}
                onChange={(event) => setForm({ ...form, video_final_summary_prompt: event.target.value })}
              />
              <p className="mt-1 text-xs leading-5 text-slate-500">
                用于汇总所有 segment summary，调用的是该目录的总结模型，不会再次投喂视频帧图片。
              </p>
            </div>
          </div>

          {modelsQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取 Ollama 模型列表，仍可手动输入模型名：{modelsQuery.error.message}
            </div>
          )}
          {directoryDefaultsQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取目录默认设置：{directoryDefaultsQuery.error.message}
            </div>
          )}
          {defaultPromptQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取默认图片分析提示词：{defaultPromptQuery.error.message}
            </div>
          )}
          {defaultBackgroundPromptQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取默认背景使用规则：{defaultBackgroundPromptQuery.error.message}
            </div>
          )}
          {defaultVideoSegmentPromptQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取默认视频分段识别提示词：{defaultVideoSegmentPromptQuery.error.message}
            </div>
          )}
          {defaultVideoFinalPromptQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取默认视频最终总结提示词：{defaultVideoFinalPromptQuery.error.message}
            </div>
          )}
          {(saveMutation.error ||
            deleteMutation.error ||
            scanMutation.error ||
            fullScanMutation.error ||
            generateAiMutation.error ||
            regenerateAiMutation.error ||
            oneClickGenerateMutation.error ||
            fullOneClickGenerateMutation.error ||
            browseMutation.error ||
            toggleEnabledMutation.error) && (
            <div className="mt-4 whitespace-pre-line rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {(
                saveMutation.error ??
                deleteMutation.error ??
                scanMutation.error ??
                fullScanMutation.error ??
                generateAiMutation.error ??
                regenerateAiMutation.error ??
                oneClickGenerateMutation.error ??
                fullOneClickGenerateMutation.error ??
                browseMutation.error ??
                toggleEnabledMutation.error
              )?.message}
            </div>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            <button className="btn btn-primary" type="submit" disabled={saveMutation.isPending}>
              <Save className="h-4 w-4" />
              保存
            </button>
            {selected && (
              <>
                <DirectoryActionButton
                  label="扫描媒体"
                  title="增量扫描新媒体并生成元数据和缩略图，不调用 AI"
                  icon={<Play className="h-4 w-4" />}
                  expanded={expandedAction === 'scan'}
                  disabled={scanMutation.isPending}
                  advancedDisabled={fullScanMutation.isPending}
                  advancedLabel="重新扫描全部媒体"
                  advancedTitle="全量扫描这个目录下的媒体并重做元数据和缩略图，不调用 AI"
                  advancedIcon={<RefreshCw className="h-4 w-4" />}
                  onClick={() => scanMutation.mutate(selected.id)}
                  onToggle={() => setExpandedAction((current) => (current === 'scan' ? null : 'scan'))}
                  onAdvancedClick={() => fullScanMutation.mutate(selected.id)}
                />
                <DirectoryActionButton
                  label="生成AI记录"
                  title="为已扫描但还没有 AI 记录的媒体排队"
                  icon={<Sparkles className="h-4 w-4" />}
                  expanded={expandedAction === 'ai'}
                  disabled={generateAiMutation.isPending}
                  advancedDisabled={regenerateAiMutation.isPending}
                  advancedLabel="重新生成全部AI记录"
                  advancedTitle="对已知媒体重新提取元数据并重新生成 AI 记录和搜索向量"
                  advancedIcon={<RefreshCw className="h-4 w-4" />}
                  onClick={() => generateAiMutation.mutate(selected.id)}
                  onToggle={() => setExpandedAction((current) => (current === 'ai' ? null : 'ai'))}
                  onAdvancedClick={() => regenerateAiMutation.mutate(selected.id)}
                />
                <DirectoryActionButton
                  label="一键生成"
                  title="增量扫描新媒体并生成 AI，同时补齐已有但缺少 AI 记录的媒体"
                  icon={<Sparkles className="h-4 w-4" />}
                  expanded={expandedAction === 'oneClick'}
                  disabled={oneClickGenerateMutation.isPending}
                  advancedDisabled={fullOneClickGenerateMutation.isPending}
                  advancedLabel="一键全部重新生成"
                  advancedTitle="全量扫描这个目录，并为全部媒体重新生成 AI 记录和搜索向量"
                  advancedIcon={<RefreshCw className="h-4 w-4" />}
                  onClick={() => oneClickGenerateMutation.mutate(selected.id)}
                  onToggle={() => setExpandedAction((current) => (current === 'oneClick' ? null : 'oneClick'))}
                  onAdvancedClick={() => fullOneClickGenerateMutation.mutate(selected.id)}
                />
                <button
                  className="btn"
                  type="button"
                  onClick={() => deleteMutation.mutate(selected.id)}
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="h-4 w-4" />
                  删除
                </button>
              </>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

function DirectoryActionButton({
  label,
  title,
  icon,
  expanded,
  disabled,
  advancedDisabled,
  advancedLabel,
  advancedTitle,
  advancedIcon,
  onClick,
  onToggle,
  onAdvancedClick,
}: {
  label: string;
  title: string;
  icon: ReactNode;
  expanded: boolean;
  disabled: boolean;
  advancedDisabled: boolean;
  advancedLabel: string;
  advancedTitle: string;
  advancedIcon: ReactNode;
  onClick: () => void;
  onToggle: () => void;
  onAdvancedClick: () => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex">
        <button
          className="btn rounded-r-none border-r-0"
          type="button"
          onClick={onClick}
          disabled={disabled}
          title={title}
        >
          {icon}
          {label}
        </button>
        <button
          className="icon-btn rounded-l-none px-0"
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          title={expanded ? '收起全部操作' : '展开全部操作'}
        >
          <ChevronUp className={`h-4 w-4 transition ${expanded ? 'rotate-180' : ''}`} />
        </button>
      </div>
      {expanded && (
        <button
          className="btn justify-start"
          type="button"
          onClick={onAdvancedClick}
          disabled={advancedDisabled}
          title={advancedTitle}
        >
          {advancedIcon}
          {advancedLabel}
        </button>
      )}
    </div>
  );
}

function DirectoryRuleTree({
  nodes,
  selectedId,
  collapsedPaths,
  toggleDisabled,
  onSelect,
  onToggleCollapsed,
  onToggleEnabled,
}: {
  nodes: DirectoryRuleTreeNode[];
  selectedId: string | null;
  collapsedPaths: Set<string>;
  toggleDisabled: boolean;
  onSelect: (rule: DirectoryRule) => void;
  onToggleCollapsed: (path: string) => void;
  onToggleEnabled: (rule: DirectoryRule, enabled: boolean) => void;
}) {
  return (
    <div>
      {nodes.map((node) => (
        <DirectoryRuleTreeItem
          key={node.id}
          node={node}
          level={0}
          selectedId={selectedId}
          collapsedPaths={collapsedPaths}
          toggleDisabled={toggleDisabled}
          onSelect={onSelect}
          onToggleCollapsed={onToggleCollapsed}
          onToggleEnabled={onToggleEnabled}
        />
      ))}
    </div>
  );
}

function DirectoryRuleTreeItem({
  node,
  level,
  selectedId,
  collapsedPaths,
  toggleDisabled,
  onSelect,
  onToggleCollapsed,
  onToggleEnabled,
}: {
  node: DirectoryRuleTreeNode;
  level: number;
  selectedId: string | null;
  collapsedPaths: Set<string>;
  toggleDisabled: boolean;
  onSelect: (rule: DirectoryRule) => void;
  onToggleCollapsed: (path: string) => void;
  onToggleEnabled: (rule: DirectoryRule, enabled: boolean) => void;
}) {
  const selected = selectedId === node.id;
  const visibleChildren = node.enabled ? node.children : [];
  const hasChildren = visibleChildren.length > 0;
  const collapsed = collapsedPaths.has(node.normalized_path);
  const Icon = selected || (hasChildren && !collapsed) ? FolderOpen : Folder;

  return (
    <div>
      <div
        className={`mb-2 flex w-full items-stretch overflow-hidden rounded-md border text-sm transition ${
          selected ? 'border-accent bg-emerald-50' : 'border-line bg-white hover:border-signal'
        }`}
        style={{ marginLeft: `${level * 16}px` }}
      >
        <div className="flex shrink-0 items-center pl-2">
          {hasChildren ? (
            <button
              type="button"
              className="flex h-8 w-6 items-center justify-center rounded text-slate-500 hover:text-signal"
              aria-label={collapsed ? '展开目录' : '折叠目录'}
              aria-expanded={!collapsed}
              title={collapsed ? '展开目录' : '折叠目录'}
              onClick={() => onToggleCollapsed(node.normalized_path)}
            >
              {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </button>
          ) : (
            <span className="h-8 w-6" />
          )}
        </div>
        <button
          className="min-w-0 flex-1 p-3 pl-1 text-left"
          type="button"
          onClick={() => onSelect(node)}
          title={normalizeDisplayPath(node.path)}
        >
          <div className="flex min-w-0 items-center gap-2">
            <Icon className="h-4 w-4 shrink-0" />
            <span className="truncate font-medium">{directoryRuleLabel(node)}</span>
          </div>
          <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
            <span>{node.recursive ? '递归扫描' : '仅当前层'}</span>
            <span>{node.enabled ? '已启用' : '已停用'}</span>
          </div>
          <div className="mt-2 truncate text-xs text-slate-600">{node.vision_model}</div>
        </button>
        <div className="flex shrink-0 items-center border-l border-line px-3">
          <Switch
            checked={node.enabled}
            disabled={toggleDisabled}
            label={node.enabled ? '停用目录' : '启用目录'}
            onChange={(enabled) => onToggleEnabled(node, enabled)}
          />
        </div>
      </div>
      {!collapsed &&
        visibleChildren.map((child) => (
          <DirectoryRuleTreeItem
            key={child.id}
            node={child}
            level={level + 1}
            selectedId={selectedId}
            collapsedPaths={collapsedPaths}
            toggleDisabled={toggleDisabled}
            onSelect={onSelect}
            onToggleCollapsed={onToggleCollapsed}
            onToggleEnabled={onToggleEnabled}
          />
        ))}
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
      />
      <datalist id={id}>
        {models.map((model) => (
          <option key={model} value={model} />
        ))}
      </datalist>
    </label>
  );
}

function NumberField({
  label,
  value,
  min = 1,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
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

function OptionalNumberField({
  label,
  value,
  min = 1,
  max,
  placeholder,
  onChange,
}: {
  label: string;
  value: number | null;
  min?: number;
  max?: number;
  placeholder?: string;
  onChange: (value: number | null) => void;
}) {
  return (
    <label>
      <span className="mb-1 block text-sm font-medium">{label}</span>
      <input
        className="control w-full"
        type="number"
        min={min}
        max={max}
        value={value ?? ''}
        placeholder={placeholder}
        onChange={(event) => {
          const next = event.target.value;
          onChange(next === '' ? null : Number(next));
        }}
      />
    </label>
  );
}

function normalizeDirectoryPath(path: string) {
  return path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

function normalizeDisplayPath(path: string) {
  const text = path.trim().replace(/\\/g, '/');
  if (/^[A-Za-z]:\/*$/.test(text)) {
    return `${text[0].toUpperCase()}:/`;
  }
  if (text.startsWith('//')) {
    return `//${text.slice(2).replace(/\/+$/, '')}`;
  }
  return text.replace(/\/+$/, '') || text;
}

function normalizedDirectoryRulePayload(payload: DirectoryRulePayload): DirectoryRulePayload {
  return {
    ...payload,
    path: normalizeDisplayPath(payload.path),
  };
}

function buildDirectoryRuleTree(rules: DirectoryRule[]): DirectoryRuleTreeNode[] {
  const nodes = new Map<string, DirectoryRuleTreeNode>();
  for (const rule of rules) {
    const path = normalizeDirectoryPath(rule.normalized_path || rule.path);
    if (!path) {
      continue;
    }
    nodes.set(path, {
      ...rule,
      normalized_path: path,
      children: [],
      parentPath: null,
      parentDisplayPath: null,
    });
  }

  const sorted = Array.from(nodes.values()).sort(compareDirectoryRuleNodes);
  const roots: DirectoryRuleTreeNode[] = [];
  for (const node of sorted) {
    const parent = findLongestRuleParent(node, sorted);
    if (parent) {
      node.parentPath = parent.normalized_path;
      node.parentDisplayPath = parent.path;
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  for (const root of roots) {
    sortRuleChildren(root);
  }
  return roots.sort(compareDirectoryRuleNodes);
}

function findLongestRuleParent(
  node: DirectoryRuleTreeNode,
  candidates: DirectoryRuleTreeNode[],
): DirectoryRuleTreeNode | null {
  let parent: DirectoryRuleTreeNode | null = null;
  for (const candidate of candidates) {
    if (
      candidate.normalized_path === node.normalized_path ||
      candidate.normalized_path.length >= node.normalized_path.length
    ) {
      continue;
    }
    if (
      directoryHasPrefix(node.normalized_path, candidate.normalized_path) &&
      (!parent || candidate.normalized_path.length > parent.normalized_path.length)
    ) {
      parent = candidate;
    }
  }
  return parent;
}

function sortRuleChildren(node: DirectoryRuleTreeNode) {
  node.children.sort(compareDirectoryRuleNodes);
  for (const child of node.children) {
    sortRuleChildren(child);
  }
}

function compareDirectoryRuleNodes(left: DirectoryRuleTreeNode, right: DirectoryRuleTreeNode) {
  return left.path.localeCompare(right.path, 'zh-Hans-CN', {
    numeric: true,
    sensitivity: 'base',
  });
}

function collectCollapsibleRulePaths(nodes: DirectoryRuleTreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.children.length > 0) {
      paths.push(node.normalized_path);
    }
    paths.push(...collectCollapsibleRulePaths(node.children));
  }
  return paths;
}

function directoryRuleLabel(node: DirectoryRuleTreeNode) {
  if (!node.parentPath) {
    return normalizeDisplayPath(node.path);
  }
  if (node.parentDisplayPath) {
    const displayPath = normalizeDisplayPath(node.path);
    const parentDisplayPath = normalizeDisplayPath(node.parentDisplayPath);
    if (normalizeDirectoryPath(displayPath).startsWith(`${normalizeDirectoryPath(parentDisplayPath)}/`)) {
      return displayPath.slice(parentDisplayPath.length + 1) || node.path;
    }
  }
  return node.normalized_path.slice((node.parentPath ?? '').length + 1) || node.path;
}

function directoryHasPrefix(path: string, prefix: string) {
  return path === prefix || path.startsWith(`${prefix}/`);
}

function directoryRuleIsDescendant(rule: DirectoryRule, parent: DirectoryRule) {
  const path = normalizeDirectoryPath(rule.normalized_path || rule.path);
  const parentPath = normalizeDirectoryPath(parent.normalized_path || parent.path);
  return path.length > parentPath.length && directoryHasPrefix(path, parentPath);
}

function Switch({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className={`relative inline-flex h-6 w-11 shrink-0 items-center ${
        disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
      }`}
      title={label}
    >
      <input
        aria-label={label}
        className="peer sr-only"
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="absolute inset-0 rounded-full bg-slate-300 transition peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-signal/30" />
      <span className="absolute left-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition peer-checked:translate-x-5" />
    </label>
  );
}
