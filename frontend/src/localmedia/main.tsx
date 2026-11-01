import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Aperture,
  Search,
  FolderPlus,
  Folder,
  RefreshCw,
  Settings,
  X,
  Play,
  Pause,
  Square,
  Sparkles,
  Images,
  Image,
  Film,
  ChevronLeft,
  ChevronRight,
  Maximize2,
  SlidersHorizontal,
  Check,
  Activity,
  HardDrive,
  AlertCircle,
  Trash2,
  EyeOff,
  Power,
  Menu,
} from "lucide-react";
import "./style.css";
import { useGamepad } from "./useGamepad";
import { localDateBoundary, settingsPatch, previewUrl } from "./uiLogic";

type Media = {
  title?: string;
  id: string;
  name: string;
  kind: string;
  sort_time: number;
  preview_state: string;
  thumbnail_state?: string;
  preview_version?: string;
  analysis_state: string;
  analysis_error?: string | null;
  vector_state: string;
  availability: string;
  error?: string;
  score?: number;
  path?: string;
  size?: number;
  note?: string;
  description?: string;
  metadata?: Record<string, unknown>;
  batches?: { start: number; end: number; description: string }[];
  frames?: { id: number; time: number; end_time?: number; kind: string }[];
  frame_generations?: { count: number; retained: number };
};
type Root = {
  id: string;
  parent_id?: string|null;
  path: string;
  background: string;
  state: string;
  count: number;
  total_count?: number;
  enabled: boolean;
  effective_enabled: boolean;
  visual_enhancement: boolean;
  error?: string;
};
type Config = Record<string, string | number | boolean>;
type Run = {
  id: string;
  state: string;
  reason?: string;
  created: number;
  kind: "analysis" | "frames";
  counts: { state: string; count: number }[];
  phases: { extracting: number; ai: number };
};
const labels: Record<string, string> = {
  online: "在线",
  offline: "不可访问",
  pending: "待处理",
  scanning: "扫描中",
  ready: "已就绪",
  stale: "待更新",
  done: "已分析",
  missing: "失联",
  queued: "排队中",
  running: "处理中",
  pausing: "暂停中",
  paused: "已暂停",
  failed: "失败",
  completed: "完成",
  manual: "手动暂停",
  interrupt: "已停止",
  time_budget: "视频耗时达到上限",
  restart: "重启后等待恢复",
  source_changed: "媒体内容改变",
  weekly_quota: "周额度不足",
  five_hour_quota: "5 小时额度不足",
  unknown: "额度状态未知",
  protected: "额度保护已触发",
  disabled: "已禁用",
  unavailable: "等待标准预览",
};
const label = (s: string) => labels[s] || s;
async function api<T = any>(
  url: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const response = await fetch("/api" + url, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string" ? result.detail : "请求参数不正确",
    );
  return result;
}
const date = (seconds: number) =>
  new Date(seconds * 1000).toLocaleDateString("zh-CN");
const shortPath = (path: string) =>
  path.split(/[\\/]/).filter(Boolean).pop() || path;

function DirectoryInput({initial='',title}:{initial?:string;title:string}) {
  const [path,setPath]=useState(initial),[busy,setBusy]=useState(false),[error,setError]=useState('');
  return <div><label>{title}<div style={{display:'flex',gap:8}}>
    <input name="path" required value={path} placeholder="D:\照片\2026" onChange={e=>setPath(e.target.value)} />
    <button type="button" disabled={busy} style={{whiteSpace:'nowrap'}} onClick={async()=>{
      setBusy(true);setError('');
      try{const result=await api<{path:string|null}>('/directories/pick',{text:path});if(result.path)setPath(result.path);}
      catch(e){setError((e as Error).message);}finally{setBusy(false);}
    }}>{busy?'等待选择…':'浏览…'}</button>
  </div></label>{error&&<p role="alert">{error}</p>}</div>;
}

function App() {
  const [gamepadIndex, setGamepadIndex] = useState<number | null>(null);
  const [gamepadArea, setGamepadArea] = useState<"media" | "directories">("media");
  const gamepadDirectory = useRef<string>("all");
  const viewerVideo = useRef<HTMLVideoElement>(null);
  const [playbackReady, setPlaybackReady] = useState<string>("");
  const [originalPlayback, setOriginalPlayback] = useState(false);
  const [playbackStatus, setPlaybackStatus] = useState("");
  const playbackPosition = useRef<{time: number; paused: boolean} | null>(null);
  const [removingPaths,setRemovingPaths]=useState<string[]>([]);
  const isRemoving=(path?:string)=>!!path&&removingPaths.some(p=>path===p||path.startsWith(p.replace(/[\\/]+$/,'')+'\\'));
  const [collapsedRoots,setCollapsedRoots]=useState<Set<string>>(new Set());
  const [roots, setRoots] = useState<Root[]>([]),
    [root, setRoot] = useState(""),
    [parent, setParent] = useState("");
  const [folders, setFolders] = useState<{ path: string; parent_path:string|null; root_id:string; count: number }[]>(
    [],
  );
  const [missing, setMissing] = useState(false),
    [kind, setKind] = useState(""),
    [status, setStatus] = useState("");
  const [after, setAfter] = useState(""),
    [before, setBefore] = useState(""),
    [query, setQuery] = useState(""),
    [search, setSearch] = useState(""),
    [semantic, setSemantic] = useState(false);
  const [total, setTotal] = useState(0),
    [pages, setPages] = useState<Record<number, Media[]>>({}),
    [selected, setSelected] = useState<Set<string>>(new Set());
  const [detail, setDetail] = useState<Media | null>(null),
    [viewer, setViewer] = useState(false),
    [zoom, setZoom] = useState(1);
  const [imageFallback, setImageFallback] = useState(false);
  const previewClient = useRef(`${Date.now()}-${Math.random().toString(36).slice(2)}`);
  useEffect(() => { setImageFallback(false); }, [detail?.id]);
  useEffect(() => {
    if (!detail || detail.preview_state !== 'pending') return;
    let active = true;
    const timer = setInterval(() => {
      void api<Media>('/media/' + detail.id).then(row => {
        if (active) setDetail(previous => previous?.id === row.id ? row : previous);
      }).catch(() => {});
    }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [detail?.id, detail?.preview_state]);
  const [settings, setSettings] = useState(false),
    [sidebarOpen, setSidebarOpen] = useState(false),
    [panel, setPanel] = useState(false),
    [add, setAdd] = useState(false),
    [rootEdit, setRootEdit] = useState<Root | null>(null);
  useEffect(() => {
    setPlaybackReady("");
    setOriginalPlayback(false);
    setPlaybackStatus("");
    playbackPosition.current = null;
    if (!viewer || detail?.kind !== "video") return;
    const id = detail.id;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const prepare = async () => {
      try {
        const result = await api<{state:string; encoder?:string}>("/media/" + id + "/playback", {});
        if (cancelled) return;
        if (result.state === "ready") {
          const video = viewerVideo.current;
          playbackPosition.current = video ? {time:video.currentTime, paused:video.paused} : null;
          setPlaybackReady(id);
          setPlaybackStatus("流畅播放 · 1080p H.264");
        } else if (result.state === "failed") {
          setPlaybackStatus("使用原视频播放");
        } else {
          setPlaybackStatus("正在准备流畅播放副本…");
          timer = setTimeout(() => void prepare(), 2000);
        }
      } catch {
        if (!cancelled) setPlaybackStatus("使用原视频播放");
      }
    };
    void prepare();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [viewer, detail?.id, detail?.kind]);
  const [config, setConfig] = useState<Config>({}),
    [runs, setRuns] = useState<Run[]>([]),
    [quota, setQuota] = useState("unknown"),
    [vectors, setVectors] = useState<{ state: string; count: number }[]>([]);
  const [previewCounts, setPreviewCounts] = useState<
    { state: string; count: number }[]
  >([]);
  const [syncRoots, setSyncRoots] = useState<Root[]>([]);
  const [thumbnailCounts, setThumbnailCounts] = useState<{state:string;count:number}[]>([]);
  const [previewWorkers, setPreviewWorkers] = useState({image:0,video:0});
  const importing = syncRoots.some(r => r.state === 'scanning' || r.state === 'pending') || previewCounts.some(c => c.state === 'pending' && c.count > 0);
  const [notice, setNotice] = useState(""),
    [error, setError] = useState(""),
    [version, setVersion] = useState(0),
    [loading, setLoading] = useState(false);
  const grid = useRef<HTMLDivElement>(null),
    [width, setWidth] = useState(900);
  const generation = useRef(0),
    inflight = useRef(new Set<number>());
  const activeRoots = roots.filter((entry) => entry.effective_enabled);
  const disabledRoots = roots.filter((entry) => !entry.enabled);
  const columns = Math.max(2, Math.floor(width / 205));
  const rowHeight = Math.floor(((width - 32) / columns) * 0.78) + 52;
  const virtual = useVirtualizer({
    count: Math.ceil(total / columns),
    getScrollElement: () => grid.current,
    estimateSize: () => rowHeight,
    overscan: 3,
  });
  const params = new URLSearchParams();
  if (root) params.set("root_id", root);
  if (parent) params.set("parent", parent);
  if (kind) params.set("kind", kind);
  if (status) params.set("status", status);
  if (missing) params.set("missing", "true");
  if (search) params.set("q", search);
  if (after) params.set("after", String(localDateBoundary(after)));
  if (before)
    params.set("before", String(localDateBoundary(before, true)));
  const filterKey = params.toString();
  const act = async (fn: () => Promise<unknown>, message?: string) => {
    try {
      setError("");
      await fn();
      if (message) setNotice(message);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const refresh = () => setVersion((v) => v + 1);
  const refreshStatus = async () => {
    const [rs, ts] = await Promise.all([api<Root[]>("/roots"), api("/runs")]);
    setRoots(rs);
    setRuns(ts.runs);
    setQuota(ts.quota.state);
    setVectors(ts.vectors);
    setPreviewCounts(ts.previews || []);
    setThumbnailCounts(ts.thumbnails || []);
    setPreviewWorkers(ts.preview_workers || {image:0,video:0});
    setSyncRoots(ts.sync || []);
  };
  useEffect(() => {
    void act(async () => {
      setConfig(await api("/settings"));
      await refreshStatus();
    });
  }, []);
  useEffect(() => {
    const timer = setInterval(() => { if (!viewer) void refreshStatus().catch(() => {}); }, 3000);
    return () => clearInterval(timer);
  }, [viewer]);
  useEffect(() => {
    void api<typeof folders>("/folder-tree").then(setFolders).catch(() => {});
  }, [version, JSON.stringify(roots)]);
  useEffect(() => {
    const obs = new ResizeObserver((entries) =>
      setWidth(entries[0].contentRect.width),
    );
    if (grid.current) obs.observe(grid.current);
    return () => obs.disconnect();
  }, []);
  useEffect(() => {
    virtual.measure();
  }, [rowHeight]);
  const load = async (page: number, gen: number) => {
    if (inflight.current.has(page)) return;
    inflight.current.add(page);
    try {
      const data = await api<{ items: Media[]; total: number }>(
        "/media?" + filterKey + "&limit=100&offset=" + page * 100,
      );
      if (gen === generation.current) {
        setTotal(data.total);
        setPages((old) => {
          const next = { ...old, [page]: data.items };
          Object.keys(next).forEach((k) => {
            if (Math.abs(Number(k) - page) > 5) delete next[Number(k)];
          });
          return next;
        });
      }
    } catch (e) {
      if (gen === generation.current) setError((e as Error).message);
    } finally {
      if (gen === generation.current) inflight.current.delete(page);
    }
  };
  useEffect(() => {
    const gen = ++generation.current;
    inflight.current.clear();
    setPages({});
    setTotal(0);
    setSelected(new Set());
    setGamepadIndex(null);
    setLoading(true);
    grid.current?.scrollTo(0, 0);
    if (semantic && search && !missing) {
      void api<{ items: Media[]; total: number }>("/search?" + filterKey)
        .then((d) => {
          if (gen === generation.current) {
            setPages({ 0: d.items.slice(0, 100), 1: d.items.slice(100) });
            setTotal(d.total);
          }
        })
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    } else void load(0, gen).finally(() => setLoading(false));
  }, [filterKey, semantic]);
  useEffect(() => {
    if (!version) return;
    if (semantic && search && !missing) {
      const gen = generation.current;
      void api<{ items: Media[]; total: number }>("/search?" + filterKey)
        .then((data) => {
          if (gen === generation.current) {
            setPages({ 0: data.items.slice(0, 100), 1: data.items.slice(100) });
            setTotal(data.total);
          }
        })
        .catch((cause) => setError((cause as Error).message));
      return;
    }
    const firstRow = Math.floor((grid.current?.scrollTop || 0) / rowHeight);
    const page = Math.floor((firstRow * columns) / 100);
    void load(page, generation.current);
  }, [version]);
  useEffect(() => {
    if (!sidebarOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [sidebarOpen]);
  const visible = virtual.getVirtualItems();
  const viewportIds = visible
    .filter(row => row.end > (grid.current?.scrollTop || 0) && row.start < (grid.current?.scrollTop || 0) + (grid.current?.clientHeight || 0))
    .flatMap(row => Array.from({length:columns}, (_, col) => {
      const index = row.index * columns + col;
      return pages[Math.floor(index / 100)]?.[index % 100]?.id;
    })).filter((id): id is string => !!id).slice(0, 160);
  const viewportKey = viewportIds.join(',');
  useEffect(() => {
    const send = () => void api('/previews/priority', {
      client:previewClient.current, ids:viewer ? [] : viewportIds, detail_id:detail?.id || null,
    }).catch(() => {});
    const delay = setTimeout(send, 120);
    const heartbeat = setInterval(send, 3000);
    return () => { clearTimeout(delay); clearInterval(heartbeat); };
  }, [viewportKey, detail?.id, viewer]);
  useEffect(() => {
    if (semantic && search && !missing) return;
    for (const row of visible) {
      for (
        let i = row.index * columns;
        i < Math.min(total, (row.index + 1) * columns);
        i++
      ) {
        const page = Math.floor(i / 100);
        if (!pages[page]) void load(page, generation.current);
      }
    }
  }, [visible.map((r) => r.index).join(","), pages]);
  useEffect(() => {
    if (viewer || (semantic && search && !missing)) return;
    const timer = setInterval(() => {
      const page = Math.floor(((visible[0]?.index || 0) * columns) / 100);
      void load(page, generation.current);
    }, importing ? 1000 : 3000);
    return () => clearInterval(timer);
  }, [filterKey, semantic, visible[0]?.index, columns, viewer, importing]);
  useEffect(() => {
    const count = activeRoots.reduce((n, r) => n + r.count, 0);
    if (!search && count !== total && !root && !missing) refresh();
  }, [activeRoots.reduce((n, r) => n + r.count, 0)]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 4500);
    return () => clearTimeout(timer);
  }, [notice]);
  const choose = (id: string, open = false) =>
    void act(async () => {
      setDetail(await api<Media>("/media/" + id));
      if (open) {
        setZoom(1);
        setViewer(true);
      }
    });
  const selectedRoot = activeRoots.find((r) => r.id === root);
  const selectedDirectoryPath = parent || selectedRoot?.path || "";
  const selectedDirectoryRoot = activeRoots.find(
    (entry) => entry.path === selectedDirectoryPath,
  );
  const items = Object.values(pages).flat();
  const next = (delta: number) => {
    const i = items.findIndex((m) => m.id === detail?.id);
    if (items[i + delta]) {
      choose(items[i + delta].id);
      setZoom(1);
    } else if (!(semantic && search)) {
      const pageEntry = Object.entries(pages).find(([, values]) =>
        values.some((m) => m.id === detail?.id),
      );
      if (!pageEntry) return;
      const target =
        Number(pageEntry[0]) * 100 +
        pageEntry[1].findIndex((m) => m.id === detail?.id) +
        delta;
      if (target < 0 || target >= total) return;
      void act(async () => {
        const data = await api<{ items: Media[] }>(
          "/media?" +
            filterKey +
            "&limit=100&offset=" +
            Math.floor(target / 100) * 100,
        );
        setPages((old) => ({ ...old, [Math.floor(target / 100)]: data.items }));
        if (data.items[target % 100]) choose(data.items[target % 100].id);
        setZoom(1);
      });
    }
  };
  useEffect(() => {
    if (!viewer) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setViewer(false);
      if (e.key === "ArrowLeft") next(-1);
      if (e.key === "ArrowRight") next(1);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [viewer, detail, items]);
  const start = async (force = false, mediaId?: string) => {
    const currentConfig = await api<Config>("/settings");
    setConfig(currentConfig);
    const activeProvider = currentConfig.active_profile
      ? JSON.parse(String(currentConfig.active_profile))[0]
      : currentConfig.embedding_provider;
    const cloud =
      currentConfig.provider === "codex" ||
      currentConfig.provider === "openai" ||
      activeProvider === "openai";
    if (
      force &&
      !window.confirm("重新分析成功后将覆盖已有描述，包括手动修改。继续？")
    )
      return;
    if (
      cloud &&
      !window.confirm(
        "云端服务会接收分析预览、视频抽帧、原始文件名、文件所在目录、目录背景和媒体备注；向量服务会接收描述文字。确认启动这一批？",
      )
    )
      return;
    await act(async () => {
      const created = await api("/runs", {
        ids: mediaId ? [mediaId] : selected.size ? [...selected] : null,
        root_id: root || null,
        parent: parent || null,
        force,
        cloud_ack: cloud,
      });
      if (!created.count)
        throw new Error(
          "没有可加入的新任务：媒体可能已有未结束任务，或已完成分析。请在任务中心恢复已有任务。",
        );
      setPanel(true);
      await refreshStatus();
    }, "已固定本次目标集合，开始排队");
  };
  const gamepadConnected = useGamepad((action) => {
    if (action === "back") {
      if (viewer) setViewer(false);
      else if (settings) setSettings(false);
      else if (add) setAdd(false);
      else if (rootEdit) setRootEdit(null);
      else if (panel) setPanel(false);
      else if (gamepadArea === "directories" || sidebarOpen) {
        setGamepadArea("media");
        setSidebarOpen(false);
        if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      }
      return;
    }
    if (settings || add || rootEdit || panel) return;
    const focused = document.activeElement;
    if (focused instanceof HTMLElement && (focused.matches("input, textarea, select") || focused.isContentEditable)) return;
    const directoryButtons = () => Array.from(document.querySelectorAll<HTMLButtonElement>("#library-sidebar [data-gamepad-directory]"));
    const focusDirectory = (button?: HTMLButtonElement) => {
      if (!button) return;
      gamepadDirectory.current = button.dataset.gamepadDirectory || "all";
      button.focus({ preventScroll: true });
      button.scrollIntoView({ block: "nearest", inline: "nearest" });
    };
    if (action === "directories") {
      if (viewer) return;
      if (gamepadArea === "directories") {
        setGamepadArea("media");
        setSidebarOpen(false);
        if (focused instanceof HTMLElement) focused.blur();
      } else {
        setGamepadArea("directories");
        setSidebarOpen(true);
        const buttons = directoryButtons();
        focusDirectory(buttons.find(button => button.dataset.gamepadDirectory === gamepadDirectory.current) || buttons[0]);
      }
      return;
    }
    if (gamepadArea === "directories") {
      const buttons = directoryButtons();
      const index = Math.max(0, buttons.findIndex(button => button.dataset.gamepadDirectory === gamepadDirectory.current));
      const button = buttons[index];
      if (action === "up" || action === "down") {
        focusDirectory(buttons[Math.max(0, Math.min(buttons.length - 1, index + (action === "up" ? -1 : 1)))]);
      } else if (action === "open") {
        button?.click();
        setGamepadArea("media");
        button?.blur();
      } else if (action === "left" || action === "right") {
        const toggle = button?.previousElementSibling;
        if (toggle instanceof HTMLButtonElement && toggle.style.visibility !== "hidden" && toggle.getAttribute("aria-expanded") === (action === "left" ? "true" : "false")) toggle.click();
        focusDirectory(button);
      }
      return;
    }
    if (sidebarOpen) return;
    if (viewer) {
      if (detail?.kind === "video" && (action === "previous" || action === "next")) {
        const video = viewerVideo.current;
        if (video && video.readyState >= 1 && Number.isFinite(video.duration)) {
          video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + (action === "next" ? 30 : -15)));
        }
        return;
      }
      if (action === "left" || action === "previous") next(-1);
      if (action === "right" || action === "next") next(1);
      return;
    }
    if (!total) return;
    if (action === "open") {
      const index = gamepadIndex ?? 0;
      const media = pages[Math.floor(index / 100)]?.[index % 100];
      if (media && !isRemoving(media.path)) choose(media.id, true);
      return;
    }
    const delta = { left: -1, right: 1, up: -columns, down: columns, previous: -1, next: 1 }[action];
    const firstVisible = Math.floor((grid.current?.scrollTop || 0) / rowHeight) * columns;
    const index = gamepadIndex === null ? Math.min(total - 1, firstVisible) : Math.max(0, Math.min(total - 1, gamepadIndex + delta));
    setGamepadIndex(index);
    virtual.scrollToIndex(Math.floor(index / columns), { align: "auto" });
  });
  useEffect(() => {
    if (gamepadArea !== "directories") return;
    if (!sidebarOpen) {
      setGamepadArea("media");
      return;
    }
    if (!gamepadConnected) return;
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>("#library-sidebar [data-gamepad-directory]"));
    const button = buttons.find(entry => entry.dataset.gamepadDirectory === gamepadDirectory.current) || buttons[0];
    if (button) {
      gamepadDirectory.current = button.dataset.gamepadDirectory || "all";
      button.focus({ preventScroll: true });
      button.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [gamepadArea, sidebarOpen, gamepadConnected, folders, collapsedRoots]);
  const startFrames = async () => {
    await act(async () => {
      const created = await api("/runs", {
        ids: selected.size ? [...selected] : null,
        root_id: root || null,
        parent: parent || null,
        operation: "frames",
      });
      if (!created.count)
        throw new Error(
          "没有需要继续抽帧的视频：当前范围可能只有图片、视频已经完整抽帧，或已有未结束任务。",
        );
      setPanel(true);
      await refreshStatus();
    }, "已固定视频范围，开始抽帧");
  };
  const treeRows:{entry:(typeof folders)[number];depth:number;hasChildren:boolean}[]=[];
  const appendFolders=(parentPath:string|null,depth:number)=>{
    for(const entry of folders.filter(f=>f.parent_path===parentPath&&!isRemoving(f.path)).sort((a,b)=>a.path.localeCompare(b.path,'zh-CN'))){
      const hasChildren=folders.some(f=>f.parent_path===entry.path);
      treeRows.push({entry,depth,hasChildren});
      if(!collapsedRoots.has(entry.path))appendFolders(entry.path,depth+1);
    }
  };
  appendFolders(null,0);
  return (
    <div className="app">
      <header className="topbar">
        <button className="icon mobile-menu" type="button" aria-label="打开目录菜单"
          aria-controls="library-sidebar" aria-expanded={sidebarOpen}
          onClick={() => setSidebarOpen(true)}><Menu size={22} /></button>
        <div className="brand">
          <Aperture size={27} />
          <span>
            LocalMedia<small>本地影像库</small>
          </span>
          <span className="version">02</span>
        </div>
        <form
          className="search"
          onSubmit={(e) => {
            e.preventDefault();
            setSearch(query);
          }}
        >
          <Search size={18} />
          <input
            aria-label="搜索图库"
            placeholder="寻找一个画面、一段回忆…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            aria-label="搜索方式"
            value={semantic ? "semantic" : "text"}
            onChange={(e) => setSemantic(e.target.value === "semantic")}
          >
            <option value="text">文字搜索</option>
            <option value="semantic">自然语言</option>
          </select>
          <button type="submit">搜索</button>
        </form>
        <button
          className="icon"
          title="任务面板"
          onClick={() => setPanel(true)}
        >
          <Activity size={19} />
          <span className="dot" />
        </button>
        <button className="icon" title="设置" onClick={() => setSettings(true)}>
          <Settings size={19} />
        </button>
      </header>
      {sidebarOpen && <button className="sidebar-backdrop" type="button"
        aria-label="关闭目录菜单" onClick={() => setSidebarOpen(false)} />}
      <aside id="library-sidebar" className={"sidebar" + (sidebarOpen ? " open" : "") + (gamepadConnected && gamepadArea === "directories" ? " gamepad-directories" : "")}>
        {gamepadConnected && gamepadArea === "directories" && <div className="gamepad-help">↑↓ 选择 · ←→ 收起/展开 · A 打开 · B / X 返回</div>}
        <button className="icon sidebar-close" type="button" aria-label="关闭目录菜单"
          onClick={() => setSidebarOpen(false)}><X size={20} /></button>
        <div className="section-label">工作区</div>
        <button
          className={"nav " + (!root && !missing ? "active" : "")}
          data-gamepad-directory="all"
          onClick={() => {
            setRoot("");
            setParent("");
            setMissing(false);
            setSidebarOpen(false);
          }}
        >
          <Image size={17} />
          全部媒体
          <span>{activeRoots.reduce((n, r) => n + r.count, 0).toLocaleString()}</span>
        </button>
        <button
          className={"nav " + (missing ? "active" : "")}
          data-gamepad-directory="missing"
          onClick={() => {
            setRoot("");
            setParent("");
            setMissing(true);
            setSidebarOpen(false);
          }}
        >
          <AlertCircle size={17} />
          失联媒体
        </button>
        {!!disabledRoots.length && (
          <>
            <div className="section-label disabled-label">
              禁用目录<span>{disabledRoots.length}</span>
            </div>
            <div className="disabled-list">
              {disabledRoots
                .slice()
                .sort((a, b) => a.path.localeCompare(b.path, "zh-CN"))
                .map((entry) => (
                  <button
                    key={entry.id}
                    className="nav disabled-root"
                    title={`重新启用 ${entry.path}`}
                    onClick={() =>
                      void act(async () => {
                        await api(
                          "/roots/" + entry.id + "/enabled",
                          { enabled: true },
                          "PATCH",
                        );
                        await refreshStatus();
                        refresh();
                      }, "目录已启用，正在恢复扫描")
                    }
                  >
                    <EyeOff size={16} />
                    <span className="folder-name">
                      {shortPath(entry.path)}
                      <small>{entry.path}</small>
                    </span>
                    <Power size={14} />
                  </button>
                ))}
            </div>
          </>
        )}
        <div className="section-label folders-label">
          目录
          <button
            className="icon"
            title="添加目录"
            onClick={() => setAdd(true)}
          >
            <FolderPlus size={17} />
          </button>
        </div>
        <div className="folder-list">
          {treeRows.map(({entry:f,depth,hasChildren}) => (
            <div key={f.path} style={{paddingLeft:depth*16,display:'flex',alignItems:'center'}}>
              <button className="icon" style={{width:24,flexShrink:0,visibility:hasChildren?'visible':'hidden'}}
                title={collapsedRoots.has(f.path)?'展开子目录':'收起子目录'} aria-expanded={!collapsedRoots.has(f.path)}
                onClick={()=>setCollapsedRoots(previous=>{
                  const next=new Set(previous);if(next.has(f.path))next.delete(f.path);else next.add(f.path);return next;
                })}><ChevronRight size={14} style={{transform:collapsedRoots.has(f.path)?undefined:'rotate(90deg)'}} /></button>
              <button title={f.path} style={{flex:1,minWidth:0}}
                data-gamepad-directory={f.path}
                className={"nav root "+((parent||roots.find(r=>r.id===root)?.path)===f.path?'active':'')}
                onClick={()=>{setRoot(f.root_id);setParent(f.path);setMissing(false);setSidebarOpen(false);}}>
                <Folder size={17}/><span className="folder-name">{shortPath(f.path)}</span><span>{f.count.toLocaleString()}</span>
              </button>
              <button className="icon" title="从图库移除此目录" onClick={()=>{
                if(!confirm(`从图库移除 ${f.path} 及其子目录？原始文件不会删除；图库记录将移除，缩略图保留 30 分钟后开始清理。`))return;
                setRemovingPaths(previous=>[...previous,f.path]);setDetail(null);setViewer(false);setSelected(new Set());
                void act(async()=>{try{await api('/directories/remove',{path:f.path});setRoot('');setParent('');setPages({});await refreshStatus();refresh();}catch(e){setRemovingPaths(previous=>previous.filter(p=>p!==f.path));throw e;}},'目录已移除，相关任务已取消，缩略图将在 30 分钟后开始清理');
              }}><Trash2 size={14}/></button>
            </div>
          ))}
        </div>
        <button className="add-root" onClick={() => setAdd(true)}>
          <FolderPlus size={17} />
          添加媒体目录
        </button>
        <div className="local-note">
          <HardDrive size={16} />
          <span>
            原始媒体始终只读<small>预览与分析保存在本机</small>
          </span>
        </div>
      </aside>
      <main className="library">
        <div className="library-heading">
          <div>
            <div className="eyebrow">YOUR MOMENTS, IN FOCUS</div>
            <h1>
              {missing
                ? "失联媒体"
                : parent
                  ? shortPath(parent)
                  : selectedRoot
                    ? shortPath(selectedRoot.path)
                    : "全部影像"}
              <span>{total.toLocaleString()} 项</span>
            </h1>
          </div>
          <div className="actions">
            {selectedDirectoryPath && (
              <button onClick={() => setRootEdit(selectedDirectoryRoot || {
                id: "",
                path: selectedDirectoryPath,
                background: "",
                state: "online",
                count: 0,
                enabled: true,
                effective_enabled: true,
                visual_enhancement: false,
              })}>
                <SlidersHorizontal size={15} />
                目录设置
              </button>
            )}
            <button
              title="扫描目录并刷新列表"
              onClick={() =>
                void act(async () => {
                  await api("/sync", {});
                  refresh();
                }, "已请求同步；预览会自动生成")
              }
            >
              <RefreshCw size={15} />
              刷新
            </button>
            <button className="primary" onClick={() => void start()}>
              <Sparkles size={16} />
              {selected.size ? `分析选中 ${selected.size} 项` : "开始 AI 分析"}
            </button>
            <button onClick={() => void startFrames()}>
              <Images size={16} />
              {selected.size ? "抽帧选中视频" : "开始抽帧"}
            </button>
          </div>
        </div>
        <div className="filters">
          <select
            aria-label="媒体类型"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">全部类型</option>
            <option value="image">照片</option>
            <option value="video">视频</option>
          </select>
          <select
            aria-label="分析状态"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">全部分析状态</option>
            <option value="pending">未分析</option>
            <option value="stale">待重新分析</option>
            <option value="done">已分析</option>
            <option value="failed">分析失败</option>
          </select>
          <input
            aria-label="拍摄开始日期"
            type="date"
            value={after}
            onChange={(e) => setAfter(e.target.value)}
          />
          <span>—</span>
          <input
            aria-label="拍摄结束日期"
            type="date"
            value={before}
            onChange={(e) => setBefore(e.target.value)}
          />
          <span className="sort">拍摄时间 ↓</span>
        </div>
        {selected.size > 0 && (
          <div className="selection-bar">
            已选择 {selected.size} 项
            <button onClick={() => setSelected(new Set())}>取消选择</button>
            <button onClick={() => void start(true)}>重新分析并覆盖描述</button>
          </div>
        )}
        {error && (
          <div role="alert" className="error">
            <AlertCircle size={16} />
            {error}
            <button className="icon" onClick={() => setError("")}>
              <X size={15} />
            </button>
          </div>
        )}
        <div className="grid-scroll" ref={grid}>
          {gamepadConnected && <div className="gamepad-help">手柄：方向键 / 左摇杆选择 · A 打开 · B 返回 · X 目录 · LB / RB 切换</div>}
          {!total ? (
            <div className="empty">
              <div className="empty-icon">
                <Aperture size={48} strokeWidth={1} />
              </div>
              <h2>
                {loading
                  ? "正在寻找影像…"
                  : activeRoots.length
                    ? "这里还没有匹配的影像"
                    : "让回忆，有迹可循"}
              </h2>
              <p>
                {activeRoots.length
                  ? "新增文件会自动入库。可以刷新列表或调整筛选条件。"
                  : "添加一个照片或视频目录，即可开始浏览。"}
                <br />
                AI 分析由你手动启动。
              </p>
              {!activeRoots.length && (
                <button className="primary" onClick={() => setAdd(true)}>
                  <FolderPlus size={17} />
                  添加第一个目录
                </button>
              )}
            </div>
          ) : (
            <div
              style={{ height: virtual.getTotalSize(), position: "relative" }}
            >
              {visible.map((row) => (
                <div
                  key={row.key}
                  className="media-row"
                  style={{
                    position: "absolute",
                    top: row.start,
                    left: 0,
                    right: 0,
                    height: rowHeight,
                    gridTemplateColumns: `repeat(${columns},minmax(0,1fr))`,
                  }}
                >
                  {Array.from({ length: columns }, (_, col) => {
                    const i = row.index * columns + col;
                    if (i >= total) return null;
                    const m = pages[Math.floor(i / 100)]?.[i % 100];
                    if(m&&isRemoving(m.path))return null;
                    return m ? (
                      <article
                        key={m.id}
                        className={
                          "card " +
                          (gamepadConnected && gamepadArea === "media" && gamepadIndex === i ? "gamepad-focused " : "") +
                          (detail?.id === m.id ? "focused " : "") +
                          (selected.has(m.id) ? "selected" : "")
                        }
                        onClick={() => choose(m.id, true)}
                      >
                        <div className="thumb">
                          {m.preview_state === "ready" || m.thumbnail_state === "ready" ? (
                            <img
                              loading="lazy"
                              src={previewUrl(m, 'thumbnail')}
                              alt={m.name}
                            />
                          ) : (
                            <div className="placeholder">
                              {m.kind === "video" ? <Film /> : <Image />}
                              <small>{m.error || label(m.preview_state)}</small>
                            </div>
                          )}
                          <button
                            className="check"
                            aria-label={"选择 " + m.name}
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelected((old) => {
                                const next = new Set(old);
                                next.has(m.id)
                                  ? next.delete(m.id)
                                  : next.add(m.id);
                                return next;
                              });
                            }}
                          >
                            {selected.has(m.id) && <Check size={14} />}
                          </button>
                          {m.kind === "video" && (
                            <span className="badge">
                              <Film size={12} />
                              视频
                            </span>
                          )}
                          {m.analysis_state === "done" && (
                            <span className="ai-badge">
                              <Sparkles size={12} />
                            </span>
                          )}
                          {m.analysis_error && (
                            <span className="analysis-failure" title={m.analysis_error}>
                              <AlertCircle size={13} />分析失败
                            </span>
                          )}
                        </div>
                        <div className="caption">
                          <strong title={m.name}>{m.title || m.name}</strong>
                          <small>
                            {date(m.sort_time)}
                            {m.score !== undefined &&
                              ` · ${(m.score * 100).toFixed(0)}%`}
                          </small>
                        </div>
                      </article>
                    ) : (
                      <div key={i} className="skeleton" />
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>
        <footer className="statusbar">
          <span className="online-dot" />
          本机图库<span>添加目录后自动生成预览 · AI 按需分析</span>
          <span>
            {selected.size ? `${selected.size} 项已选择` : "单击打开详情"}
          </span>
        </footer>
      </main>
      <aside className="inspector">
        {detail ? (
          <>
            <div className="inspector-title">
              影像详情
              <button
                title="关闭详情"
                className="icon"
                onClick={() => setDetail(null)}
              >
                <X size={17} />
              </button>
            </div>
            <div
              className="detail-preview"
              onClick={() => {
                setViewer(true);
                setZoom(1);
              }}
            >
              <img
                src={previewUrl(detail)}
                alt={detail.name}
              />
              <Maximize2 size={17} />
            </div>
            <div className="detail-body">
              <h2>{detail.title || detail.name}</h2>
              <p className="muted">原文件名：{detail.name}</p>
              <p className="muted">
                {detail.kind === "video" ? "视频" : "照片"} ·{" "}
                {((detail.size || 0) / 1048576).toFixed(1)} MB ·{" "}
                {label(detail.availability)}
              </p>
              <div className="tags">
                <span>{label(detail.preview_state)}</span>
                <span>{label(detail.analysis_state)}</span>
                <span>向量 {label(detail.vector_state)}</span>
              </div>
              {detail.thumbnail_state === 'ready' && detail.preview_state === 'pending' &&
                <p className="muted">快速预览已就绪，正在准备标准预览。</p>}
              {detail.analysis_error && <p className="failure-message" role="alert">最近分析失败：{detail.analysis_error}</p>}
              {detail.preview_state === "failed" && (
                <button
                  onClick={() =>
                    void act(async () => {
                      await api("/previews/retry", {});
                      setNotice("已排队重试失败预览");
                      refresh();
                    })
                  }
                >
                  重试失败预览
                </button>
              )}
              <div className="section-label">文件信息</div>
              <dl>
                <dt>路径</dt>
                <dd className="path">{detail.path}</dd>
                {Object.entries(detail.metadata || {}).map(([k, v]) => (
                  <React.Fragment key={k}>
                    <dt>
                      {(
                        {
                          width: "宽度",
                          height: "高度",
                          camera: "相机",
                          make: "厂商",
                          lens: "镜头",
                          aperture: "光圈",
                          shutter: "快门",
                          iso: "ISO",
                          captured_at: "拍摄时间",
                          duration: "时长（秒）",
                          latitude: "纬度",
                          longitude: "经度",
                        } as Record<string, string>
                      )[k] || k}
                    </dt>
                    <dd>{String(v ?? "—")}</dd>
                  </React.Fragment>
                ))}
              </dl>
              <Editor
                key={detail.id + "note"}
                title="媒体背景备注"
                initial={detail.note || ""}
                placeholder="记录画面之外的背景，优先于目录背景…"
                save={(text) =>
                  act(async () => {
                    await api(
                      "/media/" + detail.id + "/note",
                      { text },
                      "PATCH",
                    );
                    setDetail({ ...detail, note: text });
                  }, "备注已保存；不会自动分析")
                }
              />
              <button className="primary" disabled={detail.preview_state!=='ready'} onClick={()=>void start(!!detail.description,detail.id)}>
                {detail.description?'重新生成此照片描述':'生成此照片描述'}
              </button>
              <Editor
                key={detail.id + "description" + detail.description}
                title="AI 描述"
                initial={detail.description || ""}
                placeholder="启动 AI 分析后，这里将显示中文描述。也可以手动填写。"
                save={(text) =>
                  act(async () => {
                    await api(
                      "/media/" + detail.id + "/description",
                      { text },
                      "PATCH",
                    );
                    setDetail({ ...detail, description: text });
                    refresh();
                  }, "描述已保存，向量正在更新")
                }
              />
              {detail.kind === "video" && (
                <p className="muted">基于抽帧分析，可能遗漏短暂事件。</p>
              )}
              {detail.batches?.map((b, i) => (
                <details key={i}>
                  <summary>
                    {b.start.toFixed(1)}–{b.end.toFixed(1)} 秒
                  </summary>
                  <p className="description">{b.description}</p>
                </details>
              ))}
              {detail.availability === "missing" && (
                <button
                  className="danger"
                  onClick={() => {
                    if (
                      window.confirm(
                        "仅清理这条失联记录，原文件不会被删除。继续？",
                      )
                    )
                      void act(async () => {
                        await api(
                          "/media/" + detail.id + "/missing-record",
                          {},
                          "DELETE",
                        );
                        setDetail(null);
                        refresh();
                      });
                  }}
                >
                  清理失联记录
                </button>
              )}
            </div>
          </>
        ) : (
          <div className="detail-empty">
            <SlidersHorizontal size={27} />
            <h3>每张影像，都有故事</h3>
            <p>
              选择一张照片或视频
              <br />
              查看摄影信息、备注与 AI 描述
            </p>
            <div className="shortcut">
              <span>单击</span>打开详情页
              <br />
              <span>← →</span>切换影像
            </div>
          </div>
        )}
      </aside>
      {notice && (
        <div role="status" className="toast">
          <Check size={16} />
          {notice}
        </div>
      )}
      {add && (
        <Modal title="添加媒体目录" close={() => setAdd(false)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const data = new FormData(e.currentTarget);
              void act(async () => {
                await api("/roots", {
                  path: data.get("path"),
                  background: data.get("background"),
                });
                setAdd(false);
                setRemovingPaths([]);
                await refreshStatus();
                refresh();
              }, "目录已添加，正在扫描");
            }}
          >
            <p className="muted">
              递归读取媒体并生成预览。不会自动启动 AI 分析。
            </p>
            <DirectoryInput title="本机目录路径" />
            <label>
              目录背景
              <textarea
                name="background"
                placeholder="例如：2026 年秋天，川西旅行。"
              />
            </label>
            <button className="primary" type="submit">
              添加目录
            </button>
          </form>
        </Modal>
      )}
      {rootEdit && (
        <Modal title="目录设置" close={() => setRootEdit(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const data = new FormData(e.currentTarget);
              void act(async () => {
                const enabled = data.get("enabled") === "on";
                const visualEnhancement = data.get("visual_enhancement") === "on";
                let ident = rootEdit.id;
                let savedPath = String(data.get("path"));
                if (!ident) {
                  const created = await api<{id:string;path:string}>("/roots", {
                    path: savedPath,
                    background: data.get("background"),
                    visual_enhancement: visualEnhancement,
                  });
                  ident = created.id;
                  savedPath = created.path;
                } else {
                  if (savedPath !== rootEdit.path) {
                    const moved = await api<{ok:boolean;path:string}>(
                      "/roots/" + ident + "/location",
                      { path: savedPath },
                      "PATCH",
                    );
                    savedPath = moved.path;
                  }
                  await api(
                    "/roots/" + ident + "/background",
                    { text: data.get("background") },
                    "PATCH",
                  );
                }
                if (enabled !== rootEdit.enabled)
                  await api(
                    "/roots/" + ident + "/enabled",
                    { enabled },
                    "PATCH",
                  );
                if (visualEnhancement !== rootEdit.visual_enhancement)
                  await api(
                    "/roots/" + ident + "/visual-enhancement",
                    { enabled: visualEnhancement },
                    "PATCH",
                  );
                setRootEdit(null);
                if (!enabled) {
                  setRoot("");
                  setParent("");
                  setDetail(null);
                  setViewer(false);
                  setSelected(new Set());
                  setPages({});
                } else {
                  setRoot(ident);
                  setParent(savedPath);
                }
                await refreshStatus();
                refresh();
              }, "目录设置已保存");
            }}
          >
            <DirectoryInput title="当前目录位置" initial={rootEdit.path} />
            <p className="muted">
              {rootEdit.id
                ? "搬家后修改此路径，系统会校验内容并匹配旧记录；匹配不明确时保留为独立项。"
                : "这是导入目录下的子目录。首次保存会为当前子目录建立独立设置，不会移动或复制原文件。"}
            </p>
            <label>
              目录背景
              <textarea name="background" defaultValue={rootEdit.background} />
            </label>
            <p className="muted">
              修改背景不会自动重分析，也不会改变已有描述。
            </p>
            <label className="directory-enabled">
              <input
                type="checkbox"
                name="enabled"
                defaultChecked={rootEdit.enabled}
              />
              <span>
                启用此目录
                <small>关闭后，此目录及其所有子目录会从工作区隐藏并停止扫描。</small>
              </span>
            </label>
            <label className="directory-enabled">
              <input
                type="checkbox"
                name="visual_enhancement"
                defaultChecked={rootEdit.visual_enhancement}
              />
              <span>
                开启视觉加强
                <small>新建分析任务会附带本机视觉类别参考包；已有描述和正在执行的任务不变。</small>
              </span>
            </label>
            <button className="primary">保存</button>
          </form>
        </Modal>
      )}
      {settings && (
        <SettingsDialog
          config={config}
          close={() => setSettings(false)}
          save={async (body) => {
            const result = await api<Config>("/settings", body, "PATCH");
            setConfig(result);
            setNotice("设置已保存");
          }}
          error={setError}
        />
      )}
      {panel && (
        <Modal title="任务中心" close={() => setPanel(false)} wide>
          <div className="run">
            <strong>目录同步与预览</strong>
            <p>快速缩略图：{thumbnailCounts.map(c => `${label(c.state)} ${c.count}`).join(' · ')}<br />
              当前处理：照片 {previewWorkers.image} · 视频 {previewWorkers.video}</p>
            <p>
              标准预览：
              {previewCounts
                .map((c) => `${label(c.state)} ${c.count}`)
                .join(" · ")}
            </p>
            {syncRoots.map((r) => (
              <p key={r.id}>
                {shortPath(r.path)} · {label(r.state)} {r.error}
              </p>
            ))}
            <button onClick={() => void act(() => api("/sync", {}))}>
              重新扫描
            </button>
            <button onClick={() => void act(() => api("/previews/retry", {}))}>
              重试失败预览
            </button>
          </div>
          <p className="muted">
            普通暂停等待当前请求结束；立即停止尝试中断请求并保留完成批次。重启后需手动继续。
          </p>
          <div className="quota">
            <Activity size={16} />
            {label(quota)} · 额度仅供查看，不会自动暂停任务
          </div>
          {!runs.length && (
            <p className="muted">
              暂无任务。选中文件或目录后可开始 AI 分析或仅抽帧。
            </p>
          )}
          {runs.map((r) => (
            <div className="run" key={r.id}>
              <div>
                <strong>
                  {r.kind === "frames" ? "仅抽帧" : "AI 分析"} · {new Date(r.created * 1000).toLocaleString("zh-CN")}
                </strong>
                <span className="run-state">{label(r.state)}</span>
              </div>
              <p>
                {r.counts
                  .map((c) => `${label(c.state)} ${c.count}`)
                  .join(" · ")}{" "}
                · 抽帧 {r.phases?.extracting || 0} · AI 分析 {r.phases?.ai || 0}
                {r.reason && " · " + label(r.reason)}
              </p>
              <div className="actions">
                {(["pause", "stop", "resume", "retry"] as const).map((a, i) => (
                  <button
                    key={a}
                    onClick={() =>
                      void act(async () => {
                        await api("/runs/" + r.id + "/" + a, {});
                        await refreshStatus();
                      })
                    }
                  >
                    {
                      [
                        <Pause size={13} />,
                        <Square size={13} />,
                        <Play size={13} />,
                        <RefreshCw size={13} />,
                      ][i]
                    }
                    {["暂停", "立即停止", "恢复", "失败重试"][i]}
                  </button>
                ))}
                <button onClick={()=>{
                  if(!confirm('删除此任务？正在运行和排队的工作会取消，任务进度将移除。已生成的抽帧、描述、向量和缩略图保留。'))return;
                  void act(async()=>{await api('/runs/'+r.id,undefined,'DELETE');setRuns(previous=>previous.filter(run=>run.id!==r.id));await refreshStatus();},'任务已删除');
                }}><Trash2 size={13}/>删除任务</button>
              </div>
              <TaskDetails id={r.id} />
            </div>
          ))}
          {!!vectors.length && (
            <div className="run">
              <strong>向量任务</strong>
              <p>
                {vectors.map((v) => `${label(v.state)} ${v.count}`).join(" · ")}
              </p>
              <button onClick={() => void act(() => api("/vectors/pause", {}))}>
                暂停
              </button>
              <button
                onClick={() => void act(() => api("/vectors/resume", {}))}
              >
                恢复 / 重试
              </button>
            </div>
          )}
        </Modal>
      )}
      {viewer && detail && (
        <div className="viewer" role="dialog" aria-label="媒体详情页">
          <div className="viewer-bar">
            <span>{detail.title || detail.name}</span>
            {detail.kind === "video" && <small>{playbackStatus}</small>}
            {detail.kind === "video" && playbackReady === detail.id && <button onClick={() => {
              const video = viewerVideo.current;
              playbackPosition.current = video ? {time:video.currentTime, paused:video.paused} : null;
              setOriginalPlayback(value => !value);
            }}>{originalPlayback ? "流畅播放" : "原画播放"}</button>}
            {gamepadConnected && <small>{detail.kind === "video" ? "LB 回退 15 秒 · RB 快进 30 秒 · ←→ 切换媒体 · B 返回" : "LB / RB 切换媒体 · B 返回"}</small>}
            <button onClick={() => setZoom((v) => Math.max(0.25, v - 0.25))}>
              −
            </button>
            <span>{Math.round(zoom * 100)}%</span>
            <button onClick={() => setZoom((v) => Math.min(4, v + 0.25))}>
              ＋
            </button>
            <button onClick={() => setZoom(1)}>适合窗口</button>
            <button
              title="关闭查看器"
              className="icon"
              onClick={() => setViewer(false)}
            >
              <X />
            </button>
          </div>
          <button
            className="viewer-prev"
            title="上一项"
            onClick={() => next(-1)}
          >
            <ChevronLeft />
          </button>
          <div className="viewer-layout">
            <div className="viewer-canvas">
              {detail.kind === "video" ? (
                <video
                  ref={viewerVideo}
                  key={detail.id}
                  controls
                  autoPlay
                  preload="auto"
                  onLoadedMetadata={(event) => {
                    const position = playbackPosition.current;
                    if (playbackReady !== detail.id || !position) return;
                    playbackPosition.current = null;
                    const video = event.currentTarget;
                    video.currentTime = Math.min(position.time, Number.isFinite(video.duration) ? video.duration : position.time);
                    if (position.paused) video.pause();
                    else void video.play().catch(() => {});
                  }}
                  onError={() => {
                    if (playbackReady === detail.id) {
                      setPlaybackReady("");
                      setPlaybackStatus("播放副本不可用，使用原视频");
                    }
                  }}
                  src={"/api/media/" + detail.id + (playbackReady === detail.id && !originalPlayback ? "/playback" : "/original")}
                />
              ) : (
                <img
                  style={{ transform: `scale(${zoom})` }}
                  src={imageFallback ? previewUrl(detail) : "/api/media/" + detail.id + "/original"}
                  onError={() => {
                    if (!imageFallback) setImageFallback(true);
                  }}
                  alt={detail.name}
                />
              )}
            </div>
            <aside className="viewer-info">
              <h2>{detail.title || detail.name}</h2>
              <p className="muted">原文件名：{detail.name}</p>
              <p className="muted">{detail.path}</p>
              <div className="tags">
                <span>{detail.kind === "video" ? "视频" : "照片"}</span>
                <span>{((detail.size || 0) / 1048576).toFixed(1)} MB</span>
                <span>{label(detail.availability)}</span>
                <span>{label(detail.analysis_state)}</span>
              </div>
              <div className="section-label">文件与拍摄数据</div>
              <dl>
                {Object.entries(detail.metadata || {}).map(([k, v]) => <React.Fragment key={k}>
                  <dt>{({width:"宽度",height:"高度",camera:"相机",make:"厂商",lens:"镜头",aperture:"光圈",shutter:"快门",iso:"ISO",captured_at:"拍摄时间",duration:"时长（秒）",latitude:"纬度",longitude:"经度"} as Record<string,string>)[k] || k}</dt>
                  <dd>{String(v ?? "—")}</dd>
                </React.Fragment>)}
              </dl>
              <div className="section-label">媒体背景备注</div>
              <p className="description">{detail.note || "暂无备注"}</p>
              <div className="section-label">AI 描述</div>
              {detail.analysis_error && <p className="failure-message" role="alert">最近分析失败：{detail.analysis_error}</p>}
              <p className="description">{detail.description || "尚未生成描述"}</p>
              {detail.kind === "video" && <>
                <div className="section-label">本次全部抽帧 · {detail.frames?.length || 0} 张</div>
                {!!detail.frame_generations && detail.frame_generations.count > 1 &&
                  <p className="muted">共保留 {detail.frame_generations.count} 次抽帧记录、{detail.frame_generations.retained} 张文件；当前按最近一次任务展示，避免历史重分析帧重复。</p>}
                <div className="viewer-frames">
                  {detail.frames?.map(f => <figure key={f.id}>
                    <img loading="lazy" src={`/api/media/${detail.id}/frames/${f.id}`} alt={`${f.time.toFixed(1)} 秒抽帧`} />
                    <figcaption>{f.time.toFixed(1)} 秒 · {f.kind === "scene" ? "场景" : f.kind === "interval" ? "定时" : "历史"}</figcaption>
                  </figure>)}
                </div>
              </>}
              {detail.batches?.map((b,i)=><details key={i}><summary>{b.start.toFixed(1)}–{b.end.toFixed(1)} 秒分析</summary><p className="description">{b.description}</p></details>)}
            </aside>
          </div>
          <button
            className="viewer-next"
            title="下一项"
            onClick={() => next(1)}
          >
            <ChevronRight />
          </button>
          <p>← → 切换 · Esc 关闭 · 播放器直接读取原文件，图库不会修改原媒体</p>
        </div>
      )}
    </div>
  );
}

function Editor({
  title,
  initial,
  placeholder,
  save,
}: {
  title: string;
  initial: string;
  placeholder: string;
  save: (s: string) => Promise<unknown>;
}) {
  const [text, setText] = useState(initial),
    [busy, setBusy] = useState(false);
  return (
    <div className="editor">
      <div className="section-label">
        {title}
        <button
          disabled={busy || text === initial}
          onClick={() => {
            setBusy(true);
            void save(text).finally(() => setBusy(false));
          }}
        >
          {busy ? "保存中" : "保存"}
        </button>
      </div>
      <textarea
        aria-label={title}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
      />
    </div>
  );
}
function Modal({
  title,
  close,
  children,
  wide = false,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        role="dialog"
        aria-label={title}
        className={"modal " + (wide ? "wide" : "")}
      >
        <header>
          <h2>{title}</h2>
          <button className="icon" title="关闭" onClick={close}>
            <X size={19} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
function TaskDetails({ id }: { id: string }) {
  type TaskProgress = {
    id: string;
    name: string;
    kind: "image" | "video";
    elapsed: number;
    operation: "analysis" | "frames";
    state: "queued" | "running" | "paused" | "failed";
    reason?: string | null;
    error?: string | null;
    stage?: "extract_queued" | "extracting" | "ai_queued" | "ai" | "processing";
    batch?: { current: number; total: number } | null;
    extraction?: { percent: number; completed_duration: number; total_duration: number } | null;
  };
  const [data, setData] = useState<TaskProgress[]>([]);
  const [loadError, setLoadError] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    let active = true;
    const refresh = () =>
      void api<TaskProgress[]>("/runs/" + id + "/tasks").then((rows) => {
        if (active) { setData(rows); setLoadError(""); }
      }).catch((cause) => { if (active) setLoadError((cause as Error).message); });
    refresh();
    const timer = window.setInterval(refresh, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [id, open]);
  return (
    <details
      onToggle={(e) => {
        setOpen(e.currentTarget.open);
      }}
    >
      <summary>查看文件进度与失败原因</summary>
      {loadError && <p className="failure-message" role="alert">{loadError}</p>}
      {!loadError && data.length === 0 && <p>当前没有排队、运行、暂停或失败的文件</p>}
      {data.map((t) => (
        <p key={t.id}>
          {t.name} · {label(t.state)} · 已处理 {Math.round(t.elapsed)} 秒
          {t.reason && ` · ${label(t.reason)}`}
          {t.error && <span className="failure-message"> · {t.error}</span>}
          {t.kind === "video" && (t.state === "queued" || t.state === "running") && (
            <> · {t.extraction ? `抽帧 ${t.extraction.percent}%` : "抽帧准备中"}
              {t.operation === "analysis" && ` · ${t.batch ? `AI 批次 ${t.batch.current}/${t.batch.total}` : t.stage === "ai" ? "AI 分析中" : t.stage === "ai_queued" ? "AI 排队中" : "AI 等待抽帧"}`}</>
          )}
        </p>
      ))}
    </details>
  );
}
type ServiceModel = {id:string;label:string;vision:boolean|null;text:boolean|null;embedding:boolean|null;efforts?:{value:string;description:string}[];default_effort?:string};
function SettingsDialog({
  config,
  close,
  save,
  error,
}: {
  config: Config;
  close: () => void;
  save: (c: Config) => Promise<void>;
  error: (s: string) => void;
}) {
  const [form, setForm] = useState<Config>({ ...config }),
    [status, setStatus] = useState(""),
    [models, setModels] = useState<ServiceModel[]>([]),
    [embeddingModels, setEmbeddingModels] = useState<ServiceModel[]>([]),
    [modelStatus,setModelStatus] = useState(""),
    [embeddingStatus,setEmbeddingStatus] = useState(""),
    [loadingModels,setLoadingModels] = useState(false),
    [loadingEmbeddings,setLoadingEmbeddings] = useState(false),
    [loggedIn,setLoggedIn] = useState<boolean|null>(null),
    [busy, setBusy] = useState(false);
  const discoveryGeneration = useRef({description:0,embedding:0});
  const discover = async (target:'description'|'embedding') => {
    const generation = ++discoveryGeneration.current[target];
    const embedding = target==='embedding';
    const setLoading = embedding?setLoadingEmbeddings:setLoadingModels;
    const setMessage = embedding?setEmbeddingStatus:setModelStatus;
    setLoading(true);setMessage('正在获取可用模型…');
    try {
      const result = await api<{models:ServiceModel[];logged_in?:boolean;message:string}>('/services/models',{
        target, provider:embedding?form.embedding_provider:form.provider,
        url:embedding?form.embedding_url:form.base_url,
        key:embedding?form.embedding_key:form.api_key,
      });
      if(generation!==discoveryGeneration.current[target])return;
      (embedding?setEmbeddingModels:setModels)(result.models);
      if(!embedding)setLoggedIn(result.logged_in??null);
      setMessage(result.message);
    } catch(e){if(generation===discoveryGeneration.current[target])setMessage((e as Error).message);}
    finally{if(generation===discoveryGeneration.current[target])setLoading(false);}
  };
  useEffect(()=>{
    ++discoveryGeneration.current.description;setModels([]);setLoggedIn(null);setLoadingModels(true);
    const timer=setTimeout(()=>void discover('description'),450);
    return()=>{clearTimeout(timer);++discoveryGeneration.current.description;};
  },[form.provider,form.base_url,form.api_key]);
  useEffect(()=>{
    ++discoveryGeneration.current.embedding;setEmbeddingModels([]);setLoadingEmbeddings(true);
    const timer=setTimeout(()=>void discover('embedding'),450);
    return()=>{clearTimeout(timer);++discoveryGeneration.current.embedding;};
  },[form.embedding_provider,form.embedding_url,form.embedding_key]);
  const effortField=(key:string,title:string,modelId:unknown)=>{
    if(form.provider!=='codex')return null;
    const model=models.find(m=>m.id===modelId);
    const efforts=model?.efforts??[];
    const selected = String(form[key] || '');
    return <label>{title}<select value={selected}
      disabled={loadingModels||!efforts.length} onChange={e=>setForm({...form,[key]:e.target.value})}>
      <option value="">模型默认{model?.default_effort?`（${model.default_effort}）`:''}</option>
      {selected && !efforts.some(e=>e.value===selected) && <option value={selected}>{selected}（已保存，待验证）</option>}
      {efforts.map(e=><option key={e.value} value={e.value} title={e.description}>{e.value}</option>)}
    </select></label>;
  };
  const modelField=(key:string,title:string,cap:'vision'|'text'|'embedding',inherit=false)=>{
    const list=(cap==='embedding'?embeddingModels:models).filter(m=>m[cap]!==false);
    const loading=cap==='embedding'?loadingEmbeddings:loadingModels;
    const selected=String(form[key] || '');
    return <label>{title}<select value={selected} disabled={loading||!list.length} onChange={e=>setForm(previous=>({
      ...previous,[key]:e.target.value,
      ...(key==='model'?{codex_effort:''}:key==='summary_model'?{codex_summary_effort:''}:{}),
    }))}>
      <option value="">{loading?'正在获取模型…':inherit?'沿用视觉描述模型':list.length?'请选择模型':'没有可用模型'}</option>
      {selected && !list.some(m=>m.id===selected) && <option value={selected}>{selected}（已保存，待验证）</option>}
      {list.map(m=><option key={m.id} value={m.id}>{m.label===m.id?m.id:`${m.label} (${m.id})`}</option>)}
    </select></label>;
  };
  const field = (key: string, title: string, type = "text") => (
    <label>
      {title}
      <input
        type={type}
        value={String(form[key] ?? "")}
        placeholder={
          type === "password" && config[key + "_configured"]
            ? "已保存，留空保持不变"
            : ""
        }
        onChange={(e) =>
          setForm({
            ...form,
            [key]: type === "number" ? Number(e.target.value) : e.target.value,
          })
        }
      />
    </label>
  );
  const persist = async () => {
    // Connection discovery is advisory. Offline services must not prevent local
    // resource/frame settings or connection details from being saved.
    await save(settingsPatch(form, config));
  };
  const action = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setStatus((e as Error).message);
      error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="图库与 AI 设置" close={close} wide>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action(async () => {
            await persist();
            setStatus("设置已保存");
          });
        }}
      >
        <h3 className="settings-heading">模型服务</h3>
        <div className="settings-grid settings-cards">
          <section>
            <h3>描述服务</h3>
            <label>
              服务
              <select
                value={String(form.provider)}
                onChange={(e) =>
                  setForm({
                    ...form,
                    provider: e.target.value,
                    model:"", summary_model:"", api_key:"",
                    base_url:
                      e.target.value === "ollama"
                        ? "http://localhost:11434"
                        : e.target.value === "openai"
                          ? "https://api.openai.com/v1"
                          : String(form.base_url),
                  })
                }
              >
                <option value="ollama">Ollama · 本地</option>
                <option value="codex">Codex App Server · ChatGPT 登录</option>
                <option value="openai">OpenAI 兼容 API</option>
              </select>
            </label>
            {form.provider === 'codex' ? <p className="muted">自动调用本机 Codex，复用已有 ChatGPT 登录，无需填写程序路径或 API 密钥。</p> : field('base_url','服务地址（兼容 API 含 /v1）')}
            {form.provider === 'openai' && field('api_key','API 密钥','password')}
            {modelField('model','视觉描述模型','vision')}
            {effortField('codex_effort','视觉推理强度（effort）',form.model)}
            {modelField('summary_model','文本汇总模型','text',true)}
            {effortField('codex_summary_effort','汇总推理强度（effort）',form.summary_model||form.model)}
            {form.provider==='codex'&&<p className="muted">推理档位由所选模型提供。视觉分析与文本汇总可分别设置，留空使用各模型默认值；保存后用于新启动的分析任务。</p>}
            <p role="status" className="muted">{modelStatus}</p>
            <button type="button" disabled={loadingModels} onClick={()=>void discover('description')}>刷新可用模型 / 登录状态</button>
            {form.provider === "codex" && loggedIn === false && (
              <button
                type="button"
                onClick={() =>
                  void action(async () => {
                    const result = await api("/services/codex/login", {});
                    if (result.authUrl)
                      window.open(result.authUrl, "_blank", "noopener");
                    setStatus("请在打开的官方页面完成登录，再验证连接。");
                  })
                }
              >
                ChatGPT 登录
              </button>
            )}
          </section>
          <section>
            <h3>向量服务</h3>
            <label>
              服务
              <select
                value={String(form.embedding_provider)}
                onChange={(e) =>
                  setForm({
                    ...form,
                    embedding_provider: e.target.value,
                    embedding_model:"", embedding_key:"",
                    embedding_url:
                      e.target.value === "ollama"
                        ? "http://localhost:11434"
                        : "https://api.openai.com/v1",
                  })
                }
              >
                <option value="ollama">Ollama · 本地</option>
                <option value="openai">OpenAI 兼容 API</option>
              </select>
            </label>
            {field("embedding_url", "向量服务地址")}
            {modelField("embedding_model", "Embedding 模型", "embedding")}
            {form.embedding_provider === "openai" &&
              field("embedding_key", "向量 API 密钥", "password")}
            <p role="status" className="muted">{embeddingStatus}</p>
            <button type="button" disabled={loadingEmbeddings} onClick={()=>void discover('embedding')}>刷新向量模型</button>
            <p className="muted">
              切换模型后需手动重建；完整切换前继续使用旧向量空间。文件移动不影响向量。
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    "按当前向量配置重建全库描述向量？云端向量服务会收到描述文字。",
                  )
                )
                  void action(async () => {
                    await persist();
                    await api("/vectors/rebuild", {});
                    setStatus("已启动向量重建");
                  });
              }}
            >
              保存并重建向量
            </button>
          </section>
        </div>
        <h3 className="settings-heading">分析任务</h3>
        <section className="settings-card">
          <h3>导入与预览</h3>
          <div className="settings-grid">
            {field('preview_image_concurrency', '照片预览并发数（1–8）', 'number')}
            {field('preview_video_concurrency', '视频预览并发数（1–4）', 'number')}
          </div>
          <p className="muted">先显示快速缩略图，再补充标准预览。优先处理当前可见影像与打开的详情；照片和视频独立排队。AI 分析使用标准预览，降低并发不打断已开始的处理。</p>
        </section>
        <div className="settings-grid settings-cards">
          <section>
            <h3>资源与并发</h3>
            {field("max_cpu_cores", "最多使用的 CPU 逻辑核心数（0 不限制）", "number")}
            {field("image_concurrency", "同时处理的图片任务数", "number")}
            {field("cpu_decode_concurrency", "CPU 解码并发数", "number")}
            {field("intel_decode_concurrency", "Intel 核显解码并发数", "number")}
            {field("nvidia_decode_concurrency", "NVIDIA 解码并发数", "number")}
            {field("ai_request_concurrency", "Codex 描述并发数", "number")}
            {field("embedding_request_concurrency", "向量生成并发数", "number")}
            {field("request_timeout", "单请求超时（秒，含 Codex RPC 等待）", "number")}
            {field("video_budget", "单视频累计上限（秒，0 不限时）", "number")}
            <p className="muted">视频流程不设总并发上限；CPU、Intel 核显、NVIDIA 独显分别按各自并发数抽帧，Codex 描述和本地向量生成分别限流。等待资源槽位不计入单请求超时，本地 FFmpeg 也不使用 AI 请求超时。CPU 核心限制只作用于独立工作进程及其子进程；保存后动态生效。降低并发数后，已开始的阶段会继续完成。</p>
          </section>
          <section>
            <h3>视频取帧与分批</h3>
            <label>
              视频抽帧模式
              <select value={String(form.video_frame_mode ?? "quality")} onChange={(e)=>setForm({...form,video_frame_mode:e.target.value})}>
                <option value="quality">高质量（定时帧 + 画面变化检测）</option>
                <option value="fast">快速（仅按时间定时抽帧）</option>
              </select>
            </label>
            <label>
              视频解码设备
              <select value={String(form.video_decoder ?? "cpu")} onChange={(e)=>setForm({...form,video_decoder:e.target.value})}>
                <option value="cpu">CPU（兼容性最好）</option>
                <option value="intel">Intel 核显（Quick Sync，低功耗）</option>
                <option value="nvidia">NVIDIA 独显（NVDEC，高吞吐）</option>
                <option value="intel_nvidia">核显 + GPU（并行分流）</option>
                <option value="cpu_intel_nvidia">CPU + 核显 + GPU（并行分流）</option>
              </select>
            </label>
            {field("frame_interval", "基础抽帧间隔（秒）", "number")}
            {field("scene_fps", "场景检测采样帧率（FPS）", "number")}
            {field("batch_size", "每批帧数", "number")}
            {field("batch_overlap", "批次重叠帧数", "number")}
            <p className="muted">快速模式只按基础抽帧间隔定位取帧，不再顺序解码整段视频检测画面变化，速度更快但可能漏掉两个采样点之间的短暂事件。高质量模式保留定时帧和画面变化检测。视频预览及实际启用的抽帧步骤会使用所选解码设备；组合模式会把不同 FFmpeg 作业轮流分配给各设备，并不会把单个视频同时拆给多种设备。硬件不支持当前编码或配置时会自动尝试其他设备并最终回退 CPU。场景相似度仍由 CPU 计算。设置会应用于新建分析任务；已创建任务继续使用创建时的配置。所有成功抽出的帧都会长期保留并展示，不再折叠重复帧。</p>
          </section>
        </div>
        <h3 className="settings-heading">内容与隐私</h3>
        <section className="settings-card settings-prompt">
          <label>
            全库提示词
            <textarea
              value={String(form.prompt)}
              onChange={(e) => setForm({ ...form, prompt: e.target.value })}
            />
          </label>
          <p className="muted">
            云端分析发送预览、视频抽帧与背景文字。不会自动切换服务。抽帧不能保证捕捉所有短暂事件；Codex 额度不足时服务请求仍可能失败，但程序不会按额度阈值自动暂停。
          </p>
        </section>
        {status && <p role="status">{status}</p>}
        <button className="primary" disabled={busy}>
          {busy ? "处理中…" : "保存设置"}
        </button>
      </form>
    </Modal>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
