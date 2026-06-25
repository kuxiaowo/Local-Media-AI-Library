import { FormEvent, useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { RefreshCw, Save, Trash2 } from 'lucide-react';
import { getOllamaModels, getOllamaStatus } from '../api/models';
import {
  cleanupStaleMedia,
  getDefaultAnalysisPrompt,
  getRuntimeSettings,
  resetDefaultAnalysisPrompt,
  updateDefaultAnalysisPrompt,
  updateRuntimeSettings,
} from '../api/settings';
import type { RuntimeSettings } from '../types';

const emptyRuntimeSettings: RuntimeSettings = {
  default_embedding_model: 'nomic-embed-text',
  scan_worker_concurrency: 1,
  metadata_worker_concurrency: 6,
  vision_worker_concurrency: 1,
  embedding_worker_concurrency: 2,
};

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [runtimeForm, setRuntimeForm] = useState<RuntimeSettings>(emptyRuntimeSettings);
  const [defaultPromptForm, setDefaultPromptForm] = useState('');
  const statusQuery = useQuery({ queryKey: ['ollama-status'], queryFn: getOllamaStatus });
  const modelsQuery = useQuery({ queryKey: ['ollama-models'], queryFn: getOllamaModels });
  const runtimeQuery = useQuery({ queryKey: ['runtime-settings'], queryFn: getRuntimeSettings });
  const modelOptions = modelsQuery.data?.models ?? [];
  const defaultPromptQuery = useQuery({
    queryKey: ['default-analysis-prompt'],
    queryFn: getDefaultAnalysisPrompt,
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

  function submitRuntime(event: FormEvent) {
    event.preventDefault();
    saveRuntimeMutation.mutate(runtimeForm);
  }

  function submitDefaultPrompt(event: FormEvent) {
    event.preventDefault();
    saveDefaultPromptMutation.mutate({ prompt: defaultPromptForm });
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

      <form className="panel p-4" onSubmit={submitDefaultPrompt}>
        <div className="mb-3">
          <h2 className="text-base font-semibold">默认图片分析提示词</h2>
          <p className="text-sm text-slate-500">
            新建目录规则和未单独设置提示词的目录会使用这里的默认提示词。请保留 JSON 字段结构说明，否则模型更容易返回不可解析内容。
          </p>
        </div>

        <textarea
          className="control min-h-96 w-full resize-y font-mono text-xs leading-5"
          value={defaultPromptForm}
          onChange={(event) => setDefaultPromptForm(event.target.value)}
          required
        />

        {(defaultPromptQuery.error || saveDefaultPromptMutation.error || resetDefaultPromptMutation.error) && (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {
              (defaultPromptQuery.error ?? saveDefaultPromptMutation.error ?? resetDefaultPromptMutation.error)
                ?.message
            }
          </div>
        )}
        {saveDefaultPromptMutation.isSuccess && (
          <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
            默认提示词已保存。
          </div>
        )}
        {resetDefaultPromptMutation.isSuccess && (
          <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            已恢复内置默认提示词。
          </div>
        )}

        <div className="mt-5 flex flex-wrap gap-2">
          <button
            className="btn btn-primary"
            type="submit"
            disabled={saveDefaultPromptMutation.isPending}
          >
            <Save className="h-4 w-4" />
            保存默认提示词
          </button>
          <button
            className="btn"
            type="button"
            disabled={resetDefaultPromptMutation.isPending}
            onClick={() => resetDefaultPromptMutation.mutate()}
            title="恢复代码内置的完整 JSON 结构提示词"
          >
            <RefreshCw className="h-4 w-4" />
            恢复内置默认
          </button>
        </div>
      </form>

      <form className="panel p-4" onSubmit={submitRuntime}>
        <div className="mb-3">
          <h2 className="text-base font-semibold">运行设置</h2>
          <p className="text-sm text-slate-500">
            Embedding 模型用于生成和搜索向量；后台并发保存后会立即重载 worker 池，正在运行中的单张照片任务会先跑完。
          </p>
        </div>
        <div className="mb-4 max-w-xl">
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
            已保存，并已重载后台并发设置。
          </div>
        )}

        <button className="btn btn-primary mt-5" type="submit" disabled={saveRuntimeMutation.isPending}>
          <Save className="h-4 w-4" />
          保存并发设置
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
