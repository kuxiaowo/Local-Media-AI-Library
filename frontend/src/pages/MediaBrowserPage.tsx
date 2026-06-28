import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { ChevronDown, ChevronLeft, ChevronRight, Folder, FolderCog, FolderOpen, RefreshCw } from 'lucide-react';
import { listMedia, listMediaDirectories } from '../api/media';
import { MediaGrid } from '../components/MediaGrid';
import type { MediaDirectory } from '../types';

const pageSize = 60;
const mediaTypeValues = new Set(['any', 'image', 'video']);
const mediaStatusValues = new Set([
  'any',
  'pending',
  'metadata_done',
  'analyzing',
  'embedding_pending',
  'done',
  'failed',
  'missing',
  'needs_reanalysis',
]);

type DirectoryTreeNode = MediaDirectory & {
  children: DirectoryTreeNode[];
  parentPath: string | null;
  parentDisplayPath: string | null;
  totalMediaCount: number;
};

export function MediaBrowserPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = readPageParam(searchParams.get('page'));
  const mediaType = readFilterParam(searchParams.get('media_type'), mediaTypeValues);
  const status = readFilterParam(searchParams.get('status'), mediaStatusValues);
  const selectedDirectoryPath = searchParams.get('directory_path') || null;
  const detailReturnState = useMemo(
    () => ({
      returnTo: `${location.pathname}${location.search}`,
      returnLabel: '返回媒体库',
    }),
    [location.pathname, location.search],
  );

  const directoriesQuery = useQuery({
    queryKey: ['media-directories'],
    queryFn: listMediaDirectories,
  });
  const directoryTree = useMemo(
    () => buildDirectoryTree(directoriesQuery.data ?? []),
    [directoriesQuery.data],
  );
  const selectedDirectory = useMemo(
    () => findDirectoryNode(directoryTree, selectedDirectoryPath),
    [directoryTree, selectedDirectoryPath],
  );
  const [collapsedPaths, setCollapsedPaths] = useState<Set<string>>(() => new Set());
  const knownCollapsiblePaths = useRef<Set<string>>(new Set());
  const allDirectoryMediaCount = useMemo(
    () => directoryTree.reduce((total, node) => total + node.totalMediaCount, 0),
    [directoryTree],
  );

  useEffect(() => {
    const collapsiblePaths = collectCollapsibleDirectoryPaths(directoryTree);
    const newPaths = collapsiblePaths.filter((path) => !knownCollapsiblePaths.current.has(path));
    if (newPaths.length === 0) {
      return;
    }
    for (const path of newPaths) {
      knownCollapsiblePaths.current.add(path);
    }
    setCollapsedPaths((current) => {
      const next = new Set(current);
      for (const path of newPaths) {
        next.add(path);
      }
      return next;
    });
  }, [directoryTree]);

  const query = useQuery({
    queryKey: ['media', page, mediaType, status, selectedDirectoryPath],
    queryFn: () =>
      listMedia({
        offset: page * pageSize,
        limit: pageSize,
        mediaType,
        status,
        directoryPath: selectedDirectoryPath,
      }),
  });
  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((query.data?.total ?? 0) / pageSize)),
    [query.data?.total],
  );

  function selectDirectory(path: string | null) {
    updateBrowserState({ page: 0, directoryPath: path });
  }

  function toggleDirectoryCollapsed(path: string) {
    setCollapsedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  function updateBrowserState(nextState: {
    page?: number;
    mediaType?: string;
    status?: string;
    directoryPath?: string | null;
  }) {
    const nextPage = Math.max(0, nextState.page ?? page);
    const nextMediaType = nextState.mediaType ?? mediaType;
    const nextStatus = nextState.status ?? status;
    const nextDirectoryPath =
      nextState.directoryPath !== undefined ? nextState.directoryPath : selectedDirectoryPath;
    const nextParams = new URLSearchParams();

    if (nextPage > 0) {
      nextParams.set('page', String(nextPage + 1));
    }
    if (nextMediaType !== 'any') {
      nextParams.set('media_type', nextMediaType);
    }
    if (nextStatus !== 'any') {
      nextParams.set('status', nextStatus);
    }
    if (nextDirectoryPath) {
      nextParams.set('directory_path', nextDirectoryPath);
    }

    setSearchParams(nextParams, { replace: true });
  }

  function openDirectorySettings() {
    if (!selectedDirectory) {
      navigate('/library');
      return;
    }
    const params = new URLSearchParams({
      path: selectedDirectory.display_path,
      normalized_path: selectedDirectory.path,
    });
    navigate(`/library?${params}`);
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">媒体浏览</h1>
          <p className="text-sm text-slate-500">按目录、类型和状态查看缩略图与 AI 摘要</p>
        </div>
        <div className="flex gap-2">
          <button
            className="btn"
            title={selectedDirectory ? '新建或调整当前目录设置' : '打开目录设置'}
            onClick={openDirectorySettings}
          >
            <FolderCog className="h-4 w-4" />
            目录设置
          </button>
          <button
            className="icon-btn"
            title="刷新"
            onClick={() => {
              directoriesQuery.refetch();
              query.refetch();
            }}
          >
            <RefreshCw className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="panel overflow-hidden">
          <div className="border-b border-line px-4 py-3">
            <div className="text-sm font-semibold">目录</div>
            <div className="mt-1 text-xs text-slate-500">已记录的媒体目录</div>
          </div>
          <div className="max-h-[calc(100vh-220px)] overflow-auto p-2">
            <button
              className={`mb-1 flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition ${
                selectedDirectoryPath === null
                  ? 'bg-emerald-50 text-signal'
                  : 'text-slate-700 hover:bg-slate-50'
              }`}
              onClick={() => selectDirectory(null)}
              title="显示全部目录"
            >
              <FolderOpen className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">全部目录</span>
              <span className="text-xs tabular-nums text-slate-500">{allDirectoryMediaCount}</span>
            </button>

            {directoriesQuery.error && (
              <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                {directoriesQuery.error.message}
              </div>
            )}
            {!directoriesQuery.error && directoryTree.length === 0 && (
              <div className="p-4 text-sm text-slate-500">暂无已记录目录</div>
            )}
            <DirectoryTree
              nodes={directoryTree}
              selectedPath={selectedDirectoryPath}
              collapsedPaths={collapsedPaths}
              onSelect={selectDirectory}
              onToggleCollapsed={toggleDirectoryCollapsed}
            />
          </div>
        </aside>

        <main className="min-w-0 space-y-4">
          <section className="panel flex flex-wrap items-center gap-3 p-3">
            <select
              className="control"
              value={mediaType}
              onChange={(event) => {
                updateBrowserState({ page: 0, mediaType: event.target.value });
              }}
            >
              <option value="any">全部类型</option>
              <option value="image">图片</option>
              <option value="video">视频</option>
            </select>
            <select
              className="control"
              value={status}
              onChange={(event) => {
                updateBrowserState({ page: 0, status: event.target.value });
              }}
            >
              <option value="any">全部状态</option>
              <option value="pending">pending</option>
              <option value="metadata_done">metadata_done</option>
              <option value="analyzing">analyzing</option>
              <option value="embedding_pending">embedding_pending</option>
              <option value="done">done</option>
              <option value="failed">failed</option>
              <option value="missing">missing</option>
              <option value="needs_reanalysis">needs_reanalysis</option>
            </select>
            <div className="min-w-0 flex-1 truncate text-sm text-slate-500">
              {selectedDirectory ? selectedDirectory.display_path : '全部目录'}
            </div>
            <div className="text-sm text-slate-500">共 {query.data?.total ?? 0} 项</div>
          </section>

          {query.error && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {query.error.message}
            </div>
          )}
          <MediaGrid items={query.data?.items ?? []} detailReturnState={detailReturnState} />

          <div className="flex items-center justify-end gap-2">
            <button className="btn" disabled={page === 0} onClick={() => updateBrowserState({ page: page - 1 })}>
              <ChevronLeft className="h-4 w-4" />
              上一页
            </button>
            <span className="text-sm text-slate-600">
              {page + 1} / {totalPages}
            </span>
            <button
              className="btn"
              disabled={page + 1 >= totalPages}
              onClick={() => updateBrowserState({ page: page + 1 })}
            >
              下一页
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}

function DirectoryTree({
  nodes,
  selectedPath,
  collapsedPaths,
  onSelect,
  onToggleCollapsed,
}: {
  nodes: DirectoryTreeNode[];
  selectedPath: string | null;
  collapsedPaths: Set<string>;
  onSelect: (path: string | null) => void;
  onToggleCollapsed: (path: string) => void;
}) {
  return (
    <div className="space-y-1">
      {nodes.map((node) => (
        <DirectoryTreeItem
          key={node.path}
          node={node}
          level={0}
          selectedPath={selectedPath}
          collapsedPaths={collapsedPaths}
          onSelect={onSelect}
          onToggleCollapsed={onToggleCollapsed}
        />
      ))}
    </div>
  );
}

function DirectoryTreeItem({
  node,
  level,
  selectedPath,
  collapsedPaths,
  onSelect,
  onToggleCollapsed,
}: {
  node: DirectoryTreeNode;
  level: number;
  selectedPath: string | null;
  collapsedPaths: Set<string>;
  onSelect: (path: string | null) => void;
  onToggleCollapsed: (path: string) => void;
}) {
  const selected = selectedPath === node.path;
  const hasChildren = node.children.length > 0;
  const collapsed = collapsedPaths.has(node.path);
  const Icon = selected || (hasChildren && !collapsed) ? FolderOpen : Folder;
  const label = directoryLabel(node);

  return (
    <div>
      <div
        className={`flex w-full items-center rounded-md text-sm transition ${
          selected ? 'bg-emerald-50 text-signal' : 'text-slate-700 hover:bg-slate-50'
        }`}
        style={{ paddingLeft: `${level * 16 + 4}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="flex h-8 w-6 shrink-0 items-center justify-center rounded text-slate-500 hover:text-signal"
            aria-label={collapsed ? '展开目录' : '折叠目录'}
            aria-expanded={!collapsed}
            title={collapsed ? '展开目录' : '折叠目录'}
            onClick={() => onToggleCollapsed(node.path)}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
        ) : (
          <span className="h-8 w-6 shrink-0" />
        )}
        <button
          className="flex min-w-0 flex-1 items-center gap-2 py-2 pr-2 text-left"
          type="button"
          onClick={() => onSelect(node.path)}
          title={node.display_path}
        >
          <Icon className="h-4 w-4 shrink-0" />
          <span className="min-w-0 flex-1 truncate">{label}</span>
          <span className="text-xs tabular-nums text-slate-500">{node.totalMediaCount}</span>
        </button>
      </div>
      {!collapsed && node.children.map((child) => (
        <DirectoryTreeItem
          key={child.path}
          node={child}
          level={level + 1}
          selectedPath={selectedPath}
          collapsedPaths={collapsedPaths}
          onSelect={onSelect}
          onToggleCollapsed={onToggleCollapsed}
        />
      ))}
    </div>
  );
}

function buildDirectoryTree(directories: MediaDirectory[]): DirectoryTreeNode[] {
  const nodes = new Map<string, DirectoryTreeNode>();

  for (const directory of directories) {
    const path = normalizeDirectoryPath(directory.path);
    if (!path) {
      continue;
    }
    const existing = nodes.get(path);
    if (existing) {
      existing.direct_media_count += directory.direct_media_count;
      existing.totalMediaCount += directory.direct_media_count;
      continue;
    }
    nodes.set(path, {
      ...directory,
      path,
      children: [],
      parentPath: null,
      parentDisplayPath: null,
      totalMediaCount: directory.direct_media_count,
    });
  }

  const sorted = Array.from(nodes.values()).sort(compareDirectoryNodes);
  const roots: DirectoryTreeNode[] = [];

  for (const node of sorted) {
    const parent = findLongestParent(node, sorted);
    if (parent) {
      node.parentPath = parent.path;
      node.parentDisplayPath = parent.display_path;
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  for (const root of roots) {
    calculateTotalMediaCount(root);
    sortChildren(root);
  }

  return roots.sort(compareDirectoryNodes);
}

function findLongestParent(
  node: DirectoryTreeNode,
  candidates: DirectoryTreeNode[],
): DirectoryTreeNode | null {
  let parent: DirectoryTreeNode | null = null;
  for (const candidate of candidates) {
    if (candidate.path === node.path || candidate.path.length >= node.path.length) {
      continue;
    }
    if (directoryHasPrefix(node.path, candidate.path) && (!parent || candidate.path.length > parent.path.length)) {
      parent = candidate;
    }
  }
  return parent;
}

function calculateTotalMediaCount(node: DirectoryTreeNode): number {
  node.totalMediaCount =
    node.direct_media_count +
    node.children.reduce((total, child) => total + calculateTotalMediaCount(child), 0);
  return node.totalMediaCount;
}

function sortChildren(node: DirectoryTreeNode) {
  node.children.sort(compareDirectoryNodes);
  for (const child of node.children) {
    sortChildren(child);
  }
}

function compareDirectoryNodes(left: DirectoryTreeNode, right: DirectoryTreeNode) {
  return left.display_path.localeCompare(right.display_path, 'zh-Hans-CN', {
    numeric: true,
    sensitivity: 'base',
  });
}

function findDirectoryNode(nodes: DirectoryTreeNode[], path: string | null): DirectoryTreeNode | null {
  if (!path) {
    return null;
  }
  for (const node of nodes) {
    if (node.path === path) {
      return node;
    }
    const child = findDirectoryNode(node.children, path);
    if (child) {
      return child;
    }
  }
  return null;
}

function collectCollapsibleDirectoryPaths(nodes: DirectoryTreeNode[]): string[] {
  const paths: string[] = [];
  for (const node of nodes) {
    if (node.children.length > 0) {
      paths.push(node.path);
    }
    paths.push(...collectCollapsibleDirectoryPaths(node.children));
  }
  return paths;
}

function directoryLabel(node: DirectoryTreeNode) {
  if (!node.parentPath) {
    return node.display_path;
  }
  if (node.parentDisplayPath) {
    const displayPath = node.display_path.replace(/\\/g, '/').replace(/\/+$/, '');
    const parentDisplayPath = node.parentDisplayPath.replace(/\\/g, '/').replace(/\/+$/, '');
    if (normalizeDirectoryPath(displayPath).startsWith(`${normalizeDirectoryPath(parentDisplayPath)}/`)) {
      return displayPath.slice(parentDisplayPath.length + 1) || node.name || node.display_path;
    }
  }
  const relative = node.path.slice(node.parentPath.length + 1);
  return relative || node.name || node.display_path;
}

function directoryHasPrefix(path: string, prefix: string) {
  return path === prefix || path.startsWith(`${prefix}/`);
}

function normalizeDirectoryPath(path: string) {
  return path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

function readPageParam(value: string | null) {
  if (!value) {
    return 0;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) {
    return 0;
  }
  return Math.floor(parsed) - 1;
}

function readFilterParam(value: string | null, allowedValues: Set<string>) {
  if (!value || !allowedValues.has(value)) {
    return 'any';
  }
  return value;
}
