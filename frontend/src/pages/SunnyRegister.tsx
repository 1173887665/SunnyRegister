import { Fragment, useDeferredValue, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import type { Dispatch, PointerEvent as ReactPointerEvent, ReactNode, SetStateAction } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { Activity, ArrowLeft, ArrowRight, ChevronDown, CircleHelp, Crown, Download, Globe2, Inbox, ListChecks, Loader2, Pencil, Plus, RefreshCw, RotateCw, Save, Search, Settings2, Sparkles, Trash2, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ConfirmBubble } from "@/components/ui/confirm-bubble";
import { API_BASE, apiDownload, apiFetch, cn, triggerBrowserDownload } from "@/lib/utils";
import { useI18n } from "@/lib/i18n-context";
import { useSunnyGsap } from "@/lib/useSunnyGsap";

type AnyObj = Record<string, any>;
type ToastState = { type: "ok" | "fail"; text: string } | null;
type DataTableColumn = { width: number; minWidth: number; maxWidth?: number };

const DATA_TABLE_COLUMNS: Record<string, DataTableColumn[]> = {
  workbench: [
    { width: 44, minWidth: 44, maxWidth: 72 }, { width: 300, minWidth: 180 }, { width: 160, minWidth: 110 },
    { width: 120, minWidth: 90 }, { width: 120, minWidth: 90 }, { width: 190, minWidth: 150 }, { width: 110, minWidth: 88, maxWidth: 320 },
  ],
  mailboxes: [
    { width: 44, minWidth: 44, maxWidth: 72 }, { width: 300, minWidth: 180 }, { width: 160, minWidth: 110 },
    { width: 110, minWidth: 88 }, { width: 110, minWidth: 88 }, { width: 80, minWidth: 64 }, { width: 80, minWidth: 64 },
    { width: 100, minWidth: 82 }, { width: 190, minWidth: 150 }, { width: 200, minWidth: 150, maxWidth: 520 },
  ],
  phones: [
    { width: 44, minWidth: 44, maxWidth: 72 }, { width: 190, minWidth: 140 }, { width: 120, minWidth: 92 },
    { width: 120, minWidth: 96 }, { width: 420, minWidth: 240 }, { width: 190, minWidth: 150 }, { width: 130, minWidth: 110, maxWidth: 360 },
  ],
  proxies: [
    { width: 44, minWidth: 44, maxWidth: 72 }, { width: 520, minWidth: 280 }, { width: 140, minWidth: 100 },
    { width: 140, minWidth: 110 }, { width: 190, minWidth: 150 }, { width: 170, minWidth: 130, maxWidth: 420 },
  ],
  sessions: [
    { width: 44, minWidth: 44, maxWidth: 72 }, { width: 280, minWidth: 180 }, { width: 150, minWidth: 100 },
    { width: 110, minWidth: 88 }, { width: 110, minWidth: 88 }, { width: 130, minWidth: 110 }, { width: 72, minWidth: 60 }, { width: 72, minWidth: 60 },
    { width: 96, minWidth: 72 }, { width: 190, minWidth: 150 }, { width: 190, minWidth: 150 }, { width: 320, minWidth: 260, maxWidth: 640 },
  ],
};

function clampDataTableWidth(column: DataTableColumn, width: number) {
  return Math.max(column.minWidth, Math.min(column.maxWidth || 800, Math.round(width)));
}

function fillDataTableViewport(widths: number[], columns: DataTableColumn[], viewportWidth: number, preferredIndex = columns.length - 2) {
  if (!viewportWidth || columns.length < 2) return widths;
  const total = widths.reduce((sum, width)=>sum + width, 0);
  const missing = Math.floor(viewportWidth - total);
  if (missing <= 0) return widths;
  const actionIndex = columns.length - 1;
  const targetIndex = Math.max(0, Math.min(actionIndex - 1, preferredIndex));
  const next = [...widths];
  next[targetIndex] += missing;
  return next;
}

function resizeDataTableColumn(widths: number[], columns: DataTableColumn[], index: number, targetWidth: number, viewportWidth: number) {
  const actionIndex = columns.length - 1;
  if (index < 0 || index >= columns.length) return widths;
  const startWidth = widths[index];
  const leftWidth = clampDataTableWidth(columns[index], targetWidth);
  if (leftWidth === startWidth) return widths;
  const next = [...widths];
  next[index] = leftWidth;
  const total = widths.reduce((sum, width)=>sum + width, 0);
  const fillsViewport = viewportWidth > 0 && total <= viewportWidth + 2;
  const fillIndex = index + 1 < actionIndex ? index + 1 : Math.max(0, index - 1);
  if (fillsViewport && index + 1 < actionIndex) {
    const rightIndex = index + 1;
    next[rightIndex] = clampDataTableWidth(columns[rightIndex], widths[rightIndex] - (leftWidth - startWidth));
  } else if (fillsViewport && index === actionIndex) {
    const donorIndex = Math.max(0, actionIndex - 1);
    next[donorIndex] = clampDataTableWidth(columns[donorIndex], widths[donorIndex] - (leftWidth - startWidth));
  }
  return fillDataTableViewport(next, columns, viewportWidth, fillIndex);
}

function initialDataTableWidths(tableKey: string, columns: DataTableColumn[]) {
  const defaults = columns.map((column)=>column.width);
  if (typeof window === "undefined") return defaults;
  try {
    const stored = JSON.parse(window.localStorage.getItem(`sunnyregister.table-widths.${tableKey}`) || "[]");
    if (!Array.isArray(stored) || stored.length !== columns.length) return defaults;
    return stored.map((value,index)=>Math.max(columns[index].minWidth,Math.min(columns[index].maxWidth||800,Number(value)||defaults[index])));
  } catch { return defaults; }
}

function ResizableDataTable({ tableKey, columns, headers, className="", children }: { tableKey:string; columns:DataTableColumn[]; headers:ReactNode[]; className?:string; children:ReactNode }) {
  const [widths,setWidths]=useState<number[]>(()=>initialDataTableWidths(tableKey,columns));
  const [viewportWidth,setViewportWidth]=useState(0);
  const tableRef=useRef<HTMLTableElement|null>(null);
  const resizeCleanup=useRef<null|(()=>void)>(null);
  useEffect(()=>{
    try { window.localStorage.setItem(`sunnyregister.table-widths.${tableKey}`,JSON.stringify(widths)); } catch { /* private browsing may disable storage */ }
  },[tableKey,widths]);
  useEffect(()=>()=>resizeCleanup.current?.(),[]);
  useLayoutEffect(()=>{
    const table=tableRef.current;
    const viewport=table?.parentElement;
    if(!viewport) return;
    const updateViewport=()=>{
      const nextWidth=Math.floor(viewport.clientWidth);
      setViewportWidth(nextWidth);
      setWidths((current)=>fillDataTableViewport(current,columns,nextWidth));
    };
    const observer=new ResizeObserver(updateViewport);
    observer.observe(viewport);
    updateViewport();
    return ()=>observer.disconnect();
  },[columns]);
  const setColumnWidth=(index:number,width:number)=>setWidths((current)=>resizeDataTableColumn(current,columns,index,width,viewportWidth));
  const startResize=(event:ReactPointerEvent<HTMLSpanElement>,index:number)=>{
    event.preventDefault();
    event.stopPropagation();
    resizeCleanup.current?.();
    const startX=event.clientX;
    const startWidths=[...widths];
    const startWidth=startWidths[index];
    const onMove=(moveEvent:PointerEvent)=>setWidths(resizeDataTableColumn(startWidths,columns,index,startWidth+moveEvent.clientX-startX,viewportWidth));
    const cleanup=()=>{
      window.removeEventListener("pointermove",onMove);
      window.removeEventListener("pointerup",cleanup);
      window.removeEventListener("pointercancel",cleanup);
      document.body.classList.remove("sr-column-resizing");
      resizeCleanup.current=null;
    };
    resizeCleanup.current=cleanup;
    document.body.classList.add("sr-column-resizing");
    window.addEventListener("pointermove",onMove);
    window.addEventListener("pointerup",cleanup);
    window.addEventListener("pointercancel",cleanup);
  };
  const resizeTitle=typeof document!=="undefined"&&document.documentElement.lang.startsWith("en")?"Drag to resize; double-click to reset":"拖动调整列宽，双击恢复默认宽度";
  const tableWidth=widths.reduce((sum,width)=>sum+width,0);
  const actionIndex=columns.length-1;
  return <table ref={tableRef} className={cn("sr-account-table sr-resizable-table",className)} style={{width:tableWidth,minWidth:tableWidth,maxWidth:"none"}}>
    <colgroup>{widths.map((width,index)=><col key={index} style={{width}}/>)}</colgroup>
    <thead><tr>{headers.map((header,index)=><th key={index}><span className="sr-table-header-content">{header}</span><span className={cn("sr-column-resizer",index===actionIndex&&"is-last")} role="separator" aria-orientation="vertical" tabIndex={0} title={resizeTitle} onPointerDown={(event)=>startResize(event,index)} onDoubleClick={()=>setColumnWidth(index,columns[index].width)} onKeyDown={(event)=>{if(event.key==="ArrowLeft"||event.key==="ArrowRight"){event.preventDefault();setColumnWidth(index,widths[index]+(event.key==="ArrowRight"?12:-12));}else if(event.key==="Home"){event.preventDefault();setColumnWidth(index,columns[index].width);}}}/></th>)}</tr></thead>
    {children}
  </table>;
}
type LogEntry = { id: number | string; time: string; level: string; module: string; message: string; email?: string; rawMessage?: string; detail?: AnyObj };
type RegisterStage = "register_only" | "codex_phone_bind" | "import_reverse_proxy" | "agent_identity_reverse_proxy";
type ProtocolChallengeStrategy = "native_headless" | "sentinel_protocol";
type RegistrationProgressState = "pending" | "running" | "completed" | "abnormal";
type AccountRegistrationProgress = {
  email: string;
  stage: RegisterStage;
  checkpoint: string;
  current: number;
  total: number;
  state: RegistrationProgressState;
  error?: string;
  updatedAt: number;
};
type RegistrationTaskProgress = {
  taskId: string;
  stage: RegisterStage;
  accounts: Record<string, AccountRegistrationProgress>;
  order: string[];
};
const REGISTER_ONLY: RegisterStage = "register_only";
const CODEX_PHONE_BIND: RegisterStage = "codex_phone_bind";
const IMPORT_REVERSE_PROXY: RegisterStage = "import_reverse_proxy";
const AGENT_IDENTITY_REVERSE_PROXY: RegisterStage = "agent_identity_reverse_proxy";

function registrationStageTotal(stage: RegisterStage): number {
  return stage === IMPORT_REVERSE_PROXY ? 12 : stage === CODEX_PHONE_BIND ? 10 : stage === AGENT_IDENTITY_REVERSE_PROXY ? 9 : 7;
}

function createRegistrationTaskProgress(taskId: string, stage: RegisterStage, emails: string[]): RegistrationTaskProgress {
  const normalized = Array.from(new Set(emails.map((email) => String(email || "").trim()).filter(Boolean)));
  const total = registrationStageTotal(stage);
  return {
    taskId,
    stage,
    order: normalized,
    accounts: Object.fromEntries(normalized.map((email) => [email.toLowerCase(), {
      email,
      stage,
      checkpoint: "queued",
      current: 0,
      total,
      state: "pending" as RegistrationProgressState,
      updatedAt: Date.now(),
    }])),
  };
}

const sunnyStateCache = new Map<string, unknown>();
const SUNNY_STATE_STORAGE_PREFIX = "sunnyregister.state.";
const persistedWorkbenchKeys = new Set([
  "workbench.activeTaskId",
  "workbench.activeTaskMailboxIds",
  "workbench.globalLogs",
  "workbench.selectedLogs",
  "workbench.registrationProgress",
  "workbench.globalCardView",
  "workbench.accountCardView",
  "workbench.currentLogEmail",
  "workbench.taskEventCursor",
]);

function persistedStateKey(key: string) {
  return `${SUNNY_STATE_STORAGE_PREFIX}${key}`;
}

export function clearSunnyRegisterTaskHistory() {
  for (const key of persistedWorkbenchKeys) {
    sunnyStateCache.delete(key);
    try { window.localStorage.removeItem(persistedStateKey(key)); } catch { /* storage may be unavailable */ }
  }
}

function useCachedState<T>(key: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    if (sunnyStateCache.has(key)) return sunnyStateCache.get(key) as T;
    if (persistedWorkbenchKeys.has(key)) {
      try {
        const saved = window.localStorage.getItem(persistedStateKey(key));
        if (saved !== null) {
          const parsed = JSON.parse(saved) as T;
          sunnyStateCache.set(key, parsed);
          return parsed;
        }
      } catch { /* use the initial value when persisted state is invalid */ }
    }
    return typeof initial === "function" ? (initial as () => T)() : initial;
  });
  const setCachedValue: Dispatch<SetStateAction<T>> = (next) => {
    const prev = (sunnyStateCache.has(key) ? sunnyStateCache.get(key) : value) as T;
    const resolved = typeof next === "function" ? (next as (old: T) => T)(prev) : next;
    sunnyStateCache.set(key, resolved);
    if (persistedWorkbenchKeys.has(key)) {
      try { window.localStorage.setItem(persistedStateKey(key), JSON.stringify(resolved)); } catch { /* in-memory cache still works */ }
    }
    setValue(resolved);
  };
  useEffect(() => {
    sunnyStateCache.set(key, value);
    if (persistedWorkbenchKeys.has(key)) {
      try { window.localStorage.setItem(persistedStateKey(key), JSON.stringify(value)); } catch { /* in-memory cache still works */ }
    }
  }, [key, value]);
  return [value, setCachedValue];
}

type PersistentSessionTaskKind = "refresh-at" | "acquire-rt";
type SessionTaskState = "running" | "succeeded" | "failed" | "cancelled";
type SessionRenewalProgress = {
  email: string;
  current: number;
  total: number;
  checkpoint: string;
  state: SessionTaskState;
  error?: string;
  updatedAt: number;
};
type PersistentSessionTask = {
  clientId: string;
  taskId: string;
  kind: PersistentSessionTaskKind;
  sessionIds: number[];
  email?: string;
  state: SessionTaskState;
  progress: Record<string, SessionRenewalProgress>;
  dismissedEmails: string[];
  error?: string;
};
type PersistentSessionTaskSnapshot = { tasks: PersistentSessionTask[] };

const SESSION_TASK_STORAGE_KEY = "sunnyregister.active-session-tasks";
const sessionTaskListeners = new Set<() => void>();
const sessionTaskPromises = new Map<string, Promise<AnyObj>>();

function readPersistentSessionTasks(): PersistentSessionTask[] {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(window.localStorage.getItem(SESSION_TASK_STORAGE_KEY) || "[]");
    return Array.isArray(value) ? value
      .filter((item) => item?.clientId && item?.taskId && Array.isArray(item?.sessionIds))
      .map((item) => ({ ...item, state: "running" as SessionTaskState, progress: item.progress || {}, dismissedEmails: [] })) : [];
  } catch {
    return [];
  }
}

let sessionTaskSnapshot: PersistentSessionTaskSnapshot = { tasks: readPersistentSessionTasks() };

function publishSessionTasks(tasks: PersistentSessionTask[]) {
  sessionTaskSnapshot = { tasks };
  try {
    window.localStorage.setItem(SESSION_TASK_STORAGE_KEY, JSON.stringify(tasks
      .filter((task) => task.taskId && task.state === "running")
      .map((task) => ({ ...task, dismissedEmails: [] }))));
  } catch { /* in-memory state remains available */ }
  sessionTaskListeners.forEach((listener) => listener());
}

function upsertSessionTask(task: PersistentSessionTask) {
  const exists = sessionTaskSnapshot.tasks.some((item) => item.clientId === task.clientId);
  publishSessionTasks(exists
    ? sessionTaskSnapshot.tasks.map((item) => item.clientId === task.clientId ? task : item)
    : [...sessionTaskSnapshot.tasks, task]);
}

function updateSessionTask(clientId: string, updater: (task: PersistentSessionTask) => PersistentSessionTask) {
  publishSessionTasks(sessionTaskSnapshot.tasks.map((task) => task.clientId === clientId ? updater(task) : task));
}

function dismissSessionRenewal(clientId: string, email: string) {
  const key = email.toLowerCase();
  updateSessionTask(clientId, (task) => ({ ...task, dismissedEmails: Array.from(new Set([...task.dismissedEmails, key])) }));
}

function clearPreviousSessionTaskResults(kind: PersistentSessionTaskKind, sessionIds: number[]) {
  const ids = new Set(sessionIds.map(Number));
  publishSessionTasks(sessionTaskSnapshot.tasks.filter((task) => task.state === "running" || task.kind !== kind || !task.sessionIds.some((id) => ids.has(Number(id)))));
}

function subscribeSessionTasks(listener: () => void) {
  sessionTaskListeners.add(listener);
  return () => sessionTaskListeners.delete(listener);
}

const renewalCheckpointFromRegistration: Record<string, { current: number; checkpoint: string }> = {
  initializing: { current: 5, checkpoint: "headless_login_started" },
  proxy_ready: { current: 6, checkpoint: "proxy_ready" },
  browser_started: { current: 7, checkpoint: "authentication_running" },
  protocol_started: { current: 7, checkpoint: "authentication_running" },
  email_submitted: { current: 7, checkpoint: "authentication_running" },
  email_verified: { current: 8, checkpoint: "session_reading" },
  auth_completed: { current: 8, checkpoint: "session_reading" },
  registered: { current: 8, checkpoint: "session_refreshed" },
};

function applySessionTaskEvents(clientId: string, events: AnyObj[]) {
  if (!events.length) return;
  updateSessionTask(clientId, (task) => {
    if (task.kind !== "refresh-at") return task;
    const progress = { ...task.progress };
    events.forEach((event) => {
      const detail = event.detail || {};
      const email = String(detail.email || "").trim();
      if (!email) return;
      const key = email.toLowerCase();
      if (event.type === "renewal_progress" || detail.progress_type === "access_token_renewal") {
        progress[key] = {
          email,
          current: Math.max(0, Number(detail.current || 0)),
          total: Math.max(1, Number(detail.total || 1)),
          checkpoint: String(detail.checkpoint || "preparing"),
          state: detail.state === "succeeded" ? "succeeded" : detail.state === "failed" ? "failed" : "running",
          error: String(detail.error || ""),
          updatedAt: Date.now(),
        };
        return;
      }
      if (event.type === "registration_progress" || detail.progress_type === "account_registration") {
        const mapped = renewalCheckpointFromRegistration[String(detail.checkpoint || "")];
        if (!mapped) return;
        const existing = progress[key];
        progress[key] = {
          email,
          current: Math.max(existing?.current || 0, mapped.current),
          total: 9,
          checkpoint: mapped.checkpoint,
          state: detail.state === "abnormal" ? "failed" : "running",
          error: String(detail.error || existing?.error || ""),
          updatedAt: Date.now(),
        };
      }
    });
    return { ...task, progress };
  });
}

function markSessionTaskTerminal(clientId: string, current: AnyObj) {
  updateSessionTask(clientId, (task) => {
    const result = current.result || {};
    const errors: string[] = Array.isArray(result.errors) ? result.errors.map((message: unknown) => String(message)) : [];
    const items = Array.isArray(result.items) ? result.items : [];
    const progress = { ...task.progress };
    const knownEmails = Array.from(new Set([
      ...Object.values(progress).map((entry) => entry.email),
      ...items.map((entry: AnyObj) => String(entry.email || "")),
      ...(task.email ? [task.email] : []),
    ].map((email) => email.trim()).filter(Boolean)));
    const cancelled = ["cancelled", "interrupted"].includes(String(current.status || "").toLowerCase());
    knownEmails.forEach((email) => {
      if (!email) return;
      const key = email.toLowerCase();
      const error = errors.find((message) => message.toLowerCase().includes(`[${key}]`)) || "";
      const existing = progress[key];
      const succeeded = items.some((entry: AnyObj) => String(entry.email || "").toLowerCase() === key) && !error;
      progress[key] = {
        email,
        current: succeeded ? Math.max(existing?.total || 1, existing?.current || 0) : existing?.current || 0,
        total: existing?.total || (succeeded ? 1 : 9),
        checkpoint: succeeded ? "completed" : cancelled ? "cancelled" : "failed",
        state: succeeded ? "succeeded" : cancelled ? "cancelled" : "failed",
        error: error || (!succeeded ? String(current.error || "") : ""),
        updatedAt: Date.now(),
      };
    });
    const failed = errors.length > 0 || current.status === "failed";
    const state: SessionTaskState = cancelled ? "cancelled" : failed && Number(result.success || current.success_count || 0) === 0 ? "failed" : "succeeded";
    return { ...task, progress, state, error: errors[0] || String(current.error || "") };
  });
}

function ensureSessionTaskPolling(task: PersistentSessionTask, initial?: AnyObj): Promise<AnyObj> {
  const existing = sessionTaskPromises.get(task.clientId);
  if (existing) return existing;
  const promise = (async () => {
    let since = 0;
    let current = initial || await apiFetch(`/tasks/${task.taskId}`);
    while (!current.terminal) {
      const eventResult = await apiFetch(`/tasks/${task.taskId}/events?since=${since}`).catch(() => ({ items: [] }));
      const events = Array.isArray(eventResult.items) ? eventResult.items : [];
      if (events.length) {
        since = Math.max(since, ...events.map((event: AnyObj) => Number(event.id || 0)));
        applySessionTaskEvents(task.clientId, events);
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
      current = await apiFetch(`/tasks/${task.taskId}`);
    }
    const finalEvents = await apiFetch(`/tasks/${task.taskId}/events?since=${since}`).catch(() => ({ items: [] }));
    applySessionTaskEvents(task.clientId, Array.isArray(finalEvents.items) ? finalEvents.items : []);
    markSessionTaskTerminal(task.clientId, current);
    return current;
  })().catch((error) => {
    updateSessionTask(task.clientId, (item) => ({ ...item, state: "failed", error: error instanceof Error ? error.message : String(error) }));
    throw error;
  }).finally(() => {
    sessionTaskPromises.delete(task.clientId);
  });
  sessionTaskPromises.set(task.clientId, promise);
  return promise;
}

async function runPersistentSessionTask(
  kind: PersistentSessionTaskKind,
  sessionIds: number[],
  email: string | undefined,
  createTask: () => Promise<AnyObj>,
) {
  const clientId = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
  clearPreviousSessionTaskResults(kind, sessionIds);
  const pending: PersistentSessionTask = { clientId, taskId: "", kind, sessionIds, email, state: "running", progress: {}, dismissedEmails: [] };
  upsertSessionTask(pending);
  try {
    const created = await createTask();
    const active = { ...pending, taskId: String(created.id || "") };
    if (!active.taskId) throw new Error("Task ID is missing");
    upsertSessionTask(active);
    return await ensureSessionTaskPolling(active, created);
  } catch (error) {
    updateSessionTask(clientId, (task) => ({ ...task, state: "failed", error: error instanceof Error ? error.message : String(error) }));
    throw error;
  }
}

function usePersistentSessionTasks() {
  const snapshot = useSyncExternalStore(subscribeSessionTasks, () => sessionTaskSnapshot, () => sessionTaskSnapshot);
  useEffect(() => {
    snapshot.tasks.forEach((task) => {
      if (task.taskId && task.state === "running") void ensureSessionTaskPolling(task).catch(() => undefined);
    });
  }, [snapshot]);
  return snapshot.tasks;
}

function prependUniqueLogs(entries: LogEntry[], existing: LogEntry[], limit = 200): LogEntry[] {
  if (!entries.length) return existing;
  const known = new Set(existing.map((item) => String(item.id)));
  return [...entries.filter((item) => !known.has(String(item.id))), ...existing].slice(0, limit);
}

function useDebouncedValue<T>(value: T, delay = 260): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function useLoadingTracker() {
  const [loading, setLoading] = useState(false);
  const pendingRef = useRef(0);
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  async function track<T>(task: () => Promise<T>): Promise<T> {
    pendingRef.current += 1;
    if (mountedRef.current) setLoading(true);
    try {
      return await task();
    } finally {
      pendingRef.current = Math.max(0, pendingRef.current - 1);
      if (mountedRef.current && pendingRef.current === 0) setLoading(false);
    }
  }

  return { loading, track };
}

function ListLoadingOverlay({ loading, label }: { loading: boolean; label: string }) {
  if (!loading) return null;
  return <div className="sr-list-loading" role="status" aria-live="polite">
    <div className="sr-list-loading-content"><Loader2 className="h-5 w-5 animate-spin"/><span>{label}</span></div>
  </div>;
}

const zh: AnyObj = new Proxy({
  workbench: "工作台", mailbox: "邮箱配置", phone: "接码配置", sub2api: "反代配置", proxy: "代理配置", session: "账户管理",
  title: "SunnyRegister 注册机控制台", desc: "使用自建邮箱池注册/登录 GPT 账户，并统一管理账户状态、Session、RT 和日志。",
  register: "注册或登录", refresh: "刷新", import: "导入", save: "保存", export: "导出", copy: "复制", close: "关闭", copySuccess: "复制成功", secretKeyUnavailable: "该邮箱凭证信息不完整，无法复制 SK", newGroup: "新建分组", move: "迁移到分组",
  mailboxTip: "微软邮箱支持 OAuth 四段凭证；Apple iCloud 邮箱按所选渠道使用 API 查询邮件与验证码。",
  mailboxPoolName: "自建邮箱池", mailboxPoolGlobalSwitch: "使用自建邮箱池", mailboxPoolSwitchTip: "关闭后，注册机不会从自建邮箱池分配邮箱。", mailboxOverviewTotal: "邮箱总数", mailboxOverviewPending: "待注册", mailboxOverviewRegistered: "已注册", mailboxOverviewPhoneBound: "已接码", mailboxOverviewReversed: "已反代", mailboxOverviewBanned: "已封禁", mailboxOverviewNeeds2FA: "待二验", mailboxOverviewRefreshing: "登录刷新", mailboxOverviewFailed: "失败",
  phoneTip: "格式：+手机号----接码链接。成功后冷却 5 小时，最多 3 次。", phonePool: "自建手机号池", phonePoolGlobalSwitch: "使用自建手机号池", importPhones: "导入手机号", phonePoolSwitchTip: "关闭后，注册机不会从自建手机号池分配号码；后续可切换为外部接码平台。", phonePoolOn: "可用于接码", phonePoolOff: "不用于接码", phoneImportHelp: "每行一个长效接码：第一个字符必须是 +，手机号与接码链接之间必须使用四个中横线 ---- 连接。", phoneImportPlaceholder: "+12025550123----https://sms.example.com/messages?token=example", phoneImportInvalid: "手机号导入格式错误", phoneSearch: "搜索手机号...", phoneNumber: "手机号", smsLink: "接码链接", usedCount: "已用次数", countFilter: "次数筛选", allCount: "全部次数", lastUsedAt: "最近使用时间", phoneEdit: "编辑手机号", phoneStatusEnabled: "启用", phoneStatusDisabled: "停用", phoneConfirmDelete: "确认删除该手机号？此操作不可撤销。", phoneConfirmBatchDelete: "确认删除选中的手机号？此操作不可撤销。",
  lubanProvider: "LubanSMS 接码供应商", lubanDesc: "通过供应商编号从 LubanSMS 获取一次性手机号并自动轮询验证码。", lubanSwitch: "启用 LubanSMS", lubanApiKey: "API Key", lubanServiceId: "供应商编号", lubanBaseURL: "接口地址", lubanCheck: "检测连接", lubanChecked: "LubanSMS 连接正常", lubanSaved: "LubanSMS 配置已保存",
  smsbowerProvider: "SMSBower 接码供应商", smsbowerDesc: "当自建手机号池不可用或无可用号码时，注册机会使用 SMSBower API 自动获取一次性手机号。", smsbowerSwitch: "启用 SMSBower", smsbowerReady: "SMSBower 已配置", smsbowerApiKey: "API Key", smsbowerCountry: "默认国家", smsbowerService: "默认服务", smsbowerMaxPrice: "最大价格", smsbowerBaseURL: "接口地址", smsbowerCheck: "检测余额", smsbowerBalance: "余额：{balance}", smsbowerSaved: "SMSBower 配置已保存",
  smspoolProvider: "SMSPool 接码供应商", smspoolDesc: "SMSPool 临时号码平台，可在自建手机号池和 SMSBower 不可用时自动购买一次性接码号码。", smspoolSwitch: "启用 SMSPool", smspoolReady: "SMSPool 已配置", smspoolApiKey: "API Key", smspoolCountry: "默认国家", smspoolService: "默认服务", refreshProviderOptions: "获取列表", providerOptionSearch: "搜索 ID、代码或名称...", providerOptionNoResults: "没有匹配的选项", smspoolMaxPrice: "最大价格", smspoolBaseURL: "接口地址", smspoolCheck: "检测余额", smspoolBalance: "余额：{balance}", smspoolSaved: "SMSPool 配置已保存",
  firefoxProvider: "FireFox 接码供应商", firefoxDesc: "FireFox 临时号码平台。取号时强制限制最高单价，每 5 秒轮询验证码，不可用号码将在 35 秒后自动释放。", firefoxSwitch: "启用 FireFox", firefoxReady: "FireFox 已配置", firefoxApiName: "API 账号", firefoxPassword: "API 密码", firefoxCountry: "默认国家", firefoxService: "默认服务", firefoxMaxPrice: "单号最高价格", firefoxBaseURL: "接口地址", firefoxCheck: "检测余额", firefoxBalance: "余额：{balance}", firefoxSaved: "FireFox 配置已保存", firefoxMaxPriceRequired: "FireFox 单号最高价格必须大于 0",
  proxyTip: "管理注册机发起注册/登录请求时使用的出站代理池；批量检测仅检测代理服务连通性，不访问 ChatGPT 官网。", proxyPool: "代理池", proxyEnabled: "启用", proxyDisabled: "停用", proxyAvailable: "失效", proxySearch: "搜索代理地址...", proxyCountry: "国家", proxyAllCountry: "全部国家", proxyAddress: "代理地址", proxyBatchCheck: "批量检测", proxyBatchDelete: "批量删除", proxyBatchEdit: "批量修改", proxyAdd: "新增代理", proxyEdit: "编辑代理", proxyCheckDone: "代理检测完成", proxyNoData: "暂无代理", proxyNoDataDesc: "请先新增代理地址，再对启用代理进行批量检测。", proxyStatusEnabled: "启用", proxyStatusDisabled: "停用", proxyStatusInvalid: "失效", proxyLastChecked: "上次检测", proxyLatency: "延迟", proxyCountryPlaceholder: "例如 US / HK / JP / Brazil", proxyCountryKeep: "留空保持各代理原国家不变", proxyAddressPlaceholder: "每行一个代理，例如 http://user:pass@host:port 或 socks5://host:port", proxyConfirmDelete: "确认删除该代理？此操作不可撤销。", proxyConfirmBatchDelete: "确认删除选中的代理？此操作不可撤销。", proxyTrafficSwitch: "注册流量代理", proxyTrafficOn: "代理开启", proxyTrafficOff: "代理关闭", proxyTrafficOnHint: "注册/登录请求走代理池", proxyTrafficOffHint: "使用服务器系统网络出口", proxySwitchSaved: "代理出口设置已更新",
  selected: "已选", selectedItems: "已选 {count} 项", selectAll: "全选", selectAllDone: "已选中当前筛选结果中的 {count} 条记录", clearSelection: "清除选择", globalLogs: "全局日志", selectedLogs: "当前邮箱日志", registrationTaskProgress: "注册任务进度", accountRegistrationProgress: "账户注册进度", clearLogs: "清除", latest: "查询最近邮件", done: "操作完成", failed: "操作失败", file: "选择文件", status: "状态", prev: "上一页", next: "下一页", pageSize: "每页", pageInfo: "第 {page} / {pages} 页", pageRange: "显示 {from} 至 {to} 共 {total} 条结果", noLogs: "暂无日志", noRegistrationTask: "暂无注册任务", noAccountProgress: "暂无正在处理的邮箱账户", taskTotal: "任务总数", taskCompleted: "当前完成", completedAccounts: "已完成注册", pendingAccounts: "未完成注册", abnormalAccounts: "注册状态异常", currentStep: "当前步骤", total: "总计", yes: "是", no: "否", step: "步骤",
  progressSteps: { queued: "等待任务调度", initializing: "初始化邮箱任务", proxy_ready: "代理与出口准备完成", browser_started: "启动隔离浏览器", protocol_started: "建立纯协议注册会话", email_submitted: "提交注册邮箱", email_verified: "完成邮箱验证码验证", auth_completed: "完成注册或登录认证", registered: "保存 ChatGPT Session", phone_started: "分配手机号并开始接码", phone_code_received: "收到并提交手机验证码", phone_bound: "完成 Codex 接码绑定", reverse_importing: "正在导入反代平台", reverse_imported: "完成反代平台导入", agent_identity_importing: "正在创建 Agent Identity 并导入反代平台", agent_identity_imported: "完成 Agent Identity 反代导入", stage_incomplete: "目标阶段未完成", cancelled: "任务已由用户中断", failed: "注册流程异常" },
  logProxy: "代理", logMailbox: "邮箱", logPhone: "手机", logSession: "Session", logAuth: "认证", logSystem: "系统",
  defaultGroup: "默认分组", allGroups: "全部分组", mailboxGroup: "所属分组", groupSearch: "搜索邮箱分组...", groupNoResults: "没有匹配的邮箱分组", importMailboxes: "导入邮箱", manualImport: "手动导入", fileImport: "文件导入", dragFile: "拖拽邮箱文件到这里，或点击选择文件", importToGroup: "导入到分组", addGroup: "新建分组", editGroup: "编辑分组名", deleteGroup: "删除分组", enterGroup: "输入分组名后回车", groupCreated: "邮箱分组新建成功", groupRenamed: "邮箱分组名称修改成功", groupDeleted: "邮箱分组删除成功", groupNotEmpty: "该邮箱分组下存在邮箱账户，请移除后再删除分组", defaultGroupCannotDelete: "默认分组不能删除", groupNameConflict: "邮箱分组名称已存在", confirmDeleteGroup: "确认删除该邮箱分组？", mailboxCount: "邮箱数量", validationOk: "校验通过", validationFailed: "校验失败", mailboxList: "邮箱列表", enabled: "启用", updatedAt: "更新时间", actions: "操作", queryMailbox: "搜索邮箱...", allStatus: "全部状态", allPlanTypes: "全部套餐", edit: "编辑", delete: "删除", batchDelete: "批量删除", batchEdit: "批量编辑", confirmDeleteMailbox: "确认删除该邮箱记录？此操作不可撤销。", confirmBatchDeleteMailbox: "确认删除选中的邮箱记录？此操作不可撤销。", queryMail: "邮件查询", currentMailbox: "当前邮箱", getMail: "获取邮件", mailFetchCount: "查询数量", mailFetchCountSuffix: "封", mailList: "邮件列表", sender: "发件人", receiver: "收件人", time: "时间", subject: "主题", content: "邮件内容", emptyMail: "暂无邮件", mailboxName: "邮箱名", password: "密码", chatgptPassword: "ChatGPT 密码", totpSecret: "2FA 密钥", keepCredential: "留空则保持现有凭证", clearCredential: "清除现有凭证", clientId: "client_id", refreshToken: "refresh_token", openaiAccessToken: "OpenAI Access Token", batchEditMailboxTitle: "批量编辑邮箱", applyToSelected: "应用到选中的邮箱", mailboxType: "邮箱类型", microsoftMailbox: "微软邮箱", appleMailbox: "苹果邮箱", channelType: "渠道类型", xbovoChannel: "xbovo", xbovoChannelTip: "验证码查询入口：https://icloud.xbovo.online/code", urlAPIChannel: "url_api", urlAPIChannelTip: "通过邮箱专属取码 URL 查询最新邮件，单次响应最长约 30 秒", icloudAccessKey: "查询 Key", icloudQueryURL: "取码 URL", urlAPIBrowser: "URL API 邮件浏览器", browserBack: "后退", browserForward: "前进", browserReload: "刷新页面", browserLoading: "正在加载邮件页面", browserGetOnly: "当前预览仅支持网页链接和 GET 表单跳转",
  autoRegister: "自动注册", interruptTask: "停止", interruptingTask: "停止中...", interruptTaskTip: "停止整批注册任务，包括提交、排队、Worker 启动和邮箱执行阶段。", interruptTaskRequested: "已请求停止整批注册任务，正在关闭任务进程、浏览器与邮箱读取资源", interruptTaskFailed: "停止任务失败", registerTaskRunning: "当前注册任务正在执行，请等待任务结束或先停止任务", manualNew: "手动新增", searchAccount: "搜索账号邮箱...", refreshQuota: "刷新额度", refreshList: "刷新列表", refreshDone: "列表已刷新", loadingData: "正在更新数据...", refreshStatus: "刷新账号状态", statusChangedAt: "状态变更时间", planType: "套餐类型", email: "邮箱", trialLink: "试用链接", registeredAt: "注册时间", operation: "操作", noData: "暂无数据", noDataDesc: "当前平台没有找到任何账号记录。请先到邮箱配置中导入邮箱，然后选择邮箱进行自动注册。", chooseMailbox: "请选择邮箱", createTaskLog: "创建 ChatGPT 注册任务，数量", taskSubmitted: "注册任务已提交，正在开始执行", taskCreated: "自动注册任务已创建", taskDone: "任务完成", taskFailed: "任务失败", taskPollRecovered: "检测到上次注册任务仍在进行，已恢复日志轮询", taskPollLost: "任务状态轮询暂时失败，将继续等待任务状态：{error}", taskPollTimeout: "任务状态轮询时间较长，仍将继续等待；可使用停止按钮中断任务", importDone: "导入完成", exportDone: "导出完成", manualNewTip: "请到邮箱配置中手动新增邮箱", autoRegisterTitle: "自动注册 ChatGPT", step1Title: "选择注册身份", step1Desc: "当前优先使用自建 Outlook/Hotmail 邮箱池进行邮箱验证。", systemMailbox: "系统邮箱", systemMailboxPoolDisabled: "系统邮箱池功能未启用，请先启用邮箱池功能", smsConfigDisabled: "请前往接码配置页面启用接码配置", registerStageUnavailable: "请先启用至少一种邮箱注册方式", googleMailboxDisabled: "Google 邮箱功能未启用，请先启用对应的邮箱功能", microsoftMailboxDisabled: "Microsoft 邮箱功能未启用，请先启用对应的邮箱功能", systemMailboxDesc: "使用邮箱池自动收取验证码并完成注册", googleDesc: "预留身份，后续接入 Google 账号", microsoftDesc: "预留身份，后续接入 Microsoft 账号", step2Title: "选择执行方式", step2Desc: "支持后台浏览器自动与可视浏览器自动；后台模式不显示窗口，更适合批量执行。", protocolMode: "协议模式", protocolDesc: "占位能力，暂未开放选择", protocolChallengeStrategy: "浏览器挑战策略", protocolNativeChallenge: "原生无头接管", protocolNativeChallengeDesc: "遇到挑战时由完整后台浏览器接管注册流程", protocolSentinelChallenge: "Sentinel 协议运行时", protocolSentinelChallengeDesc: "注册请求保持协议模式，仅用窄范围 Camoufox 生成浏览器证明", backgroundMode: "后台浏览器自动", backgroundDesc: "无窗口 Headless 执行，仍使用隔离无痕浏览器上下文自动注册", visibleMode: "可视浏览器自动", visibleDesc: "会打开浏览器窗口，适合排查人机验证或页面异常", registerCount: "注册数量", concurrency: "并发数", identityLabel: "注册身份", modeLabel: "执行方式", registerAccounts: "注册账号", verifyStrategy: "验证策略：自动识别 Outlook/Hotmail Graph API 或 IMAP/XOAUTH2 并读取验证码", step3Title: "选择注册阶段", step3Desc: "控制本次任务执行到哪个阶段，默认仅完成 ChatGPT 注册/登录与 Session 存储。", registerOnly: "仅注册 ChatGPT", registerOnlyDesc: "注册或登录成功后，只读取并保存 ChatGPT Session 信息", codexPhoneBind: "Codex接码绑定", codexPhoneBindDesc: "注册/登录后继续使用接码配置完成手机验证并获取 Refresh Token", importReverseProxy: "导入反代平台", importReverseProxyDesc: "完成账号 Session/RT 后导入已配置的 sub2api 反代平台", agentIdentityReverseProxy: "绕过接码导入反代平台", agentIdentityReverseProxyDesc: "注册/登录后使用 Access Token 创建 Agent Identity，跳过手机号绑定并直接导入 sub2api", stageLabel: "注册阶段", startAutoRegister: "开始自动注册", cancel: "取消", noMailbox: "暂无邮箱", noMailboxDesc: "请点击右上角“导入邮箱”添加自建 Outlook/Hotmail 邮箱池。", inbox: "收件箱", fillOrChooseMailboxFile: "请先填写或选择邮箱文件",
  sub2apiDesc: "用于“导入反代平台”阶段。填写 sub2api 地址与管理员 Key 后，注册任务可将已获取 Session/RT 的 GPT 账号导入平台。", baseURL: "Base URL", adminToken: "Admin Token", accountNamePrefix: "账号名前缀", targetGroup: "目标分组", targetGroupPlaceholder: "请选择目标分组", noGroupsFetch: "暂无分组，请点击右侧“获取”", fetch: "获取", priority: "优先级", remoteProxy: "远端代理", noRemoteProxy: "不使用远端代理", loadFactor: "负载因子", modelWhitelist: "模型白名单", importSub2API: "导入反代", importingSub2API: "导入中...", sub2NoSelection: "请选择需要导入反代的账户", sub2ImportSummary: "反代导入完成：选中 {selected} 个，提交 {uploaded} 个，确认成功 {confirmed} 个，失败 {failed} 个，跳过 {skipped} 个", sub2ImportDetails: "反代导入明细", check: "检测", configUnchanged: "配置未更改", fillURLToken: "请先填写 Base URL 和 Admin Token", fetchedGroups: "已获取 {count} 个目标分组", fillURLTokenShort: "请先填写 URL 和 Token", checking: "检测中...", checkPassedGroups: "检测通过，发现 {count} 个分组", checkFailed: "检测失败：{error}", lineFormatPhone: "+手机号----https://接码链接", sessionJSON: "Auth Session", accessToken: "Access Token", mailboxAccountExport: "邮箱账户", exportFormat: "导出内容", selectExportRows: "请选择需要导出的账号", tokenPreview: "Token预览", sessionRefreshToken: "Refresh Token", secretKey: "Secret Key", allInfo: "全部信息", exportSK: "导出SK", exportAT: "导出AT", exportSUB: "导出SUB", acquireRT: "获取", acquiringRT: "正在获取RT", acquireRTDone: "账户 {email} 的 RT 已获取", acquireRTFailed: "无法获取该账户RT", sessionFieldTitle: "查看 {field}", sessionFieldLoading: "正在获取 {field}，请耐心等待...", sessionFieldEmpty: "该账户暂无 {field}", updated: "更新时间", groupFilter: "所属分组", atExpiresAt: "AT过期时间", atInvalidOrExpired: "AT无效或已过期", atRenewalFailed: "AT续期失败", atProbeFailed: "AT检测失败", lastHealthCheckedAt: "最近测活时间", accountHealthCheckFailed: "账户测活失败", refreshAT: "续期", refreshingAT: "续期中...", updateAT: "续期", refreshATDone: "账户 {email} 的 AT 已更新（AT续期）", refreshATSummary: "AT检测完成：有效 {valid} 个，无效 {invalid} 个，检测失败 {failed} 个；已对无效账户启动续期", refreshATNoSelection: "请选择需要续期 AT 的账户", stopRenewal: "停止续期", stoppingRenewal: "停止中...", stopRenewalTip: "停止当前所有正在执行或等待执行的 AT 续期任务", stopRenewalRequested: "已请求停止续期任务，正在关闭当前续期流程；等待中的账户将不再执行", stopRenewalFailed: "停止续期任务失败", closeRenewalProgress: "关闭续期进度", healthCheck: "测活", healthChecking: "测活中...", healthCheckSummary: "测活完成：测试 {total} 个，存活 {alive} 个，封禁 {banned} 个，失败 {failed} 个", healthAlive: "账户 {email}：存活", healthBanned: "账户 {email}：已封禁", currentATValid: "当前AT有效，无需续期", currentATInvalid: "当前AT无效，已启动AT续期任务", atCheckFailed: "AT检测失败：{error}", alreadyBanned: "该账户当前状态无需测活", healthNoSelection: "请选择需要测活的账户", failureDetails: "失败详情", maintenanceSettings: "定时任务设置", healthSchedule: "账户定时测活", atSchedule: "AT定时检测", scheduleTime: "执行时间", scheduleFrequency: "执行频率（小时）", restartRequired: "设置已保存，将在下一次服务重启后生效", saveSettings: "保存设置",
  subscriptionCheck: "订阅", subscriptionChecking: "检测中...", subscriptionNoSelection: "请选择需要检测订阅的账户", subscriptionCheckSummary: "订阅检测完成：检测 {total} 个，已订阅 {subscribed} 个，未检测到 {notSubscribed} 个，失败 {failed} 个", subscriptionConfirmed: "账户 {email}：已订阅 Plus", subscriptionNotFound: "账户 {email}：未检测到订阅成功邮件", subscriptionCheckFailed: "账户 {email}：订阅检测失败",
  trialEligibility: "试用资格", allTrialEligibility: "全部试用资格", trialCheck: "试用", trialChecking: "检测中...", trialEligible: "有0元试用", trialIneligible: "无0元试用", trialUnknown: "未检测", trialNoSelection: "请选择需要检测试用资格的账户", trialUnavailable: "仅已注册且套餐为 free 的账户支持试用检测", trialCheckSummary: "试用检测完成：检测 {total} 个，有资格 {eligible} 个，无资格 {ineligible} 个，跳过 {skipped} 个，失败 {failed} 个", trialEligibleResult: "账户 {email}：有0元试用", trialIneligibleResult: "账户 {email}：无0元试用", trialCheckFailed: "账户 {email}：试用资格检测失败",
  renewalSteps: { queued: "等待续期任务调度", preparing: "准备续期任务", credentials_loaded: "读取账户凭证", refresh_token_ready: "Refresh Token 已就绪", token_received: "已获取新 Access Token", saving_session: "正在保存 Session", session_saved: "Session 已更新", refresh_token_unavailable: "Refresh Token 续期不可用，切换登录流程", refresh_token_missing: "未发现 Refresh Token，切换登录流程", mailbox_ready: "邮箱凭证已就绪", protocol_login_started: "正在启动协议登录与原生挑战接管", headless_login_started: "正在启动后台无头登录", headless_login_fallback: "协议登录未完成，正在降级到后台无头登录", sentinel_login_retry: "认证证明失效，正在建立新会话重试", proxy_ready: "代理与网络出口准备完成", authentication_running: "正在完成账户登录验证", session_reading: "正在读取并更新 Session", session_refreshed: "Session 已刷新", stopping: "正在停止续期任务", cancelled: "续期已中断", completed: "续期成功", failed: "续期失败" },
  linkedMailboxConfig: "联动邮箱配置", linkedPhoneConfig: "联动接码配置", linkedReverseConfig: "联动反代配置", resourceReady: "可用", resourceMissing: "不可用", usablePhones: "可用手机号 {count} 个", existingRTReady: "所选账号已有 RT，无需接码", sub2apiReady: "sub2api 已配置", sub2apiMissing: "sub2api 未完整配置", stageDisabledTip: "该阶段依赖的配置暂不可用，请先完成对应菜单配置。",
  statusLabels: { "未注册": "未注册", "已注册": "已注册", "registered": "已注册", "已接码": "已接码", "phone_bound": "已接码", "已反代": "已反代", "reverse_proxied": "已反代", "已封禁": "已封禁", "需二验": "需二验", "注册中": "注册中", "登录刷新": "登录刷新", "失败": "失败", "failed": "失败", "禁用": "禁用" },
}, {
  get(target, prop) {
    if (typeof prop === 'string') return prop in target ? (target as AnyObj)[prop] : prop;
    return (target as AnyObj)[prop as any];
  }
}) as AnyObj;
const en = {
  workbench: "Workbench", mailbox: "Mailbox", phone: "SMS", sub2api: "Reverse Proxy", proxy: "Proxy", session: "Account Management",
  title: "SunnyRegister Console", desc: "Register/login GPT accounts with a self-managed mailbox pool, then manage account status, sessions, RTs and logs.",
  register: "Register / Login", refresh: "Refresh", import: "Import", save: "Save", export: "Export", copy: "Copy", close: "Close", copySuccess: "Copied", secretKeyUnavailable: "This mailbox credential is incomplete and its SK cannot be copied", newGroup: "New Group", move: "Move Group",
  mailboxTip: "Microsoft mailboxes use four-part OAuth credentials. Apple iCloud mailboxes use the selected channel API for mail and OTP queries.", mailboxPoolName: "Self-managed Mailbox Pool", mailboxPoolGlobalSwitch: "Use Self-managed Mailbox Pool", mailboxPoolSwitchTip: "When disabled, SunnyRegister will not allocate mailboxes from the self-managed pool.", mailboxOverviewTotal: "Total Mailboxes", mailboxOverviewPending: "Pending", mailboxOverviewRegistered: "Registered", mailboxOverviewPhoneBound: "Phone Bound", mailboxOverviewReversed: "Reverse Proxied", mailboxOverviewBanned: "Banned", mailboxOverviewNeeds2FA: "Needs 2FA", mailboxOverviewRefreshing: "Login Refresh", mailboxOverviewFailed: "Failed",
  phoneTip: "Format: +phone----SMS URL. Cooldown 5 hours after success, max 3 successes.",
  lubanProvider: "LubanSMS Provider", lubanDesc: "Rent one-time phone numbers by LubanSMS service ID and poll verification codes automatically.", lubanSwitch: "Enable LubanSMS", lubanApiKey: "API Key", lubanServiceId: "Service ID", lubanBaseURL: "API URL", lubanCheck: "Check Connection", lubanChecked: "LubanSMS connection succeeded", lubanSaved: "LubanSMS config saved",
  phonePool: "Self-managed Phone Pool", phonePoolGlobalSwitch: "Use Self-managed Phone Pool", importPhones: "Import Phones", phonePoolSwitchTip: "When disabled, SunnyRegister will not allocate numbers from this phone pool. You can switch to external SMS providers later.", phonePoolOn: "Usable for SMS", phonePoolOff: "Not used for SMS", phoneImportHelp: "One long-lived SMS record per line. The first character must be +, and the phone number and SMS URL must be separated with exactly four hyphens: ----.", phoneImportPlaceholder: "+12025550123----https://sms.example.com/messages?token=example", phoneImportInvalid: "Invalid phone import format", phoneSearch: "Search phone number...", phoneNumber: "Phone Number", smsLink: "SMS Link", usedCount: "Used Count", countFilter: "Count", allCount: "All Counts", lastUsedAt: "Last Used", phoneEdit: "Edit Phone", phoneStatusEnabled: "Enabled", phoneStatusDisabled: "Disabled", phoneConfirmDelete: "Delete this phone number? This cannot be undone.", phoneConfirmBatchDelete: "Delete selected phone numbers? This cannot be undone.", smsbowerProvider: "SMSBower Provider", smsbowerDesc: "When the self-managed phone pool is unavailable or empty, SunnyRegister can use SMSBower API to rent a one-time number automatically.", smsbowerSwitch: "Enable SMSBower", smsbowerReady: "SMSBower configured", smsbowerApiKey: "API Key", smsbowerCountry: "Default Country", smsbowerService: "Default Service", smsbowerMaxPrice: "Max Price", smsbowerBaseURL: "API URL", smsbowerCheck: "Check Balance", smsbowerBalance: "Balance: {balance}", smsbowerSaved: "SMSBower config saved", smspoolProvider: "SMSPool Provider", smspoolDesc: "SMSPool is a temporary-number provider used when the self-managed phone pool and SMSBower are unavailable.", smspoolSwitch: "Enable SMSPool", smspoolReady: "SMSPool configured", smspoolApiKey: "API Key", smspoolCountry: "Default Country", smspoolService: "Default Service", refreshProviderOptions: "Fetch options", providerOptionSearch: "Search by ID, code, or name...", providerOptionNoResults: "No matching options", smspoolMaxPrice: "Max Price", smspoolBaseURL: "API URL", smspoolCheck: "Check Balance", smspoolBalance: "Balance: {balance}", smspoolSaved: "SMSPool config saved", firefoxProvider: "FireFox Provider", firefoxDesc: "FireFox temporary-number provider. Acquisitions enforce a maximum unit price, poll every 5 seconds, and release rejected numbers after 35 seconds.", firefoxSwitch: "Enable FireFox", firefoxReady: "FireFox configured", firefoxApiName: "API Account", firefoxPassword: "API Password", firefoxCountry: "Default Country", firefoxService: "Default Service", firefoxMaxPrice: "Maximum Unit Price", firefoxBaseURL: "API URL", firefoxCheck: "Check Balance", firefoxBalance: "Balance: {balance}", firefoxSaved: "FireFox config saved", firefoxMaxPriceRequired: "FireFox maximum unit price must be greater than 0",
  proxyTip: "Manage the outbound proxy pool for register/login requests. Batch check only tests proxy server connectivity and does not access chatgpt.com.",
  proxyPool: "Proxy Pool", proxyEnabled: "Enabled", proxyAvailable: "Invalid", proxySearch: "Search proxy address...", proxyCountry: "Country", proxyAllCountry: "All Countries", proxyAddress: "Proxy Address", proxyBatchCheck: "Batch Check", proxyBatchDelete: "Batch Delete", proxyBatchEdit: "Batch Edit", proxyAdd: "Add Proxy", proxyEdit: "Edit Proxy", proxyCheckDone: "Proxy check completed", proxyNoData: "No Proxies", proxyNoDataDesc: "Add proxy addresses first, then batch-check enabled proxies.", proxyStatusEnabled: "Enabled", proxyStatusDisabled: "Disabled", proxyStatusInvalid: "Invalid", proxyLastChecked: "Last Checked", proxyLatency: "Latency", proxyCountryPlaceholder: "e.g. US / HK / JP / Brazil", proxyCountryKeep: "Leave blank to keep each proxy's current country", proxyAddressPlaceholder: "One proxy per line, e.g. http://user:pass@host:port or socks5://host:port", proxyConfirmDelete: "Delete this proxy? This cannot be undone.", proxyConfirmBatchDelete: "Delete selected proxies? This cannot be undone.", proxyTrafficSwitch: "Register Traffic Proxy", proxyTrafficOn: "Proxy On", proxyTrafficOff: "Proxy Off", proxyTrafficOnHint: "Register/login requests use proxy pool", proxyTrafficOffHint: "Use server/system network", proxySwitchSaved: "Proxy outlet setting updated",
  selected: "Selected", selectedItems: "{count} selected", selectAll: "Select All", selectAllDone: "Selected {count} records from the current filters", clearSelection: "Clear Selection", globalLogs: "Global Logs", selectedLogs: "Current Mailbox Logs", registrationTaskProgress: "Registration Progress", accountRegistrationProgress: "Account Progress", clearLogs: "Clear", latest: "Latest Mail", done: "Done", failed: "Failed", file: "Choose File", status: "Status", prev: "Prev", next: "Next", pageSize: "Per page", pageInfo: "Page {page} / {pages}", pageRange: "Showing {from} to {to} of {total} results", noLogs: "No logs", noRegistrationTask: "No registration task yet", noAccountProgress: "No mailbox account is being processed", taskTotal: "Task Total", taskCompleted: "Completed", completedAccounts: "Completed Accounts", pendingAccounts: "Pending Accounts", abnormalAccounts: "Abnormal Accounts", currentStep: "Current Step", total: "Total", yes: "Yes", no: "No", step: "STEP",
  progressSteps: { queued: "Waiting for task scheduling", initializing: "Initializing mailbox task", proxy_ready: "Proxy and network outlet ready", browser_started: "Starting isolated browser", protocol_started: "Establishing protocol registration session", email_submitted: "Submitting registration email", email_verified: "Email verification completed", auth_completed: "Registration or login authenticated", registered: "ChatGPT Session saved", phone_started: "Allocating phone and starting SMS", phone_code_received: "Phone code received and submitted", phone_bound: "Codex phone binding completed", reverse_importing: "Importing to reverse proxy", reverse_imported: "Reverse proxy import completed", agent_identity_importing: "Creating Agent Identity and importing to reverse proxy", agent_identity_imported: "Agent Identity reverse proxy import completed", stage_incomplete: "Target stage incomplete", cancelled: "Task interrupted by user", failed: "Registration flow failed" },
  logProxy: "Proxy", logMailbox: "Mailbox", logPhone: "Phone", logSession: "Session", logAuth: "Auth", logSystem: "System",
  defaultGroup: "Default Group", allGroups: "All Groups", mailboxGroup: "Group", groupSearch: "Search mailbox groups...", groupNoResults: "No matching mailbox groups", importMailboxes: "Import Mailboxes", manualImport: "Manual", fileImport: "File", dragFile: "Drag mailbox file here, or click to choose a file", importToGroup: "Import to group", addGroup: "New Group", editGroup: "Rename Group", deleteGroup: "Delete Group", enterGroup: "Type group name and press Enter", groupCreated: "Mailbox group created", groupRenamed: "Mailbox group renamed", groupDeleted: "Mailbox group deleted", groupNotEmpty: "This mailbox group contains accounts. Move them before deleting the group.", defaultGroupCannotDelete: "The default group cannot be deleted", groupNameConflict: "A mailbox group with this name already exists", confirmDeleteGroup: "Delete this mailbox group?", mailboxCount: "Mailboxes", validationOk: "Validation passed", validationFailed: "Validation failed", mailboxList: "Mailbox List", enabled: "Enabled", updatedAt: "Updated", actions: "Actions", queryMailbox: "Search mailbox...",
  allStatus: "All Status", allPlanTypes: "All Plans", edit: "Edit", delete: "Delete", batchDelete: "Batch Delete", batchEdit: "Batch Edit", confirmDeleteMailbox: "Delete this mailbox record? This cannot be undone.", confirmBatchDeleteMailbox: "Delete the selected mailbox records? This cannot be undone.", queryMail: "Mail Query", currentMailbox: "Current Mailbox", getMail: "Get Mail", mailFetchCount: "Count", mailFetchCountSuffix: "mails", mailList: "Mail List", sender: "Sender", receiver: "Receiver", time: "Time", subject: "Subject", content: "Mail Content", emptyMail: "No mails", mailboxName: "Mailbox", password: "Password", chatgptPassword: "ChatGPT Password", totpSecret: "2FA Secret", keepCredential: "Leave blank to keep the current credential", clearCredential: "Clear current credential", clientId: "client_id", refreshToken: "refresh_token", openaiAccessToken: "OpenAI Access Token", batchEditMailboxTitle: "Batch Edit Mailboxes", applyToSelected: "Apply to selected mailboxes", mailboxType: "Mailbox Type", microsoftMailbox: "Microsoft Mailbox", appleMailbox: "Apple Mailbox", channelType: "Channel", xbovoChannel: "xbovo", xbovoChannelTip: "Verification code portal: https://icloud.xbovo.online/code", urlAPIChannel: "url_api", urlAPIChannelTip: "Read the newest mail from a mailbox-specific URL; one request may take up to about 30 seconds", icloudAccessKey: "Query Key", icloudQueryURL: "Code URL", urlAPIBrowser: "URL API Mail Browser", browserBack: "Back", browserForward: "Forward", browserReload: "Reload page", browserLoading: "Loading mail page", browserGetOnly: "This preview supports web links and GET form navigation only",
  autoRegister: "Auto Register", interruptTask: "Stop", interruptingTask: "Stopping...", interruptTaskTip: "Stop the entire registration batch during submission, queueing, Worker startup or mailbox execution.", interruptTaskRequested: "Stop requested for the entire batch; closing task processes, browsers and mailbox readers", interruptTaskFailed: "Failed to stop task", registerTaskRunning: "A registration task is running. Wait for it to finish or stop it first.", manualNew: "Manual Add", searchAccount: "Search account email...", refreshQuota: "Refresh Quota", refreshList: "Refresh List", refreshDone: "List refreshed", loadingData: "Updating data...", refreshStatus: "Refresh Account Status", statusChangedAt: "Status Changed At", planType: "Plan Type", email: "Email", trialLink: "Trial Link", registeredAt: "Registered At", operation: "Action", noData: "No Data", noDataDesc: "No mailbox records were found. Import mailboxes in Mailbox settings, then select mailboxes to start auto registration.", chooseMailbox: "Please select mailboxes", createTaskLog: "Created ChatGPT register task, count", taskSubmitted: "Registration task submitted and starting", taskCreated: "Auto register task created", taskDone: "Task completed", taskFailed: "Task failed", taskPollRecovered: "Detected an unfinished registration task and resumed log polling", taskPollLost: "Task status polling temporarily failed; the app will keep waiting: {error}", taskPollTimeout: "Task polling is taking longer than expected. The app will keep waiting; use Stop to interrupt it.", importDone: "Import completed", exportDone: "Export completed", manualNewTip: "Please add mailboxes manually in Mailbox settings", autoRegisterTitle: "Auto Register ChatGPT", step1Title: "Choose Identity", step1Desc: "The self-managed Outlook/Hotmail mailbox pool is used first for email verification.", systemMailbox: "System Mailbox", systemMailboxPoolDisabled: "System mailbox pool is not enabled. Please enable the mailbox pool first.", smsConfigDisabled: "Please enable SMS settings on the SMS configuration page first.", registerStageUnavailable: "Please enable at least one mailbox registration method first.", googleMailboxDisabled: "Google mailbox is not enabled. Please enable the corresponding mailbox feature first.", microsoftMailboxDisabled: "Microsoft mailbox is not enabled. Please enable the corresponding mailbox feature first.", systemMailboxDesc: "Use mailbox pool to receive verification codes and complete registration", googleDesc: "Reserved identity; Google account integration will be added later", microsoftDesc: "Reserved identity; Microsoft account integration will be added later", step2Title: "Choose Execution Mode", step2Desc: "Background browser and visible browser automation are supported. Background mode runs without a window and is better for batches.", protocolMode: "Protocol Mode", protocolDesc: "Reserved; not selectable yet", protocolChallengeStrategy: "Browser challenge strategy", protocolNativeChallenge: "Native headless takeover", protocolNativeChallengeDesc: "Let the full background browser take over when a challenge is encountered", protocolSentinelChallenge: "Sentinel protocol runtime", protocolSentinelChallengeDesc: "Keep registration requests in protocol mode and use narrow Camoufox only for browser proofs", backgroundMode: "Background Browser", backgroundDesc: "Run headless without a visible window while still using an isolated incognito browser context", visibleMode: "Visible Browser", visibleDesc: "Open a browser window for easier challenge or page issue troubleshooting", registerCount: "Register Count", concurrency: "Concurrency", identityLabel: "Identity", modeLabel: "Execution Mode", registerAccounts: "Accounts", verifyStrategy: "Verification: automatically detect Outlook/Hotmail Graph API or IMAP/XOAUTH2 and read the code", step3Title: "Choose Registration Stage", step3Desc: "Control how far this task should run. Default only completes ChatGPT registration/login and Session storage.", registerOnly: "Register ChatGPT Only", registerOnlyDesc: "After register/login, only read and save ChatGPT Session info", codexPhoneBind: "Codex Phone Binding", codexPhoneBindDesc: "Continue phone verification with SMS settings and acquire Refresh Token", importReverseProxy: "Import Reverse Proxy", importReverseProxyDesc: "Import the account into configured sub2api after Session/RT is ready", agentIdentityReverseProxy: "Bypass SMS and Import Reverse Proxy", agentIdentityReverseProxyDesc: "Create Agent Identity from the Access Token after register/login, skip phone binding, and import directly into sub2api", stageLabel: "Stage", startAutoRegister: "Start Auto Register", cancel: "Cancel", noMailbox: "No Mailboxes", noMailboxDesc: "Click 'Import Mailboxes' in the upper-right corner to add your Outlook/Hotmail mailbox pool.", inbox: "Inbox", fillOrChooseMailboxFile: "Please fill in or choose a mailbox file",
  sub2apiDesc: "Used by the 'Import Reverse Proxy' stage. After Base URL and Admin Key are configured, registration tasks can import GPT accounts with Session/RT into the platform.", baseURL: "Base URL", adminToken: "Admin Token", accountNamePrefix: "Account Name Prefix", targetGroup: "Target Group", targetGroupPlaceholder: "Select target groups", noGroupsFetch: "No groups yet. Click 'Fetch' on the right.", fetch: "Fetch", priority: "Priority", remoteProxy: "Remote Proxy", noRemoteProxy: "No remote proxy", loadFactor: "Load Factor", modelWhitelist: "Model Whitelist", importSub2API: "Import Proxy", importingSub2API: "Importing...", sub2NoSelection: "Select accounts to import", sub2ImportSummary: "Proxy import complete: {selected} selected, {uploaded} submitted, {confirmed} confirmed, {failed} failed, {skipped} skipped", sub2ImportDetails: "Proxy import details", check: "Check", configUnchanged: "Configuration unchanged", fillURLToken: "Please fill in Base URL and Admin Token first", fetchedGroups: "Fetched {count} target groups", fillURLTokenShort: "Please fill in URL and Token first", checking: "Checking...", checkPassedGroups: "Check passed, found {count} groups", checkFailed: "Check failed: {error}", lineFormatPhone: "+phone----https://sms-url", sessionJSON: "Auth Session", accessToken: "Access Token", mailboxAccountExport: "Mailbox Account", exportFormat: "Export Content", selectExportRows: "Please select accounts to export", tokenPreview: "Token Preview", sessionRefreshToken: "Refresh Token", secretKey: "Secret Key", allInfo: "All Info", exportSK: "Export SK", exportAT: "Export AT", exportSUB: "Export SUB", acquireRT: "Get", acquiringRT: "Getting RT", acquireRTDone: "RT acquired for {email}", acquireRTFailed: "Unable to acquire this account RT", sessionFieldTitle: "View {field}", sessionFieldLoading: "Fetching {field}. Please wait...", sessionFieldEmpty: "This account has no {field}", updated: "Updated", groupFilter: "Group", atExpiresAt: "AT Expires", atInvalidOrExpired: "AT invalid or expired", atRenewalFailed: "AT renewal failed", atProbeFailed: "AT check failed", lastHealthCheckedAt: "Last Health Check", accountHealthCheckFailed: "Account health check failed", refreshAT: "Renew", refreshingAT: "Renewing...", updateAT: "Renew", refreshATDone: "AT renewed for {email}", refreshATSummary: "AT check complete: {valid} valid, {invalid} invalid, {failed} failed; renewal started for invalid accounts", refreshATNoSelection: "Select accounts to renew AT", stopRenewal: "Stop Renewal", stoppingRenewal: "Stopping...", stopRenewalTip: "Stop all AT renewal tasks that are running or waiting", stopRenewalRequested: "Renewal stop requested; closing the active renewal flow and skipping waiting accounts", stopRenewalFailed: "Failed to stop renewal task", closeRenewalProgress: "Close renewal progress", healthCheck: "Health Check", healthChecking: "Checking...", healthCheckSummary: "Health check complete: tested {total}, alive {alive}, banned {banned}, failed {failed}", healthAlive: "Account {email}: alive", healthBanned: "Account {email}: banned", currentATValid: "The current AT is valid; renewal is not required", currentATInvalid: "The current AT is invalid; AT renewal has started", atCheckFailed: "AT check failed: {error}", alreadyBanned: "This account status is not eligible for a health check", healthNoSelection: "Select accounts to check", failureDetails: "Failure details", maintenanceSettings: "Scheduled task settings", healthSchedule: "Scheduled account health check", atSchedule: "Scheduled AT check", scheduleTime: "Run time", scheduleFrequency: "Frequency (hours)", restartRequired: "Settings saved and will take effect after the next service restart", saveSettings: "Save settings",
  subscriptionCheck: "Subscription", subscriptionChecking: "Checking...", subscriptionNoSelection: "Select accounts to check subscription", subscriptionCheckSummary: "Subscription check complete: checked {total}, subscribed {subscribed}, not found {notSubscribed}, failed {failed}", subscriptionConfirmed: "Account {email}: Plus subscription confirmed", subscriptionNotFound: "Account {email}: no successful subscription email found", subscriptionCheckFailed: "Account {email}: subscription check failed",
  trialEligibility: "Trial Eligibility", allTrialEligibility: "All Trial Eligibility", trialCheck: "Trial", trialChecking: "Checking...", trialEligible: "$0 Trial Available", trialIneligible: "No $0 Trial", trialUnknown: "Not Checked", trialNoSelection: "Select accounts to check trial eligibility", trialUnavailable: "Only registered free-plan accounts support trial checks", trialCheckSummary: "Trial check complete: checked {total}, eligible {eligible}, ineligible {ineligible}, skipped {skipped}, failed {failed}", trialEligibleResult: "Account {email}: $0 trial available", trialIneligibleResult: "Account {email}: no $0 trial", trialCheckFailed: "Account {email}: trial eligibility check failed",
  renewalSteps: { queued: "Waiting for renewal scheduling", preparing: "Preparing renewal", credentials_loaded: "Loading account credentials", refresh_token_ready: "Refresh Token ready", token_received: "New Access Token received", saving_session: "Saving Session", session_saved: "Session updated", refresh_token_unavailable: "Refresh Token unavailable; switching to login flow", refresh_token_missing: "No Refresh Token; switching to login flow", mailbox_ready: "Mailbox credentials ready", protocol_login_started: "Starting protocol login and native challenge takeover", headless_login_started: "Starting background headless login", headless_login_fallback: "Protocol login incomplete; falling back to background headless login", sentinel_login_retry: "Authentication proof rejected; retrying with a fresh session", proxy_ready: "Proxy and network outlet ready", authentication_running: "Completing account authentication", session_reading: "Reading and updating Session", session_refreshed: "Session refreshed", stopping: "Stopping renewal task", cancelled: "Renewal interrupted", completed: "Renewal succeeded", failed: "Renewal failed" },
  linkedMailboxConfig: "Uses Mailbox config", linkedPhoneConfig: "Uses SMS config", linkedReverseConfig: "Uses Reverse Proxy config", resourceReady: "Ready", resourceMissing: "Unavailable", usablePhones: "{count} usable phones", existingRTReady: "Selected accounts already have RT; SMS is not required", sub2apiReady: "sub2api configured", sub2apiMissing: "sub2api incomplete", stageDisabledTip: "The configuration required by this stage is unavailable. Complete the linked menu first.",
  statusLabels: { "未注册": "Unregistered", "已注册": "Registered", "registered": "Registered", "已接码": "Phone Bound", "phone_bound": "Phone Bound", "已反代": "Reverse Proxied", "reverse_proxied": "Reverse Proxied", "已封禁": "Banned", "需二验": "Needs 2FA", "注册中": "Registering", "登录刷新": "Refreshing Login", "失败": "Failed", "failed": "Failed", "禁用": "Disabled" },
};

const MAILBOX_STATUSES = ["未注册", "已注册", "已接码", "已反代", "已封禁", "需二验", "登录刷新", "失败"];
const PLAN_TYPE_OPTIONS = ["free", "plus", "k12", "team", "pro"];
const HEALTH_CHECKABLE_STATUSES = new Set(["已注册", "已接码", "已反代", "PLUS试用中", "需二验", "registered", "phone_bound", "reverse_proxied"]);
const PROTOCOL_MODE_COPY = {
  zh: {
    step2Desc: "支持纯协议、后台浏览器与可视浏览器自动；协议模式不加载网页资源，流量占用更低。",
    desc: "优先通过 HTTP/TLS 完成注册或登录；后续接码/OAuth 阶段需要页面流程时，仅由后台无头浏览器接管续段。",
  },
  en: {
    step2Desc: "Protocol, background browser, and visible browser automation are supported. Protocol mode avoids page assets and uses less traffic.",
    desc: "Prefer HTTP/TLS for register/login. A background headless browser only takes over the later SMS/OAuth continuation when the page flow is required.",
  },
};
function template(text: string, values: Record<string, string | number>) {
  return text.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ""));
}

async function copyTextToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Fall through for HTTP deployments where Clipboard API may be unavailable.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("Clipboard copy failed");
}

function Tip({ text }: { text: string }) { return <span title={text} className="inline-flex"><CircleHelp className="tip-icon h-4 w-4" /></span>; }
function Label({ children, tip }: { children: React.ReactNode; tip?: string }) { return <div className="form-label mb-2"><span className="inline-flex items-center gap-1.5">{children}{tip && <Tip text={tip} />}</span></div>; }
function Input(props: React.InputHTMLAttributes<HTMLInputElement>) { return <input {...props} className={cn("control-surface h-11", props.className)} />; }
function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) { return <textarea {...props} className={cn("control-surface min-h-28", props.className)} />; }
type SelectBoxOption = { value: string | number; label: React.ReactNode; searchText?: string };

function SelectBox({ value, onChange, options, className, searchable = false, searchPlaceholder = "Search...", noResultsLabel = "No results" }: { value: string | number; onChange: (v: string | number) => void; options: SelectBoxOption[]; className?: string; searchable?: boolean; searchPlaceholder?: string; noResultsLabel?: string }) {
  const [open, setOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const [menuRect, setMenuRect] = useState<{ left: number; top: number; width: number; maxHeight: number; zIndex: number } | null>(null);
  const active = options.find((x) => String(x.value) === String(value)) || options[0];
  const normalizedQuery = useDeferredValue(searchQuery).trim().toLocaleLowerCase();
  const filteredOptions = normalizedQuery
    ? options.filter((opt) => {
        const primitiveLabel = typeof opt.label === "string" || typeof opt.label === "number" ? String(opt.label) : "";
        return String(opt.searchText || `${opt.value} ${primitiveLabel}`).toLocaleLowerCase().includes(normalizedQuery);
      })
    : options;
  const updateRect = () => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (rect) {
      const desiredHeight = Math.min(320, options.length * 44 + (searchable ? 62 : 12));
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 800;
      const spaceBelow = viewportHeight - rect.bottom - 14;
      const spaceAbove = rect.top - 14;
      const openUp = spaceBelow < desiredHeight && spaceAbove > spaceBelow;
      const maxHeight = Math.max(120, openUp ? spaceAbove - 8 : spaceBelow - 8);
      setMenuRect({
        left: rect.left,
        top: openUp ? Math.max(12, rect.top - Math.min(desiredHeight, maxHeight) - 8) : rect.bottom + 8,
        width: rect.width,
        maxHeight,
        zIndex: wrapRef.current?.closest(".sr-modal-mask") ? 600 : 220,
      });
    }
  };
  useEffect(() => {
    if (!open) return;
    updateRect();
    const onMove = () => updateRect();
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open, searchable, options.length]);
  useEffect(() => {
    if (!open || !searchable) return;
    const frame = window.requestAnimationFrame(() => searchRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [open, searchable]);
  useEffect(() => {
    if (!open) return;
    const closeOnOutsideClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!wrapRef.current?.contains(target) && !menuRef.current?.contains(target)) {
        setOpen(false);
        setSearchQuery("");
      }
    };
    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, [open]);
  const closeMenu = () => {
    setOpen(false);
    setSearchQuery("");
  };
  const menu = open && menuRect ? createPortal(<div ref={menuRef} role="listbox" className={cn("sr-custom-select-menu sr-custom-select-menu-portal", searchable && "sr-custom-select-menu-searchable", className?.includes("sr-page-size-select") && "sr-page-size-select-menu", className?.includes("sr-mailbox-group-select") && "sr-mailbox-group-select-menu")} style={{ position: "fixed", left: menuRect.left, top: menuRect.top, width: menuRect.width, maxHeight: menuRect.maxHeight, overflowY: "auto", right: "auto", zIndex: menuRect.zIndex }}>
      {searchable && <div className="sr-custom-select-search" onMouseDown={(event)=>event.stopPropagation()}>
        <Search className="h-4 w-4" aria-hidden="true"/>
        <input ref={searchRef} value={searchQuery} onChange={(event)=>setSearchQuery(event.target.value)} placeholder={searchPlaceholder} aria-label={searchPlaceholder} onKeyDown={(event)=>{
          if (event.key === "Escape") { closeMenu(); triggerRef.current?.focus(); }
          if (event.key === "ArrowDown") { event.preventDefault(); menuRef.current?.querySelector<HTMLButtonElement>(".sr-custom-select-option")?.focus(); }
          if (event.key === "Enter" && filteredOptions.length === 1) { event.preventDefault(); onChange(filteredOptions[0].value); closeMenu(); triggerRef.current?.focus(); }
        }}/>
      </div>}
      {filteredOptions.map((opt) => <button type="button" role="option" aria-selected={String(opt.value) === String(value)} key={String(opt.value)} className={cn("sr-custom-select-option", String(opt.value) === String(value) && "active")} onMouseDown={(e)=>e.preventDefault()} onClick={() => { onChange(opt.value); closeMenu(); }}>{opt.label}</button>)}
      {searchable && filteredOptions.length === 0 && <div className="sr-custom-select-empty" role="status">{noResultsLabel}</div>}
    </div>, document.body) : null;
  return <div ref={wrapRef} className={cn("sr-custom-select", className)}>
    <button ref={triggerRef} type="button" aria-haspopup="listbox" aria-expanded={open} className={cn("sr-custom-select-trigger", open && "open")} onClick={() => { updateRect(); setSearchQuery(""); setOpen((v) => !v); }} onKeyDown={(event)=>{ if (event.key === "Escape" && open) closeMenu(); }}>
      <span>{active?.label}</span><ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180")} />
    </button>
    {menu}
  </div>;
}

function sortMailboxGroups(groups: AnyObj[]) {
  return [...groups].sort((a,b)=>{
    const aDefault=String(a?.name||"")==="默认分组";
    const bDefault=String(b?.name||"")==="默认分组";
    if(aDefault!==bDefault) return aDefault ? -1 : 1;
    const aCreated=Date.parse(String(a?.created_at||""));
    const bCreated=Date.parse(String(b?.created_at||""));
    if(Number.isFinite(aCreated)&&Number.isFinite(bCreated)&&aCreated!==bCreated) return bCreated-aCreated;
    return Number(b?.id||0)-Number(a?.id||0);
  });
}

function mailboxGroupOptions(t: typeof zh, groups: AnyObj[]) {
  return sortMailboxGroups(groups).map((group)=>{
    const rawName=String(group?.name||"");
    const label=rawName==="默认分组" ? t.defaultGroup : (rawName||t.defaultGroup);
    return {value:group.id,label,searchText:`${rawName} ${label}`};
  });
}

function Toast({ toast, clear }: { toast: ToastState; clear: () => void }) {
  const [hovering, setHovering] = useState(false);
  useEffect(() => {
    if (!toast || hovering) return;
    const timer = window.setTimeout(clear, 2600);
    return () => window.clearTimeout(timer);
  }, [toast, hovering, clear]);
  useEffect(() => {
    if (!toast) setHovering(false);
  }, [toast]);
  return toast ? <div className={cn("sr-toast", toast.type === "ok" ? "ok" : "fail")} onMouseEnter={() => setHovering(true)} onMouseLeave={() => setHovering(false)}>
    <span>{toast.text}</span><button onClick={clear}><X className="h-4 w-4" /></button>
  </div> : null;
}
function formatDateTime(value: any) {
  if (!value) return "-";
  const raw = String(value);
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw.replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 19) || "-";
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "medium" }).format(d).replace("T", " ");
}
type SortOrder = "asc" | "desc";
function nextSortOrder(v: SortOrder): SortOrder { return v === "asc" ? "desc" : "asc"; }
function SortTimeHeader({ label, order, onToggle }: { label: string; order: SortOrder; onToggle: () => void }) {
  return <button type="button" className="sr-sort-th" onClick={onToggle} title={order === "asc" ? "ASC" : "DESC"}><span>{label}</span><span className="sr-sort-icon">{order === "asc" ? "↑" : "↓"}</span></button>;
}
function SelectionSummary({ t, count, total, selectingAll, onSelectAll, onClear }: { t: typeof zh; count: number; total: number; selectingAll: boolean; onSelectAll: () => void; onClear: () => void }) {
  return <div className="sr-selection-summary" aria-live="polite">
    <button type="button" className="sr-select-all" disabled={selectingAll || total <= 0} onClick={onSelectAll}>{selectingAll ? <Loader2 className="h-3.5 w-3.5 animate-spin"/> : <ListChecks className="h-3.5 w-3.5"/>}<span>{t.selectAll}</span></button>
    {count > 0 && <><span className="sr-selected-count">{template(t.selectedItems, { count })}</span><button type="button" className="sr-clear-selection" onClick={onClear}>{t.clearSelection}</button></>}
  </div>;
}

function allSelectionParams(params: URLSearchParams) {
  const next = new URLSearchParams(params);
  next.delete("page");
  next.delete("page_size");
  next.set("selection", "all");
  return next;
}

function selectionIDs(result: AnyObj): number[] {
  const ids = (Array.isArray(result?.ids) ? result.ids : []).map(Number).filter((id: number)=>id > 0);
  return Array.from(new Set<number>(ids));
}
const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];
function pageCount(total: number, pageSize: number) {
  return Math.max(1, Math.ceil(Math.max(0, Number(total || 0)) / Math.max(1, Number(pageSize || 10))));
}
function paginationTokens(page: number, pages: number): Array<number | "..."> {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const out: Array<number | "..."> = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(pages - 1, page + 1);
  if (start > 2) out.push("...");
  for (let n = start; n <= end; n++) out.push(n);
  if (end < pages - 1) out.push("...");
  out.push(pages);
  return out;
}
function PaginationBar({ t, total, page, pageSize, setPage, setPageSize }: { t: typeof zh; total: number; page: number; pageSize: number; setPage: (v: number) => void; setPageSize: (v: number) => void }) {
  const pages = pageCount(total, pageSize);
  const safePage = Math.min(Math.max(1, page), pages);
  const from = total <= 0 ? 0 : (safePage - 1) * pageSize + 1;
  const to = Math.min(total, safePage * pageSize);
  const tokens = paginationTokens(safePage, pages);
  return <div className="sr-pagination">
    <div className="sr-pagination-left">
      <span className="sr-pagination-range">{template(t.pageRange, { from, to, total })}</span>
      <span className="sr-page-size-label">{t.pageSize}:</span>
      <SelectBox className="sr-page-size-select" value={pageSize} onChange={(v)=>{ setPageSize(Number(v)); setPage(1); }} options={PAGE_SIZE_OPTIONS.map((n)=>({value:n,label:String(n)}))} />
    </div>
    <div className="sr-pagination-actions" aria-label="pagination">
      <button type="button" className="sr-page-nav" disabled={safePage<=1 || total <= 0} onClick={()=>setPage(safePage-1)} title={t.prev}>‹</button>
      {tokens.map((token, idx) => token === "..."
        ? <span key={`ellipsis-${idx}`} className="sr-page-ellipsis">...</span>
        : <button key={token} type="button" className={cn("sr-page-number", token === safePage && "active")} onClick={()=>setPage(token)}>{token}</button>
      )}
      <button type="button" className="sr-page-nav" disabled={safePage>=pages || total <= 0} onClick={()=>setPage(safePage+1)} title={t.next}>›</button>
    </div>
  </div>;
}
function logModule(message: string) {
  const text = String(message || "").replace(/^\[[^\]\s]+@[^\]\s]+\]\s*/, "").trim();
  const explicit = text.match(/^\[([^\]]+)\]/);
  if (explicit) return explicit[1];
  const lower = text.toLowerCase();
  if (/proxy|ipinfo|代理|出口/.test(lower)) return "Proxy";
  if (/imap|mail|email|outlook|otp|邮箱|邮件|验证码/.test(lower)) return "Mailbox";
  if (/sms|phone|mobile|手机号|电话/.test(lower)) return "Phone";
  if (/session|access token|accesstoken|rt/.test(lower)) return "Session";
  if (/register|login|oauth|auth|chatgpt|openai|注册|登录|认证/.test(lower)) return "Auth";
  return "System";
}
function logMessage(message: string) {
  return String(message || "").replace(/^\[[^\]\s]+@[^\]\s]+\]\s*/, "").replace(/^\[[^\]]+\]\s*/, "").trim();
}
function logFromEvent(event: AnyObj): LogEntry {
  const message = String(event.message || event.line || "");
  const detail = event.detail || {};
  return {
    id: event.id || `${Date.now()}-${Math.random()}`,
    time: formatDateTime(detail.local_created_at || event.created_at || new Date()).slice(11, 19),
    level: String(event.level || "info"),
    module: logModule(message),
    message: logMessage(message),
    email: String(detail.email || event.email || ""),
    rawMessage: message,
    detail,
  };
}
function localLog(message: string, level = "info"): LogEntry {
  return { id: `${Date.now()}-${Math.random()}`, time: new Date().toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }), level, module: logModule(message), message: logMessage(message), rawMessage: message, detail: {} };
}
function batchSeparatorLog(label: string): LogEntry {
  return { id: `sep-${Date.now()}-${Math.random()}`, time: new Date().toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }), level: "separator", module: "绯荤粺", message: label, rawMessage: label, detail: { separator: true } };
}

export default function SunnyRegister() {
  const { language } = useI18n();
  const t = language === "en-US" ? en : zh;
  const location = useLocation();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const page = location.pathname.includes("mailbox") ? "mailbox" : location.pathname.includes("phone") ? "phone" : location.pathname.includes("sub2api") ? "sub2api" : location.pathname.includes("proxy") ? "proxy" : location.pathname.includes("session") ? "session" : "workbench";
  const renderedPage = useDeferredValue(page);
  useSunnyGsap(rootRef, renderedPage);
  const [toast, setToast] = useState<ToastState>(null);
  const notify = (type: "ok" | "fail", text: string) => { setToast({ type, text }); };
  return <div ref={rootRef} className="sunny-page space-y-6" aria-busy={renderedPage !== page}><Toast toast={toast} clear={() => setToast(null)} />{renderedPage === "workbench" && <Hero t={t} />}{renderedPage === "workbench" && <Workbench t={t} notify={notify} />}{renderedPage === "mailbox" && <MailboxConfig t={t} notify={notify} />}{renderedPage === "phone" && <PhoneConfig t={t} notify={notify} />}{renderedPage === "sub2api" && <Sub2APIConfig t={t} notify={notify} />}{renderedPage === "proxy" && <ProxyConfigPage t={t} notify={notify} />}{renderedPage === "session" && <SessionManager t={t} notify={notify} />}</div>;
}

function Hero({ t }: { t: typeof zh }) { return <section className="hero-card rounded-[34px] border border-[var(--border)] p-6 md:p-8"><Badge className="rounded-full px-3 py-1">SunnyRegister</Badge><h1 className="mt-4 text-4xl font-black tracking-[-0.05em] md:text-5xl">{t.title}</h1><p className="mt-3 max-w-4xl leading-7 text-[var(--text-secondary)]">{t.desc}</p></section>; }

function Workbench({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [mailboxes, setMailboxes] = useCachedState<AnyObj[]>("workbench.mailboxes", []);
  const [groups, setGroups] = useCachedState<AnyObj[]>("workbench.groups", []);
  const [selected, setSelected] = useCachedState<number[]>("workbench.selected", []);
  const [selectingAll, setSelectingAll] = useState(false);
  const [selectedRowCache, setSelectedRowCache] = useCachedState<Record<string,AnyObj>>("workbench.selectedRows", {});
  const [query, setQuery] = useCachedState("workbench.query", "");
  const debouncedQuery = useDebouncedValue(query);
  const [status, setStatus] = useCachedState("workbench.status", "");
  const [planFilter, setPlanFilter] = useCachedState("workbench.planFilter", "");
  const [groupFilter, setGroupFilter] = useCachedState("workbench.groupFilter", 0);
  const [pageNo, setPageNo] = useCachedState("workbench.page", 1);
  const [pageSize, setPageSize] = useCachedState("workbench.pageSize", 10);
  const [total, setTotal] = useCachedState("workbench.total", 0);
  const [timeSort, setTimeSort] = useCachedState<SortOrder>("workbench.timeSort", "desc");
  const [busy, setBusy] = useCachedState("workbench.busy", false);
  const { loading: listLoading, track: trackListLoad } = useLoadingTracker();
  const [submittingTask, setSubmittingTask] = useState(false);
  const [activeTaskId, setActiveTaskId] = useCachedState("workbench.activeTaskId", "");
  const [activeTaskMailboxIds, setActiveTaskMailboxIds] = useCachedState<number[]>("workbench.activeTaskMailboxIds", []);
  const [stopRequested, setStopRequested] = useCachedState("workbench.stopRequested", false);
  const [autoOpen, setAutoOpen] = useCachedState("workbench.autoOpen", false);
  const [modalConcurrency, setModalConcurrency] = useCachedState("workbench.concurrency", 1);
  const [identity, setIdentity] = useCachedState<"system" | "google" | "microsoft">("workbench.identity", "system");
  const [mode, setMode] = useCachedState<"protocol" | "background" | "visible">("workbench.mode", "visible");
  const [protocolChallengeStrategy, setProtocolChallengeStrategy] = useCachedState<ProtocolChallengeStrategy>("workbench.protocolChallengeStrategy", "native_headless");
  const [stage, setStage] = useCachedState<RegisterStage>("workbench.stage", "register_only");
  const [allTrafficProxyPool, setAllTrafficProxyPool] = useState(false);
  const [globalLogs, setGlobalLogs] = useCachedState<LogEntry[]>("workbench.globalLogs", []);
  const [selectedLogs, setSelectedLogs] = useCachedState<LogEntry[]>("workbench.selectedLogs", []);
  const [registrationProgress, setRegistrationProgress] = useCachedState<RegistrationTaskProgress | null>("workbench.registrationProgress", null);
  const [globalCardView, setGlobalCardView] = useCachedState<"progress" | "logs">("workbench.globalCardView", "progress");
  const [accountCardView, setAccountCardView] = useCachedState<"progress" | "logs">("workbench.accountCardView", "progress");
  const [, setCurrentLogEmail] = useCachedState("workbench.currentLogEmail", "");
  const [taskEventCursor, setTaskEventCursor] = useCachedState<{ taskId: string; last: number }>("workbench.taskEventCursor", { taskId: "", last: 0 });
  const taskEventCursorRef = useRef(taskEventCursor);
  const workbenchMountedRef = useRef(true);
  const pollingTaskIdsRef = useRef<Set<string>>(new Set());
  const resumedTaskIdsRef = useRef<Set<string>>(new Set());
  const stopAfterSubmitRef = useRef(false);
  useEffect(() => { taskEventCursorRef.current = taskEventCursor; }, [taskEventCursor]);
  useEffect(() => () => { workbenchMountedRef.current = false; }, []);
  const load = () => trackListLoad(async () => {
    const params = new URLSearchParams({ page: String(pageNo), page_size: String(pageSize), enabled: "true", sort_by: "status_changed_at", sort_order: timeSort });
    params.set("summary", "true");
    if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim());
    if (groupFilter) params.set("group_id", String(groupFilter));
    if (status) params.set("status", status);
    if (planFilter) params.set("plan_type", planFilter);
    const m = await apiFetch(`/sunny/mailboxes?${params.toString()}`);
    setMailboxes(m.items || []);
    setTotal(Number(m.total || 0));
  });
  const refreshList = async () => {
    try {
      await load();
      notify("ok", t.refreshDone);
    } catch (e: any) {
      notify("fail", e.message || String(e));
    }
  };
  const selectAllFiltered = async () => {
    setSelectingAll(true);
    try {
      const params = new URLSearchParams({ enabled: "true", summary: "true", sort_by: "status_changed_at", sort_order: timeSort });
      if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim());
      if (groupFilter) params.set("group_id", String(groupFilter));
      if (status) params.set("status", status);
      if (planFilter) params.set("plan_type", planFilter);
      const result = await apiFetch(`/sunny/mailboxes?${allSelectionParams(params).toString()}`);
      const ids = selectionIDs(result);
      const selectionItems = Array.isArray(result.items) ? result.items : [];
      setSelected(ids);
      setSelectedRowCache(Object.fromEntries(selectionItems.map((item: AnyObj)=>[String(item.id), { id:Number(item.id), email:String(item.email || ""), account:{id:0} }])));
      notify("ok", template(t.selectAllDone, { count: ids.length }));
    } catch (e: any) {
      notify("fail", e.message || String(e));
    } finally {
      setSelectingAll(false);
    }
  };
  useEffect(() => { void load(); }, [pageNo, pageSize, debouncedQuery, status, planFilter, groupFilter, timeSort]);
  useEffect(() => { void apiFetch("/sunny/mailbox-groups").then((g) => setGroups(sortMailboxGroups(g.items || []))).catch(() => {}); }, []);
  const rows: AnyObj[] = mailboxes
    .map((m: AnyObj) => ({ ...m, account: { id: Number(m.account_id || 0) } }) as AnyObj);
  const safePageNo = Math.min(Math.max(1, pageNo), pageCount(total, pageSize));
  const pagedRows = rows;
  useEffect(() => {
    if (!selected.length || !rows.length) return;
    setSelectedRowCache((old) => {
      let changed = false;
      const next = { ...old };
      rows.forEach((row) => {
        const id = Number(row.id);
        if (selected.includes(id) && !next[String(id)]) {
          next[String(id)] = row;
          changed = true;
        }
      });
      return changed ? next : old;
    });
  }, [mailboxes, selected]);
  useEffect(()=>{setPageNo(1)},[query, status, planFilter, groupFilter, pageSize, timeSort]);
  useEffect(()=>{if (pageNo !== safePageNo) setPageNo(safePageNo)},[pageNo, safePageNo]);
  async function createRegisterTask(directIds?: number[]) {
    if (busy || activeTaskId) { notify("fail", t.registerTaskRunning); return; }
    const ids = directIds?.length ? directIds : selected;
    if (!ids.length) { notify("fail", t.chooseMailbox); return; }
    setBusy(true);
    setSubmittingTask(true);
    setStopRequested(false);
    stopAfterSubmitRef.current = false;
    setAutoOpen(false);
    const availableRows = [...rows, ...Object.values(selectedRowCache)];
    const taskEmails = ids.map((mailboxId) => String(availableRows.find((row) => Number(row.id) === mailboxId)?.email || "")).filter(Boolean);
    setRegistrationProgress(createRegistrationTaskProgress("", stage, taskEmails));
    const sep = batchSeparatorLog(`========= SunnyRegister ${t.autoRegister} · ${formatDateTime(new Date())} =========`);
    setGlobalLogs((old) => [localLog(`${t.createTaskLog} ${ids.length}`), sep, ...old]);
    setSelectedLogs((old) => [sep, ...old]);
    try {
      const res = await apiFetch("/sunny/tasks/register", { method: "POST", body: JSON.stringify({ mailbox_ids: ids, count: ids.length, concurrency: Math.max(1, Math.min(Number(modalConcurrency) || 1, ids.length)), identity, execution_mode: mode, protocol_challenge_strategy: protocolChallengeStrategy, registration_stage: stage, proxy_all_traffic: allTrafficProxyPool }) });
      notify("ok", t.taskSubmitted);
      setGlobalLogs((old) => [localLog(t.taskSubmitted), ...old].slice(0, 160));
      const taskId = String(res.id || res.task_id || "");
      if (!taskId) throw new Error(t.taskFailed);
      setRegistrationProgress((old) => old ? { ...old, taskId } : createRegistrationTaskProgress(taskId, stage, taskEmails));
      taskEventCursorRef.current = { taskId, last: 0 };
      setTaskEventCursor(taskEventCursorRef.current);
      setActiveTaskId(taskId);
      setActiveTaskMailboxIds(ids);
      setSubmittingTask(false);
      void poll(taskId, ids);
      if (stopAfterSubmitRef.current) {
        stopAfterSubmitRef.current = false;
        await requestTaskCancellation(taskId);
      }
    } catch (e: any) {
      setRegistrationProgress((old) => old ? {
        ...old,
        accounts: Object.fromEntries(Object.entries(old.accounts).map(([key, value]) => [key, { ...value, state: "abnormal", checkpoint: "failed", error: e.message || String(e), updatedAt: Date.now() }])),
      } : old);
      notify("fail", e.message || String(e));
      setSubmittingTask(false);
      setBusy(false);
      setStopRequested(false);
      stopAfterSubmitRef.current = false;
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
    }
  }
  async function requestTaskCancellation(taskId: string) {
    try {
      await apiFetch(`/tasks/${taskId}/cancel`, { method: "POST" });
      notify("ok", t.interruptTaskRequested);
    } catch (e: any) {
      setStopRequested(false);
      notify("fail", `${t.interruptTaskFailed}: ${e?.message || String(e)}`);
    }
  }
  async function cancelActiveTask() {
    const taskId = String(activeTaskId || "");
    if ((!taskId && !submittingTask) || stopRequested) return;
    const msg = t.interruptTaskRequested;
    setStopRequested(true);
    setGlobalLogs((old) => [localLog(msg, "warning"), ...old].slice(0, 200));
    setSelectedLogs((old) => [localLog(msg, "warning"), ...old].slice(0, 200));
    if (!taskId) {
      stopAfterSubmitRef.current = true;
      notify("ok", msg);
      return;
    }
    await requestTaskCancellation(taskId);
  }
  function applyRegistrationProgressEvents(taskId: string, events: AnyObj[]) {
    if (!events.length) return;
    setRegistrationProgress((old) => {
      const firstDetail = events[0]?.detail || {};
      const eventStage = ([REGISTER_ONLY, CODEX_PHONE_BIND, IMPORT_REVERSE_PROXY, AGENT_IDENTITY_REVERSE_PROXY].includes(firstDetail.stage) ? firstDetail.stage : stage) as RegisterStage;
      const next: RegistrationTaskProgress = old && (!old.taskId || old.taskId === taskId)
        ? { ...old, taskId, accounts: { ...old.accounts }, order: [...old.order] }
        : createRegistrationTaskProgress(taskId, eventStage, []);
      for (const event of events) {
        const detail = event.detail || {};
        const email = String(detail.email || event.email || "").trim();
        if (!email) continue;
        const key = email.toLowerCase();
        const accountStage = ([REGISTER_ONLY, CODEX_PHONE_BIND, IMPORT_REVERSE_PROXY, AGENT_IDENTITY_REVERSE_PROXY].includes(detail.stage) ? detail.stage : next.stage) as RegisterStage;
        const total = Math.max(1, Number(detail.total || registrationStageTotal(accountStage)));
        const previous = next.accounts[key] || { email, stage: accountStage, checkpoint: "queued", current: 0, total, state: "pending", updatedAt: 0 };
        const state = (["pending", "running", "completed", "abnormal"].includes(detail.state) ? detail.state : "running") as RegistrationProgressState;
        const current = state === "completed" ? total : Math.min(total, Math.max(Number(previous.current || 0), Number(detail.current || 0)));
        next.accounts[key] = {
          ...previous,
          email,
          stage: accountStage,
          checkpoint: String(detail.checkpoint || previous.checkpoint || "queued"),
          current,
          total,
          state,
          error: String(detail.error || previous.error || ""),
          updatedAt: Date.now(),
        };
        if (!next.order.some((item) => item.toLowerCase() === key)) next.order.push(email);
      }
      return next;
    });
  }
  function reconcileRegistrationProgress(taskId: string, task: AnyObj) {
    setRegistrationProgress((old) => {
      if (!old || (old.taskId && old.taskId !== taskId)) return old;
      const next = { ...old, taskId, accounts: { ...old.accounts }, order: [...old.order] };
      const resultItems = Array.isArray(task.result?.items) ? task.result.items : [];
      for (const item of resultItems) {
        const email = String(item.email || "").trim();
        if (!email) continue;
        const key = email.toLowerCase();
        const previous = next.accounts[key] || { email, stage: next.stage, checkpoint: "queued", current: 0, total: registrationStageTotal(next.stage), state: "pending", updatedAt: 0 };
        const complete = item.stage_complete !== false;
        next.accounts[key] = { ...previous, email, state: complete ? "completed" : "abnormal", current: complete ? previous.total : previous.current, checkpoint: complete ? ({ register_only: "registered", codex_phone_bind: "phone_bound", import_reverse_proxy: "reverse_imported", agent_identity_reverse_proxy: "agent_identity_imported" }[previous.stage] || "registered") : "stage_incomplete", error: String(item.stage_error || previous.error || ""), updatedAt: Date.now() };
        if (!next.order.some((value) => value.toLowerCase() === key)) next.order.push(email);
      }
      const errorTexts = Array.isArray(task.result?.errors) ? task.result.errors : [];
      for (const raw of errorTexts) {
        const match = String(raw || "").match(/^\[([^\]]+@[^\]]+)\]/);
        if (!match) continue;
        const key = match[1].toLowerCase();
        const previous = next.accounts[key];
        if (previous) next.accounts[key] = { ...previous, state: "abnormal", checkpoint: "failed", error: String(raw), updatedAt: Date.now() };
      }
      if (task.status === "failed") {
        for (const key of Object.keys(next.accounts)) {
          const account = next.accounts[key];
          if (account.state === "running") next.accounts[key] = { ...account, state: "abnormal", checkpoint: "failed", error: account.error || String(task.error || t.taskFailed), updatedAt: Date.now() };
        }
      }
      if (task.status === "cancelled" || task.status === "interrupted") {
        for (const key of Object.keys(next.accounts)) {
          const account = next.accounts[key];
          if (account.state === "running") next.accounts[key] = { ...account, state: "pending", checkpoint: "cancelled", updatedAt: Date.now() };
        }
      }
      return next;
    });
  }
  async function poll(id: string, ids: number[]) {
    const taskId = String(id || "");
    if (!taskId) {
      setBusy(false);
      setStopRequested(false);
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
      return;
    }
    if (pollingTaskIdsRef.current.has(taskId)) return;
    pollingTaskIdsRef.current.add(taskId);
    let last = taskEventCursorRef.current.taskId === taskId ? Number(taskEventCursorRef.current.last || 0) : 0;
    const emails = mailboxes.filter((m) => ids.includes(m.id)).map((m) => String(m.email || "").toLowerCase());
    let activeLogEmail = "";
    let failures = 0;
    let stream: EventSource | null = null;
    let streamDone = false;
    let streamFailures = 0;
    const applyEvents = (rawItems: AnyObj[]) => {
      if (!workbenchMountedRef.current) return;
      const items = rawItems
        .filter((item) => Number(item?.id || 0) > last)
        .sort((a, b) => Number(a.id || 0) - Number(b.id || 0));
      if (!items.length) return;
      last = Math.max(last, ...items.map((item) => Number(item.id || 0)));
      taskEventCursorRef.current = { taskId, last };
      setTaskEventCursor(taskEventCursorRef.current);
      const progressEvents = items.filter((item: AnyObj) => item.type === "registration_progress" || item.detail?.progress_type === "account_registration");
      applyRegistrationProgressEvents(taskId, progressEvents);
      const entries: LogEntry[] = items.filter((item: AnyObj) => !progressEvents.includes(item)).map((item: AnyObj) => logFromEvent(item));
      setGlobalLogs((old) => prependUniqueLogs(entries, old));
      const scoped = entries.filter((item) => item.email && (!emails.length || emails.includes(item.email.toLowerCase())));
      if (scoped.length) {
        const activeEmail = scoped[scoped.length - 1].email || activeLogEmail;
        setCurrentLogEmail(activeEmail);
        setSelectedLogs((old) => prependUniqueLogs(scoped, old));
        activeLogEmail = activeEmail;
      }
    };
    const openStream = () => {
      if (stream || streamDone) return;
      const apiBase = String(API_BASE || "/api").replace(/\/$/, "");
      const source = new EventSource(`${apiBase}/tasks/${encodeURIComponent(taskId)}/logs/stream?since=${last}`, { withCredentials: true });
      stream = source;
      source.onopen = () => { streamFailures = 0; };
      source.onmessage = (message) => {
        try {
          const payload = JSON.parse(message.data || "{}");
          if (payload.done) {
            streamDone = true;
            source.close();
            if (stream === source) stream = null;
            return;
          }
          applyEvents([payload]);
        } catch {
          // A malformed external log line must not stop the task stream.
        }
      };
      source.onerror = () => {
        source.close();
        if (stream === source) stream = null;
        streamFailures += 1;
      };
    };
    try {
      openStream();
      for (let i = 0; ; i++) {
        if (!workbenchMountedRef.current) return;
        try {
          const task = await apiFetch(`/tasks/${taskId}`);
          if (!workbenchMountedRef.current) return;
          failures = 0;
          if (task.terminal) {
            const remaining = await apiFetch(`/tasks/${taskId}/events?since=${last}`).catch(() => ({ items: [] }));
            applyEvents(remaining.items || []);
            reconcileRegistrationProgress(taskId, task);
            setBusy(false);
            setStopRequested(false);
            setActiveTaskId("");
            setActiveTaskMailboxIds([]);
            setSelected([]);
            setSelectedRowCache({});
            notify(task.status === "succeeded" ? "ok" : "fail", task.status === "succeeded" ? t.taskDone : (task.error || t.taskFailed));
            void load();
            return;
          }
          if (!stream && !streamDone) {
            if (streamFailures > 0) {
              const fallbackEvents = await apiFetch(`/tasks/${taskId}/events?since=${last}`).catch(() => ({ items: [] }));
              applyEvents(fallbackEvents.items || []);
            }
            openStream();
          }
        } catch (e: any) {
          failures += 1;
          if (failures >= 12) {
            const msg = template(t.taskPollLost, { error: e?.message || String(e) });
            setGlobalLogs((old) => [localLog(msg, "error"), ...old].slice(0, 200));
            notify("fail", msg);
            failures = 0;
            await new Promise((r) => setTimeout(r, 4000));
          }
        }
        if (i > 0 && i % 1800 === 0) {
          setGlobalLogs((old) => [localLog(t.taskPollTimeout, "warning"), ...old].slice(0, 200));
          notify("fail", t.taskPollTimeout);
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    } finally {
      (stream as EventSource | null)?.close();
      pollingTaskIdsRef.current.delete(taskId);
    }
  }
  useEffect(() => {
    if (!activeTaskId) {
      if (submittingTask) return;
      if (busy) setBusy(false);
      if (stopRequested) setStopRequested(false);
      return;
    }
    if (!busy) setBusy(true);
    if (!resumedTaskIdsRef.current.has(activeTaskId)) {
      resumedTaskIdsRef.current.add(activeTaskId);
      setGlobalLogs((old) => [localLog(t.taskPollRecovered), ...old].slice(0, 200));
    }
    void poll(activeTaskId, activeTaskMailboxIds);
  }, [busy, submittingTask, stopRequested, activeTaskId, activeTaskMailboxIds]);
  async function importFile(file?: File) {
    if (!file) return;
    const text = await file.text();
    try { await apiFetch("/sunny/mailboxes/import", { method: "POST", body: JSON.stringify({ lines: text }) }); notify("ok", t.importDone); void load(); } catch (e: any) { notify("fail", e.message || String(e)); }
  }
  async function exportAccounts() {
    try {
      const details = await trackListLoad(() => Promise.all(rows.map((r) => apiFetch(`/sunny/mailboxes/${r.id}`))));
      const text = details.map((r) => `${r.email}----${r.password || ""}----${r.client_id || ""}----${r.refresh_token || ""}`).join("\n") + "\n";
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      triggerBrowserDownload(blob, "sunnyregister-chatgpt-accounts.txt");
      notify("ok", t.exportDone);
    } catch (e: any) {
      notify("fail", e.message || String(e));
    }
  }
  async function refreshAccountStatus(row: AnyObj) {
    const accountId = Number(row.account?.id || 0);
    if (!accountId) {
      await load();
      notify("ok", t.done);
      return;
    }
    setBusy(true);
    setGlobalLogs((old) => [localLog(`${t.refreshStatus}: ${row.email}`), ...old]);
    try {
      const res = await apiFetch("/sunny/tasks/refresh-session", { method: "POST", body: JSON.stringify({ account_ids: [accountId], concurrency: 1 }) });
      const taskId = String(res.id || res.task_id || "");
      setActiveTaskId(taskId);
      setActiveTaskMailboxIds([row.id]);
      void poll(taskId, [row.id]);
    } catch (e: any) {
      notify("fail", e.message || String(e));
      setBusy(false);
      setActiveTaskId("");
      setActiveTaskMailboxIds([]);
    }
  }
  const selectedRows = selected.map((id)=>selectedRowCache[String(id)] || rows.find((row)=>row.id===id)).filter(Boolean) as AnyObj[];
  const allChecked = pagedRows.length > 0 && pagedRows.every((r) => selected.includes(r.id));
  const selectRow = (row: AnyObj, checked: boolean) => {
    if (checked) {
      setSelected((old)=>Array.from(new Set([...old, Number(row.id)])));
      setSelectedRowCache((old)=>({...old, [String(row.id)]: row}));
      return;
    }
    setSelected((old)=>old.filter((id)=>id!==Number(row.id)));
    setSelectedRowCache((old)=>{ const next={...old}; delete next[String(row.id)]; return next; });
  };
  const selectCurrentPage = (checked: boolean) => {
    const pageIds = pagedRows.map((row)=>Number(row.id));
    setSelected((old)=>checked ? Array.from(new Set([...old, ...pageIds])) : old.filter((id)=>!pageIds.includes(id)));
    setSelectedRowCache((old)=>{
      const next={...old};
      pagedRows.forEach((row)=>{ if (checked) next[String(row.id)]=row; else delete next[String(row.id)]; });
      return next;
    });
  };
  const clearWorkbenchSelection = () => { setSelected([]); setSelectedRowCache({}); };
  return <div className="space-y-5">
    <div className="grid gap-4 lg:grid-cols-2">
      <LogCard t={t} title={t.globalLogs} progressTitle={t.registrationTaskProgress} view={globalCardView} onView={setGlobalCardView} logs={globalLogs} busy={busy} onClear={()=>{ setGlobalLogs([]); if (!busy && !activeTaskId) { setRegistrationProgress(null); taskEventCursorRef.current = { taskId: "", last: 0 }; setTaskEventCursor(taskEventCursorRef.current); } }} progressContent={<TaskRegistrationProgress t={t} progress={registrationProgress}/>}/>
      <LogCard t={t} title={t.selectedLogs} progressTitle={t.accountRegistrationProgress} view={accountCardView} onView={setAccountCardView} logs={selectedLogs} busy={busy} onClear={()=>setSelectedLogs([])} progressContent={<AccountRegistrationProgressList t={t} progress={registrationProgress}/>}/>
    </div>
    <Card className="sr-toolbar rounded-[18px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-4"><h2 className="text-2xl font-bold text-slate-950 dark:text-white">ChatGPT</h2><SelectionSummary t={t} count={selected.length} total={total} selectingAll={selectingAll} onSelectAll={selectAllFiltered} onClear={clearWorkbenchSelection}/></div>
        <div className="flex flex-wrap gap-2">
          <button className="sr-btn sr-danger-btn disabled:cursor-not-allowed disabled:opacity-50" title={activeTaskId || submittingTask ? t.interruptTaskTip : ""} onClick={cancelActiveTask} disabled={(!activeTaskId && !submittingTask) || stopRequested}><X className="h-4 w-4"/>{stopRequested ? t.interruptingTask : t.interruptTask}</button>
          <span title={busy ? t.registerTaskRunning : !selected.length ? t.chooseMailbox : ""}>
            <Button className="rounded-xl bg-blue-600 px-4 text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={() => setAutoOpen(true)} disabled={busy || selected.length === 0}><Plus className="mr-2 h-4 w-4"/>{t.autoRegister}</Button>
          </span>
          <label className="sr-btn"><Download className="h-4 w-4"/>{t.import}<input type="file" className="hidden" onChange={(e)=>importFile(e.target.files?.[0])}/></label>
          <button className="sr-btn" onClick={exportAccounts} disabled={!rows.length}><Upload className="h-4 w-4"/>{t.export}</button>
        </div>
      </div>
      <div className="mt-5 border-t border-slate-100 pt-4 dark:border-white/10">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 flex-wrap gap-3">
            <div className="sr-search-control relative"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.searchAccount} /></div>
            <SelectBox searchable searchPlaceholder={t.groupSearch} noResultsLabel={t.groupNoResults} className="sr-select-like" value={groupFilter} onChange={(v)=>setGroupFilter(Number(v))} options={[{value:0,label:t.allGroups,searchText:t.allGroups}, ...mailboxGroupOptions(t,groups)]} />
            <SelectBox className="sr-select-like" value={status} onChange={(v)=>setStatus(String(v))} options={[{value:"",label:t.allStatus}, ...MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))]} />
            <SelectBox className="sr-select-like" value={planFilter} onChange={(v)=>setPlanFilter(String(v))} options={[{value:"",label:t.allPlanTypes}, ...PLAN_TYPE_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))]} />
          </div>
          <button className="sr-text-btn sr-action-refresh" title={t.refreshList} onClick={refreshList}><RefreshCw className="h-5 w-5"/></button>
        </div>
      </div>
    </Card>
    <Card className="sr-table-card overflow-hidden rounded-[18px] p-0" aria-busy={listLoading}>
      <ListLoadingOverlay loading={listLoading} label={t.loadingData}/>
      <ResizableDataTable tableKey="workbench" columns={DATA_TABLE_COLUMNS.workbench} headers={[<input type="checkbox" checked={allChecked} onChange={(e)=>selectCurrentPage(e.target.checked)}/>,t.email,t.mailboxGroup,t.status,t.planType,<SortTimeHeader label={t.statusChangedAt} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/>,t.operation]}><tbody>{rows.length ? pagedRows.map((r) => <tr key={r.id}><td><input type="checkbox" checked={selected.includes(r.id)} onChange={(e)=>selectRow(r,e.target.checked)}/></td><td title={r.email}>{r.email}</td><td title={r.group_name || t.defaultGroup}>{r.group_name || t.defaultGroup}</td><td><StatusBadge t={t} status={r.status || "未注册"} /></td><td><PlanTypeBadge value={r.account?.plan_type || r.plan_type} /></td><td>{formatDateTime(r.status_changed_at)}</td><td><button className="sr-link inline-flex items-center gap-1" title={t.refreshStatus} disabled={busy} onClick={()=>refreshAccountStatus(r)}><RefreshCw className="h-4 w-4"/>{t.refresh}</button></td></tr>) : <tr><td colSpan={7}><div className="sr-empty"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.noData}</div><p className="mt-2 text-sm text-slate-400">{t.noDataDesc}</p></div></td></tr>}</tbody></ResizableDataTable>
      <PaginationBar t={t} total={total} page={safePageNo} pageSize={pageSize} setPage={setPageNo} setPageSize={setPageSize} />
    </Card>
    {autoOpen && <AutoRegisterModal t={t} busy={busy} selectedEmails={selectedRows.map((m)=>m.email)} selectedNeedPhone={selectedRows.some((m)=>m.has_openai_rt !== true)} concurrency={modalConcurrency} setConcurrency={setModalConcurrency} identity={identity} setIdentity={setIdentity} mode={mode} setMode={setMode} protocolChallengeStrategy={protocolChallengeStrategy} setProtocolChallengeStrategy={setProtocolChallengeStrategy} stage={stage} setStage={setStage} allTrafficProxyPool={allTrafficProxyPool} setAllTrafficProxyPool={setAllTrafficProxyPool} onClose={()=>setAutoOpen(false)} onStart={()=>createRegisterTask()} notify={notify} />}
  </div>;
}

function AutoRegisterModal({ t, busy, selectedEmails, selectedNeedPhone, concurrency, setConcurrency, identity, setIdentity, mode, setMode, protocolChallengeStrategy, setProtocolChallengeStrategy, stage, setStage, allTrafficProxyPool, setAllTrafficProxyPool, onClose, onStart, notify }: { t: typeof zh; busy: boolean; selectedEmails: string[]; selectedNeedPhone: boolean; concurrency: number; setConcurrency: (v:number)=>void; identity: "system"|"google"|"microsoft"; setIdentity: (v:"system"|"google"|"microsoft")=>void; mode: "protocol"|"background"|"visible"; setMode:(v:"protocol"|"background"|"visible")=>void; protocolChallengeStrategy: ProtocolChallengeStrategy; setProtocolChallengeStrategy:(v:ProtocolChallengeStrategy)=>void; stage: RegisterStage; setStage:(v:RegisterStage)=>void; allTrafficProxyPool: boolean; setAllTrafficProxyPool: (v:boolean)=>void; onClose:()=>void; onStart:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
	const mailboxVerificationDescription = t === zh
		? "系统将按邮箱类型自动选择 Microsoft OAuth 或 Apple iCloud 渠道完成邮箱验证。"
		: "The system automatically selects Microsoft OAuth or the Apple iCloud channel based on each mailbox type.";
  const [phoneCfg, setPhoneCfg] = useState<AnyObj>({ pool_enabled: true, usable_count: 0 });
  const [reverseCfg, setReverseCfg] = useState<AnyObj>({});
  const [mailboxCfg, setMailboxCfg] = useState<AnyObj>({ pool_enabled: true });
  useEffect(() => {
    let alive = true;
    Promise.all([
      apiFetch("/sunny/phones/config").catch(() => ({})),
      apiFetch("/sunny/sub2api-config").catch(() => ({})),
      apiFetch("/sunny/mailboxes/config").catch(() => ({})),
    ]).then(([phone, reverse, mailbox]) => {
      if (!alive) return;
      setPhoneCfg(phone || {});
      setReverseCfg(reverse || {});
      setMailboxCfg(mailbox || { pool_enabled: true });
    });
    return () => { alive = false; };
  }, []);
  const identityText = identity === "system" ? t.systemMailbox : identity === "google" ? "Google" : "Microsoft";
  const protocolCopy = t === en ? PROTOCOL_MODE_COPY.en : PROTOCOL_MODE_COPY.zh;
  const modeText = mode === "protocol" ? t.protocolMode : mode === "background" ? t.backgroundMode : t.visibleMode;
  const stageText = stage === CODEX_PHONE_BIND ? t.codexPhoneBind : stage === IMPORT_REVERSE_PROXY ? t.importReverseProxy : stage === AGENT_IDENTITY_REVERSE_PROXY ? t.agentIdentityReverseProxy : t.registerOnly;
  const usablePhones = Number(phoneCfg.usable_count || 0);
  const poolPhoneReady = phoneCfg.pool_enabled !== false && usablePhones > 0;
  const smsbowerReady = phoneCfg.smsbower_enabled === true && !!String(phoneCfg.smsbower_api_key || "").trim();
  const smspoolReady = phoneCfg.smspool_enabled === true && !!String(phoneCfg.smspool_api_key || "").trim();
  const firefoxReady = phoneCfg.firefox_enabled === true
    && !!String(phoneCfg.firefox_api_name || "").trim()
    && !!String(phoneCfg.firefox_password || "").trim()
    && !!String(phoneCfg.firefox_default_country || "").trim()
    && !!String(phoneCfg.firefox_default_service || "").trim()
    && Number(phoneCfg.firefox_max_price || 0) > 0;
  const phoneResourceReady = !selectedNeedPhone || poolPhoneReady || smsbowerReady || smspoolReady || firefoxReady;
  const sub2apiReady = reverseCfg.enabled !== false && !!String(reverseCfg.base_url || "").trim() && !!String(reverseCfg.admin_token || "").trim() && Array.isArray(reverseCfg.group_ids) && reverseCfg.group_ids.length > 0;
  const mailboxPoolReady = mailboxCfg.pool_enabled !== false;
  const googleMailboxReady = false;
  const microsoftMailboxReady = false;
  const identityValid = (identity === "system" && mailboxPoolReady) || (identity === "google" && googleMailboxReady) || (identity === "microsoft" && microsoftMailboxReady);
  const modeValid = mode === "visible" || mode === "background" || mode === "protocol";
  const registerOnlyDisabled = !identityValid;
  const stageValid = identityValid && (stage !== CODEX_PHONE_BIND || phoneResourceReady);
  const startDisabled = busy || !identityValid || !modeValid || !stageValid;
  const mailboxHint = t.linkedMailboxConfig + " · " + (mailboxPoolReady ? t.resourceReady : t.resourceMissing);
  const phoneHint = t.linkedPhoneConfig + " · " + (!selectedNeedPhone ? t.existingRTReady : poolPhoneReady ? template(t.usablePhones, { count: usablePhones }) : smsbowerReady ? t.smsbowerReady : smspoolReady ? t.smspoolReady : firefoxReady ? t.firefoxReady : t.resourceMissing);
  const reverseHint = t.linkedReverseConfig + " · " + (sub2apiReady ? t.sub2apiReady : t.sub2apiMissing);
  const codexDisabled = !identityValid || !phoneResourceReady;
  const importDisabled = !identityValid;
  const agentIdentityDisabled = !identityValid;
  const safeConcurrency = Math.max(1, Math.min(Number(concurrency) || 1, Math.max(1, selectedEmails.length)));
  const allTrafficProxyPoolLabel = t === zh ? "全流程使用代理池" : "Use proxy pool for all traffic";
  const allTrafficProxyPoolTip = t === zh ? "默认关闭：只有 ChatGPT 官方注册/登录使用代理池；邮箱读取、接码、试用和其他外部 API 使用系统代理。勾选后，整个注册任务统一使用代理池出口。" : "Off by default: only ChatGPT registration/login uses the proxy pool. Mail, SMS, trial checks and other external APIs use the system proxy. Turn this on to route the entire registration task through the proxy pool.";
  return <div className="sr-modal-mask"><div className="sr-modal sr-register-modal">
    <div className="sr-modal-head"><h3>{t.autoRegisterTitle}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body">
      <div className="sr-step">{t.step} 1</div>
		<h4>{t.step1Title}</h4><p>{mailboxVerificationDescription}</p>
      <div className="sr-choice-grid two">
        <Choice disabled={!mailboxPoolReady} disabledMessage={t.systemMailboxPoolDisabled} active={mailboxPoolReady && identity==="system"} title={t.systemMailbox} desc={t.systemMailboxDesc} onClick={()=>{ setIdentity("system"); setStage(REGISTER_ONLY); }} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled disabledMessage={t.googleMailboxDisabled} active={false} title="Google" desc={t.googleDesc} onClick={()=>setIdentity("google")} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled disabledMessage={t.microsoftMailboxDisabled} active={false} title="Microsoft" desc={t.microsoftDesc} onClick={()=>setIdentity("microsoft")} onDisabledClick={(msg)=>notify("fail", msg)} />
      </div>
      <div className="sr-step mt-7">{t.step} 2</div>
      <h4>{t.step2Title}</h4><p>{protocolCopy.step2Desc}</p>
      <div className="sr-choice-grid three">
        <Choice active={mode==="protocol"} title={t.protocolMode} desc={protocolCopy.desc} onClick={()=>setMode("protocol")} />
        <Choice active={mode==="background"} title={t.backgroundMode} desc={t.backgroundDesc} onClick={()=>setMode("background")} />
        <Choice active={mode==="visible"} title={t.visibleMode} desc={t.visibleDesc} onClick={()=>setMode("visible")} />
      </div>
      {mode === "protocol" ? <div className="sr-protocol-strategy" role="group" aria-label={t.protocolChallengeStrategy}>
        <span>{t.protocolChallengeStrategy}</span>
        <button type="button" className={cn(protocolChallengeStrategy === "native_headless" && "active")} aria-pressed={protocolChallengeStrategy === "native_headless"} title={t.protocolNativeChallengeDesc} onClick={()=>setProtocolChallengeStrategy("native_headless")}>{t.protocolNativeChallenge}</button>
        <button type="button" className={cn(protocolChallengeStrategy === "sentinel_protocol" && "active")} aria-pressed={protocolChallengeStrategy === "sentinel_protocol"} title={t.protocolSentinelChallengeDesc} onClick={()=>setProtocolChallengeStrategy("sentinel_protocol")}>{t.protocolSentinelChallenge}</button>
      </div> : null}
      <div className="sr-step mt-7">{t.step} 3</div>
      <h4>{t.step3Title}</h4><p>{t.step3Desc}</p>
      <div className="sr-choice-grid four">
        <Choice disabled={registerOnlyDisabled} disabledMessage={t.registerStageUnavailable} active={identityValid && stage===REGISTER_ONLY} title={t.registerOnly} desc={t.registerOnlyDesc + "\n" + mailboxHint} onClick={()=>setStage(REGISTER_ONLY)} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled={codexDisabled} disabledMessage={t.registerStageUnavailable} active={!codexDisabled && stage===CODEX_PHONE_BIND} title={t.codexPhoneBind} desc={t.codexPhoneBindDesc + "\n" + phoneHint + (codexDisabled ? " · " + t.stageDisabledTip : "")} onClick={()=>setStage(CODEX_PHONE_BIND)} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled={importDisabled} disabledMessage={t.registerStageUnavailable} active={!importDisabled && stage===IMPORT_REVERSE_PROXY} title={t.importReverseProxy} desc={t.importReverseProxyDesc + "\n" + phoneHint + "\n" + reverseHint + (importDisabled ? " · " + t.stageDisabledTip : "")} onClick={()=>setStage(IMPORT_REVERSE_PROXY)} onDisabledClick={(msg)=>notify("fail", msg)} />
        <Choice disabled={agentIdentityDisabled} disabledMessage={t.registerStageUnavailable} active={!agentIdentityDisabled && stage===AGENT_IDENTITY_REVERSE_PROXY} title={t.agentIdentityReverseProxy} desc={t.agentIdentityReverseProxyDesc + "\n" + reverseHint + (agentIdentityDisabled ? " · " + t.stageDisabledTip : "")} onClick={()=>setStage(AGENT_IDENTITY_REVERSE_PROXY)} onDisabledClick={(msg)=>notify("fail", msg)} />
      </div>
      <div className="sr-summary sr-register-summary"><div><b>{t.identityLabel}</b><span>{identityText}</span></div><div><b>{t.modeLabel}</b><span>{modeText}</span></div><div><b>{t.stageLabel}</b><span>{stageText}</span></div><div><b>{t.registerAccounts}</b><span>{selectedEmails.length}</span></div><div><b>{t.concurrency}</b><input className="sr-concurrency-input" type="number" min={1} max={Math.max(1, selectedEmails.length)} value={safeConcurrency} onChange={(e)=>setConcurrency(Math.max(1, Math.min(Number(e.target.value || 1), Math.max(1, selectedEmails.length))))}/></div><div className="sr-register-account-list">{selectedEmails.map((email)=><div key={email}>{email}</div>)}</div></div>
      <div className="sr-register-actions"><label className="mr-3 flex min-h-12 items-center gap-2 whitespace-nowrap text-sm text-slate-600" title={allTrafficProxyPoolTip}><input type="checkbox" checked={allTrafficProxyPool} onChange={(e)=>setAllTrafficProxyPool(e.target.checked)} disabled={busy}/><span>{allTrafficProxyPoolLabel}</span></label><Button className="h-12 flex-1 rounded-xl bg-blue-600 text-lg text-white hover:bg-blue-700" disabled={startDisabled} onClick={onStart}>{busy ? <Loader2 className="mr-2 h-5 w-5 animate-spin"/> : null}{t.startAutoRegister}</Button><button className="sr-register-cancel" onClick={onClose}>{t.cancel}</button></div>
    </div>
  </div></div>;
}

function Choice({ active, disabled, disabledMessage, title, desc, onClick, onDisabledClick }: { active: boolean; disabled?: boolean; disabledMessage?: string; title: string; desc: string; onClick: () => void; onDisabledClick?: (message:string)=>void }) {
  return <button type="button" className={cn("sr-choice", active && "active", disabled && "disabled")} aria-disabled={disabled} onClick={() => { if (disabled) { onDisabledClick?.(disabledMessage || "Disabled"); return; } onClick(); }}><b>{title}</b><span>{desc}</span></button>;
}

function mailboxLineErrors(lines: string, mailboxType: "microsoft" | "apple", mailboxChannel = "xbovo"): string[] {
  const errors: string[] = [];
  String(lines || "").split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    const parts = line.split("----").map((x) => x.trim());
    const urlAPIInvalid = mailboxType === "apple" && mailboxChannel === "url_api" && (
      parts.length < 1 || parts.length > 4 || !parts[0] || !parts[0].includes("@")
      || parts.slice(1).some((value)=>!value)
      || parts.slice(1).filter((value)=>/^https?:\/\//i.test(value)).length > 1
      || parts.slice(2).filter((value)=>value && !/^https?:\/\//i.test(value)).some((value)=>!(/^[A-Z2-7\s=]{16,128}$/i.test(value)))
    );
    const invalid = mailboxType === "apple"
      ? (mailboxChannel === "url_api" ? urlAPIInvalid : parts.length !== 2 || !parts[0] || !parts[0].includes("@") || !parts[1])
      : parts.length < 4 || !parts[0] || !parts[0].includes("@") || !parts[2] || !parts[3];
    if (invalid) {
      errors.push(`Line ${index + 1}: ${mailboxType === "apple" ? (mailboxChannel === "url_api" ? "邮箱 / 邮箱----密码 / 邮箱----收码URL / 可选2FA" : "icloud_email----key") : "email----password----client_id----refresh_token"}`);
    }
  });
  return errors;
}

function MailboxConfig({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
	const emptyMailboxDescription = t === zh
		? "请点击右上角“导入邮箱”添加微软邮箱或 Apple iCloud 邮箱。"
		: "Click 'Import Mailboxes' in the upper-right corner to add Microsoft or Apple iCloud mailboxes.";
  const [items,setItems]=useCachedState<AnyObj[]>("mailbox.items", []);
  const [groups,setGroups]=useCachedState<AnyObj[]>("mailbox.groups", []);
  const [page,setPage]=useCachedState("mailbox.page", 1);
  const [pageSize,setPageSize]=useCachedState("mailbox.pageSize", 10);
  const [total,setTotal]=useCachedState("mailbox.total", 0);
  const [mailboxTotal,setMailboxTotal]=useState(0);
  const [statusCounts,setStatusCounts]=useState<Record<string,number>>({});
  const [query,setQuery]=useCachedState("mailbox.query", "");
  const debouncedQuery = useDebouncedValue(query);
  const [groupFilter,setGroupFilter]=useCachedState("mailbox.groupFilter", 0);
  const [statusFilter,setStatusFilter]=useCachedState("mailbox.statusFilter", "");
  const [planFilter,setPlanFilter]=useCachedState("mailbox.planFilter", "");
  const [timeSort,setTimeSort]=useCachedState<SortOrder>("mailbox.timeSort", "desc");
  const [selected,setSelected]=useCachedState<number[]>("mailbox.selected", []);
  const [selectingAll,setSelectingAll]=useState(false);
  const [importOpen,setImportOpen]=useState(false);
  const [editing,setEditing]=useState<AnyObj|null>(null);
  const [batchEditing,setBatchEditing]=useState(false);
  const [mailboxForMail,setMailboxForMail]=useState<AnyObj|null>(null);
  const [mailboxCfg,setMailboxCfg]=useCachedState<AnyObj>("mailbox.config",{pool_enabled:true});
  const [fieldLoading,setFieldLoading]=useState<Record<string,boolean>>({});
  const { loading: listLoading, track: trackListLoad } = useLoadingTracker();
  const load=()=>trackListLoad(async()=>{
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    params.set("summary", "true");
    if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim());
    if (groupFilter) params.set("group_id", String(groupFilter));
    if (statusFilter) params.set("status", statusFilter);
    if (planFilter) params.set("plan_type", planFilter);
    params.set("sort_by", "updated_at");
    params.set("sort_order", timeSort);
    const m=await apiFetch(`/sunny/mailboxes?${params.toString()}`);
    setItems(m.items||[]);
    setTotal(m.total||0);
    setMailboxTotal(Number(m.mailbox_total || 0));
    setStatusCounts(m.status_counts || {});
  });
  const selectAllFiltered=async()=>{
    setSelectingAll(true);
    try {
      const params=new URLSearchParams({summary:"true",sort_by:"updated_at",sort_order:timeSort});
      if(debouncedQuery.trim()) params.set("q",debouncedQuery.trim());
      if(groupFilter) params.set("group_id",String(groupFilter));
      if(statusFilter) params.set("status",statusFilter);
      if(planFilter) params.set("plan_type",planFilter);
      const result=await apiFetch(`/sunny/mailboxes?${allSelectionParams(params).toString()}`);
      const ids=selectionIDs(result);
      setSelected(ids);
      notify("ok",template(t.selectAllDone,{count:ids.length}));
    } catch(e:any) { notify("fail",e.message||String(e)); }
    finally { setSelectingAll(false); }
  };
  useEffect(()=>{void load()},[page, debouncedQuery, groupFilter, statusFilter, planFilter, timeSort, pageSize]);
  const loadGroups=()=>apiFetch("/sunny/mailbox-groups").then((g)=>{const next=sortMailboxGroups(g.items||[]);setGroups(next);return next});
  useEffect(()=>{void loadGroups().catch(()=>{})},[]);
  useEffect(()=>{apiFetch("/sunny/mailboxes/config").then((cfg)=>setMailboxCfg(cfg || {pool_enabled:true})).catch(()=>{})},[]);
  useEffect(()=>{setPage(1)},[query, groupFilter, statusFilter, planFilter, timeSort, pageSize]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  async function run(label:string, fn:()=>Promise<any>){try{await fn();notify("ok",label);void load();void loadGroups().catch(()=>{})}catch(e:any){notify("fail",e.message||String(e))}}
  async function deleteMailbox(m: AnyObj) {
    await run(t.done,()=>apiFetch(`/sunny/mailboxes/${m.id}`,{method:"DELETE"}));
  }
  async function openMailboxEditor(m: AnyObj) {
    try {
      const detail = await trackListLoad(() => apiFetch(`/sunny/mailboxes/${m.id}`));
      setEditing(detail);
    } catch (e: any) {
      notify("fail", e.message || String(e));
    }
  }
  async function openMailboxMail(m: AnyObj) {
    try {
      const detail = await trackListLoad(() => apiFetch(`/sunny/mailboxes/${m.id}`));
      setMailboxForMail(detail);
    } catch (e: any) {
      notify("fail", e.message || String(e));
    }
  }
  async function copyMailboxField(m: AnyObj, field: "access_token" | "secret_key") {
    const key = `${m.id}:${field}`;
    setFieldLoading((old)=>({...old,[key]:true}));
    try {
      const result = await apiFetch(`/sunny/mailboxes/${m.id}/field?name=${field}`);
      const value = String(result.value || "").trim();
      if (!value) throw new Error(field === "access_token" ? t.sessionFieldEmpty.replace("{field}", "AT") : t.secretKeyUnavailable);
      await copyTextToClipboard(value);
      notify("ok", t.copySuccess);
    } catch (e: any) {
      notify("fail", e.message || String(e));
    } finally {
      setFieldLoading((old)=>{const next={...old};delete next[key];return next;});
    }
  }
  async function batchDelete(){
    if (!selected.length) return;
    await run(t.done, async()=>{ await Promise.all(selected.map((id)=>apiFetch(`/sunny/mailboxes/${id}`,{method:"DELETE"}))); setSelected([]); });
  }
  async function toggleMailboxPoolEnabled() {
    const next = !(mailboxCfg.pool_enabled !== false);
    try {
      const saved = await apiFetch("/sunny/mailboxes/config", { method:"PUT", body: JSON.stringify({ ...mailboxCfg, pool_enabled: next }) });
      setMailboxCfg(saved || { pool_enabled: next });
      notify("ok", t.done);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  const allChecked = items.length > 0 && items.every((m)=>selected.includes(m.id));
  const mailboxPoolEnabled = mailboxCfg.pool_enabled !== false;
  const overviewCards = [
    { status:"", label:t.mailboxOverviewTotal, count:mailboxTotal, tone:"total" },
    { status:"未注册", label:t.mailboxOverviewPending, count:Number(statusCounts["未注册"] || 0), tone:"pending" },
    { status:"已注册", label:t.mailboxOverviewRegistered, count:Number(statusCounts["已注册"] || 0), tone:"registered" },
    { status:"已接码", label:t.mailboxOverviewPhoneBound, count:Number(statusCounts["已接码"] || 0), tone:"phone" },
    { status:"已反代", label:t.mailboxOverviewReversed, count:Number(statusCounts["已反代"] || 0), tone:"reversed" },
    { status:"已封禁", label:t.mailboxOverviewBanned, count:Number(statusCounts["已封禁"] || 0), tone:"banned" },
    { status:"需二验", label:t.mailboxOverviewNeeds2FA, count:Number(statusCounts["需二验"] || 0), tone:"verify" },
    { status:"登录刷新", label:t.mailboxOverviewRefreshing, count:Number(statusCounts["登录刷新"] || 0), tone:"refreshing" },
    { status:"失败", label:t.mailboxOverviewFailed, count:Number(statusCounts["失败"] || 0), tone:"failed" },
  ];
  return <div className="space-y-4">
    <Card className="sr-sms-provider-card sr-mailbox-provider-card rounded-[24px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold">{t.mailboxPoolName}</h2>
        </div>
        <button type="button" aria-label={t.mailboxPoolGlobalSwitch} title={t.mailboxPoolSwitchTip} className={cn("sr-switch-only", mailboxPoolEnabled && "on")} onClick={toggleMailboxPoolEnabled}>
          <span />
        </button>
      </div>
      {mailboxPoolEnabled && <div className="sr-mailbox-expanded mt-5 space-y-4">
        <div className="sr-mailbox-stats-grid" aria-label={t.mailboxPoolName}>
          {overviewCards.map((card)=><button type="button" key={card.status || "all"} data-tone={card.tone} className={cn("sr-mailbox-stat-card", statusFilter===card.status && "active")} aria-pressed={statusFilter===card.status} onClick={()=>{setStatusFilter(card.status);setPage(1)}}>
            <span className="sr-mailbox-stat-label"><i />{card.label}</span>
            <strong>{card.count}</strong>
          </button>)}
        </div>
        <div className="sr-toolbar sr-toolbar-compact sr-mailbox-toolbar sr-mailbox-inner-toolbar rounded-[18px] p-4">
          <div className="sr-mailbox-toolbar-row gap-2">
            <div className="sr-mailbox-filters flex min-w-0 flex-nowrap gap-2">
              <div className="sr-search-control sr-mailbox-search relative"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.queryMailbox}/></div>
              <SelectBox searchable searchPlaceholder={t.groupSearch} noResultsLabel={t.groupNoResults} className="sr-select-like" value={groupFilter} onChange={(v)=>setGroupFilter(Number(v))} options={[{value:0,label:t.allGroups,searchText:t.allGroups}, ...mailboxGroupOptions(t,groups)]} />
              <SelectBox className="sr-select-like" value={statusFilter} onChange={(v)=>setStatusFilter(String(v))} options={[{value:"",label:t.allStatus}, ...MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))]} />
              <SelectBox className="sr-select-like" value={planFilter} onChange={(v)=>setPlanFilter(String(v))} options={[{value:"",label:t.allPlanTypes}, ...PLAN_TYPE_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))]} />
            </div>
            <div className="sr-mailbox-actions flex flex-nowrap items-center gap-2"><SelectionSummary t={t} count={selected.length} total={total} selectingAll={selectingAll} onSelectAll={selectAllFiltered} onClear={()=>setSelected([])}/>{selected.length > 0 && <ConfirmBubble message={t.confirmBatchDeleteMailbox} detail={`${selected.length} ${t.selected}`} onConfirm={batchDelete}><Button variant="outline" className="rounded-xl border-red-200 text-red-500">{t.batchDelete} ({selected.length})</Button></ConfirmBubble>}{selected.length > 0 && <Button variant="outline" className="rounded-xl border-emerald-200 text-emerald-700" onClick={()=>setBatchEditing(true)}>{t.batchEdit} ({selected.length})</Button>}<button className="sr-text-btn sr-action-refresh" onClick={load}><RefreshCw className="h-4 w-4"/>{t.refresh}</button><Button className="rounded-xl bg-emerald-600 px-4 text-white hover:bg-emerald-700" onClick={()=>setImportOpen(true)}><Download className="mr-2 h-4 w-4"/>{t.importMailboxes}</Button></div>
          </div>
        </div>
        <div className="sr-table-card sr-mailbox-table-panel overflow-hidden rounded-[18px] p-0" aria-busy={listLoading}>
          <ListLoadingOverlay loading={listLoading} label={t.loadingData}/>
          <ResizableDataTable tableKey="mailboxes" columns={DATA_TABLE_COLUMNS.mailboxes} headers={[<input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected,...items.map((m)=>m.id)])) : selected.filter((id)=>!items.some((m)=>m.id===id)))}/>,t.mailbox,t.mailboxGroup,t.status,t.planType,"AT","SK",t.enabled,<SortTimeHeader label={t.updatedAt} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/>,t.actions]}>
            <tbody>{items.length ? items.map((m)=><tr key={m.id}>
              <td><input type="checkbox" checked={selected.includes(m.id)} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected,m.id])) : selected.filter((id)=>id!==m.id))}/></td>
              <td title={m.email}><div className="font-semibold">{m.email}</div></td>
              <td title={m.group_name || t.defaultGroup}>{m.group_name || t.defaultGroup}</td>
              <td><StatusBadge t={t} status={m.status || "未注册"} /></td>
              <td><PlanTypeBadge value={m.plan_type} /></td>
              <td>{m.has_access_token ? <button className="sr-session-field-button" title={t.copy} disabled={fieldLoading[`${m.id}:access_token`]} onClick={()=>void copyMailboxField(m,"access_token")}>{fieldLoading[`${m.id}:access_token`] ? <Loader2 className="h-4 w-4 animate-spin"/> : "AT"}</button> : "-"}</td>
              <td><button className="sr-session-field-button" title={t.copy} disabled={fieldLoading[`${m.id}:secret_key`]} onClick={()=>void copyMailboxField(m,"secret_key")}>{fieldLoading[`${m.id}:secret_key`] ? <Loader2 className="h-4 w-4 animate-spin"/> : "SK"}</button></td>
              <td><button className={cn("sr-toggle", m.enabled && "on")} onClick={()=>run(t.done,()=>apiFetch(`/sunny/mailboxes/${m.id}`,{method:"PUT",body:JSON.stringify({enabled:!m.enabled})}))}>{m.enabled ? "ON" : "OFF"}</button></td>
              <td>{formatDateTime(m.updated_at)}</td>
              <td><div className="flex flex-wrap gap-2"><button className="sr-link" onClick={()=>void openMailboxMail(m)}>{t.queryMail}</button><button className="sr-link" onClick={()=>void openMailboxEditor(m)}>{t.edit}</button><ConfirmBubble message={t.confirmDeleteMailbox} detail={m.email || ""} onConfirm={()=>deleteMailbox(m)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td>
			</tr>) : <tr><td colSpan={10}><div className="sr-empty"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.noMailbox}</div><p className="mt-2 text-sm text-slate-400">{emptyMailboxDescription}</p></div></td></tr>}</tbody>
          </ResizableDataTable>
          <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
        </div>
      </div>}
    </Card>
    {importOpen && <MailboxImportModal t={t} groups={groups} onGroupsChanged={setGroups} onClose={()=>setImportOpen(false)} onImported={()=>{setImportOpen(false); notify("ok",t.done); void load(); void loadGroups().catch(()=>{});}} notify={notify}/>}
    {editing && <MailboxEditModal t={t} mailbox={editing} groups={groups} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok",t.done); void load();}} notify={notify}/>}
    {batchEditing && <MailboxBatchEditModal t={t} selected={selected} groups={groups} onClose={()=>setBatchEditing(false)} onSaved={()=>{setBatchEditing(false); setSelected([]); notify("ok",t.done); void load();}} notify={notify}/>}
    {mailboxForMail && <MailboxMailModal t={t} mailbox={mailboxForMail} onClose={()=>setMailboxForMail(null)} notify={notify}/>}
  </div>;
}

function StatusBadge({ t, status }: { t: typeof zh; status: string }) {
  const normalized = status === "registered" ? "已注册" : status === "phone_bound" ? "已接码" : status === "reverse_proxied" ? "已反代" : status === "failed" ? "失败" : status;
  const map: Record<string,string> = {
    "未注册": "gray",
    "已注册": "blue",
    "已接码": "green",
    "已反代": "cyan",
    "已封禁": "red",
    "需二验": "amber",
    "注册中": "amber",
    "登录刷新": "blue",
    "失败": "red",
    "禁用": "red",
  };
  return <span className={cn("sr-status", "sr-status-" + (map[normalized] || "gray"))}>{t.statusLabels[normalized as keyof typeof t.statusLabels] || normalized}</span>;
}

function formatPlanType(value: any) {
  const text = String(value || "").trim();
  if (!text || text === "-") return "-";
  const lower = text.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

function PlanTypeBadge({ value }: { value: any }) {
  const label = formatPlanType(value);
  if (label === "-") return <span className="text-slate-400">-</span>;
  const key = String(value || "").trim().toLowerCase();
  const color = ({ free:"free", plus:"plus", k12:"k12", team:"team", pro:"pro" } as Record<string,string>)[key] || "default";
  return <span className={cn("sr-plan-badge", `sr-plan-${color}`)}>{label}</span>;
}

function MailboxEditModal({ t, mailbox, groups, onClose, onSaved, notify }: { t: typeof zh; mailbox: AnyObj; groups: AnyObj[]; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>(()=>({...mailbox, plan_type: mailbox.account_type || mailbox.plan_type || "free"}));
  const [clearChatGPTPassword,setClearChatGPTPassword]=useState(false);
  const [clearTOTPSecret,setClearTOTPSecret]=useState(false);
  const isApple = String(form.mailbox_type || "microsoft") === "apple";
  async function save() {
    const urlAPI = isApple && String(form.mailbox_channel || "") === "url_api";
    if (!String(form.email || "").includes("@") || (isApple ? (!urlAPI && !String(form.access_key || "").trim()) : (!String(form.client_id || "").trim() || !String(form.refresh_token || "").trim()))) {
      notify("fail", t.validationFailed);
      return;
    }
    try {
      await apiFetch(`/sunny/mailboxes/${mailbox.id}`, { method:"PUT", body: JSON.stringify({
        email: form.email, mailbox_type: isApple ? "apple" : "microsoft", mailbox_channel: isApple ? String(form.mailbox_channel || "xbovo") : "outlook",
        access_key: isApple ? form.access_key : "", chatgpt_password: urlAPI && !clearChatGPTPassword && form.chatgpt_password ? form.chatgpt_password : undefined, clear_chatgpt_password: urlAPI && clearChatGPTPassword, totp_secret: urlAPI && !clearTOTPSecret && form.totp_secret ? form.totp_secret : undefined, clear_totp_secret: urlAPI && clearTOTPSecret, password: form.password, client_id: form.client_id, refresh_token: form.refresh_token,
        access_token: form.access_token, group_id: Number(form.group_id), status: form.status, plan_type: form.plan_type || form.account_type, trial_eligibility: form.trial_eligibility || "unknown", enabled: !!form.enabled,
      })});
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.edit} {t.mailbox}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.mailboxName}</Label><Input value={form.email||""} onChange={(e)=>setForm({...form,email:e.target.value})}/></div>
        <div><Label>{t.mailboxType}</Label><Input disabled value={isApple ? t.appleMailbox : t.microsoftMailbox}/></div>
        {isApple ? <>
          <div><Label>{t.channelType}</Label><Input disabled value={String(form.mailbox_channel || "xbovo") === "url_api" ? t.urlAPIChannel : t.xbovoChannel}/></div>
          <div><Label>{String(form.mailbox_channel || "xbovo") === "url_api" ? t.icloudQueryURL : t.icloudAccessKey}</Label><Input type={String(form.mailbox_channel || "xbovo") === "url_api" ? "url" : "password"} autoComplete="new-password" value={form.access_key||""} onChange={(e)=>setForm({...form,access_key:e.target.value})}/></div>
          {String(form.mailbox_channel || "xbovo") === "url_api" ? <>
            <div><Label>{t.chatgptPassword}</Label><Input type="password" autoComplete="new-password" disabled={clearChatGPTPassword} value={form.chatgpt_password||""} placeholder={form.has_chatgpt_password?t.keepCredential:""} onChange={(e)=>setForm({...form,chatgpt_password:e.target.value})}/>{form.has_chatgpt_password && <label className="mt-2 flex items-center gap-2 text-xs text-slate-500"><input type="checkbox" checked={clearChatGPTPassword} onChange={(e)=>setClearChatGPTPassword(e.target.checked)}/>{t.clearCredential}</label>}</div>
            <div><Label>{t.totpSecret}</Label><Input type="password" autoComplete="new-password" disabled={clearTOTPSecret} value={form.totp_secret||""} placeholder={form.has_totp_secret?t.keepCredential:""} onChange={(e)=>setForm({...form,totp_secret:e.target.value})}/>{form.has_totp_secret && <label className="mt-2 flex items-center gap-2 text-xs text-slate-500"><input type="checkbox" checked={clearTOTPSecret} onChange={(e)=>setClearTOTPSecret(e.target.checked)}/>{t.clearCredential}</label>}</div>
          </> : null}
        </> : <>
          <div><Label>{t.password}</Label><Input type="password" autoComplete="new-password" value={form.password||""} onChange={(e)=>setForm({...form,password:e.target.value})}/></div>
          <div><Label>{t.clientId}</Label><Input value={form.client_id||""} onChange={(e)=>setForm({...form,client_id:e.target.value})}/></div>
          <div><Label>{t.refreshToken}</Label><Input value={form.refresh_token||""} onChange={(e)=>setForm({...form,refresh_token:e.target.value})}/></div>
        </>}
        <div><Label>{t.openaiAccessToken}</Label><Input value={form.access_token||""} onChange={(e)=>setForm({...form,access_token:e.target.value})}/></div>
        <div><Label>{t.mailboxGroup}</Label><SelectBox searchable searchPlaceholder={t.groupSearch} noResultsLabel={t.groupNoResults} value={form.group_id||0} onChange={(v)=>setForm({...form,group_id:Number(v)})} options={mailboxGroupOptions(t,groups)} /></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||MAILBOX_STATUSES[0]} onChange={(v)=>setForm({...form,status:String(v)})} options={MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))} /></div>
        <div><Label>{t.planType}</Label><SelectBox value={form.plan_type === "-" ? "free" : (form.plan_type || form.account_type || "free")} onChange={(v)=>setForm({...form,plan_type:String(v),account_type:String(v)})} options={PLAN_TYPE_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))} /></div>
        <div><Label>{t.trialEligibility}</Label><SelectBox value={form.trial_eligibility || "unknown"} onChange={(v)=>setForm({...form,trial_eligibility:String(v)})} options={TRIAL_ELIGIBILITY_OPTIONS.map((value)=>({value,label:trialEligibilityLabel(t,value)}))} /></div>
        <div className="flex items-end"><button className={cn("sr-toggle", form.enabled && "on")} onClick={()=>setForm({...form,enabled:!form.enabled})}>{form.enabled ? "ON" : "OFF"}</button></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}>{t.save}</Button></div>
  </div></div>;
}

function MailboxBatchEditModal({ t, selected, groups, onClose, onSaved, notify }: { t: typeof zh; selected: number[]; groups: AnyObj[]; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({ group_id: groups[0]?.id || 0, status: MAILBOX_STATUSES[0], plan_type: "free", enabled: true });
  async function save() {
    if (!selected.length) {
      notify("fail", t.chooseMailbox);
      return;
    }
    try {
      const body = { group_id:Number(form.group_id), status:String(form.status), plan_type:String(form.plan_type), enabled:!!form.enabled };
      await Promise.all(selected.map((id)=>apiFetch(`/sunny/mailboxes/${id}`, { method:"PUT", body:JSON.stringify(body) })));
      onSaved();
    } catch(e:any) {
      notify("fail", e.message || String(e));
    }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.batchEditMailboxTitle}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3 text-sm font-bold text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-200">{t.selected}: {selected.length}</div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.mailboxGroup}</Label><SelectBox searchable searchPlaceholder={t.groupSearch} noResultsLabel={t.groupNoResults} value={form.group_id||0} onChange={(v)=>setForm({...form,group_id:Number(v)})} options={mailboxGroupOptions(t,groups)} /></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||MAILBOX_STATUSES[0]} onChange={(v)=>setForm({...form,status:String(v)})} options={MAILBOX_STATUSES.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))} /></div>
        <div><Label>{t.planType}</Label><SelectBox value={form.plan_type||"free"} onChange={(v)=>setForm({...form,plan_type:String(v)})} options={PLAN_TYPE_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))} /></div>
        <div><Label>{t.enabled}</Label><button className={cn("sr-toggle", form.enabled && "on")} onClick={()=>setForm({...form,enabled:!form.enabled})}>{form.enabled ? "ON" : "OFF"}</button></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}><Save className="mr-2 h-4 w-4"/>{t.applyToSelected}</Button></div>
  </div></div>;
}

function URLAPIMailBrowser({ t, mailboxId, initialHTML }: { t: typeof zh; mailboxId:number; initialHTML:string }) {
  const frameRef=useRef<HTMLIFrameElement|null>(null);
  const [history,setHistory]=useState<string[]>([""]);
  const [historyIndex,setHistoryIndex]=useState(0);
  const [currentURL,setCurrentURL]=useState("");
  const [pageHTML,setPageHTML]=useState(initialHTML);
  const [documentVersion,setDocumentVersion]=useState(0);
  const [loading,setLoading]=useState(true);
  const [reloadKey,setReloadKey]=useState(0);
  const [notice,setNotice]=useState("");
  const target=history[historyIndex] || "";

  useEffect(()=>{
    setHistory([""]);
    setHistoryIndex(0);
    setCurrentURL("");
    setPageHTML(initialHTML);
    setDocumentVersion((value)=>value+1);
    setNotice("");
    setLoading(true);
  },[mailboxId,initialHTML]);

  useEffect(()=>{
    if(!target) {
      setPageHTML(initialHTML);
      return;
    }
    const controller=new AbortController();
    setCurrentURL(target);
    setLoading(true);
    void fetch(`${API_BASE}/sunny/mailboxes/${mailboxId}/url-api-preview?target=${encodeURIComponent(target)}`,{
      credentials:"include",
      headers:{Accept:"text/html"},
      signal:controller.signal,
    }).then(async(response)=>{
      if(response.status===401) {
        window.location.reload();
        return "";
      }
      return response.text();
    }).then((html)=>{
      if(html) {
        setPageHTML(html);
        setDocumentVersion((value)=>value+1);
      }
    }).catch((error)=>{
      if(error?.name!=="AbortError") setNotice(String(error?.message||error));
    }).finally(()=>{
      if(!controller.signal.aborted) setLoading(false);
    });
    return ()=>controller.abort();
  },[initialHTML,mailboxId,reloadKey,target]);

  useEffect(()=>{
    function receive(event:MessageEvent) {
      if(event.source!==frameRef.current?.contentWindow) return;
      const data=event.data as AnyObj;
      if(data?.source!=="sunny-url-api-preview" || Number(data.mailboxId)!==mailboxId) return;
      if(data.type==="ready") {
        setCurrentURL(String(data.url||""));
        setLoading(false);
        setNotice("");
      }
      if(data.type==="unsupported") setNotice(t.browserGetOnly);
      if(data.type==="navigate" && /^https?:\/\//i.test(String(data.url||""))) {
        const next=String(data.url);
        setPageHTML("<!doctype html><html><body></body></html>");
        setDocumentVersion((value)=>value+1);
        setHistory((previous)=>{
          const entries=[...previous.slice(0,historyIndex+1),next];
          setHistoryIndex(entries.length-1);
          return entries;
        });
        setLoading(true);
        setNotice("");
      }
    }
    window.addEventListener("message",receive);
    return ()=>window.removeEventListener("message",receive);
  },[historyIndex,mailboxId,t.browserGetOnly]);

  let address=t.urlAPIBrowser;
  if(currentURL) {
    try { address=new URL(currentURL).host; } catch { address=t.urlAPIBrowser; }
  }
  function move(index:number) {
    if(index<0 || index>=history.length) return;
    setPageHTML(index===0 ? initialHTML : "<!doctype html><html><body></body></html>");
    setDocumentVersion((value)=>value+1);
    setHistoryIndex(index);
    setLoading(true);
    setNotice("");
  }
  return <div className="sr-url-api-browser">
    <div className="sr-url-api-browser-toolbar">
      <div className="sr-url-api-browser-nav">
        <button type="button" title={t.browserBack} aria-label={t.browserBack} disabled={historyIndex===0} onClick={()=>move(historyIndex-1)}><ArrowLeft/></button>
        <button type="button" title={t.browserForward} aria-label={t.browserForward} disabled={historyIndex>=history.length-1} onClick={()=>move(historyIndex+1)}><ArrowRight/></button>
        <button type="button" title={t.browserReload} aria-label={t.browserReload} onClick={()=>{setLoading(true);setReloadKey((value)=>value+1)}}><RotateCw/></button>
      </div>
      <div className="sr-url-api-browser-address"><Globe2/><span>{address}</span>{loading&&<Loader2 className="animate-spin"/>}</div>
    </div>
    {notice&&<div className="sr-url-api-browser-notice">{notice}</div>}
    <iframe
      key={`${mailboxId}:${historyIndex}:${reloadKey}:${documentVersion}:${target}`}
      ref={frameRef}
      title={t.urlAPIBrowser}
      sandbox="allow-scripts allow-forms"
      srcDoc={pageHTML}
      onLoad={()=>setLoading(false)}
    />
  </div>;
}

function MailboxMailModal({ t, mailbox, onClose, notify }: { t: typeof zh; mailbox: AnyObj; onClose:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [items,setItems]=useState<AnyObj[]>([]);
  const [selected,setSelected]=useState(0);
  const [loading,setLoading]=useState(false);
  const [limit,setLimit]=useState(5);
  async function load() {
    setLoading(true);
    try {
      const r = await apiFetch(`/sunny/mailboxes/${mailbox.id}/latest-mail`, { method:"POST", body: JSON.stringify({ limit }) });
      const list = Array.isArray(r.items) ? r.items : (r.empty ? [] : [r]);
      setItems(list);
      setSelected(0);
      notify("ok", t.done);
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setLoading(false); }
  }
  useEffect(()=>{void load()},[]);
  const mail = items[selected] || {};
  const useURLAPIBrowser=String(mailbox.mailbox_type||"").toLowerCase()==="apple" && String(mailbox.mailbox_channel||"").toLowerCase()==="url_api";
  return createPortal(<div className="sr-modal-mask"><div className="sr-modal sr-mail-modal">
    <div className="sr-mail-head">
      <div className="sr-current-mail">{t.currentMailbox}: <b>{mailbox.email}</b></div>
      <div className="sr-mail-actions">
        <span className="sr-mail-count-label">{t.mailFetchCount}</span>
        <SelectBox className="sr-mail-count-select" value={limit} onChange={(v)=>setLimit(Number(v))} options={[5,10,20,50].map((n)=>({value:n,label:`${n}${t.mailFetchCountSuffix}`}))} />
        <Button className="rounded-xl bg-black px-5 !text-white hover:bg-slate-800" onClick={load} disabled={loading}>{loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : <Inbox className="mr-2 h-4 w-4"/>}{t.getMail}</Button>
        <button onClick={onClose}><X className="h-5 w-5"/></button>
      </div>
    </div>
    <div className="sr-mail-layout">
      <aside className="sr-mail-list">
        <div className="sr-mail-title">{t.mailList} <span>({items.length})</span></div>
        {items.length ? items.map((m,i)=><button key={`${m.id || i}`} className={cn("sr-mail-item", i===selected && "active")} onClick={()=>setSelected(i)}>
          <div className="sr-mail-from">{m.from || "-"}</div>
          <div className="sr-mail-subject">{m.subject || "(no subject)"}</div>
          <p>{m.body_preview || m.body || ""}</p>
          <div className="sr-mail-tags"><span>{m.folder || t.inbox}</span>{m.otp ? <span>OTP {m.otp}</span> : null}</div>
        </button>) : <div className="sr-empty !min-h-[360px]"><Inbox className="h-8 w-8 text-slate-400"/><p>{t.emptyMail}</p></div>}
      </aside>
      <section className="sr-mail-detail">
        {items.length ? <>
          <h2>{mail.subject || "(no subject)"}</h2>
          <div className="sr-mail-meta"><span>{t.sender}</span><b>{mail.from || "-"}</b><span>{t.receiver}</span><b>{mail.to || mailbox.email}</b><span>{t.time}</span><b>{mail.date || "-"}</b></div>
          <div className="sr-mail-content">
            {useURLAPIBrowser ? <URLAPIMailBrowser key={String(mail.id||selected)} t={t} mailboxId={Number(mailbox.id)} initialHTML={String(mail.preview_html||mail.raw_html||"")}/> : mail.raw_html && /<html|<body|<div|<p|<table/i.test(String(mail.raw_html)) ? <iframe title="mail-content" sandbox="" srcDoc={String(mail.raw_html)} /> : <pre>{mail.body || mail.body_preview || ""}</pre>}
          </div>
        </> : <div className="sr-empty"><Inbox className="h-10 w-10 text-slate-400"/><p>{t.emptyMail}</p></div>}
      </section>
    </div>
  </div></div>, document.body);
}

function MailboxImportModal({ t, groups, onGroupsChanged, onClose, onImported, notify }: { t: typeof zh; groups: AnyObj[]; onGroupsChanged:(groups:AnyObj[])=>void; onClose:()=>void; onImported:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [mode,setMode]=useState<"file"|"manual">("manual");
  const [mailboxType,setMailboxType]=useState<"microsoft"|"apple">("microsoft");
  const [mailboxChannel,setMailboxChannel]=useState<"xbovo"|"url_api">("xbovo");
  const [lines,setLines]=useState("");
  const [groupId,setGroupId]=useState<number>(()=>Number(sortMailboxGroups(groups)[0]?.id || 0));
  const [localGroups,setLocalGroups]=useState<AnyObj[]>(()=>sortMailboxGroups(groups));
  const [adding,setAdding]=useState(false);
  const [renaming,setRenaming]=useState(false);
  const [newGroup,setNewGroup]=useState("");
  const [drag,setDrag]=useState(false);
  const errors = mailboxLineErrors(lines, mailboxType, mailboxChannel);
  const validCount = lines.split(/\r?\n/).filter((x)=>x.trim()).length - errors.length;
  const selectedGroup = localGroups.find((g)=>Number(g.id)===groupId);
  const selectedGroupIsDefault = String(selectedGroup?.name || "") === t.defaultGroup || String(selectedGroup?.name || "") === "默认分组";
  const groupOptions = sortMailboxGroups(localGroups).map((g)=>({
    value:g.id,
    label:<span className="sr-mailbox-group-option"><span>{String(g.name || "")==="默认分组" ? t.defaultGroup : (g.name || t.defaultGroup)}</span><b title={t.mailboxCount}>{Number(g.mailbox_count || 0)}</b></span>,
    searchText:`${g.name || ""} ${String(g.name || "")==="默认分组" ? t.defaultGroup : ""}`,
  }));
  function syncGroups(next: AnyObj[]) { const sorted=sortMailboxGroups(next); setLocalGroups(sorted); onGroupsChanged(sorted); }
  function groupErrorMessage(error:any) {
    const message = error?.message || String(error);
    if (message.includes("mailbox_group_not_empty")) return t.groupNotEmpty;
    if (message.includes("default_mailbox_group")) return t.defaultGroupCannotDelete;
    if (message.includes("mailbox_group_name_conflict") || message.toLowerCase().includes("unique")) return t.groupNameConflict;
    return message;
  }
  async function pick(file?: File) { if (!file) return; setLines(await file.text()); setMode("file"); }
  async function createGroup() {
    const name = newGroup.trim();
    if (!name) return;
    try {
      const g = await apiFetch("/sunny/mailbox-groups",{method:"POST",body:JSON.stringify({name})});
      const next = sortMailboxGroups([...localGroups, g]);
      syncGroups(next); setGroupId(g.id); setAdding(false); setRenaming(false); setNewGroup("");
      notify("ok", t.groupCreated);
    } catch(e:any) { notify("fail", groupErrorMessage(e)); }
  }
  async function renameGroup() {
    const name = newGroup.trim();
    if (!name || !selectedGroup) return;
    try {
      const updated = await apiFetch(`/sunny/mailbox-groups/${selectedGroup.id}`,{method:"PUT",body:JSON.stringify({name})});
      syncGroups(localGroups.map((g)=>Number(g.id)===Number(updated.id)?{...g,...updated}:g));
      setRenaming(false); setAdding(false); setNewGroup("");
      notify("ok", t.groupRenamed);
    } catch(e:any) { notify("fail", groupErrorMessage(e)); }
  }
  async function deleteGroup() {
    if (!selectedGroup) return;
    if (Number(selectedGroup.mailbox_count || 0) > 0) { notify("fail", t.groupNotEmpty); return; }
    try {
      await apiFetch(`/sunny/mailbox-groups/${selectedGroup.id}`,{method:"DELETE"});
      const next = localGroups.filter((g)=>Number(g.id)!==Number(selectedGroup.id));
      syncGroups(next); setGroupId(Number(next[0]?.id || 0)); setRenaming(false); setAdding(false); setNewGroup("");
      notify("ok", t.groupDeleted);
    } catch(e:any) { notify("fail", groupErrorMessage(e)); }
  }
  async function submit() {
    const trimmed = lines.trim();
    if (!trimmed) { notify("fail", t.fillOrChooseMailboxFile); return; }
    if (errors.length) { notify("fail", `${t.validationFailed}: ${errors[0]}`); return; }
    try {
      await apiFetch("/sunny/mailboxes/import",{method:"POST",body:JSON.stringify({lines:trimmed,group_id:groupId,import_mode:mode,mailbox_type:mailboxType,mailbox_channel:mailboxType==="apple"?mailboxChannel:"outlook"})});
      onImported();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.importMailboxes}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-5">
      <div className="grid gap-3 md:grid-cols-[minmax(220px,1fr)_180px_44px_44px]">
        <div><Label>{t.importToGroup}</Label><SelectBox searchable searchPlaceholder={t.groupSearch} noResultsLabel={t.groupNoResults} className="sr-mailbox-group-select" value={groupId} onChange={(v)=>{setGroupId(Number(v));setRenaming(false);setAdding(false);setNewGroup("")}} options={groupOptions} /></div>
        <div className="flex items-end">{adding || renaming ? <Input autoFocus placeholder={t.enterGroup} value={newGroup} onChange={(e)=>setNewGroup(e.target.value)} onKeyDown={(e)=>{if(e.key==="Enter") void (adding?createGroup():renameGroup()); if(e.key==="Escape"){setAdding(false);setRenaming(false);setNewGroup("")}}}/> : <Button variant="outline" className="h-11 w-full rounded-xl" onClick={()=>{setAdding(true);setRenaming(false);setNewGroup("")}}><Plus className="mr-2 h-4 w-4"/>{t.addGroup}</Button>}</div>
        <div className="flex items-end"><button type="button" className="sr-group-action" title={t.editGroup} aria-label={t.editGroup} disabled={!selectedGroup || adding || renaming} onClick={()=>{setRenaming(true);setAdding(false);setNewGroup(String(selectedGroup?.name||""))}}><Pencil className="h-4 w-4"/></button></div>
        <div className="flex items-end"><ConfirmBubble message={t.confirmDeleteGroup} detail={selectedGroup ? `${selectedGroup.name} · ${Number(selectedGroup.mailbox_count||0)}` : undefined} onConfirm={deleteGroup} disabled={!selectedGroup || selectedGroupIsDefault || adding || renaming}><button type="button" className="sr-group-action danger" title={selectedGroupIsDefault?t.defaultGroupCannotDelete:t.deleteGroup} aria-label={t.deleteGroup} disabled={!selectedGroup || selectedGroupIsDefault || adding || renaming}><Trash2 className="h-4 w-4"/></button></ConfirmBubble></div>
      </div>
      <div>
        <Label>{t.mailboxType}</Label>
        <div className="sr-import-tabs mt-2"><button className={cn(mailboxType==="microsoft"&&"active")} onClick={()=>{setMailboxType("microsoft");setLines("")}}>{t.microsoftMailbox}</button><button className={cn(mailboxType==="apple"&&"active")} onClick={()=>{setMailboxType("apple");setLines("")}}>{t.appleMailbox}</button></div>
      </div>
      {mailboxType==="apple" && <div title={mailboxChannel==="url_api" ? t.urlAPIChannelTip : t.xbovoChannelTip}><Label>{t.channelType}</Label><SelectBox className="mt-2" value={mailboxChannel} onChange={(value)=>{setMailboxChannel(String(value)==="url_api"?"url_api":"xbovo");setLines("")}} options={[{value:"xbovo",label:t.xbovoChannel},{value:"url_api",label:t.urlAPIChannel}]} /></div>}
      <div className="sr-import-tabs"><button className={cn(mode==="manual"&&"active")} onClick={()=>setMode("manual")}>{t.manualImport}</button><button className={cn(mode==="file"&&"active")} onClick={()=>setMode("file")}>{t.fileImport}</button></div>
      {mode==="file" ? <label className={cn("sr-drop-zone", drag && "drag")} onDragOver={(e)=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={(e)=>{e.preventDefault();setDrag(false);void pick(e.dataTransfer.files?.[0])}}>
        <Download className="h-8 w-8"/><span>{t.dragFile}</span><small>{lines ? `${validCount} valid line(s), ${errors.length} error(s)` : "TXT / CSV"}</small><input type="file" className="hidden" onChange={(e)=>pick(e.target.files?.[0])}/>
      </label> : <Textarea className="min-h-56 rounded-2xl" value={lines} onChange={(e)=>setLines(e.target.value)} placeholder={mailboxType==="apple" ? (mailboxChannel==="url_api" ? "email----ChatGPT密码----https://收码URL----2FA密钥（后3段均可选）" : "icloud_email----key") : "email----password----client_id----refresh_token"} />}
      {lines ? <div className={cn("sr-validation", errors.length ? "bad" : "ok")}><b>{errors.length ? t.validationFailed : t.validationOk}</b><span>{validCount} valid / {errors.length} error</span>{errors.slice(0,4).map((e)=><div key={e}>{e}</div>)}</div> : null}
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" disabled={!lines.trim() || errors.length>0 || !groupId} onClick={submit}>{t.import}</Button></div>
  </div></div>;
}

const PHONE_STATUS_OPTIONS = ["enabled", "disabled"];

function phoneLineErrors(lines: string, formatLabel: string) {
  const errors: string[] = [];
  lines.split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    const parts = line.split("----");
    if (parts.length !== 2 || !parts[0].trim().startsWith("+") || !parts[1].trim().toLowerCase().startsWith("http")) {
      errors.push(`Line ${index + 1}: ${formatLabel}`);
    }
  });
  return errors;
}

function phoneStatusText(t: typeof zh, status: string) {
  return status === "disabled" ? t.phoneStatusDisabled : t.phoneStatusEnabled;
}

function providerOptionLabel(opt: AnyObj) {
  const value = String(opt.value ?? "");
  const label = String(opt.label ?? value);
  return label && label !== value ? `${value} · ${label}` : value;
}

function ProviderOptionSelect({ value, onChange, options, placeholder, searchPlaceholder, noResultsLabel, className }: { value: string; onChange: (v: string) => void; options: AnyObj[]; placeholder: string; searchPlaceholder: string; noResultsLabel: string; className?: string }) {
  const normalized = options.map((opt)=>{
    const optionValue = String(opt.value ?? "");
    const optionLabel = providerOptionLabel(opt);
    return { value: optionValue, label: optionLabel, searchText: `${optionValue} ${String(opt.label ?? "")} ${optionLabel}` };
  }).filter((x)=>x.value);
  const hasValue = normalized.some((x)=>x.value === String(value));
  const merged = hasValue || !value ? normalized : [{ value, label: value, searchText: value }, ...normalized];
  return <SelectBox searchable searchPlaceholder={searchPlaceholder} noResultsLabel={noResultsLabel} className={className} value={value || ""} onChange={(v)=>onChange(String(v))} options={merged.length ? merged : [{ value: value || "", label: value || placeholder, searchText: value || placeholder }]} />;
}

function PhoneConfig({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items,setItems]=useCachedState<AnyObj[]>("phone.items",[]);
  const [total,setTotal]=useCachedState("phone.total",0);
  const [query,setQuery]=useCachedState("phone.query","");
  const debouncedQuery = useDebouncedValue(query);
  const [statusFilter,setStatusFilter]=useCachedState("phone.status","");
  const [countFilter,setCountFilter]=useCachedState("phone.count","all");
  const [timeSort,setTimeSort]=useCachedState<SortOrder>("phone.timeSort","desc");
  const [selected,setSelected]=useCachedState<number[]>("phone.selected",[]);
  const [selectingAll,setSelectingAll]=useState(false);
  const [page,setPage]=useCachedState("phone.page",1);
  const [pageSize,setPageSize]=useCachedState("phone.pageSize",10);
  const [phoneCfg,setPhoneCfg]=useCachedState<AnyObj>("phone.config",{pool_enabled:true, smsbower_enabled:false, smsbower_base_url:"https://smsbower.page/stubs/handler_api.php", smsbower_default_country:"187", smsbower_default_service:"dr", smsbower_max_price:-1, smspool_enabled:false, smspool_base_url:"https://api.smspool.net", smspool_default_country:"1", smspool_default_service:"671", smspool_max_price:-1, firefox_enabled:false, firefox_base_url:"http://www.firefox.fun/yhapi.ashx", firefox_api_name:"", firefox_password:"", firefox_default_country:"usa", firefox_default_service:"1096", firefox_max_price:0});
  const [savedPhoneCfg,setSavedPhoneCfg]=useState<AnyObj|null>(null);
  const [smsCheck,setSmsCheck]=useState("");
  const [lubanCheck,setLubanCheck]=useState("");
  const [smsPoolCheck,setSmsPoolCheck]=useState("");
  const [firefoxCheck,setFireFoxCheck]=useState("");
  const [smsOptions,setSmsOptions]=useCachedState<AnyObj>("phone.providerOptions",{});
  const [editing,setEditing]=useState<AnyObj|null>(null);
  const [importOpen,setImportOpen]=useState(false);
  const { loading: listLoading, track: trackListLoad } = useLoadingTracker();
  const load=()=>trackListLoad(async()=>{
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim());
    if (statusFilter) params.set("status", statusFilter);
    if (countFilter !== "all") params.set("count", countFilter);
    params.set("sort_by", "last_used_at");
    params.set("sort_order", timeSort);
    const data = await apiFetch(`/sunny/phones?${params.toString()}`);
    setItems(data.items || []);
    setTotal(data.total || 0);
  });
  const selectAllFiltered=async()=>{
    setSelectingAll(true);
    try {
      const params=new URLSearchParams({sort_by:"last_used_at",sort_order:timeSort});
      if(debouncedQuery.trim()) params.set("q",debouncedQuery.trim());
      if(statusFilter) params.set("status",statusFilter);
      if(countFilter!=="all") params.set("count",countFilter);
      const result=await apiFetch(`/sunny/phones?${allSelectionParams(params).toString()}`);
      const ids=selectionIDs(result);
      setSelected(ids);
      notify("ok",template(t.selectAllDone,{count:ids.length}));
    } catch(e:any) { notify("fail",e.message||String(e)); }
    finally { setSelectingAll(false); }
  };
  useEffect(()=>{void load()},[page, debouncedQuery, statusFilter, countFilter, timeSort, pageSize]);
  useEffect(()=>{apiFetch("/sunny/phones/config").then((cfg)=>{ const next = cfg || {pool_enabled:true}; setPhoneCfg(next); setSavedPhoneCfg(next); }).catch(()=>{})},[]);
  useEffect(()=>{setPage(1)},[query, statusFilter, countFilter, timeSort, pageSize]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  async function loadProviderOptions(provider: "smsbower"|"smspool"|"firefox", kind: "countries"|"services", refresh=false, country="") {
    const key = `${provider}_${kind}_${country || "all"}`;
    try {
      const params = new URLSearchParams({ provider, kind });
      if (country) params.set("country", country);
      const res = refresh
        ? await apiFetch("/sunny/phones/provider-options", { method:"POST", body: JSON.stringify({ provider, kind, country, refresh:true }) })
        : await apiFetch(`/sunny/phones/provider-options?${params.toString()}`);
      const items = Array.isArray(res.items) ? res.items : [];
      setSmsOptions((old: AnyObj)=>({ ...old, [key]: items }));
      if (kind === "services") {
        const field = provider === "smspool" ? "smspool_default_service" : provider === "firefox" ? "firefox_default_service" : "smsbower_default_service";
        const current = String(phoneCfg[field] || "").trim();
        const exact = items.some((item: AnyObj)=>String(item.value ?? "") === current);
        if (current && !exact) {
          const normalized = current.toLocaleLowerCase();
          const match = items.find((item: AnyObj)=>String(item.label ?? "").toLocaleLowerCase() === normalized)
            || items.find((item: AnyObj)=>String(item.label ?? "").toLocaleLowerCase().includes(normalized));
          if (match?.value != null) setPhoneCfg((old: AnyObj)=>({ ...old, [field]: String(match.value) }));
        }
      }
      if (refresh) notify("ok", t.refreshDone);
    } catch(e:any) {
      if (refresh) notify("fail", e.message || String(e));
    }
  }
  const optionsFor = (provider: "smsbower"|"smspool"|"firefox", kind: "countries"|"services", country="") => smsOptions[`${provider}_${kind}_${country || "all"}`] || [];
  useEffect(()=>{ if (phoneCfg.smsbower_enabled === true) { void loadProviderOptions("smsbower","countries"); void loadProviderOptions("smsbower","services", false, String(phoneCfg.smsbower_default_country || "")); } },[phoneCfg.smsbower_enabled, phoneCfg.smsbower_default_country]);
  useEffect(()=>{ if (phoneCfg.smspool_enabled === true) { void loadProviderOptions("smspool","countries"); void loadProviderOptions("smspool","services", false, String(phoneCfg.smspool_default_country || "")); } },[phoneCfg.smspool_enabled, phoneCfg.smspool_default_country]);
  useEffect(()=>{ if (phoneCfg.firefox_enabled === true) { void loadProviderOptions("firefox","countries"); void loadProviderOptions("firefox","services", false, String(phoneCfg.firefox_default_country || "")); } },[phoneCfg.firefox_enabled, phoneCfg.firefox_default_country]);
  async function run(label:string, fn:()=>Promise<any>){
    try{await fn(); notify("ok",label); void load();}
    catch(e:any){notify("fail",e.message||String(e));}
  }
  async function copyPhoneValue(value: unknown) {
    const text = String(value || "").trim();
    if (!text) return;
    try {
      await copyTextToClipboard(text);
      notify("ok", t.copySuccess);
    } catch (e: any) {
      notify("fail", e.message || String(e));
    }
  }
  async function deletePhone(p: AnyObj) {
    await run(t.done,()=>apiFetch(`/sunny/phones/${p.id}`,{method:"DELETE"}));
  }
  async function batchDelete(){
    if (!selected.length) return;
    await run(t.done, async()=>{ await Promise.all(selected.map((id)=>apiFetch(`/sunny/phones/${id}`,{method:"DELETE"}))); setSelected([]); });
  }
  const LUBAN_CONFIG_KEYS = ["luban_base_url", "luban_api_key", "luban_service_id"];
  const SMSBOWER_CONFIG_KEYS = ["smsbower_base_url", "smsbower_api_key", "smsbower_default_country", "smsbower_default_service", "smsbower_max_price"];
  const SMSPOOL_CONFIG_KEYS = ["smspool_base_url", "smspool_api_key", "smspool_default_country", "smspool_default_service", "smspool_max_price"];
  const FIREFOX_CONFIG_KEYS = ["firefox_base_url", "firefox_api_name", "firefox_password", "firefox_default_country", "firefox_default_service", "firefox_max_price"];
  const pickConfig = (cfg: AnyObj | null | undefined, keys: string[]) => {
    const out: AnyObj = {};
    keys.forEach((key)=>{ out[key] = cfg?.[key] ?? ""; });
    return out;
  };
  const configChanged = (keys: string[]) => {
    if (!savedPhoneCfg) return false;
    return JSON.stringify(pickConfig(phoneCfg, keys)) !== JSON.stringify(pickConfig(savedPhoneCfg, keys));
  };
  const lubanDirty = configChanged(LUBAN_CONFIG_KEYS);
  const smsbowerDirty = configChanged(SMSBOWER_CONFIG_KEYS);
  const smspoolDirty = configChanged(SMSPOOL_CONFIG_KEYS);
  const firefoxDirty = configChanged(FIREFOX_CONFIG_KEYS);
  const mergeSavedProviderFields = (saved: AnyObj, keys: string[]) => {
    const patch = pickConfig(saved, keys);
    setSavedPhoneCfg(saved);
    setPhoneCfg((current: AnyObj)=>({
      ...current,
      ...patch,
      pool_enabled: saved.pool_enabled,
      luban_enabled: saved.luban_enabled,
      smsbower_enabled: saved.smsbower_enabled,
      smspool_enabled: saved.smspool_enabled,
      firefox_enabled: saved.firefox_enabled,
      usable_count: saved.usable_count ?? current.usable_count,
      total_count: saved.total_count ?? current.total_count,
    }));
  };
  async function savePhoneSwitch(key: "pool_enabled" | "luban_enabled" | "smsbower_enabled" | "smspool_enabled" | "firefox_enabled", next: boolean) {
    const before = phoneCfg;
    setPhoneCfg((current: AnyObj)=>({ ...current, [key]: next }));
    try {
      const persistedBase = savedPhoneCfg || before;
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify({ ...persistedBase, [key]: next }) });
      setSavedPhoneCfg(saved || { ...persistedBase, [key]: next });
      setPhoneCfg((current: AnyObj)=>({ ...current, [key]: next, usable_count: saved?.usable_count ?? current.usable_count, total_count: saved?.total_count ?? current.total_count }));
      notify("ok", t.done);
    } catch(e:any) {
      setPhoneCfg(before);
      notify("fail", e.message || String(e));
    }
  }
  async function togglePoolEnabled() {
    const next = !(phoneCfg.pool_enabled !== false);
    await savePhoneSwitch("pool_enabled", next);
  }
  async function toggleSMSBowerEnabled() {
    await savePhoneSwitch("smsbower_enabled", !(phoneCfg.smsbower_enabled === true));
  }
  async function toggleLubanEnabled() {
    await savePhoneSwitch("luban_enabled", !(phoneCfg.luban_enabled === true));
  }
  async function saveLubanConfig() {
    if (!lubanDirty) return;
    try {
      const body = { ...(savedPhoneCfg || phoneCfg), ...pickConfig(phoneCfg, LUBAN_CONFIG_KEYS), luban_enabled: phoneCfg.luban_enabled === true };
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify(body) });
      mergeSavedProviderFields(saved || body, LUBAN_CONFIG_KEYS);
      notify("ok", t.lubanSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function checkLuban() {
    setLubanCheck(t.checking);
    try {
      await apiFetch("/sunny/phones/luban/check", { method:"POST", body: JSON.stringify(phoneCfg) });
      setLubanCheck(t.lubanChecked);
      notify("ok", t.lubanChecked);
    } catch(e:any) {
      const msg=e.message||String(e); setLubanCheck(msg); notify("fail",msg);
    }
  }
  async function toggleSMSPoolEnabled() {
    await savePhoneSwitch("smspool_enabled", !(phoneCfg.smspool_enabled === true));
  }
  async function toggleFireFoxEnabled() {
    await savePhoneSwitch("firefox_enabled", !(phoneCfg.firefox_enabled === true));
  }
  async function saveSMSBowerConfig() {
    if (!smsbowerDirty) return;
    try {
      const body = { ...(savedPhoneCfg || phoneCfg), ...pickConfig(phoneCfg, SMSBOWER_CONFIG_KEYS), smsbower_enabled: phoneCfg.smsbower_enabled === true };
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify(body) });
      mergeSavedProviderFields(saved || body, SMSBOWER_CONFIG_KEYS);
      notify("ok", t.smsbowerSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function checkSMSBower() {
    setSmsCheck(t.checking);
    try {
      const res = await apiFetch("/sunny/phones/smsbower/check", { method:"POST", body: JSON.stringify(phoneCfg) });
      const text = template(t.smsbowerBalance, { balance: res.balance || res.raw || "-" });
      setSmsCheck(text);
      notify("ok", text);
    } catch(e:any) {
      const msg = e.message || String(e);
      setSmsCheck(msg);
      notify("fail", msg);
    }
  }
  async function saveSMSPoolConfig() {
    if (!smspoolDirty) return;
    try {
      const body = { ...(savedPhoneCfg || phoneCfg), ...pickConfig(phoneCfg, SMSPOOL_CONFIG_KEYS), smspool_enabled: phoneCfg.smspool_enabled === true };
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify(body) });
      mergeSavedProviderFields(saved || body, SMSPOOL_CONFIG_KEYS);
      notify("ok", t.smspoolSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function checkSMSPool() {
    setSmsPoolCheck(t.checking);
    try {
      const res = await apiFetch("/sunny/phones/smspool/check", { method:"POST", body: JSON.stringify(phoneCfg) });
      const text = template(t.smspoolBalance, { balance: res.balance || res.raw || "-" });
      setSmsPoolCheck(text);
      notify("ok", text);
    } catch(e:any) {
      const msg = e.message || String(e);
      setSmsPoolCheck(msg);
      notify("fail", msg);
    }
  }
  async function saveFireFoxConfig() {
    if (!firefoxDirty) return;
    if (Number(phoneCfg.firefox_max_price || 0) <= 0) {
      notify("fail", t.firefoxMaxPriceRequired);
      return;
    }
    try {
      const body = { ...(savedPhoneCfg || phoneCfg), ...pickConfig(phoneCfg, FIREFOX_CONFIG_KEYS), firefox_enabled: phoneCfg.firefox_enabled === true };
      const saved = await apiFetch("/sunny/phones/config", { method:"PUT", body: JSON.stringify(body) });
      mergeSavedProviderFields(saved || body, FIREFOX_CONFIG_KEYS);
      notify("ok", t.firefoxSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function checkFireFox() {
    if (Number(phoneCfg.firefox_max_price || 0) <= 0) {
      notify("fail", t.firefoxMaxPriceRequired);
      return;
    }
    setFireFoxCheck(t.checking);
    try {
      const res = await apiFetch("/sunny/phones/firefox/check", { method:"POST", body: JSON.stringify(phoneCfg) });
      const text = template(t.firefoxBalance, { balance: res.balance || res.raw || "-" });
      setFireFoxCheck(text);
      notify("ok", text);
    } catch(e:any) {
      const msg = e.message || String(e);
      setFireFoxCheck(msg);
      notify("fail", msg);
    }
  }
  const allChecked = items.length > 0 && items.every((p)=>selected.includes(p.id));
  const countOptions = [{value:"all",label:t.allCount}, ...[0,1,2,3].map((n)=>({value:String(n),label:`${n} ${t.usedCount}`}))];
  const poolEnabled = phoneCfg.pool_enabled !== false;
  const lubanEnabled = phoneCfg.luban_enabled === true;
  const smsbowerEnabled = phoneCfg.smsbower_enabled === true;
  const smspoolEnabled = phoneCfg.smspool_enabled === true;
  const firefoxEnabled = phoneCfg.firefox_enabled === true;
  return <div className="space-y-6">
    <Card className="rounded-[24px] p-5 sr-sms-provider-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.lubanProvider}</h2><Tip text={t.lubanDesc}/></div>
        <button type="button" aria-label={t.lubanSwitch} title={t.lubanSwitch} className={cn("sr-switch-only", lubanEnabled && "on")} onClick={toggleLubanEnabled}><span /></button>
      </div>
      {lubanEnabled && <div className="sr-sms-provider-form mt-4 space-y-3">
        <div className="sr-sms-provider-top-row">
          <div className="sr-sms-provider-api"><Label>{t.lubanApiKey}</Label><Input type="password" value={phoneCfg.luban_api_key||""} onChange={(e)=>setPhoneCfg({...phoneCfg,luban_api_key:e.target.value})}/></div>
          <div><Label>{t.lubanServiceId}</Label><Input value={phoneCfg.luban_service_id||""} onChange={(e)=>setPhoneCfg({...phoneCfg,luban_service_id:e.target.value})} placeholder="121949"/></div>
        </div>
        <div className="sr-sms-provider-bottom-row">
          <div><Label>{t.lubanBaseURL}</Label><Input value={phoneCfg.luban_base_url||"https://lubansms.com/v2/api/"} onChange={(e)=>setPhoneCfg({...phoneCfg,luban_base_url:e.target.value})}/></div>
          <div className="sr-sms-provider-actions">{lubanCheck?<span className="sr-inline-result">{lubanCheck}</span>:null}<Button variant="outline" className="rounded-xl" onClick={checkLuban}><RefreshCw className="mr-2 h-4 w-4"/>{t.lubanCheck}</Button><Button disabled={!lubanDirty} className="rounded-xl bg-emerald-600 px-5 text-white hover:bg-emerald-700 disabled:opacity-50" onClick={saveLubanConfig}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
        </div>
      </div>}
    </Card>
    <Card className="rounded-[24px] p-5 sr-sms-provider-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.smsbowerProvider}</h2><Tip text={t.smsbowerDesc}/></div>
        </div>
        <button type="button" aria-label={t.smsbowerSwitch} title={t.smsbowerSwitch} className={cn("sr-switch-only", smsbowerEnabled && "on")} onClick={toggleSMSBowerEnabled}>
          <span />
        </button>
      </div>
      {smsbowerEnabled && <div className="sr-sms-provider-form mt-4 space-y-3">
        <div className="sr-sms-provider-top-row">
          <div className="sr-sms-provider-api"><Label>{t.smsbowerApiKey}</Label><Input type="password" value={phoneCfg.smsbower_api_key||""} onChange={(e)=>setPhoneCfg({...phoneCfg,smsbower_api_key:e.target.value})} placeholder="xxxxxxxxxxxxxxxx"/></div>
          <div><Label>{t.smsbowerCountry}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smsbower_default_country||"187")} onChange={(v)=>setPhoneCfg({...phoneCfg,smsbower_default_country:v})} options={optionsFor("smsbower","countries")} placeholder="187" searchPlaceholder={t.providerOptionSearch} noResultsLabel={t.providerOptionNoResults}/></div>
          <div><Label>{t.smsbowerService}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smsbower_default_service||"dr")} onChange={(v)=>setPhoneCfg({...phoneCfg,smsbower_default_service:v})} options={optionsFor("smsbower","services",String(phoneCfg.smsbower_default_country||""))} placeholder="dr" searchPlaceholder={t.providerOptionSearch} noResultsLabel={t.providerOptionNoResults}/></div>
          <div className="sr-sms-provider-price"><Label>{t.smsbowerMaxPrice}</Label><Input type="number" value={phoneCfg.smsbower_max_price ?? -1} onChange={(e)=>setPhoneCfg({...phoneCfg,smsbower_max_price:Number(e.target.value)})} placeholder="-1"/></div>
        </div>
        <div className="sr-sms-provider-bottom-row">
        <div><Label>{t.smsbowerBaseURL}</Label><Input value={phoneCfg.smsbower_base_url||"https://smsbower.page/stubs/handler_api.php"} onChange={(e)=>setPhoneCfg({...phoneCfg,smsbower_base_url:e.target.value})}/></div>
        <div className="sr-sms-provider-actions">
          {smsCheck ? <span className="sr-inline-result">{smsCheck}</span> : null}
          <Button variant="outline" className="rounded-xl" onClick={checkSMSBower}><RefreshCw className="mr-2 h-4 w-4"/>{t.smsbowerCheck}</Button>
          <span title={!smsbowerDirty ? t.configUnchanged : ""}>
            <Button disabled={!smsbowerDirty} className="rounded-xl bg-emerald-600 px-5 text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={saveSMSBowerConfig}><Save className="mr-2 h-4 w-4"/>{t.save}</Button>
          </span>
        </div>
        </div>
      </div>}
    </Card>
    <Card className="rounded-[24px] p-5 sr-sms-provider-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.smspoolProvider}</h2><Tip text={t.smspoolDesc}/></div>
        </div>
        <button type="button" aria-label={t.smspoolSwitch} title={t.smspoolSwitch} className={cn("sr-switch-only", smspoolEnabled && "on")} onClick={toggleSMSPoolEnabled}>
          <span />
        </button>
      </div>
      {smspoolEnabled && <div className="sr-sms-provider-form mt-4 space-y-3">
        <div className="sr-sms-provider-top-row">
          <div className="sr-sms-provider-api"><Label>{t.smspoolApiKey}</Label><Input type="password" value={phoneCfg.smspool_api_key||""} onChange={(e)=>setPhoneCfg({...phoneCfg,smspool_api_key:e.target.value})} placeholder="xxxxxxxxxxxxxxxx"/></div>
          <div><Label>{t.smspoolCountry}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smspool_default_country||"1")} onChange={(v)=>setPhoneCfg({...phoneCfg,smspool_default_country:v})} options={optionsFor("smspool","countries")} placeholder="1" searchPlaceholder={t.providerOptionSearch} noResultsLabel={t.providerOptionNoResults}/></div>
          <div><Label>{t.smspoolService}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.smspool_default_service||"671")} onChange={(v)=>setPhoneCfg({...phoneCfg,smspool_default_service:v})} options={optionsFor("smspool","services",String(phoneCfg.smspool_default_country||""))} placeholder="OpenAI / ChatGPT" searchPlaceholder={t.providerOptionSearch} noResultsLabel={t.providerOptionNoResults}/></div>
          <div className="sr-sms-provider-price"><Label>{t.smspoolMaxPrice}</Label><Input type="number" value={phoneCfg.smspool_max_price ?? -1} onChange={(e)=>setPhoneCfg({...phoneCfg,smspool_max_price:Number(e.target.value)})} placeholder="-1"/></div>
        </div>
        <div className="sr-sms-provider-bottom-row">
        <div><Label>{t.smspoolBaseURL}</Label><Input value={phoneCfg.smspool_base_url||"https://api.smspool.net"} onChange={(e)=>setPhoneCfg({...phoneCfg,smspool_base_url:e.target.value})}/></div>
        <div className="sr-sms-provider-actions">
          {smsPoolCheck ? <span className="sr-inline-result">{smsPoolCheck}</span> : null}
          <Button variant="outline" className="rounded-xl" onClick={checkSMSPool}><RefreshCw className="mr-2 h-4 w-4"/>{t.smspoolCheck}</Button>
          <span title={!smspoolDirty ? t.configUnchanged : ""}>
            <Button disabled={!smspoolDirty} className="rounded-xl bg-emerald-600 px-5 text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={saveSMSPoolConfig}><Save className="mr-2 h-4 w-4"/>{t.save}</Button>
          </span>
        </div>
        </div>
      </div>}
    </Card>
    <Card className="rounded-[24px] p-5 sr-sms-provider-card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.firefoxProvider}</h2><Tip text={t.firefoxDesc}/></div>
        <button type="button" aria-label={t.firefoxSwitch} title={t.firefoxSwitch} className={cn("sr-switch-only", firefoxEnabled && "on")} onClick={toggleFireFoxEnabled}>
          <span />
        </button>
      </div>
      {firefoxEnabled && <div className="sr-sms-provider-form mt-4 space-y-3">
        <div className="sr-firefox-provider-top-row">
          <div><Label>{t.firefoxApiName}</Label><Input value={phoneCfg.firefox_api_name||""} onChange={(e)=>setPhoneCfg({...phoneCfg,firefox_api_name:e.target.value})} autoComplete="off"/></div>
          <div><Label>{t.firefoxPassword}</Label><Input type="password" value={phoneCfg.firefox_password||""} onChange={(e)=>setPhoneCfg({...phoneCfg,firefox_password:e.target.value})} autoComplete="new-password"/></div>
          <div><Label>{t.firefoxCountry}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.firefox_default_country||"usa")} onChange={(v)=>setPhoneCfg({...phoneCfg,firefox_default_country:v})} options={optionsFor("firefox","countries")} placeholder="usa" searchPlaceholder={t.providerOptionSearch} noResultsLabel={t.providerOptionNoResults}/></div>
          <div><Label>{t.firefoxService}</Label><ProviderOptionSelect className="sr-provider-option-select" value={String(phoneCfg.firefox_default_service||"1096")} onChange={(v)=>setPhoneCfg({...phoneCfg,firefox_default_service:v})} options={optionsFor("firefox","services",String(phoneCfg.firefox_default_country||""))} placeholder="1096 · OpenAI / ChatGPT" searchPlaceholder={t.providerOptionSearch} noResultsLabel={t.providerOptionNoResults}/></div>
          <div className="sr-sms-provider-price"><Label>{t.firefoxMaxPrice}</Label><Input type="number" min="0.01" step="0.01" value={phoneCfg.firefox_max_price ?? 0} onChange={(e)=>setPhoneCfg({...phoneCfg,firefox_max_price:Number(e.target.value)})} placeholder="0.65"/></div>
        </div>
        <div className="sr-sms-provider-bottom-row">
          <div><Label>{t.firefoxBaseURL}</Label><Input value={phoneCfg.firefox_base_url||"http://www.firefox.fun/yhapi.ashx"} onChange={(e)=>setPhoneCfg({...phoneCfg,firefox_base_url:e.target.value})}/></div>
          <div className="sr-sms-provider-actions">
            {firefoxCheck ? <span className="sr-inline-result">{firefoxCheck}</span> : null}
            <Button variant="outline" className="rounded-xl" onClick={checkFireFox}><RefreshCw className="mr-2 h-4 w-4"/>{t.firefoxCheck}</Button>
            <span title={!firefoxDirty ? t.configUnchanged : ""}>
              <Button disabled={!firefoxDirty} className="rounded-xl bg-emerald-600 px-5 text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" onClick={saveFireFoxConfig}><Save className="mr-2 h-4 w-4"/>{t.save}</Button>
            </span>
          </div>
        </div>
      </div>}
    </Card>
    <Card className="sr-sms-provider-card sr-phone-pool-card rounded-[24px] p-5">
      <div className="flex flex-nowrap items-center justify-between gap-3">
        <div className="flex items-center gap-2"><h2 className="text-lg font-bold">{t.phonePool}</h2><Tip text={t.phonePoolSwitchTip}/></div>
        <button type="button" aria-label={t.phonePoolGlobalSwitch} className={cn("sr-switch-only", poolEnabled && "on")} onClick={togglePoolEnabled} title={t.phonePoolSwitchTip}>
          <span />
        </button>
      </div>
      {poolEnabled ? <div className="sr-phone-expanded mt-4 space-y-4">
        <div className="sr-toolbar sr-toolbar-compact sr-phone-inner-toolbar rounded-[18px] p-4">
          <div className="sr-phone-inner-toolbar-row flex flex-nowrap items-center justify-between gap-3">
          <div className="sr-phone-inner-toolbar-filters flex min-w-0 flex-1 flex-nowrap gap-3">
            <div className="sr-search-control relative"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.phoneSearch}/></div>
            <SelectBox className="sr-select-like" value={statusFilter} onChange={(v)=>setStatusFilter(String(v))} options={[{value:"",label:t.allStatus}, ...PHONE_STATUS_OPTIONS.map((s)=>({value:s,label:phoneStatusText(t,s)}))]} />
            <SelectBox className="sr-select-like" value={countFilter} onChange={(v)=>setCountFilter(String(v))} options={countOptions} />
          </div>
          <div className="sr-phone-inner-toolbar-actions flex flex-nowrap gap-2">
            <SelectionSummary t={t} count={selected.length} total={total} selectingAll={selectingAll} onSelectAll={selectAllFiltered} onClear={()=>setSelected([])}/>
            {selected.length > 0 && <ConfirmBubble message={t.phoneConfirmBatchDelete} detail={`${selected.length} ${t.selected}`} onConfirm={batchDelete}><Button variant="outline" className="rounded-xl border-red-200 text-red-500">{t.batchDelete} ({selected.length})</Button></ConfirmBubble>}
            <button className="sr-text-btn sr-action-refresh" onClick={()=>run(t.refreshDone, load)}><RefreshCw className="h-4 w-4"/>{t.refresh}</button>
            <Button className="rounded-xl bg-emerald-600 px-4 text-white hover:bg-emerald-700" onClick={()=>setImportOpen(true)}><Download className="mr-2 h-4 w-4"/>{t.importPhones}</Button>
          </div>
          </div>
        </div>
        <div className="sr-table-card overflow-hidden rounded-[18px] p-0" aria-busy={listLoading}>
      <ListLoadingOverlay loading={listLoading} label={t.loadingData}/>
      <ResizableDataTable tableKey="phones" columns={DATA_TABLE_COLUMNS.phones} headers={[<input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected,...items.map((p)=>p.id)])) : selected.filter((id)=>!items.some((p)=>p.id===id)))}/>,t.phoneNumber,t.status,t.usedCount,t.smsLink,<SortTimeHeader label={t.lastUsedAt} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/>,t.actions]}>
        <tbody>{items.length ? items.map((p)=><tr key={p.id}>
          <td><input type="checkbox" checked={selected.includes(p.id)} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected,p.id])) : selected.filter((id)=>id!==p.id))}/></td>
          <td><button type="button" className="sr-copyable-value font-semibold" title={`${t.copy} ${t.phoneNumber}`} onClick={()=>void copyPhoneValue(p.number)}>{p.number}</button>{p.last_error ? <div className="mt-1 max-w-md truncate text-xs text-red-400">{p.last_error}</div> : null}</td>
          <td><span className={cn("sr-status", p.display_status === "disabled" ? "sr-status-gray" : "sr-status-green")}>{phoneStatusText(t, p.display_status || "enabled")}</span></td>
          <td>{p.success_count || 0}/{p.max_success || 3}</td>
          <td>{p.sms_url ? <button type="button" className="sr-copyable-value mx-auto block max-w-[520px] truncate text-left text-sm text-[var(--text-secondary)]" title={`${t.copy} ${t.smsLink}`} onClick={()=>void copyPhoneValue(p.sms_url)}>{p.sms_url}</button> : "-"}</td>
          <td>{formatDateTime(p.last_used_at)}</td>
          <td><div className="flex flex-wrap justify-center gap-2"><button className="sr-link" onClick={()=>setEditing(p)}>{t.edit}</button><ConfirmBubble message={t.phoneConfirmDelete} detail={p.number || ""} onConfirm={()=>deletePhone(p)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td>
        </tr>) : <tr><td colSpan={7}><div className="sr-empty"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.noData}</div><p className="mt-2 text-sm text-slate-400">{t.phoneImportHelp}</p></div></td></tr>}</tbody>
      </ResizableDataTable>
      <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
        </div>
      </div> : null}
    </Card>
    {importOpen && <PhoneImportModal t={t} onClose={()=>setImportOpen(false)} onImported={()=>{setImportOpen(false); notify("ok", t.done); void load();}} notify={notify}/>}
    {editing && <PhoneEditModal t={t} phone={editing} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok",t.done); void load();}} notify={notify}/>}
  </div>;
}

function PhoneImportModal({ t, onClose, onImported, notify }: { t: typeof zh; onClose:()=>void; onImported:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [mode,setMode]=useState<"file"|"manual">("file");
  const [lines,setLines]=useState("");
  const [drag,setDrag]=useState(false);
  const errors = phoneLineErrors(lines, t.lineFormatPhone);
  const validCount = lines.split(/\r?\n/).filter((x)=>x.trim()).length - errors.length;
  async function pick(file?: File) { if (!file) return; setLines(await file.text()); setMode("file"); }
  async function submit() {
    const trimmed = lines.trim();
    if (!trimmed) { notify("fail", t.phoneImportInvalid); return; }
    if (errors.length) { notify("fail", `${t.phoneImportInvalid}: ${errors[0]}`); return; }
    try {
      const res = await apiFetch("/sunny/phones/import",{method:"POST",body:JSON.stringify({lines:trimmed})});
      if (res.failed > 0) throw new Error((res.errors || []).slice(0, 2).join("\n") || t.phoneImportInvalid);
      onImported();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.importPhones}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-5">
      <p className="!mb-0 text-sm text-[var(--text-muted)]">{t.phoneImportHelp}</p>
      <div className="sr-import-tabs"><button className={cn(mode==="file"&&"active")} onClick={()=>setMode("file")}>{t.fileImport}</button><button className={cn(mode==="manual"&&"active")} onClick={()=>setMode("manual")}>{t.manualImport}</button></div>
      {mode==="file" ? <label className={cn("sr-drop-zone", drag && "drag")} onDragOver={(e)=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={(e)=>{e.preventDefault();setDrag(false);void pick(e.dataTransfer.files?.[0])}}>
        <Download className="h-8 w-8"/><span>{t.dragFile}</span><small>{lines ? `${validCount} valid line(s), ${errors.length} error(s)` : "TXT / CSV"}</small><input type="file" className="hidden" onChange={(e)=>pick(e.target.files?.[0])}/>
      </label> : <Textarea className="min-h-56 rounded-2xl" value={lines} onChange={(e)=>setLines(e.target.value)} placeholder={t.phoneImportPlaceholder}/>} 
      <div className={cn("sr-validation", errors.length ? "bad" : lines.trim() ? "ok" : "")}>{errors.length ? <><b>{t.validationFailed}</b>{errors.slice(0,3).join("；")}{errors.length>3?` ... +${errors.length-3}`:""}</> : lines.trim() ? <><b>{t.validationOk}</b>{validCount}</> : t.phoneImportPlaceholder}</div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" disabled={!lines.trim() || errors.length>0} onClick={submit}><Download className="mr-2 h-4 w-4"/>{t.importPhones}</Button></div>
  </div></div>;
}
function PhoneEditModal({ t, phone, onClose, onSaved, notify }: { t: typeof zh; phone: AnyObj; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({...phone, display_status: phone.display_status || (phone.enabled === false ? "disabled" : "enabled")});
  async function save() {
    const number = String(form.number || "").trim();
    const smsURL = String(form.sms_url || "").trim();
    const successCount = Number(form.success_count || 0);
    if (!number.startsWith("+") || !smsURL.toLowerCase().startsWith("http") || Number.isNaN(successCount) || successCount < 0 || successCount > 3) {
      notify("fail", t.validationFailed);
      return;
    }
    try {
      const status = String(form.display_status || "enabled");
      await apiFetch(`/sunny/phones/${phone.id}`, { method:"PUT", body: JSON.stringify({
        number, sms_url: smsURL, status, enabled: status !== "disabled", success_count: successCount, max_success: 3,
      })});
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.phoneEdit}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.phoneNumber}</Label><Input value={form.number||""} onChange={(e)=>setForm({...form,number:e.target.value})}/></div>
        <div><Label>{t.status}</Label><SelectBox value={form.display_status||"enabled"} onChange={(v)=>setForm({...form,display_status:String(v)})} options={PHONE_STATUS_OPTIONS.map((s)=>({value:s,label:phoneStatusText(t,s)}))} /></div>
        <div><Label>{t.usedCount}</Label><Input type="number" min={0} max={3} value={form.success_count ?? 0} onChange={(e)=>setForm({...form,success_count:Number(e.target.value)})}/></div>
        <div className="md:col-span-2"><Label>{t.smsLink}</Label><Textarea className="min-h-[92px]" value={form.sms_url||""} onChange={(e)=>setForm({...form,sms_url:e.target.value})}/></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>;
}
function normalizeSub2APIGroups(resp: AnyObj): AnyObj[] {
  const pick = (v: any): any[] => Array.isArray(v) ? v : [];
  const candidates = [pick(resp), pick(resp.items), pick(resp.data), pick(resp.groups), pick(resp.result)];
  const data = candidates.find((x)=>x.length) || [];
  const nestedCandidates = resp.data && typeof resp.data === "object" ? [pick(resp.data.items), pick(resp.data.groups), pick(resp.data.list)] : [];
  const nested = nestedCandidates.find((x)=>x.length) || [];
  return (data.length ? data : nested).map((g: AnyObj) => ({
    id: String(g.id ?? g.group_id ?? g.value ?? g.key ?? ""),
    name: String(g.name ?? g.label ?? g.group_name ?? g.display_name ?? g.id ?? ""),
  })).filter((g: AnyObj) => g.id);
}

function normalizeSub2APIProxies(resp: AnyObj): AnyObj[] {
  const data = Array.isArray(resp?.proxies) ? resp.proxies : Array.isArray(resp?.data?.proxies) ? resp.data.proxies : [];
  return data.map((proxy: AnyObj)=>({ id:String(proxy.id??""), name:String(proxy.name||proxy.ip_address||proxy.host||proxy.id||"") })).filter((proxy:AnyObj)=>proxy.id);
}

function normalizeSub2APIConfig(cfg: AnyObj) {
  const groupIds = Array.isArray(cfg.group_ids) ? cfg.group_ids.map((x:any)=>String(x)).filter(Boolean) : String(cfg.group_ids||"").split(",").map((x)=>x.trim()).filter(Boolean);
  const rawLabels = cfg.group_labels && typeof cfg.group_labels === "object" ? cfg.group_labels : {};
  const groupLabels = Object.fromEntries(groupIds.map((id)=>[id, String(rawLabels[id] || "").trim()]).filter(([,name])=>name));
  return {
    enabled: cfg.enabled !== false,
    base_url: String(cfg.base_url || "").trim(),
    admin_token: String(cfg.admin_token || "").trim(),
    name_prefix: String(cfg.name_prefix || ""),
    group_ids: groupIds,
    group_labels: groupLabels,
    proxy_id: Number(cfg.proxy_id || 0),
    concurrency: Number(cfg.concurrency || 3),
    load_factor: Number(cfg.load_factor || 0),
    priority: Number(cfg.priority || 50),
    model_whitelist: Array.isArray(cfg.model_whitelist) ? cfg.model_whitelist.map(String).filter(Boolean) : String(cfg.model_whitelist||"").split(/[\n,]+/).map((x)=>x.trim()).filter(Boolean),
    codex_image_bridge: false,
  };
}

function sub2apiGroupLabel(group: AnyObj) {
  const id = String(group.id ?? "").trim();
  const name = String(group.name ?? "").trim();
  return name && name !== id ? `${id}·${name}` : id;
}

function Sub2APIConfig({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [cfg,setCfg]=useCachedState<AnyObj>("sub2api.cfg",{});
  const [savedCfg,setSavedCfg]=useCachedState<AnyObj>("sub2api.savedCfg",{});
  const [groups,setGroups]=useCachedState<AnyObj[]>("sub2api.groups",[]);
  const [proxies,setProxies]=useCachedState<AnyObj[]>("sub2api.proxies",[]);
  const [loading,setLoading]=useCachedState("sub2api.loading",false);
  const [fetching,setFetching]=useCachedState("sub2api.fetchingGroups",false);
  const [checkStatus,setCheckStatus]=useCachedState<AnyObj|null>("sub2api.checkStatus",null);
  const [groupOpen,setGroupOpen]=useState(false);
  const selectedGroupIds = Array.isArray(cfg.group_ids) ? cfg.group_ids.map((x:any)=>String(x)) : String(cfg.group_ids||"").split(",").map((x)=>x.trim()).filter(Boolean);
  const savedGroupLabels = cfg.group_labels && typeof cfg.group_labels === "object" ? cfg.group_labels : {};
  const labelForGroupId = (id: string) => {
    const group = groups.find((g)=>String(g.id) === String(id));
    if (group) return sub2apiGroupLabel(group);
    const saved = String(savedGroupLabels[id] || "").trim();
    return saved ? `${id}·${saved}` : id;
  };
  const buildGroupLabels = (ids: string[]) => Object.fromEntries(ids.map((id)=>{
    const group = groups.find((g)=>String(g.id) === String(id));
    const name = String(group?.name || savedGroupLabels[id] || "").trim();
    return [id, name && name !== id ? name : ""];
  }).filter(([,name])=>name));
  const sub2apiEnabled = cfg.enabled !== false;
  const dirty = JSON.stringify(normalizeSub2APIConfig(cfg)) !== JSON.stringify(normalizeSub2APIConfig(savedCfg));
  const setGroupIds = (ids: string[]) => setCfg({...cfg, group_ids: ids, group_labels: buildGroupLabels(ids)});
  const toggleGroup = (id: string) => setGroupIds(selectedGroupIds.includes(id) ? selectedGroupIds.filter((x)=>x!==id) : [...selectedGroupIds, id]);
  async function fetchGroups(silent=false, sourceCfg: AnyObj = cfg){
    if (!String(sourceCfg.base_url||"").trim() || !String(sourceCfg.admin_token||"").trim()) {
      if (!silent) notify("fail", t.fillURLToken);
      return;
    }
    setFetching(true);
    try {
      const resp = await apiFetch("/sunny/sub2api/options", { method:"POST", body:JSON.stringify({base_url:String(sourceCfg.base_url||""), admin_token:String(sourceCfg.admin_token||"")}) });
      const list = normalizeSub2APIGroups(resp);
      setGroups(list);
      setProxies(normalizeSub2APIProxies(resp));
      if (selectedGroupIds.length) {
        const nextLabels = Object.fromEntries(selectedGroupIds.map((id)=>{
          const group = list.find((g)=>String(g.id) === String(id));
          const name = String(group?.name || savedGroupLabels[id] || "").trim();
          return [id, name && name !== id ? name : ""];
        }).filter(([,name])=>name));
        setCfg({...cfg, group_labels: nextLabels});
      }
      if (!silent) notify("ok", template(t.fetchedGroups, { count: list.length }));
      if (list.length) setGroupOpen(true);
    } catch(e:any) {
      if (!silent) notify("fail", e.message || String(e));
    } finally {
      setFetching(false);
    }
  }
  async function checkConnection(){
    if (!String(cfg.base_url||"").trim() || !String(cfg.admin_token||"").trim()) {
      setCheckStatus({type:"fail", text:t.fillURLTokenShort});
      return;
    }
    setCheckStatus({type:"loading", text:t.checking});
    setFetching(true);
    try {
      const resp = await apiFetch("/sunny/sub2api/options", { method:"POST", body:JSON.stringify({base_url:String(cfg.base_url||""), admin_token:String(cfg.admin_token||"")}) });
      const list = normalizeSub2APIGroups(resp);
      setGroups(list);
      setProxies(normalizeSub2APIProxies(resp));
      if (selectedGroupIds.length) {
        const nextLabels = Object.fromEntries(selectedGroupIds.map((id)=>{
          const group = list.find((g)=>String(g.id) === String(id));
          const name = String(group?.name || savedGroupLabels[id] || "").trim();
          return [id, name && name !== id ? name : ""];
        }).filter(([,name])=>name));
        setCfg({...cfg, group_labels: nextLabels});
      }
      setCheckStatus({type:"ok", text:template(t.checkPassedGroups, { count: list.length })});
    } catch(e:any) {
      setCheckStatus({type:"fail", text:template(t.checkFailed, { error: e.message || String(e) })});
    } finally {
      setFetching(false);
    }
  }
  useEffect(()=>{
    let alive = true;
    apiFetch("/sunny/sub2api-config").then((data)=>{
      if (!alive) return;
      setCfg(data);
      setSavedCfg(data);
      const next = normalizeSub2APIConfig(data);
      const base = String(next.base_url || "").trim();
      const token = String(next.admin_token || "").trim();
      if (next.enabled !== false && next.group_ids.length === 0 && base && token.length >= 8) {
        window.setTimeout(()=>{ if (alive) void fetchGroups(true, next); }, 300);
      }
    }).catch((e)=>notify("fail", e.message||String(e)));
    return ()=>{ alive = false; };
  },[]);
  async function toggleSub2APIEnabled(){
    const nextEnabled = !sub2apiEnabled;
    const next = normalizeSub2APIConfig({...cfg, enabled: nextEnabled, group_labels: buildGroupLabels(selectedGroupIds)});
    setCfg(next);
    setLoading(true);
    try{
      await apiFetch("/sunny/sub2api-config",{method:"PUT",body:JSON.stringify(next)});
      setSavedCfg(next);
      notify("ok",t.done);
    }
    catch(e:any){notify("fail",e.message||String(e))}
    finally{setLoading(false)}
  }
  async function save(){
    if (!dirty) return;
    setLoading(true);
    try{
      const next = normalizeSub2APIConfig({...cfg, group_labels: buildGroupLabels(selectedGroupIds)});
      await apiFetch("/sunny/sub2api-config",{method:"PUT",body:JSON.stringify(next)});
      setCfg(next);
      setSavedCfg(next);
      notify("ok",t.done)
    }
    catch(e:any){notify("fail",e.message||String(e))}
    finally{setLoading(false)}
  }
  return <Card className="rounded-[24px] p-5 sr-sms-provider-card">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div className="flex items-center gap-2"><h2 className="text-lg font-bold">sub2api</h2><Tip text={t.sub2apiDesc}/></div>
      </div>
      <button type="button" aria-label="sub2api" title="sub2api" className={cn("sr-switch-only", sub2apiEnabled && "on")} onClick={toggleSub2APIEnabled} disabled={loading}>
        <span />
      </button>
    </div>
    {sub2apiEnabled && <div className="mt-4 grid gap-4 md:grid-cols-2">
      <div><Label>{t.baseURL}</Label><Input placeholder="https://your-sub2api.example.com" value={cfg.base_url||""} onChange={(e)=>setCfg({...cfg,base_url:e.target.value})}/></div>
      <div><Label>{t.adminToken}</Label><Input type="password" placeholder="x-api-key" value={cfg.admin_token||""} onChange={(e)=>setCfg({...cfg,admin_token:e.target.value})}/></div>
      <div><Label>{t.accountNamePrefix}</Label><Input placeholder="Sunny-" value={cfg.name_prefix||""} onChange={(e)=>setCfg({...cfg,name_prefix:e.target.value})}/></div>
      <div>
        <Label>{t.targetGroup}</Label>
        <div className="sr-group-picker-row">
          <div className="sr-group-picker" tabIndex={0} onBlur={() => window.setTimeout(()=>setGroupOpen(false), 120)}>
            <button type="button" className={cn("sr-group-picker-trigger", groupOpen && "open")} onClick={()=>setGroupOpen((v)=>!v)}>
              <span className={cn("sr-group-picker-placeholder", selectedGroupIds.length && "has-value")}>
                {selectedGroupIds.length ? selectedGroupIds.map(labelForGroupId).join(", ") : t.targetGroupPlaceholder}
              </span>
              <ChevronDown className={cn("h-4 w-4 transition-transform", groupOpen && "rotate-180")} />
            </button>
            {groupOpen && <div className="sr-group-picker-menu">
              {groups.length ? groups.map((g)=>{
                const checked = selectedGroupIds.includes(String(g.id));
                return <button type="button" key={String(g.id)} className={cn("sr-group-picker-option", checked && "selected")} onMouseDown={(e)=>e.preventDefault()} onClick={()=>toggleGroup(String(g.id))}>
                  <span className={cn("sr-group-check", checked && "on")}>{checked ? "✓" : ""}</span>
                  <span className="sr-group-name">{sub2apiGroupLabel(g)}</span>
                  <span className="sr-group-id">ID {g.id}</span>
                </button>
              }) : <div className="sr-group-empty">{t.noGroupsFetch}</div>}
            </div>}
          </div>
          <Button variant="outline" className="h-11 rounded-xl" disabled={fetching} onClick={()=>fetchGroups(false)}>{fetching?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<RefreshCw className="mr-2 h-4 w-4"/>}{t.fetch}</Button>
        </div>
      </div>
      <div><Label>{t.concurrency}</Label><Input type="number" value={cfg.concurrency||3} onChange={(e)=>setCfg({...cfg,concurrency:Number(e.target.value||3)})}/></div>
      <div><Label>{t.remoteProxy}</Label><SelectBox value={String(cfg.proxy_id||0)} onChange={(value)=>setCfg({...cfg,proxy_id:Number(value)})} options={[{value:"0",label:t.noRemoteProxy},...proxies.map((proxy)=>({value:proxy.id,label:`${proxy.id} · ${proxy.name}`}))]}/></div>
      <div><Label>{t.loadFactor}</Label><Input type="number" min={0} value={cfg.load_factor||0} onChange={(e)=>setCfg({...cfg,load_factor:Number(e.target.value||0)})}/></div>
      <div><Label>{t.priority}</Label><Input type="number" value={cfg.priority||50} onChange={(e)=>setCfg({...cfg,priority:Number(e.target.value||50)})}/></div>
      <div className="md:col-span-2"><Label>{t.modelWhitelist}</Label><Textarea className="min-h-28 rounded-xl" value={Array.isArray(cfg.model_whitelist)?cfg.model_whitelist.join("\n"):String(cfg.model_whitelist||"")} onChange={(e)=>setCfg({...cfg,model_whitelist:e.target.value.split(/[\n,]+/).map((x)=>x.trim()).filter(Boolean)})}/></div>
    </div>}
    {sub2apiEnabled && <div className="mt-5 flex items-center justify-end gap-3">
      {checkStatus ? <span className={cn("sr-check-status", checkStatus.type === "ok" && "ok", checkStatus.type === "fail" && "fail")}>{checkStatus.text}</span> : null}
      <Button variant="outline" className="rounded-xl px-5" disabled={fetching || loading} onClick={checkConnection}>{fetching?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<RefreshCw className="mr-2 h-4 w-4"/>}{t.check}</Button>
      <span title={!dirty ? t.configUnchanged : ""}>
        <Button className="rounded-xl bg-emerald-600 px-6 text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50" disabled={loading || !dirty} onClick={save}>{loading?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<Save className="mr-2 h-4 w-4"/>}{t.save}</Button>
      </span>
    </div>}
  </Card>;
}
const PROXY_STATUSES = ["启用", "停用", "失效"];

function ProxyStatusBadge({ t, status }: { t: typeof zh; status: string }) {
  const normalized = status === "可用" ? "启用" : status;
  const map: Record<string,string> = { "启用": "green", "停用": "gray", "失效": "red" };
  const labelMap: Record<string,string> = { "启用": t.proxyStatusEnabled, "停用": t.proxyStatusDisabled, "失效": t.proxyStatusInvalid };
  return <span className={cn("sr-status", `sr-status-${map[normalized] || "gray"}`)}>{labelMap[normalized] || normalized}</span>;
}

function ProxyConfigPage({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items,setItems]=useCachedState<AnyObj[]>("proxy.items",[]);
  const [stats,setStats]=useCachedState<AnyObj>("proxy.stats",{total:0,enabled:0,available:0});
  const [countries,setCountries]=useCachedState<string[]>("proxy.countries",[]);
  const [query,setQuery]=useCachedState("proxy.query","");
  const debouncedQuery = useDebouncedValue(query);
  const [status,setStatus]=useCachedState("proxy.status","");
  const [country,setCountry]=useCachedState("proxy.country","");
  const [timeSort,setTimeSort]=useCachedState<SortOrder>("proxy.timeSort","desc");
  const [page,setPage]=useCachedState("proxy.page",1);
  const [pageSize,setPageSize]=useCachedState("proxy.pageSize",10);
  const [total,setTotal]=useCachedState("proxy.total",0);
  const [loading,setLoading]=useCachedState("proxy.loading",false);
  const { loading: listLoading, track: trackListLoad } = useLoadingTracker();
  const [editing,setEditing]=useCachedState<AnyObj|null>("proxy.editing",null);
  const [selected,setSelected]=useCachedState<number[]>("proxy.selected",[]);
  const [selectingAll,setSelectingAll]=useState(false);
  const [batchEditing,setBatchEditing]=useCachedState<AnyObj|null>("proxy.batchEditing",null);
  const [proxyCfg,setProxyCfg]=useCachedState<AnyObj>("proxy.cfg",{proxy_enabled:true});
  const [proxySaving,setProxySaving]=useCachedState("proxy.savingCfg",false);
  const load = () => trackListLoad(async () => {
    const qs = new URLSearchParams({page:String(page), page_size:String(pageSize), q:debouncedQuery, status, country, sort_by:"last_checked_at", sort_order:timeSort});
    const res = await apiFetch(`/sunny/proxy-config/pool?${qs.toString()}`);
    setItems(res.items || []);
    setStats(res.stats || {total:0,enabled:0,available:0});
    setCountries(res.countries || []);
    setTotal(Number(res.total || 0));
  });
  const selectAllFiltered=async()=>{
    setSelectingAll(true);
    try {
      const qs=new URLSearchParams({q:debouncedQuery,status,country,sort_by:"last_checked_at",sort_order:timeSort});
      const result=await apiFetch(`/sunny/proxy-config/pool?${allSelectionParams(qs).toString()}`);
      const ids=selectionIDs(result);
      setSelected(ids);
      notify("ok",template(t.selectAllDone,{count:ids.length}));
    } catch(e:any) { notify("fail",e.message||String(e)); }
    finally { setSelectingAll(false); }
  };
  const loadConfig = async () => {
    const cfg = await apiFetch("/sunny/proxy-config");
    setProxyCfg(cfg || {proxy_enabled:true});
  };
  useEffect(()=>{void load().catch((e:any)=>notify("fail", e.message || String(e)))},[page, pageSize, debouncedQuery, status, country, timeSort]);
  useEffect(()=>{void loadConfig().catch((e:any)=>notify("fail", e.message || String(e)))},[]);
  useEffect(()=>{setPage(1)},[query, status, country, timeSort, pageSize]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  const trafficProxyEnabled = proxyCfg.proxy_enabled !== false;
  async function toggleTrafficProxy(){
    setProxySaving(true);
    try {
      const next = {...proxyCfg, proxy_enabled: !trafficProxyEnabled};
      const saved = await apiFetch("/sunny/proxy-config", {method:"PUT", body: JSON.stringify(next)});
      setProxyCfg(saved || next);
      notify("ok", t.proxySwitchSaved);
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setProxySaving(false); }
  }
  async function batchCheck(){
    setLoading(true);
    try {
      const ids = selected.length ? selected : items.map((x)=>Number(x.id)).filter(Boolean);
      const res = await apiFetch("/sunny/proxy-config/pool/check", { method:"POST", body: JSON.stringify({ids}) });
      notify("ok", `${t.proxyCheckDone}: ${res.available || 0}/${res.checked || 0}`);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setLoading(false); }
  }
  async function checkOne(row: AnyObj){
    setLoading(true);
    try {
      await apiFetch(`/sunny/proxy-config/pool/${row.id}/check`, { method:"POST" });
      notify("ok", t.proxyCheckDone);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setLoading(false); }
  }
  async function deleteProxy(row: AnyObj){
    try {
      await apiFetch(`/sunny/proxy-config/pool/${row.id}`, { method:"DELETE" });
      notify("ok", t.done);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function batchDeleteProxy(){
    if (!selected.length) return;
    try {
      await Promise.all(selected.map((id)=>apiFetch(`/sunny/proxy-config/pool/${id}`, { method:"DELETE" })));
      setSelected([]);
      notify("ok", t.done);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function batchUpdateProxy(form: AnyObj){
    if (!selected.length) return;
    const statusValue = String(form.status || "启用");
    const countryValue = String(form.country || "").trim();
    const updates: AnyObj = {status: statusValue, enabled: statusValue === "启用"};
    if (countryValue) updates.country = countryValue;
    try {
      await Promise.all(selected.map((id)=>apiFetch(`/sunny/proxy-config/pool/${id}`, { method:"PUT", body: JSON.stringify(updates) })));
      setBatchEditing(null);
      notify("ok", t.done);
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  const countryOptions = [{value:"",label:t.proxyAllCountry}, ...countries.map((c)=>({value:c,label:c}))];
  const allChecked = items.length > 0 && items.every((p)=>selected.includes(Number(p.id)));
  const statusOptions = PROXY_STATUSES.map((s)=>({value:s,label:s==="启用"?t.proxyStatusEnabled:s==="停用"?t.proxyStatusDisabled:t.proxyStatusInvalid}));
  async function refreshProxyList(){
    try { await load(); notify("ok", t.refreshDone); }
    catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="space-y-4">
    <Card className="rounded-[26px] p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Label tip={t.proxyTip}>{t.proxy}</Label>
          <p className="text-sm leading-6 text-[var(--text-muted)]">{t.proxyTip}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" className={cn("sr-setting-switch", trafficProxyEnabled ? "on" : "off")} onClick={toggleTrafficProxy} disabled={proxySaving} title={t.proxyTrafficSwitch}>
            <span className="sr-setting-switch-knob" />
            <span className="sr-setting-switch-text">
              <b>{trafficProxyEnabled ? t.proxyTrafficOn : t.proxyTrafficOff}</b>
              <small>{trafficProxyEnabled ? t.proxyTrafficOnHint : t.proxyTrafficOffHint}</small>
            </span>
            {proxySaving ? <Loader2 className="ml-1 h-4 w-4 animate-spin opacity-70"/> : null}
          </button>
          <Button className="sr-proxy-command-button bg-emerald-600 text-white hover:bg-emerald-700" onClick={()=>setEditing({address:"",country:"",status:"启用",enabled:true})}><Plus className="mr-2 h-4 w-4"/>{t.proxyAdd}</Button>
        </div>
      </div>
      <div className="sr-proxy-stats-grid mt-5">
        {[{value:"",label:t.proxyPool,count:stats.total||0,tone:"total"},{value:"启用",label:t.proxyEnabled,count:stats.enabled||0,tone:"phone"},{value:"停用",label:t.proxyStatusDisabled,count:stats.disabled||0,tone:"pending"},{value:"失效",label:t.proxyStatusInvalid,count:stats.invalid||0,tone:"failed"}].map((card)=><button type="button" key={card.value||"all"} data-tone={card.tone} className={cn("sr-mailbox-stat-card sr-proxy-stat-card",status===card.value&&"active")} aria-pressed={status===card.value} onClick={()=>{setStatus(card.value);setPage(1)}}><span className="sr-mailbox-stat-label"><i/>{card.label}</span><strong>{card.count}</strong></button>)}
      </div>
    </Card>
    <Card className="sr-toolbar sr-proxy-toolbar rounded-[18px] p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 flex-wrap gap-3">
          <div className="sr-search-control relative"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.proxySearch}/></div>
          <SelectBox className="sr-select-like" value={status} onChange={(v)=>setStatus(String(v))} options={[{value:"",label:t.allStatus}, ...statusOptions]} />
          <SelectBox className="sr-select-like" value={country} onChange={(v)=>setCountry(String(v))} options={countryOptions} />
        </div>
        <div className="sr-proxy-toolbar-actions flex flex-wrap gap-2">
          <SelectionSummary t={t} count={selected.length} total={total} selectingAll={selectingAll} onSelectAll={selectAllFiltered} onClear={()=>setSelected([])}/>
          {selected.length > 0 && <Button variant="outline" className="sr-proxy-command-button" onClick={()=>setBatchEditing({country:"",status:"启用"})}>{t.proxyBatchEdit} ({selected.length})</Button>}
          {selected.length > 0 && <ConfirmBubble message={t.proxyConfirmBatchDelete} detail={`${selected.length} ${t.selected}`} onConfirm={batchDeleteProxy}><Button variant="outline" className="sr-proxy-command-button border-red-200 text-red-500">{t.proxyBatchDelete} ({selected.length})</Button></ConfirmBubble>}
          <button className="sr-text-btn sr-action-refresh sr-proxy-command-button" onClick={refreshProxyList}><RefreshCw className="h-4 w-4"/>{t.refresh}</button>
          <Button variant="outline" className="sr-proxy-command-button" disabled={loading || !items.length} onClick={batchCheck}>{loading?<Loader2 className="mr-2 h-4 w-4 animate-spin"/>:<Settings2 className="mr-2 h-4 w-4"/>}{t.proxyBatchCheck}</Button>
        </div>
      </div>
    </Card>
    <Card className="sr-table-card overflow-hidden rounded-[18px] p-0" aria-busy={listLoading}>
      <ListLoadingOverlay loading={listLoading} label={t.loadingData}/>
      <ResizableDataTable tableKey="proxies" columns={DATA_TABLE_COLUMNS.proxies} className="sr-proxy-table" headers={[<input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected,...items.map((p)=>Number(p.id))])) : selected.filter((id)=>!items.some((p)=>Number(p.id)===id)))}/>,t.proxyAddress,t.proxyCountry,t.status,<SortTimeHeader label={t.proxyLastChecked} order={timeSort} onToggle={()=>setTimeSort(nextSortOrder(timeSort))}/>,t.operation]}>
        <tbody>{items.length ? items.map((p)=><tr key={p.id}>
          <td><input type="checkbox" checked={selected.includes(Number(p.id))} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected,Number(p.id)])) : selected.filter((id)=>id!==Number(p.id)))}/></td>
          <td title={p.address}><div className="font-semibold">{p.address}</div>{p.last_error ? <div className="mt-1 max-w-xl truncate text-xs text-red-400">{p.last_error}</div> : null}</td>
          <td>{p.country || "-"}</td>
          <td><ProxyStatusBadge t={t} status={p.status || "启用"} />{p.latency_ms ? <div className="mt-1 text-xs text-[var(--text-muted)]">{t.proxyLatency}: {p.latency_ms}ms</div> : null}</td>
          <td>{formatDateTime(p.last_checked_at)}</td>
          <td><div className="flex flex-wrap justify-center gap-2"><button className="sr-link" disabled={loading} onClick={()=>checkOne(p)}>{t.refresh}</button><button className="sr-link" onClick={()=>setEditing(p)}>{t.edit}</button><ConfirmBubble message={t.proxyConfirmDelete} detail={p.address || ""} onConfirm={()=>deleteProxy(p)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td>
        </tr>) : <tr><td colSpan={6}><div className="sr-empty"><div className="sr-empty-icon"><Settings2 className="h-7 w-7"/></div><div className="mt-3 text-base font-medium text-slate-900 dark:text-white">{t.proxyNoData}</div><p className="mt-2 text-sm text-slate-400">{t.proxyNoDataDesc}</p></div></td></tr>}</tbody>
      </ResizableDataTable>
      <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
    </Card>
    {editing && <ProxyEditModal key={editing.id || "new"} t={t} proxy={editing} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok", t.done); void load();}} notify={notify}/>}
    {batchEditing && <ProxyBatchEditModal t={t} count={selected.length} form={batchEditing} setForm={setBatchEditing} onClose={()=>setBatchEditing(null)} onSaved={()=>batchUpdateProxy(batchEditing)} />}
  </div>;
}

function ProxyEditModal({ t, proxy, onClose, onSaved, notify }: { t: typeof zh; proxy: AnyObj; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({...proxy, country:String(proxy.country || ""), status: proxy.status || "启用"});
  const isNew = !form.id;
  async function save(){
    const lines = String(form.address || "").split(/\r?\n/).map((x)=>x.trim()).filter(Boolean);
    if (!lines.length) { notify("fail", t.validationFailed); return; }
    const status = String(form.status || "启用");
    try {
      const editBody: AnyObj = {address:lines[0], status, enabled:status==="启用"};
      const country = String(form.country || "").trim();
      if (country && country !== String(proxy.country || "").trim()) editBody.country = country;
      await apiFetch(isNew ? "/sunny/proxy-config/pool" : `/sunny/proxy-config/pool/${form.id}`, {
        method: isNew ? "POST" : "PUT",
        body: JSON.stringify(isNew ? {addresses:lines, country, status, enabled:status==="启用"} : editBody),
      });
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{isNew ? t.proxyAdd : t.proxyEdit}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div><Label>{t.proxyAddress}</Label>{isNew ? <Textarea className="min-h-40 rounded-[14px]" placeholder={t.proxyAddressPlaceholder} value={form.address||""} onChange={(e)=>setForm({...form,address:e.target.value})}/> : <Input placeholder={t.proxyAddressPlaceholder} value={form.address||""} onChange={(e)=>setForm({...form,address:e.target.value})}/>}</div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.proxyCountry}</Label><Input placeholder={t.proxyCountryPlaceholder} value={form.country||""} onChange={(e)=>setForm({...form,country:e.target.value})}/></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||"启用"} onChange={(v)=>setForm({...form,status:String(v)})} options={PROXY_STATUSES.map((s)=>({value:s,label:s==="启用"?t.proxyStatusEnabled:s==="停用"?t.proxyStatusDisabled:t.proxyStatusInvalid}))} /></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={save}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>;
}

function ProxyBatchEditModal({ t, count, form, setForm, onClose, onSaved }: { t: typeof zh; count: number; form: AnyObj; setForm:(v:AnyObj)=>void; onClose:()=>void; onSaved:()=>void }) {
  return <div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal">
    <div className="sr-modal-head"><h3>{t.proxyBatchEdit}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 px-4 py-3 text-sm font-bold text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-200">{t.selected}: {count}</div>
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.proxyCountry}</Label><Input placeholder={t.proxyCountryKeep} value={form.country||""} onChange={(e)=>setForm({...form,country:e.target.value})}/></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||"启用"} onChange={(v)=>setForm({...form,status:String(v)})} options={PROXY_STATUSES.map((s)=>({value:s,label:s==="启用"?t.proxyStatusEnabled:s==="停用"?t.proxyStatusDisabled:t.proxyStatusInvalid}))} /></div>
      </div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" onClick={onSaved}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>;
}
const SESSION_PLAN_OPTIONS = PLAN_TYPE_OPTIONS;
const SESSION_STATUS_OPTIONS = MAILBOX_STATUSES;
const TRIAL_ELIGIBILITY_OPTIONS = ["unknown", "eligible", "ineligible"];

function trialEligibilityLabel(t: AnyObj, value: any) {
  if (value === "eligible") return t.trialEligible;
  if (value === "ineligible") return t.trialIneligible;
  return t.trialUnknown;
}

function trialCheckable(row: AnyObj) {
  return ["已注册", "registered"].includes(String(row.status || "")) && String(row.plan_type || "").toLowerCase() === "free";
}

function TrialEligibilityBadge({ t, row }: { t: AnyObj; row: AnyObj }) {
  if (!trialCheckable(row)) return <span className="text-slate-400">-</span>;
  if (row.trial_eligibility === "eligible") return <span className="font-semibold text-emerald-600 dark:text-emerald-400">{t.trialEligible}</span>;
  if (row.trial_eligibility === "ineligible") return <span className="font-semibold text-red-500">{t.trialIneligible}</span>;
  return <span className="text-slate-400">-</span>;
}
type SessionFieldName = "access_token" | "refresh_token" | "secret_key";
const SESSION_FIELD_LABELS: Record<SessionFieldName, string> = { access_token: "AT", refresh_token: "RT", secret_key: "SK" };

function renewalViewForSession(tasks: PersistentSessionTask[], row: AnyObj) {
  const task = [...tasks].reverse().find((item) => item.kind === "refresh-at"
    && item.sessionIds.some((id) => Number(id) === Number(row.id))
    && !item.dismissedEmails.includes(String(row.email || "").toLowerCase()));
  if (!task) return null;
  const key = String(row.email || "").toLowerCase();
  const progress = task.progress[key] || {
    email: String(row.email || ""),
    current: task.state === "succeeded" ? 1 : 0,
    total: task.state === "succeeded" ? 1 : 7,
    checkpoint: task.state === "succeeded" ? "completed" : task.state === "failed" ? "failed" : task.state === "cancelled" ? "cancelled" : "queued",
    state: task.state,
    error: task.error,
    updatedAt: Date.now(),
  };
  return { task, progress };
}

function renewalStepLabel(t: AnyObj, checkpoint: string) {
  if (checkpoint === "account_deactivated") return t.statusLabels?.["已封禁"] || "Account banned";
  return t.renewalSteps?.[checkpoint] || checkpoint;
}

function SessionManager({ t, notify }: { t: typeof zh; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items,setItems]=useCachedState<AnyObj[]>("session.items",[]);
  const [fmt,setFmt]=useCachedState("session.fmt","sk");
  const [query,setQuery]=useCachedState("session.query","");
  const debouncedQuery = useDebouncedValue(query);
  const [status,setStatus]=useCachedState("session.status","");
  const [plan,setPlan]=useCachedState("session.plan","");
  const [trialEligibility,setTrialEligibility]=useCachedState("session.trialEligibility","");
  const [group,setGroup]=useCachedState("session.group","");
  const [groups,setGroups]=useState<AnyObj[]>([]);
  const [selected,setSelected]=useCachedState<number[]>("session.selected",[]);
  const [selectingAll,setSelectingAll]=useState(false);
  const [editing,setEditing]=useCachedState<AnyObj|null>("session.editing",null);
  const [fieldLoading,setFieldLoading]=useState<Record<string,boolean>>({});
  const [mailboxForMail,setMailboxForMail]=useState<AnyObj|null>(null);
  const [healthBusy,setHealthBusy]=useState(false);
  const [subscriptionCheckingSessionIds,setSubscriptionCheckingSessionIds]=useState<number[]>([]);
  const [batchSubscriptionBusy,setBatchSubscriptionBusy]=useState(false);
  const [trialCheckingSessionIds,setTrialCheckingSessionIds]=useState<number[]>([]);
  const [batchTrialBusy,setBatchTrialBusy]=useState(false);
  const [atCheckingSessionIds,setATCheckingSessionIds]=useState<number[]>([]);
  const [batchATCheckBusy,setBatchATCheckBusy]=useState(false);
  const [sub2ImportingSessionIds,setSub2ImportingSessionIds]=useState<number[]>([]);
  const [batchSub2Busy,setBatchSub2Busy]=useState(false);
  const [maintenanceOpen,setMaintenanceOpen]=useState(false);
  const [failureDetail,setFailureDetail]=useState<{title:string;content:string}|null>(null);
  const persistentTasks = usePersistentSessionTasks();
  const activeRenewalTasks = persistentTasks.filter((task)=>task.kind==="refresh-at" && task.state==="running" && task.taskId);
  const [stoppingRenewalTaskIds,setStoppingRenewalTaskIds]=useState<string[]>([]);
  const stoppingRenewal = activeRenewalTasks.some((task)=>stoppingRenewalTaskIds.includes(task.taskId));
  const refreshingSessionIds = Array.from(new Set(persistentTasks.filter((task)=>task.kind==="refresh-at" && task.state==="running").flatMap((task)=>task.sessionIds)));
  const acquiringRTSessionIds = Array.from(new Set(persistentTasks.filter((task)=>task.kind==="acquire-rt" && task.state==="running").flatMap((task)=>task.sessionIds)));
  const [sortBy,setSortBy]=useCachedState("session.sortBy","last_health_checked_at");
  const [timeSort,setTimeSort]=useCachedState<SortOrder>("session.timeSort","desc");
  const [page,setPage]=useCachedState("session.page",1);
  const [pageSize,setPageSize]=useCachedState("session.pageSize",10);
  const [total,setTotal]=useCachedState("session.total",0);
  const { loading: listLoading, track: trackListLoad } = useLoadingTracker();
  useEffect(()=>{
    const activeIds=new Set(activeRenewalTasks.map((task)=>task.taskId));
    setStoppingRenewalTaskIds((old)=>{
      const next=old.filter((taskId)=>activeIds.has(taskId));
      return next.length===old.length ? old : next;
    });
  },[persistentTasks]);
  const load=()=>trackListLoad(async()=>{
    const qs = new URLSearchParams({ page:String(page), page_size:String(pageSize), sort_by:sortBy, sort_order:timeSort });
    if (debouncedQuery.trim()) qs.set("q", debouncedQuery.trim());
    if (status) qs.set("status", status);
    if (plan) qs.set("plan_type", plan);
    if (trialEligibility) qs.set("trial_eligibility", trialEligibility);
    if (group) qs.set("group_id", group);
    const res = await apiFetch(`/sunny/sessions?${qs.toString()}`);
    setItems(res.items||[]);
    setTotal(Number(res.total || 0));
  });
  const selectAllFiltered=async()=>{
    setSelectingAll(true);
    try {
      const qs=new URLSearchParams({sort_by:sortBy,sort_order:timeSort});
      if(debouncedQuery.trim()) qs.set("q",debouncedQuery.trim());
      if(status) qs.set("status",status);
      if(plan) qs.set("plan_type",plan);
      if(trialEligibility) qs.set("trial_eligibility",trialEligibility);
      if(group) qs.set("group_id",group);
      const result=await apiFetch(`/sunny/sessions?${allSelectionParams(qs).toString()}`);
      const ids=selectionIDs(result);
      setSelected(ids);
      notify("ok",template(t.selectAllDone,{count:ids.length}));
    } catch(e:any) { notify("fail",e.message||String(e)); }
    finally { setSelectingAll(false); }
  };
  useEffect(()=>{void load()},[sortBy, timeSort, page, pageSize, debouncedQuery, status, plan, trialEligibility, group]);
  useEffect(()=>{setPage(1)},[sortBy, timeSort, pageSize, query, status, plan, trialEligibility, group]);
  useEffect(()=>{apiFetch("/sunny/mailbox-groups").then((res)=>setGroups(sortMailboxGroups(res.items||[]))).catch(()=>setGroups([]));},[]);
  useEffect(()=>{const pages=pageCount(total,pageSize); if(page>pages) setPage(pages);},[total,pageSize,page]);
  const exportFormat = ["sk", "at", "sub"].includes(fmt) ? fmt : "sk";
  const allChecked = items.length > 0 && items.every((x)=>selected.includes(x.id));
  async function exp(ids?: number[], format = exportFormat){
    const sessionIds = ids?.length ? ids : selected;
    if (!sessionIds.length) { notify("fail", t.selectExportRows); return; }
    try{const {blob,filename}=await apiDownload("/sunny/sessions/export",{method:"POST",body:JSON.stringify({format, session_ids: sessionIds})});triggerBrowserDownload(blob,filename);notify("ok",t.done)}
    catch(e:any){notify("fail",e.message||String(e))}
  }
  async function del(row: AnyObj) {
    try { await apiFetch(`/sunny/sessions/${row.id}`, { method:"DELETE" }); notify("ok", t.done); setSelected((old)=>old.filter((id)=>id!==row.id)); void load(); }
    catch(e:any){ notify("fail", e.message || String(e)); }
  }
  async function refreshSessionList() {
    try { await load(); notify("ok", t.refreshDone); }
    catch(e:any){ notify("fail", e.message || String(e)); }
  }
  async function runHealthCheck(ids: number[], row?: AnyObj) {
    if (row && !HEALTH_CHECKABLE_STATUSES.has(String(row.status || ""))) { notify("ok", t.alreadyBanned); return; }
    if (!ids.length) { notify("fail", t.healthNoSelection); return; }
    setHealthBusy(true);
    try {
      let task = await apiFetch("/sunny/sessions/health-check", { method:"POST", body:JSON.stringify({ session_ids: ids }) });
      while (!task.terminal) {
        await new Promise((resolve)=>setTimeout(resolve, 1200));
        task = await apiFetch(`/tasks/${task.id}`);
      }
      const result = task.result || {};
      if (row) {
        const item = (result.items || []).find((entry: AnyObj)=>String(entry.email).toLowerCase() === String(row.email).toLowerCase());
        if (item?.status === "banned") notify("fail", template(t.healthBanned,{email:row.email}));
        else if (item?.status === "alive") notify("ok", template(t.healthAlive,{email:row.email}));
        else notify("fail", item?.error || t.failed);
      } else {
        notify("ok", template(t.healthCheckSummary, { total: Number(result.requested || 0), alive: Number(result.alive || 0), banned: Number(result.banned || 0), failed: Number(result.failed || 0) }));
      }
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally { setHealthBusy(false); }
  }
  async function runSubscriptionCheck(ids: number[], row?: AnyObj) {
    if (!ids.length) { notify("fail", t.subscriptionNoSelection); return; }
    const targetIds=Array.from(new Set(ids.map(Number).filter(Boolean)));
    setSubscriptionCheckingSessionIds(targetIds);
    if (!row) setBatchSubscriptionBusy(true);
    try {
      let task = await apiFetch("/sunny/sessions/subscription-check", { method:"POST", body:JSON.stringify({ session_ids:targetIds }) });
      while (!task.terminal) {
        await new Promise((resolve)=>setTimeout(resolve, 1200));
        task = await apiFetch(`/tasks/${task.id}`);
      }
      const result = task.result || {};
      if (row) {
        const item = (result.items || []).find((entry:AnyObj)=>String(entry.email).toLowerCase()===String(row.email).toLowerCase());
        if (item?.status === "subscribed") notify("ok", template(t.subscriptionConfirmed,{email:row.email}));
        else if (item?.status === "not_subscribed") notify("ok", template(t.subscriptionNotFound,{email:row.email}));
        else notify("fail", item?.error || template(t.subscriptionCheckFailed,{email:row.email}));
      } else {
        const failed=Number(result.failed || 0);
        notify(failed > 0 && failed === Number(result.requested || 0) ? "fail" : "ok", template(t.subscriptionCheckSummary, {
          total:Number(result.requested || 0), subscribed:Number(result.subscribed || 0), notSubscribed:Number(result.not_subscribed || 0), failed,
        }));
      }
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally {
      setSubscriptionCheckingSessionIds([]);
      if (!row) setBatchSubscriptionBusy(false);
    }
  }
  async function runTrialCheck(ids: number[], row?: AnyObj) {
    if (row && !trialCheckable(row)) { notify("fail", t.trialUnavailable); return; }
    if (!ids.length) { notify("fail", t.trialNoSelection); return; }
    const targetIds=Array.from(new Set(ids.map(Number).filter(Boolean)));
    setTrialCheckingSessionIds((old)=>Array.from(new Set([...old,...targetIds])));
    if (!row) setBatchTrialBusy(true);
    try {
      let task = await apiFetch("/sunny/sessions/trial-check", { method:"POST", body:JSON.stringify({ session_ids:targetIds }) });
      while (!task.terminal) {
        await new Promise((resolve)=>setTimeout(resolve, 900));
        task = await apiFetch(`/tasks/${task.id}`);
      }
      const result = task.result || {};
      const renewalTaskId = String(result.renewal_task_id || "");
      const invalidSessionIds = Array.isArray(result.invalid_session_ids) ? result.invalid_session_ids.map(Number).filter(Boolean) : [];
      if (row) {
        const item = (result.items || []).find((entry:AnyObj)=>Number(entry.session_id)===Number(row.id));
        if (item?.status === "eligible") notify("ok", template(t.trialEligibleResult,{email:row.email}));
        else if (item?.status === "ineligible") notify("ok", template(t.trialIneligibleResult,{email:row.email}));
        else notify("fail", item?.error || item?.message || template(t.trialCheckFailed,{email:row.email}));
      } else {
        const failed=Number(result.failed || 0);
        notify(failed > 0 && failed === Number(result.requested || 0) ? "fail" : "ok", template(t.trialCheckSummary, {
          total:Number(result.requested || 0), eligible:Number(result.eligible || 0), ineligible:Number(result.ineligible || 0), skipped:Number(result.skipped || 0), failed,
        }));
      }
      if (renewalTaskId && invalidSessionIds.length) {
        void runPersistentSessionTask("refresh-at", invalidSessionIds, row?.email, async()=>({id:renewalTaskId}))
          .then(()=>load()).catch((e:any)=>notify("fail",e.message||String(e)));
      }
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally {
      setTrialCheckingSessionIds((old)=>old.filter((id)=>!targetIds.includes(id)));
      if (!row) setBatchTrialBusy(false);
    }
  }
  function toggleTimeSort(field: string) {
    if (sortBy === field) setTimeSort(nextSortOrder(timeSort));
    else { setSortBy(field); setTimeSort("desc"); }
  }
  async function openSessionMail(row: AnyObj) {
    if (!row.mailbox_id) { notify("fail", t.noMailbox); return; }
    try { setMailboxForMail(await apiFetch(`/sunny/mailboxes/${row.mailbox_id}`)); }
    catch(e:any) { notify("fail", e.message || String(e)); }
  }
  async function copySessionField(row: AnyObj, field: SessionFieldName) {
    const key = `${row.id}:${field}`;
    if (fieldLoading[key]) return;
    setFieldLoading((old)=>({...old,[key]:true}));
    try {
      const result = await apiFetch(`/sunny/sessions/${row.id}/field?name=${field}`);
      const value = String(result.value || "").trim();
      if (!value) throw new Error(template(t.sessionFieldEmpty,{field:SESSION_FIELD_LABELS[field]}));
      await copyTextToClipboard(value);
      notify("ok", t.copySuccess);
    } catch(e:any) {
      notify("fail", e.message || String(e));
    } finally {
      setFieldLoading((old)=>({...old,[key]:false}));
    }
  }
  async function refreshAccessTokens(ids: number[], row?: AnyObj) {
    if (!ids.length) { notify("fail", t.refreshATNoSelection); return; }
    const targetIds=Array.from(new Set(ids.map(Number).filter(Boolean)));
    setATCheckingSessionIds((old)=>Array.from(new Set([...old,...targetIds])));
    if (!row) setBatchATCheckBusy(true);
    try {
      let task = await apiFetch("/sunny/sessions/access-token-check", { method:"POST", body:JSON.stringify({ session_ids:ids }) });
      while (!task.terminal) {
        await new Promise((resolve)=>setTimeout(resolve, 900));
        task = await apiFetch(`/tasks/${task.id}`);
      }
      const result = task.result || {};
      const valid = Number(result.valid || 0);
      const invalid = Number(result.invalid || 0);
      const failed = Number(result.failed || 0);
      const renewalTaskId = String(result.renewal_task_id || "");
      const invalidSessionIds = Array.isArray(result.invalid_session_ids) ? result.invalid_session_ids.map(Number).filter(Boolean) : [];
      if (row) {
        const item = (result.items || []).find((entry:AnyObj)=>String(entry.email).toLowerCase()===String(row.email).toLowerCase());
        if (item?.status === "valid") notify("ok", t.currentATValid);
        else if (item?.status === "invalid") notify("fail", t.currentATInvalid);
        else notify("fail", template(t.atCheckFailed,{error:item?.error || task.error || t.failed}));
      } else {
        notify(failed > 0 && valid === 0 && invalid === 0 ? "fail" : "ok", template(t.refreshATSummary,{valid,invalid,failed}));
      }
      if (renewalTaskId && invalidSessionIds.length) {
        void runPersistentSessionTask("refresh-at", invalidSessionIds, row?.email, async()=>({id:renewalTaskId}))
          .then(()=>load()).catch((e:any)=>notify("fail",e.message||String(e)));
      }
      await load();
    } catch(e:any) { notify("fail", e.message || String(e)); }
    finally {
      setATCheckingSessionIds((old)=>old.filter((id)=>!targetIds.includes(id)));
      if (!row) setBatchATCheckBusy(false);
    }
  }
  async function stopRenewalTasks() {
    const tasks = activeRenewalTasks.filter((task)=>!stoppingRenewalTaskIds.includes(task.taskId));
    if (!tasks.length) return;
    const taskIds = tasks.map((task)=>task.taskId);
    setStoppingRenewalTaskIds((old)=>Array.from(new Set([...old,...taskIds])));
    tasks.forEach((task)=>updateSessionTask(task.clientId,(current)=>{
      const progress={...current.progress};
      current.sessionIds.forEach((sessionId)=>{
        const row=items.find((item)=>Number(item.id)===Number(sessionId));
        const email=String(row?.email||"").trim();
        if (!email) return;
        const key=email.toLowerCase();
        const existing=progress[key];
        progress[key]={email,current:existing?.current||0,total:existing?.total||7,checkpoint:"stopping",state:"running",error:existing?.error,updatedAt:Date.now()};
      });
      return {...current,progress};
    }));
    try {
      const results=await Promise.allSettled(taskIds.map((taskId)=>apiFetch(`/tasks/${taskId}/cancel`,{method:"POST"})));
      const rejected=results.map((result,index)=>({result,taskId:taskIds[index]})).filter((entry)=>entry.result.status==="rejected");
      if (rejected.length) {
        setStoppingRenewalTaskIds((old)=>old.filter((taskId)=>!rejected.some((entry)=>entry.taskId===taskId)));
        const reason=rejected[0].result;
        throw reason.status==="rejected" ? reason.reason : new Error(t.stopRenewalFailed);
      }
      notify("ok",t.stopRenewalRequested);
    } catch(e:any) {
      notify("fail",`${t.stopRenewalFailed}: ${e?.message||String(e)}`);
    }
  }
  async function acquireRefreshToken(row: AnyObj) {
    const id = Number(row.id || 0);
    if (!id || acquiringRTSessionIds.includes(id)) return;
    try {
      const task = await runPersistentSessionTask("acquire-rt", [id], row.email, () => apiFetch("/sunny/tasks/acquire-rt", { method:"POST", body:JSON.stringify({ session_ids:[id], execution_mode:"background", concurrency:1 }) }));
      const result = task.result || {};
      if (Number(result.success || task.success_count || 0) > 0) {
        setItems((old)=>old.map((item)=>Number(item.id)===id?{...item,has_refresh_token:true}:item));
        notify("ok", template(t.acquireRTDone,{email:row.email}));
        await load();
      } else {
        notify("fail", String(result.errors?.[0] || task.error || t.acquireRTFailed));
      }
    } catch(e:any) { notify("fail", e.message || t.acquireRTFailed); }
  }
  async function importSub2API(ids: number[], row?: AnyObj) {
    const targetIds=Array.from(new Set(ids.map(Number).filter(Boolean)));
    if (!targetIds.length) { notify("fail",t.sub2NoSelection); return; }
    setSub2ImportingSessionIds((old)=>Array.from(new Set([...old,...targetIds])));
    if (!row) setBatchSub2Busy(true);
    try {
      const accountIds=Array.from(new Set(items.filter((item)=>targetIds.includes(Number(item.id))).map((item)=>Number(item.account_id)).filter(Boolean)));
      const result=await apiFetch("/sunny/sub2api/import",{method:"POST",body:JSON.stringify({session_ids:targetIds,account_ids:accountIds})});
      const skipped=Array.isArray(result.skipped)?result.skipped:[];
      const selectedCount=Number(result.selected||targetIds.length);
      const uploaded=Number(result.uploaded||0);
      const confirmed=Number(result.confirmed||0);
      const failed=Number(result.failed||0);
      notify(failed>0 || confirmed<uploaded ? "fail" : "ok",template(t.sub2ImportSummary,{selected:selectedCount,uploaded,confirmed,failed,skipped:skipped.length}));
      if (skipped.length || failed>0 || confirmed<uploaded) {
        setFailureDetail({title:t.sub2ImportDetails,content:JSON.stringify({skipped,response:result.response||null},null,2)});
      }
      await load();
    } catch(e:any) { notify("fail",e.message||String(e)); }
    finally {
      setSub2ImportingSessionIds((old)=>old.filter((id)=>!targetIds.includes(id)));
      if (!row) setBatchSub2Busy(false);
    }
  }
  return <Card className="relative overflow-hidden rounded-[30px] p-5" aria-busy={listLoading}>
    <ListLoadingOverlay loading={listLoading} label={t.loadingData}/>
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2"><h2 className="text-xl font-bold">{t.session}</h2><button type="button" className="sr-icon-command" title={t.maintenanceSettings} onClick={()=>setMaintenanceOpen(true)}><Settings2 className="h-4 w-4"/></button></div>
      <div className="flex flex-wrap items-center gap-2">
        <SelectBox className="sr-select-like" value={exportFormat} onChange={(v)=>setFmt(String(v))} options={[{value:"sk",label:t.exportSK},{value:"at",label:t.exportAT},{value:"sub",label:t.exportSUB}]} />
        <Button className="rounded-full" onClick={()=>exp()}><Download className="mr-2 h-4 w-4"/>{t.export}</Button>
      </div>
    </div>
    <div className="sr-toolbar sr-toolbar-compact mb-4 flex flex-wrap items-center gap-2 rounded-[18px] p-3">
      <div className="sr-search-control relative"><Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"/><input className="sr-search" value={query} onChange={(e)=>setQuery(e.target.value)} placeholder={t.searchAccount} /></div>
      <SelectBox searchable searchPlaceholder={t.groupSearch} noResultsLabel={t.groupNoResults} className="sr-select-like" value={group} onChange={(v)=>setGroup(String(v))} options={[{value:"",label:t.allGroups,searchText:t.allGroups}, ...mailboxGroupOptions(t,groups).map((item)=>({...item,value:String(item.value)}))]} />
      <SelectBox className="sr-select-like" value={status} onChange={(v)=>setStatus(String(v))} options={[{value:"",label:t.allStatus}, ...SESSION_STATUS_OPTIONS.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))]} />
      <SelectBox className="sr-select-like" value={plan} onChange={(v)=>setPlan(String(v))} options={[{value:"",label:t.planType}, ...SESSION_PLAN_OPTIONS.map((p)=>({value:p,label:formatPlanType(p)}))]} />
      <SelectBox className="sr-select-like" value={trialEligibility} onChange={(v)=>setTrialEligibility(String(v))} options={[{value:"",label:t.allTrialEligibility},{value:"eligible",label:t.trialEligible},{value:"ineligible",label:t.trialIneligible}]} />
      <SelectionSummary t={t} count={selected.length} total={total} selectingAll={selectingAll} onSelectAll={selectAllFiltered} onClear={()=>setSelected([])}/>
      <div className="ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-2">
        {activeRenewalTasks.length > 0 && <button className={cn("sr-text-btn sr-action-danger",stoppingRenewal&&"is-running")} aria-busy={stoppingRenewal} disabled={stoppingRenewal} title={t.stopRenewalTip} onClick={()=>void stopRenewalTasks()}>{stoppingRenewal ? <Loader2 className="h-4 w-4 animate-spin"/> : <X className="h-4 w-4"/>}{stoppingRenewal ? t.stoppingRenewal : t.stopRenewal}</button>}
        <button className={cn("sr-text-btn sr-action-info",batchSub2Busy&&"is-running")} aria-busy={batchSub2Busy} disabled={batchSub2Busy || selected.length===0} title={selected.length===0?t.sub2NoSelection:t.importSub2API} onClick={()=>void importSub2API(selected)}>{batchSub2Busy?<Loader2 className="h-4 w-4 animate-spin"/>:<Upload className="h-4 w-4"/>}{batchSub2Busy?t.importingSub2API:t.importSub2API}</button>
        <button className={cn("sr-text-btn sr-action-info",batchTrialBusy&&"is-running")} aria-busy={batchTrialBusy} disabled={batchTrialBusy || selected.length === 0} title={selected.length === 0 ? t.trialNoSelection : t.trialCheck} onClick={()=>runTrialCheck(selected)}>{batchTrialBusy ? <Loader2 className="h-4 w-4 animate-spin"/> : <Sparkles className="h-4 w-4"/>}{batchTrialBusy ? t.trialChecking : t.trialCheck}</button>
        <button className={cn("sr-text-btn sr-action-info",batchATCheckBusy&&"is-running")} aria-busy={batchATCheckBusy} disabled={batchATCheckBusy || selected.length === 0 || selected.some((id)=>atCheckingSessionIds.includes(id)||refreshingSessionIds.includes(id))} title={selected.length === 0 ? t.refreshATNoSelection : t.refreshAT} onClick={()=>refreshAccessTokens(selected)}>{batchATCheckBusy ? <Loader2 className="h-4 w-4 animate-spin"/> : <RefreshCw className="h-4 w-4"/>}{batchATCheckBusy ? t.refreshingAT : t.refreshAT}</button>
        <button className={cn("sr-text-btn sr-action-health",healthBusy&&"is-running")} aria-busy={healthBusy} disabled={healthBusy || selected.length === 0} title={selected.length === 0 ? t.healthNoSelection : t.healthCheck} onClick={()=>runHealthCheck(selected)}>{healthBusy ? <Loader2 className="h-4 w-4 animate-spin"/> : <Activity className="h-4 w-4"/>}{healthBusy ? t.healthChecking : t.healthCheck}</button>
        <button className={cn("sr-text-btn sr-action-health",batchSubscriptionBusy&&"is-running")} aria-busy={batchSubscriptionBusy} disabled={subscriptionCheckingSessionIds.length > 0 || selected.length === 0} title={selected.length === 0 ? t.subscriptionNoSelection : t.subscriptionCheck} onClick={()=>runSubscriptionCheck(selected)}>{batchSubscriptionBusy ? <Loader2 className="h-4 w-4 animate-spin"/> : <Crown className="h-4 w-4"/>}{batchSubscriptionBusy ? t.subscriptionChecking : t.subscriptionCheck}</button>
        <button className="sr-text-btn sr-action-refresh" disabled={healthBusy} onClick={refreshSessionList}><RefreshCw className="h-4 w-4"/>{t.refresh}</button>
      </div>
    </div>
    <div className="sr-table-scroll">
      <ResizableDataTable tableKey="sessions-v2" columns={DATA_TABLE_COLUMNS.sessions} className="sr-session-table" headers={[<input type="checkbox" checked={allChecked} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected, ...items.map((x)=>x.id)])) : selected.filter((id)=>!items.some((x)=>x.id===id)))}/>,t.email,t.groupFilter,t.status,t.planType,t.trialEligibility,"SK","AT","RT",<SortTimeHeader label={t.atExpiresAt} order={sortBy==="access_token_expires_at"?timeSort:"desc"} onToggle={()=>toggleTimeSort("access_token_expires_at")}/>,<SortTimeHeader label={t.lastHealthCheckedAt} order={sortBy==="last_health_checked_at"?timeSort:"desc"} onToggle={()=>toggleTimeSort("last_health_checked_at")}/>,t.operation]}>
        <tbody>{items.length ? items.map((s)=>{
          const refreshing=refreshingSessionIds.includes(s.id);
          const checkingAT=atCheckingSessionIds.includes(s.id);
          const checkingSubscription=subscriptionCheckingSessionIds.includes(s.id);
          const checkingTrial=trialCheckingSessionIds.includes(s.id);
          const importingSub2=sub2ImportingSessionIds.includes(s.id);
          const acquiringRT=acquiringRTSessionIds.includes(s.id);
          const skLoading=fieldLoading[`${s.id}:secret_key`];
          const atLoading=fieldLoading[`${s.id}:access_token`];
          const rtLoading=fieldLoading[`${s.id}:refresh_token`];
          const renewalView=renewalViewForSession(persistentTasks,s);
          const renewalPercent=renewalView ? Math.min(100, Math.max(0, (renewalView.progress.current / renewalView.progress.total) * 100)) : 0;
          return <Fragment key={s.id}>
            <tr><td><input type="checkbox" checked={selected.includes(s.id)} onChange={(e)=>setSelected(e.target.checked ? Array.from(new Set([...selected,s.id])) : selected.filter((id)=>id!==s.id))}/></td><td title={s.email}>{s.email}</td><td title={s.group_name || "-"}>{s.group_name || "-"}</td><td><StatusBadge t={t} status={s.status || "已注册"} /></td><td><PlanTypeBadge value={s.plan_type} /></td><td><TrialEligibilityBadge t={t} row={s}/></td><td>{s.has_secret_key ? <button className="sr-session-field-button" disabled={skLoading} onClick={()=>void copySessionField(s,"secret_key")}>{skLoading ? <Loader2 className="h-4 w-4 animate-spin"/> : "SK"}</button> : "-"}</td><td>{s.has_access_token ? <button className="sr-session-field-button" disabled={atLoading} onClick={()=>void copySessionField(s,"access_token")}>{atLoading ? <Loader2 className="h-4 w-4 animate-spin"/> : "AT"}</button> : "-"}</td><td>{s.has_refresh_token ? <button className="sr-session-field-button" disabled={rtLoading} onClick={()=>void copySessionField(s,"refresh_token")}>{rtLoading ? <Loader2 className="h-4 w-4 animate-spin"/> : "RT"}</button> : <button className="sr-session-field-button text-slate-400" disabled={acquiringRT} title={t.acquiringRT} onClick={()=>void acquireRefreshToken(s)}>{acquiringRT ? <Loader2 className="h-4 w-4 animate-spin"/> : t.acquireRT}</button>}</td><td>{s.access_token_status === "renewal_failed" ? <FailureState label={t.atRenewalFailed} detail={s.access_token_error} onOpen={setFailureDetail}/> : s.access_token_status === "invalid" ? <FailureState label={t.atInvalidOrExpired} detail={s.access_token_error} onOpen={setFailureDetail}/> : s.access_token_status === "probe_failed" ? <FailureState label={t.atProbeFailed} detail={s.access_token_error} onOpen={setFailureDetail}/> : formatDateTime(s.access_token_expires_at)}</td><td>{s.health_check_status === "failed" ? <FailureState label={t.accountHealthCheckFailed} detail={s.health_check_error} onOpen={setFailureDetail}/> : formatDateTime(s.last_health_checked_at)}</td><td><div className="flex flex-wrap gap-2"><button className="sr-link" onClick={()=>void openSessionMail(s)}>{t.queryMail}</button><button className="sr-link" onClick={()=>setEditing(s)}>{t.edit}</button><button className="sr-link" onClick={()=>exp([s.id],"sub")}>{t.export}</button><button className="sr-link inline-flex items-center gap-1" disabled={!trialCheckable(s) || checkingTrial} title={!trialCheckable(s) ? t.trialUnavailable : t.trialCheck} onClick={()=>runTrialCheck([s.id],s)}>{checkingTrial ? <Loader2 className="h-4 w-4 animate-spin"/> : <Sparkles className="h-4 w-4"/>}{checkingTrial ? t.trialChecking : t.trialCheck}</button><button className="sr-link inline-flex items-center gap-1" disabled={importingSub2} onClick={()=>void importSub2API([s.id],s)}>{importingSub2?<Loader2 className="h-4 w-4 animate-spin"/>:<Upload className="h-4 w-4"/>}{importingSub2?t.importingSub2API:t.importSub2API}</button><button className="sr-link inline-flex items-center gap-1" disabled={refreshing || checkingAT} onClick={()=>refreshAccessTokens([s.id],s)}>{refreshing || checkingAT ? <Loader2 className="h-4 w-4 animate-spin"/> : <RefreshCw className="h-4 w-4"/>}{t.updateAT}</button><button className="sr-link" disabled={healthBusy} onClick={()=>runHealthCheck([s.id],s)}><Activity className="inline h-4 w-4"/>{t.healthCheck}</button><button className="sr-link" disabled={subscriptionCheckingSessionIds.length > 0} onClick={()=>runSubscriptionCheck([s.id],s)}>{checkingSubscription ? <Loader2 className="inline h-4 w-4 animate-spin"/> : <Crown className="inline h-4 w-4"/>}{checkingSubscription ? t.subscriptionChecking : t.subscriptionCheck}</button><ConfirmBubble message={t.confirmDeleteMailbox} detail={s.email} onConfirm={()=>del(s)}><button className="sr-link text-red-500">{t.delete}</button></ConfirmBubble></div></td></tr>
            {renewalView && <tr className="sr-renewal-progress-row"><td/><td colSpan={11}><div className={cn("sr-renewal-progress",`is-${renewalView.progress.state}`)}><strong className="sr-renewal-progress-count">{renewalView.progress.current}/{renewalView.progress.total}</strong><div className="sr-renewal-progress-main"><div className="sr-renewal-progress-label">{renewalStepLabel(t,renewalView.progress.checkpoint)}</div><div className="sr-renewal-progress-track"><span style={{width:`${renewalPercent}%`}}/></div>{renewalView.progress.error && <div className="sr-renewal-progress-error">{renewalView.progress.error}</div>}</div><button className="sr-renewal-progress-close" title={t.closeRenewalProgress} onClick={()=>dismissSessionRenewal(renewalView.task.clientId,s.email)}><X className="h-4 w-4"/></button></div></td></tr>}
          </Fragment>;
        }) : <tr><td colSpan={12}><div className="sr-empty !min-h-[260px]"><div className="sr-empty-icon"><Inbox className="h-7 w-7"/></div><p className="mt-3 text-sm text-slate-400">{t.noData}</p></div></td></tr>}</tbody>
      </ResizableDataTable>
    </div>
    <PaginationBar t={t} total={total} page={page} pageSize={pageSize} setPage={setPage} setPageSize={setPageSize} />
    {editing && <SessionEditModal t={t} item={editing} groups={groups} onClose={()=>setEditing(null)} onSaved={()=>{setEditing(null); notify("ok", t.done); void load();}} notify={notify}/>}
    {mailboxForMail && <MailboxMailModal t={t} mailbox={mailboxForMail} onClose={()=>setMailboxForMail(null)} notify={notify}/>}
    {maintenanceOpen && <MaintenanceSettingsModal t={t} notify={notify} onClose={()=>setMaintenanceOpen(false)}/>}
    {failureDetail && <FailureDetailModal t={t} value={failureDetail} onClose={()=>setFailureDetail(null)}/>}
  </Card>;
}

function FailureState({label,detail,onOpen}:{label:string;detail:string;onOpen:(value:{title:string;content:string})=>void}) {
  return <span className="inline-flex items-center justify-center gap-1 font-semibold text-red-500"><span>{label}</span>{detail ? <button type="button" className="sr-failure-detail-button" title={label} onClick={()=>onOpen({title:label,content:detail})}><CircleHelp className="h-4 w-4"/></button> : null}</span>;
}

function FailureDetailModal({t,value,onClose}:{t:typeof zh;value:{title:string;content:string};onClose:()=>void}) {
  return createPortal(<div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal max-w-2xl"><div className="sr-modal-head"><h3>{t.failureDetails} · {value.title}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div><div className="sr-modal-body"><pre className="sr-failure-detail-content">{value.content}</pre></div><div className="sr-modal-foot"><Button variant="outline" onClick={onClose}>{t.close}</Button></div></div></div>,document.body);
}

function MaintenanceSettingsModal({t,notify,onClose}:{t:typeof zh;notify:(type:"ok"|"fail",text:string)=>void;onClose:()=>void}) {
  const [form,setForm]=useState<AnyObj>({health_enabled:true,health_time:"06:00",health_frequency_hours:24,at_enabled:true,at_time:"06:30",at_frequency_hours:24});
  const [loading,setLoading]=useState(true);
  useEffect(()=>{apiFetch("/sunny/maintenance-config").then(setForm).catch((e:any)=>notify("fail",e.message||String(e))).finally(()=>setLoading(false));},[]);
  async function save(){
    setLoading(true);
    try { await apiFetch("/sunny/maintenance-config",{method:"PUT",body:JSON.stringify(form)}); notify("ok",t.restartRequired); onClose(); }
    catch(e:any){notify("fail",e.message||String(e));}
    finally{setLoading(false);}
  }
  const section=(prefix:"health"|"at",title:string)=><div className="sr-maintenance-section"><div className="flex items-center justify-between gap-3"><strong>{title}</strong><button type="button" className={cn("sr-switch-only",form[`${prefix}_enabled`]&&"on")} onClick={()=>setForm({...form,[`${prefix}_enabled`]:!form[`${prefix}_enabled`]})}><span/></button></div><div className="mt-4 grid gap-4 md:grid-cols-2"><div><Label>{t.scheduleTime}</Label><Input type="time" value={form[`${prefix}_time`]||""} onChange={(e)=>setForm({...form,[`${prefix}_time`]:e.target.value})}/></div><div><Label>{t.scheduleFrequency}</Label><Input type="number" min={1} max={720} value={form[`${prefix}_frequency_hours`]||24} onChange={(e)=>setForm({...form,[`${prefix}_frequency_hours`]:Number(e.target.value)})}/></div></div></div>;
  return createPortal(<div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal relative"><ListLoadingOverlay loading={loading} label={t.loadingData}/><div className="sr-modal-head"><h3>{t.maintenanceSettings}</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div><div className="sr-modal-body space-y-4">{section("health",t.healthSchedule)}{section("at",t.atSchedule)}</div><div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" disabled={loading} onClick={save}><Save className="mr-2 h-4 w-4"/>{t.saveSettings}</Button></div></div></div>,document.body);
}

function SessionEditModal({ t, item, groups, onClose, onSaved, notify }: { t: typeof zh; item: AnyObj; groups: AnyObj[]; onClose:()=>void; onSaved:()=>void; notify:(type:"ok"|"fail", text:string)=>void }) {
  const [form,setForm]=useState<AnyObj>({...item});
  const [loading,setLoading]=useState(true);
  useEffect(()=>{
    let active=true;
    setLoading(true);
    apiFetch(`/sunny/sessions/${item.id}`)
      .then((res)=>{if(active)setForm(res||item);})
      .catch((e:any)=>notify("fail",e.message||String(e)))
      .finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[item.id]);
  async function save() {
    if (loading) return;
    try {
      await apiFetch(`/sunny/sessions/${item.id}`, { method:"PUT", body:JSON.stringify({ status:form.status, group_id:Number(form.group_id || 0), plan_type:form.plan_type, trial_eligibility:form.trial_eligibility || "unknown", access_token:form.access_token, refresh_token:form.refresh_token, session_json:form.session_json }) });
      onSaved();
    } catch(e:any) { notify("fail", e.message || String(e)); }
  }
  return createPortal(<div className="sr-modal-mask"><div className="sr-modal sr-mailbox-modal relative">
    <ListLoadingOverlay loading={loading} label={t.loadingData}/>
    <div className="sr-modal-head"><h3>{t.edit} Session</h3><button onClick={onClose}><X className="h-5 w-5"/></button></div>
    <div className="sr-modal-body space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div><Label>{t.email}</Label><Input value={form.email || ""} disabled /></div>
        <div><Label>{t.status}</Label><SelectBox value={form.status||"已注册"} onChange={(v)=>setForm({...form,status:String(v)})} options={SESSION_STATUS_OPTIONS.map((s)=>({value:s,label:t.statusLabels[s as keyof typeof t.statusLabels] || s}))} /></div>
        <div><Label>{t.trialEligibility}</Label><SelectBox value={form.trial_eligibility || "unknown"} onChange={(v)=>setForm({...form,trial_eligibility:String(v)})} options={TRIAL_ELIGIBILITY_OPTIONS.map((value)=>({value,label:trialEligibilityLabel(t,value)}))} /></div>
        <div><Label>{t.mailboxGroup}</Label><SelectBox value={String(form.group_id || "")} onChange={(v)=>setForm({...form,group_id:Number(v)})} options={groups.map((group)=>({value:String(group.id),label:String(group.name || group.id)}))} /></div>
        <div><Label>{t.planType}</Label><SelectBox value={String(form.plan_type || "free")} onChange={(v)=>setForm({...form,plan_type:String(v)})} options={SESSION_PLAN_OPTIONS.map((plan)=>({value:plan,label:formatPlanType(plan)}))} /></div>
      </div>
      <div><Label>{t.accessToken}</Label><Textarea className="min-h-24 rounded-[14px]" value={form.access_token||""} onChange={(e)=>setForm({...form,access_token:e.target.value})}/></div>
      <div><Label>{t.sessionRefreshToken}</Label><Textarea className="min-h-20 rounded-[14px]" value={form.refresh_token||""} onChange={(e)=>setForm({...form,refresh_token:e.target.value})}/></div>
    </div>
    <div className="sr-modal-foot"><button onClick={onClose}>{t.cancel}</button><Button className="ml-3 rounded-xl bg-emerald-600 px-6 !text-white hover:bg-emerald-700" disabled={loading} onClick={save}><Save className="mr-2 h-4 w-4"/>{t.save}</Button></div>
  </div></div>, document.body);
}
function logModuleLabel(t: typeof zh, module: string) {
  const map: Record<string,string> = {
    "Proxy": t.logProxy, "代理": t.logProxy,
    "Mailbox": t.logMailbox, "邮箱": t.logMailbox,
    "Phone": t.logPhone, "手机": t.logPhone,
    "Session": t.logSession,
    "Auth": t.logAuth, "认证": t.logAuth,
    "System": t.logSystem, "系统": t.logSystem,
  };
  return map[module] || module;
}
function logStageLabel(t: typeof zh, value: any) {
  const text = String(value || "");
  if (text === REGISTER_ONLY || /register chatgpt only|register_only|仅注册/i.test(text)) return t.registerOnly;
  if (text === CODEX_PHONE_BIND || /phone binding|codex_phone_bind|接码/i.test(text)) return t.codexPhoneBind;
  if (text === AGENT_IDENTITY_REVERSE_PROXY || /agent identity|agent_identity_reverse_proxy|绕过接码/i.test(text)) return t.agentIdentityReverseProxy;
  if (text === IMPORT_REVERSE_PROXY || /reverse proxy|import_reverse_proxy|反代/i.test(text)) return t.importReverseProxy;
  return text || "-";
}
function localizedLogMessage(t: typeof zh, entry: LogEntry) {
  const enMode = t.workbench === en.workbench;
  const msg = String(entry.message || "");
  const detail = entry.detail || {};
  const stage = logStageLabel(t, detail.stage || "");
  const nums = {
    total: Number(detail.total ?? 0),
    success: Number(detail.success ?? 0),
    failed: Number(detail.failed ?? 0),
    partial: Number(detail.partial ?? 0),
    registered: Number(detail.registered ?? 0),
    loggedIn: Number(detail.logged_in ?? 0),
    skippedPhone: Number(detail.skipped_phone ?? 0),
    imported: Number(detail.imported ?? 0),
  };
  const pick = (zhText: string, enText: string) => enMode ? enText : zhText;
  if (/SunnyRegister Worker accepted register task/i.test(msg)) return pick("SunnyRegister Worker 已接收注册任务", "SunnyRegister Worker accepted the register task");
  if (/task stage|本次任务阶段/i.test(msg)) return pick(`本次任务阶段：${stage}，账号数量：${nums.total || detail.total || "-"}`, `Task stage: ${stage}; accounts: ${nums.total || detail.total || "-"}`);
  if (/register task concurrency|注册任务并发数/i.test(msg)) return pick(`注册任务并发数：${detail.concurrency || "-"}；每个邮箱使用独立 Worker、浏览器上下文和邮箱验证码读取器`, `Register task concurrency: ${detail.concurrency || "-"}; each mailbox uses an isolated worker, browser context and mailbox OTP reader`);
  if (/task summary|注册任务总结/i.test(msg)) return pick(`注册任务总结：成功 ${nums.success}，失败 ${nums.failed}，阶段未完成 ${nums.partial}，新注册 ${nums.registered}，登录更新 ${nums.loggedIn}，跳过接码 ${nums.skippedPhone}，导入反代 ${nums.imported}`, `Register task summary: success ${nums.success}, failed ${nums.failed}, incomplete stages ${nums.partial}, newly registered ${nums.registered}, login refreshed ${nums.loggedIn}, phone skipped ${nums.skippedPhone}, reverse-proxy imported ${nums.imported}`);
  if (/后续接码阶段未完成，账号保留为/.test(msg)) return enMode ? msg.replace("后续接码阶段未完成，账号保留为", "The phone-binding stage was not completed; account status remains ") : msg;
  if (/导入 sub2api 失败，账号保留为/.test(msg)) return enMode ? msg.replace("导入 sub2api 失败，账号保留为", "sub2api import failed; account status remains ") : msg;
  if (/ChatGPT 注册\/登录已经完成，但手机号阶段无法继续/.test(msg)) return pick("ChatGPT 注册/登录已经完成，但手机号阶段无法继续；已保存 Session 并保留已注册状态", "ChatGPT registration/login completed, but the phone stage could not continue; Session was saved and the Registered status was preserved");
  if (/已自动点击 Codex 授权继续按钮/.test(msg)) return pick("已自动点击 Codex 授权继续按钮", "Automatically clicked Continue on the Codex authorization page");
  if (/已捕获 OAuth callback，正在交换 Refresh Token/.test(msg)) return pick("已捕获 OAuth callback，正在交换 Refresh Token", "OAuth callback captured; exchanging it for a Refresh Token");
  if (/已通过页面原生表单提交邮箱验证码/.test(msg)) return pick("已通过页面原生表单提交邮箱验证码", "Email verification code submitted through the page's native form");
  if (/页面未找到可用的验证码提交控件，使用兼容接口提交/.test(msg)) return pick("页面未找到可用的验证码提交控件，使用兼容接口提交", "No usable verification submit control was found; using the compatibility API");
  if (/邮箱验证码提交后出现交互式验证/.test(msg)) return pick("邮箱验证码提交后出现交互式验证，请在当前可视浏览器中完成", "Interactive verification appeared after email code submission; complete it in the visible browser");
  if (/proxy switch|代理.*开关/i.test(msg)) {
    const proxyStats = detail.proxy_stats || {};
    const open = detail.proxy_enabled !== false && !/off|关闭/i.test(msg);
    return pick(`代理池开关：${open ? "开启" : "关闭"}；代理池总数 ${proxyStats.total ?? 0}，启用 ${proxyStats.enabled ?? 0}，停用 ${proxyStats.disabled ?? 0}，失效 ${proxyStats.invalid ?? 0}${open ? "" : "；注册机将使用服务器系统出口"}`, `Proxy switch: ${open ? "on" : "off"}; pool total ${proxyStats.total ?? 0}, enabled ${proxyStats.enabled ?? 0}, disabled ${proxyStats.disabled ?? 0}, invalid ${proxyStats.invalid ?? 0}${open ? "" : "; SunnyRegister will use the server/system network outlet"}`);
  }
  return msg;
}
function progressAccounts(progress: RegistrationTaskProgress | null): AccountRegistrationProgress[] {
  if (!progress) return [];
  return progress.order.map((email) => progress.accounts[email.toLowerCase()]).filter(Boolean);
}

function RegistrationProgressBar({ current, total, tone = "normal" }: { current: number; total: number; tone?: "normal" | "danger" }) {
  const safeTotal = Math.max(1, Number(total || 0));
  const safeCurrent = Math.min(safeTotal, Math.max(0, Number(current || 0)));
  return <div className={cn("sr-registration-progress-track", tone === "danger" && "danger")} aria-valuemin={0} aria-valuemax={safeTotal} aria-valuenow={safeCurrent} role="progressbar">
    <span style={{ width: `${(safeCurrent / safeTotal) * 100}%` }} />
  </div>;
}

function ProgressEmailGroup({ title, accounts, tone = "normal" }: { title: string; accounts: AccountRegistrationProgress[]; tone?: "normal" | "danger" }) {
  return <details className={cn("sr-progress-email-group", tone === "danger" && "danger")}>
    <summary><span>{title}</span><b>{accounts.length}</b></summary>
    <div className="sr-progress-email-list">{accounts.length ? accounts.map((account) => <div key={account.email} title={account.error || account.email}>{account.email}</div>) : <span>-</span>}</div>
  </details>;
}

function TaskRegistrationProgress({ t, progress }: { t: typeof zh; progress: RegistrationTaskProgress | null }) {
  const accounts = progressAccounts(progress);
  if (!progress || !accounts.length) return <div className="sr-progress-empty">{t.noRegistrationTask}</div>;
  const completed = accounts.filter((account) => account.state === "completed");
  const abnormal = accounts.filter((account) => account.state === "abnormal");
  const pending = accounts.filter((account) => account.state !== "completed" && account.state !== "abnormal");
  return <div className="sr-progress-panel">
    <div className="sr-task-progress-labels"><span>{t.taskTotal} <b>{accounts.length}</b></span><span>{t.taskCompleted} <b>{completed.length}</b></span></div>
    <RegistrationProgressBar current={completed.length} total={accounts.length}/>
    <div className="sr-progress-groups">
      <ProgressEmailGroup title={t.completedAccounts} accounts={completed}/>
      <ProgressEmailGroup title={t.pendingAccounts} accounts={pending}/>
      <ProgressEmailGroup title={t.abnormalAccounts} accounts={abnormal} tone="danger"/>
    </div>
  </div>;
}

function AccountRegistrationProgressList({ t, progress }: { t: typeof zh; progress: RegistrationTaskProgress | null }) {
  const accounts = progressAccounts(progress);
  const running = accounts.filter((account) => account.state === "running");
  const visible = (running.length ? running : accounts.filter((account) => account.updatedAt > 0).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 3));
  if (!visible.length) return <div className="sr-progress-empty">{t.noAccountProgress}</div>;
  return <div className="sr-account-progress-list">
    {visible.map((account) => <details key={account.email} className={cn("sr-account-progress-item", account.state === "abnormal" && "danger")}>
      <summary>
        <div className="sr-account-progress-heading"><span title={account.email}>{account.email}</span><b>{account.current}/{account.total}</b></div>
        <RegistrationProgressBar current={account.current} total={account.total} tone={account.state === "abnormal" ? "danger" : "normal"}/>
      </summary>
      <div className="sr-account-progress-step"><span>{t.currentStep}</span><b>{t.progressSteps?.[account.checkpoint] || account.checkpoint}</b>{account.error ? <small title={account.error}>{account.error}</small> : null}</div>
    </details>)}
  </div>;
}

function LogCard({ t, title, progressTitle, view, onView, logs, busy, onClear, progressContent }: { t: typeof zh; title: string; progressTitle: string; view: "progress" | "logs"; onView: (view: "progress" | "logs") => void; logs: LogEntry[]; busy: boolean; onClear: () => void; progressContent: React.ReactNode }) {
  return <Card className="sr-log-card rounded-[30px] p-5">
    <div className="mb-4 flex items-center justify-between gap-3">
      <div className="sr-log-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={view === "progress"} className={cn(view === "progress" && "active")} onClick={()=>onView("progress")}>{progressTitle}</button>
        <button type="button" role="tab" aria-selected={view === "logs"} className={cn(view === "logs" && "active")} onClick={()=>onView("logs")}>{title}</button>
      </div>
      <div className="flex items-center gap-2">
        {view === "logs" ? <button type="button" className="sr-log-clear-btn" onClick={onClear} disabled={!logs.length}>{t.clearLogs}</button> : null}
        {busy?<Loader2 className="h-5 w-5 animate-spin text-[var(--accent)]"/>:<Settings2 className="h-5 w-5 text-[var(--accent)]"/>}
      </div>
    </div>
    {view === "progress" ? progressContent : <div className="log-box sr-register-log rounded-[24px] p-4">
      {logs.length ? logs.map((x)=><div key={x.id} className={cn("sr-log-line", `level-${x.level}`)}>
        <div className="sr-log-meta">
          <span className="sr-log-time">[{x.time}]</span>
          {x.email ? <span className="sr-log-email">[{x.email}]</span> : null}
          <span className="sr-log-module">[{logModuleLabel(t, x.module)}]</span>
        </div>
        <div className="sr-log-message">{localizedLogMessage(t, x)}</div>
      </div>) : <div className="sr-log-empty">{t.noLogs}</div>}
    </div>}
  </Card>;
}
