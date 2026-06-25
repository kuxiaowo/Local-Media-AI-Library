import { FormEvent, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { FolderPlus, Play, RefreshCw, Save, Trash2 } from 'lucide-react';
import {
  createDirectoryRule,
  deleteDirectoryRule,
  listDirectoryRules,
  updateDirectoryRule,
} from '../api/directoryRules';
import { getOllamaModels } from '../api/models';
import { startScan } from '../api/scan';
import { getDefaultAnalysisPrompt } from '../api/settings';
import type { DirectoryRule, DirectoryRulePayload, ScanMode } from '../types';

function createDefaultPayload(defaultAnalysisPrompt = ''): DirectoryRulePayload {
  return {
    path: '',
    recursive: true,
    vision_model: 'qwen3-vl:8b',
    summary_model: 'qwen3:8b',
    custom_analysis_prompt: defaultAnalysisPrompt,
    background_context: '',
    video_frame_strategy: 'hybrid',
    frame_interval_seconds: 5,
    max_frames_per_video: 12,
    analysis_detail: 'normal',
    enabled: true,
  };
}

export function LibrarySettingsPage() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<DirectoryRulePayload>(() => createDefaultPayload());
  const [appliedSettingsTarget, setAppliedSettingsTarget] = useState<string | null>(null);
  const rulesQuery = useQuery({ queryKey: ['directory-rules'], queryFn: listDirectoryRules });
  const modelsQuery = useQuery({ queryKey: ['ollama-models'], queryFn: getOllamaModels });
  const defaultPromptQuery = useQuery({
    queryKey: ['default-analysis-prompt'],
    queryFn: getDefaultAnalysisPrompt,
  });
  const modelOptions = modelsQuery.data?.models ?? [];
  const defaultAnalysisPrompt = defaultPromptQuery.data?.prompt ?? '';
  const requestedPath = searchParams.get('path');
  const requestedNormalizedPath = searchParams.get('normalized_path');
  const settingsTargetKey = `${requestedNormalizedPath ?? ''}|${requestedPath ?? ''}`;

  const selected = useMemo(
    () => rulesQuery.data?.find((rule) => rule.id === selectedId) ?? null,
    [rulesQuery.data, selectedId],
  );

  const saveMutation = useMutation({
    mutationFn: () => (selected ? updateDirectoryRule(selected.id, form) : createDirectoryRule(form)),
    onSuccess: (rule) => {
      queryClient.invalidateQueries({ queryKey: ['directory-rules'] });
      setSelectedId(rule.id);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteDirectoryRule(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['directory-rules'] });
      setSelectedId(null);
      setForm(createDefaultPayload(defaultAnalysisPrompt));
    },
  });

  const scanMutation = useMutation({
    mutationFn: ({ id, mode }: { id: string; mode: ScanMode }) =>
      startScan({ directoryRuleId: id, mode }),
  });

  function selectRule(rule: DirectoryRule) {
    setSelectedId(rule.id);
    setForm({
      path: rule.path,
      recursive: rule.recursive,
      vision_model: rule.vision_model,
      summary_model: rule.summary_model,
      custom_analysis_prompt: rule.custom_analysis_prompt?.trim() || defaultAnalysisPrompt,
      background_context: rule.background_context ?? '',
      video_frame_strategy: rule.video_frame_strategy,
      frame_interval_seconds: rule.frame_interval_seconds,
      max_frames_per_video: rule.max_frames_per_video,
      analysis_detail: rule.analysis_detail,
      enabled: rule.enabled,
    });
  }

  useEffect(() => {
    if (!rulesQuery.data || settingsTargetKey === '|' || appliedSettingsTarget === settingsTargetKey) {
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
      setForm({ ...createDefaultPayload(defaultAnalysisPrompt), path: requestedPath });
    }
    setAppliedSettingsTarget(settingsTargetKey);
  }, [
    appliedSettingsTarget,
    defaultAnalysisPrompt,
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

  function submit(event: FormEvent) {
    event.preventDefault();
    saveMutation.mutate();
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
            setForm(createDefaultPayload(defaultAnalysisPrompt));
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
            {(rulesQuery.data ?? []).map((rule) => (
              <button
                key={rule.id}
                className={`mb-2 w-full rounded-md border p-3 text-left text-sm transition ${
                  selectedId === rule.id
                    ? 'border-accent bg-emerald-50'
                    : 'border-line bg-white hover:border-signal'
                }`}
                onClick={() => selectRule(rule)}
              >
                <div className="truncate font-medium">{rule.path}</div>
                <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                  <span>{rule.enabled ? '已启用' : '已停用'}</span>
                  <span>{rule.recursive ? '递归扫描' : '仅当前层'}</span>
                </div>
                <div className="mt-2 truncate text-xs text-slate-600">{rule.vision_model}</div>
              </button>
            ))}
            {rulesQuery.data?.length === 0 && (
              <div className="p-6 text-center text-sm text-slate-500">暂无目录规则</div>
            )}
          </div>
        </section>

        <form className="panel p-4" onSubmit={submit}>
          <div className="grid gap-4 lg:grid-cols-2">
            <label className="lg:col-span-2">
              <span className="mb-1 block text-sm font-medium">路径</span>
              <input
                className="control w-full"
                value={form.path}
                onChange={(event) => setForm({ ...form, path: event.target.value })}
                placeholder="D:/Photos"
                required
              />
            </label>

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
              onChange={(value) => setForm({ ...form, max_frames_per_video: value })}
            />
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.recursive}
                onChange={(event) => setForm({ ...form, recursive: event.target.checked })}
              />
              递归扫描
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
              />
              启用
            </label>
          </div>

          {modelsQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取 Ollama 模型列表，仍可手动输入模型名：{modelsQuery.error.message}
            </div>
          )}
          {defaultPromptQuery.error && (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
              无法读取默认提示词：{defaultPromptQuery.error.message}
            </div>
          )}
          {(saveMutation.error || deleteMutation.error || scanMutation.error) && (
            <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {(saveMutation.error ?? deleteMutation.error ?? scanMutation.error)?.message}
            </div>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            <button className="btn btn-primary" type="submit" disabled={saveMutation.isPending}>
              <Save className="h-4 w-4" />
              保存
            </button>
            {selected && (
              <>
                <button
                  className="btn"
                  type="button"
                  onClick={() => scanMutation.mutate({ id: selected.id, mode: 'incremental' })}
                  disabled={scanMutation.isPending}
                  title="只发现并处理这个目录下的新文件"
                >
                  <Play className="h-4 w-4" />
                  检测新文件
                </button>
                <button
                  className="btn"
                  type="button"
                  onClick={() => scanMutation.mutate({ id: selected.id, mode: 'full' })}
                  disabled={scanMutation.isPending}
                  title="重新生成这个目录下已扫描文件的元数据、AI 总结和 embedding"
                >
                  <RefreshCw className="h-4 w-4" />
                  全部重新生成
                </button>
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
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span className="mb-1 block text-sm font-medium">{label}</span>
      <input
        className="control w-full"
        type="number"
        min={1}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function normalizeDirectoryPath(path: string) {
  return path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}
