import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  CheckCircle2,
  CreditCard,
  Download,
  Globe2,
  KeyRound,
  Play,
  RefreshCw,
  RotateCw,
  Search,
  Upload,
} from "lucide-react";
import { API_BASE, apiDownload, apiFetch, triggerBrowserDownload } from "@/lib/utils";
import { useStore } from "./store/useStore";
import { getRuntimeSnapshot, setRuntimeSelection, useRuntime } from "./integration/runtime";
import { CHAIN_PROJECT_BRANCH } from "./views/FlowWorkspaceView";
import { api } from "./api/client";

type Session = Record<string, any>;
type BusyAction = "access-token-check" | "refresh-at" | "health-check" | "subscription-check" | "trial-check" | "checkout-probe" | "payment-probe" | "add-ls" | "rebind" | "sub2-import" | "export" | "acquire-rt" | "chain-start" | null;
type ProgressChain = { chain_id: string; branch: string; email?: string; account_id?: number; index?: number; progress: number; current_log?: string; status: "pending" | "running" | "succeeded" | "failed"; stages?: Record<string, any>; reasonText?: string; reason?: string };
type ChainProgress = { visible: boolean; branch: string; branchIndex: number; branchTotal: number; total: number; done: number; percent: number; success: number; failure: number; status: "idle" | "running" | "success" | "failed"; chains: ProgressChain[] };

const PAGE_SIZES = [10, 20, 50, 100];
const STATUS_OPTIONS = ["未注册", "已注册", "已接码", "已反代", "已封禁", "需二验", "注册中", "登录刷新", "失败", "已取消", "禁用"];
const PLAN_OPTIONS = ["free", "plus", "k12", "team", "pro"];
const TRIAL_OPTIONS = ["unknown", "eligible", "ineligible"];
const CHECKOUT_OPTIONS = ["unknown", "oaics", "cs_live", "cs_test"];
const BOOLEAN_FILTER_OPTIONS = ["present", "missing"];
const TRIAL_COUNTRY_OPTIONS = ["US", "GB", "AU", "VN", "BR", "NL", "IN", "KR", "PL", "CH", "ES", "ID", "PH", "JP"];
const COUNTRY_CURRENCIES: Record<string, string> = {
  AE: "AED", AU: "AUD", BR: "BRL", CA: "CAD", CH: "CHF", ES: "EUR", GB: "GBP",
  ID: "IDR", IN: "INR", JP: "JPY", KR: "KRW", MX: "MXN", NL: "EUR", PH: "PHP",
  PL: "PLN", TH: "THB", TW: "TWD", US: "USD", VN: "VND",
};

function configuredCountries(value: any): string[] {
  const rows = Array.isArray(value) ? value : [];
  return Array.from(new Set(rows.map((item) => String(item || "").trim().toUpperCase()).filter((item) => item && item !== "AUTO")));
}

function chainExecutionSettings(config: Session, branch: string) {
  const branchConfig = config?.chain?.branches?.[branch] || {};
  const checkoutCountries = configuredCountries(branchConfig?.stages?.checkout?.countries);
  const promotionCountries = configuredCountries(
    branchConfig?.stages?.update?.countries
      || branchConfig?.stages?.init?.countries,
  );
  const configuredBilling = String(branchConfig.billing_country || "auto").trim().toUpperCase();
  const country = configuredBilling && configuredBilling !== "AUTO" ? configuredBilling : checkoutCountries[0];
  if (!country) throw new Error(`${branch} 未设置账单国家或 Checkout 出口国家`);
  const currency = String(branchConfig.billing_currency || COUNTRY_CURRENCIES[country] || "").trim().toUpperCase();
  if (!currency) throw new Error(`${branch} 的账单国家 ${country} 没有对应币种`);
  // UI 的“总尝试”包含首次执行；SunnyRegister/Worker 的 retry_count
  // 表示首次失败后的追加轮数，因此这里转换为 attempts - 1。
  const configuredAttempts = Number(branchConfig.attempts);
  const retry = Number.isFinite(configuredAttempts) && configuredAttempts > 0
    ? Math.max(0, Math.min(50, Math.trunc(configuredAttempts) - 1))
    : Math.max(0, Math.min(50, Math.trunc(Number(branchConfig?.stages?.checkout?.retry || 3))));
  const checkoutRouteCountries = checkoutCountries.length ? checkoutCountries : [country];
  const promotionRouteCountries = promotionCountries.length ? promotionCountries : checkoutRouteCountries;
  return {
    country,
    currency,
    retry,
    checkoutCountries: checkoutRouteCountries,
    promotionCountries: promotionRouteCountries,
    usePromo: branchConfig.require_zero !== false,
    checkoutMode: String(branchConfig.checkout_mode || "auto").trim().toLowerCase(),
    chainConfig: branchConfig,
  };
}

function hasAccessToken(session: Session) {
  if (session.has_access_token === true || Number(session.has_access_token) === 1) return true;
  return ["available", "valid", "active", "ok"].includes(String(session.at_status || "").toLowerCase());
}

function atLabel(session: Session) {
  if (!hasAccessToken(session)) return "未绑定";
  const status = String(session.access_token_status || session.at_status || "").toLowerCase();
  if (["invalid", "expired", "renewal_failed", "probe_failed", "probe_blocked"].includes(status)) return "异常";
  return "已保存";
}

function statusLabel(value: any) {
  const key = String(value || "").trim();
  return ({ registered: "已注册", phone_bound: "已接码", reverse_proxied: "已反代", failed: "失败" } as Record<string, string>)[key] || key || "-";
}

function planLabel(value: any) {
  const text = String(value || "").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "-";
}

function trialLabel(value: any) {
  return ({ eligible: "有资格", ineligible: "无资格", unknown: "未检测" } as Record<string, string>)[String(value || "unknown")] || "未检测";
}

function checkoutLabel(value: any) {
  return ({ oaics: "OAICS", cs_live: "CS Live", cs_test: "CS Test", unknown: "未检测" } as Record<string, string>)[String(value || "unknown")] || "未检测";
}

function paymentLabel(value: any) {
  return ({ card: "银行卡", paypal: "PayPal", link: "Link", gcash: "GCash", apple_pay: "Apple Pay", google_pay: "Google Pay", upi: "UPI" } as Record<string, string>)[String(value || "")] || String(value || "-");
}

function formatDateTime(value: any) {
  if (!value) return "-";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function pageNumbers(page: number, pages: number) {
  const start = Math.max(1, Math.min(page - 2, pages - 4));
  return Array.from({ length: Math.min(5, pages) }, (_, index) => start + index);
}

function jwtAccountId(token: string) {
  try {
    const payload = token.split(".")[1];
    if (!payload) return "";
    const value = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - payload.length % 4) % 4)));
    return String(value?.["https://api.openai.com/auth"]?.chatgpt_account_id || value?.account_id || "");
  } catch { return ""; }
}

function runtimeBase(session: Session, accessToken = "", sessionToken = "", tokenId = "") {
  const id = String(session.id);
  return {
    sessionId: id,
    email: String(session.email || session.mailbox || ""),
    accountId: String(session.account_id || session.chatgpt_account_id || jwtAccountId(accessToken) || ""),
    accessToken,
    sessionToken,
    tokenId,
    country: String(session.country || session.country_code || session.exit_country || "").toUpperCase(),
    status: String(session.status || ""),
  };
}

function detectionRecord(session: Session) {
  const records = [
    session.access_token_error && `AT：${session.access_token_error}`,
    session.health_check_error && `测活：${session.health_check_error}`,
    session.trial_check_error && `试用：${session.trial_check_error}`,
    session.commerce_check_error && `Checkout：${session.commerce_check_error}`,
    session.payment_probe_error && `支付：${session.payment_probe_error}`,
  ].filter(Boolean).map(String);
  const checked = [session.access_token_checked_at, session.trial_checked_at, session.commerce_checked_at, session.payment_probed_at, session.last_health_checked_at]
    .filter(Boolean).sort().pop();
  if (records.length) return records.join("；");
  return checked ? `最近检测：${formatDateTime(checked)}` : "暂无检测记录";
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

/**
 * The task API historically returned progress as a `current/total` label,
 * while newer workers may return a numeric percentage or progress_detail.
 * Keep the UI tolerant of all three forms so malformed/missing values never
 * turn the progress bar into NaN.
 */
function taskProgressPercent(task: Session) {
  const detail = task?.progress_detail;
  const detailCurrent = Number(detail?.current);
  const detailTotal = Number(detail?.total);
  if (Number.isFinite(detailCurrent) && Number.isFinite(detailTotal) && detailTotal > 0) {
    return Math.max(0, Math.min(100, detailCurrent / detailTotal * 100));
  }

  const raw = task?.progress;
  if (typeof raw === "string") {
    const match = raw.trim().match(/^(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)$/);
    if (match) {
      const current = Number(match[1]);
      const total = Number(match[2]);
      if (Number.isFinite(current) && Number.isFinite(total) && total > 0) {
        return Math.max(0, Math.min(100, current / total * 100));
      }
    }
  }

  const numeric = Number(raw);
  return Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0;
}

function isTerminalTask(task: Session) {
  const status = String(task?.status || "").trim().toLowerCase();
  return Boolean(task?.terminal) || ["succeeded", "success", "done", "failed", "error", "cancelled", "canceled", "interrupted"].includes(status);
}

function normalizedEmail(value: any) {
  return String(value || "").trim().toLowerCase();
}

function checkoutEventDetail(item: Session) {
  return item?.detail && typeof item.detail === "object" ? item.detail : {};
}

function checkoutEventIdentity(item: Session) {
  const detail = checkoutEventDetail(item);
  return {
    email: normalizedEmail(item.email || detail.email || detail.result?.email),
    accountId: Number(item.account_id || detail.account_id || detail.result?.account_id || 0),
    index: Number(detail.index ?? detail.result?.index ?? -1),
  };
}

function progressChainMatches(chain: ProgressChain, identity: { email: string; accountId: number; index: number }) {
  if (identity.accountId > 0 && Number(chain.account_id || 0) === identity.accountId) return true;
  if (identity.email && normalizedEmail(chain.email) === identity.email) return true;
  return identity.index >= 0 && Number(chain.index ?? -1) === identity.index;
}

function summarizeProgressChains(chains: ProgressChain[]) {
  const done = chains.filter((chain) => chain.status === "succeeded" || chain.status === "failed").length;
  const success = chains.filter((chain) => chain.status === "succeeded").length;
  const failure = chains.filter((chain) => chain.status === "failed").length;
  const percent = chains.length
    ? Math.round(chains.reduce((sum, chain) => sum + Math.max(0, Math.min(100, Number(chain.progress) || 0)), 0) / chains.length)
    : 0;
  return { done, success, failure, percent };
}

function sessionChainProgress(session: Session, progress: ChainProgress) {
  const identity = { email: normalizedEmail(session.email || session.mailbox), accountId: Number(session.id || session.account_id || 0), index: -1 };
  const chains = progress.chains.filter((chain) => progressChainMatches(chain, identity));
  if (!chains.length) return null;
  const percent = Math.round(chains.reduce((sum, chain) => sum + Math.max(0, Math.min(100, Number(chain.progress) || 0)), 0) / chains.length);
  const active = chains.find((chain) => chain.branch === progress.branch && chain.status === "running")
    || [...chains].reverse().find((chain) => chain.status === "failed")
    || [...chains].reverse().find((chain) => chain.status !== "pending")
    || chains[0];
  const status = chains.some((chain) => chain.status === "running")
    ? "running"
    : chains.every((chain) => chain.status === "succeeded")
      ? "succeeded"
      : chains.some((chain) => chain.status === "failed")
        ? "failed"
        : "pending";
  return { progress: percent, message: active.current_log || "等待提链任务", status };
}

function tokenSessionId(token: Session) {
  return String(token.session_id || token.sessionId || token.sunny_session_id || "").trim();
}

function tokenMatchesSession(token: Session, session: Session) {
  const directSessionId = tokenSessionId(token);
  if (directSessionId && directSessionId === String(session.id)) return true;
  const tokenEmail = normalizedEmail(token.email || token.mailbox);
  const sessionEmail = normalizedEmail(session.email || session.mailbox);
  if (tokenEmail && sessionEmail && tokenEmail === sessionEmail) return true;
  const tokenAccountId = String(token.account_id || token.chatgpt_account_id || "").trim();
  const sessionAccountId = String(session.chatgpt_account_id || session.openai_account_id || "").trim();
  return Boolean(tokenAccountId && sessionAccountId && tokenAccountId === sessionAccountId);
}

export default function FreeppAccountPicker() {
  const setView = useStore((state) => state.setView);
  const tokens = useStore((state) => state.tokens);
  const selectedTokenIds = useStore((state) => state.selectedTokenIds);
  const selectedProjects = useStore((state) => state.selectedWorkspaceProjects);
  const toggleTokenSelect = useStore((state) => state.toggleTokenSelect);
  const pushLog = useStore((state) => state.pushLog);
  const [items, setItems] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [knownSessions, setKnownSessions] = useState<Record<string, Session>>({});
  const [manualSessionIds, setManualSessionIds] = useState<Set<string>>(new Set());
  const sessionLookupInFlight = useRef(new Set<string>());
  const [groups, setGroups] = useState<Session[]>([]);
  const [query, setQuery] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [planFilter, setPlanFilter] = useState("");
  const [trialFilter, setTrialFilter] = useState("");
  const [trialCountryFilter, setTrialCountryFilter] = useState("");
  const [checkoutFilter, setCheckoutFilter] = useState("");
  const [paymentFilter, setPaymentFilter] = useState("");
  const [loginSecretFilter, setLoginSecretFilter] = useState("");
  const [rebindEmailFilter, setRebindEmailFilter] = useState("");
  const [paymentOptions, setPaymentOptions] = useState<string[]>([]);
  const [trialCountryOptions, setTrialCountryOptions] = useState<string[]>(TRIAL_COUNTRY_OPTIONS);
  const [rebindOpen, setRebindOpen] = useState(false);
  const [rebindMailboxes, setRebindMailboxes] = useState<Session[]>([]);
  const [rebindSelected, setRebindSelected] = useState<string[]>([]);
  const [rebindLoading, setRebindLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [message, setMessage] = useState("");
  const [chainProgress, setChainProgress] = useState<ChainProgress>({ visible: false, branch: "", branchIndex: 0, branchTotal: 0, total: 0, done: 0, percent: 0, success: 0, failure: 0, status: "idle", chains: [] });
  const runtime = useRuntime();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), sort_by: "last_health_checked_at", sort_order: "desc" });
      if (query.trim()) params.set("q", query.trim());
      if (groupFilter) params.set("group_id", groupFilter);
      if (statusFilter) params.set("status", statusFilter);
      if (planFilter) params.set("plan_type", planFilter);
      if (trialFilter) params.set("trial_eligibility", trialFilter);
      if (trialCountryFilter) params.set("trial_countries", trialCountryFilter);
      if (checkoutFilter) params.set("checkout_kind", checkoutFilter);
      if (paymentFilter) params.set("payment_methods", paymentFilter);
      if (loginSecretFilter) params.set("login_secret", loginSecretFilter);
      if (rebindEmailFilter) params.set("rebind_email", rebindEmailFilter);
      const result = await apiFetch(`/sunny/sessions?${params.toString()}`);
      const nextItems = Array.isArray(result.items) ? result.items : [];
      setItems(nextItems);
      setTotal(Number(result.total || 0));
      setPaymentOptions((current) => Array.from(new Set([...current, ...(Array.isArray(result.payment_method_options) ? result.payment_method_options.map(String) : [])])));
      if (Array.isArray(result.trial_country_options)) {
        setTrialCountryOptions((current) => Array.from(new Set([...current, ...result.trial_country_options.map(String).map((value: string) => value.toUpperCase())])));
      }
      setKnownSessions((current) => ({ ...current, ...Object.fromEntries(nextItems.map((item: Session) => [String(item.id), item])) }));
    } catch (error) {
      setMessage(`账号列表加载失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, query, groupFilter, statusFilter, planFilter, trialFilter, trialCountryFilter, checkoutFilter, paymentFilter, loginSecretFilter, rebindEmailFilter]);

  useEffect(() => { void load(); }, [load]);
  // 账户管理与提炼工作台共用 SunnyRegister 数据源；定时只读取列表状态，
  // 不会再次触发 AT、测活或支付检测任务。
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") void load();
    };
    const timer = window.setInterval(refresh, 8_000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);
  useEffect(() => { setPage(1); }, [query, groupFilter, statusFilter, planFilter, trialFilter, trialCountryFilter, checkoutFilter, paymentFilter, loginSecretFilter, rebindEmailFilter, pageSize]);
  useEffect(() => {
    void apiFetch("/sunny/mailbox-groups").then((result) => setGroups(Array.isArray(result?.items) ? result.items : [])).catch(() => setGroups([]));
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  useEffect(() => { if (page > totalPages) setPage(totalPages); }, [page, totalPages]);

  const selectedTokens = useMemo(
    () => tokens.filter((token) => selectedTokenIds.has(String(token.id))),
    [tokens, selectedTokenIds],
  );

  // Token 库是提链的唯一选择源。当前页之外的账号按邮箱补查一次，
  // 这样分页或筛选变化不会让已选 Token 丢失对应会话。
  useEffect(() => {
    const known = Object.values(knownSessions);
    const unresolved = selectedTokens.filter((token) => {
      const directId = tokenSessionId(token);
      return !known.some((session) => (directId && directId === String(session.id)) || tokenMatchesSession(token, session));
    }).filter((token) => normalizedEmail(token.email || (token as Session).mailbox));
    if (!unresolved.length) return;
    let cancelled = false;
    void Promise.all(unresolved.map(async (token) => {
      const tokenId = String(token.id);
      if (sessionLookupInFlight.current.has(tokenId)) return [] as Session[];
      sessionLookupInFlight.current.add(tokenId);
      try {
        const email = normalizedEmail(token.email || (token as Session).mailbox);
        const result = await apiFetch(`/sunny/sessions?q=${encodeURIComponent(email)}&page=1&page_size=100`);
        return Array.isArray(result?.items) ? result.items as Session[] : [];
      } catch {
        return [] as Session[];
      } finally {
        sessionLookupInFlight.current.delete(tokenId);
      }
    })).then((rows) => {
      if (cancelled) return;
      const additions = rows.flat().filter((session) => session && session.id != null);
      if (!additions.length) return;
      setKnownSessions((current) => ({
        ...current,
        ...Object.fromEntries(additions.map((session) => [String(session.id), session])),
      }));
    });
    return () => { cancelled = true; };
  }, [selectedTokens, knownSessions]);

  const selectedSessions = useMemo(() => {
    const known = Object.values(knownSessions);
    const tokenSessions = selectedTokens.map((token) => {
      const directId = tokenSessionId(token);
      return known.find((session) => (directId && directId === String(session.id)) || tokenMatchesSession(token, session));
    }).filter((session): session is Session => Boolean(session));
    const manualSessions = Array.from(manualSessionIds).map((id) => known.find((session) => String(session.id) === id)).filter((session): session is Session => Boolean(session));
    const byId = new Map<string, Session>();
    [...tokenSessions, ...manualSessions].forEach((session) => byId.set(String(session.id), session));
    return Array.from(byId.values());
  }, [knownSessions, selectedTokens, manualSessionIds]);
  const selected = useMemo(() => Array.from(new Set(selectedSessions.map((session) => String(session.id)))), [selectedSessions]);

  useEffect(() => {
    const current = getRuntimeSnapshot().accounts;
    const accounts = selectedSessions.map((session) => {
      const id = String(session.id);
      const previous = current.find((item) => item.sessionId === id);
      const token = selectedTokens.find((item) => tokenMatchesSession(item, session));
      return {
        ...runtimeBase(session, previous?.accessToken || "", previous?.sessionToken || "", String(token?.id || previous?.tokenId || "")),
        ...(previous || {}),
        sessionId: id,
        tokenId: String(token?.id || previous?.tokenId || ""),
      };
    });
    setRuntimeSelection(selected, accounts);
  }, [selected, selectedSessions, selectedTokens]);

  const pageSelected = items.length > 0 && items.every((item) => selected.includes(String(item.id)));

  function tokenForSession(session: Session) {
    return tokens.find((token) => selectedTokenIds.has(String(token.id)) && tokenMatchesSession(token, session))
      || tokens.find((token) => tokenMatchesSession(token, session));
  }

  function toggle(session: Session) {
    const token = tokenForSession(session);
    const id = String(session.id);
    setKnownSessions((current) => ({ ...current, [id]: session }));
    if (token) {
      toggleTokenSelect(String(token.id));
      setManualSessionIds((current) => {
        if (!current.has(id)) return current;
        const next = new Set(current);
        next.delete(id);
        return next;
      });
      return;
    }
    setManualSessionIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function togglePage() {
    items.forEach((item) => {
      const token = tokenForSession(item);
      const id = String(item.id);
      const isSelected = selected.includes(id);
      if (pageSelected === isSelected) {
        if (token) toggleTokenSelect(String(token.id));
        else setManualSessionIds((current) => {
          const next = new Set(current);
          if (next.has(id)) next.delete(id); else next.add(id);
          return next;
        });
      }
    });
    setKnownSessions((current) => ({ ...current, ...Object.fromEntries(items.map((item) => [String(item.id), item])) }));
  }

  async function pollTask(taskId: string) {
    for (let attempt = 0; attempt < 180; attempt += 1) {
      const task = await apiFetch(`/tasks/${encodeURIComponent(taskId)}`);
      const status = String(task.status || "").toLowerCase();
      if (task.terminal || ["succeeded", "failed", "cancelled", "canceled", "interrupted"].includes(status)) return task;
      await wait(700);
    }
    throw new Error("任务仍在后台执行，请稍后刷新查看结果");
  }

  async function waitForSunnyCheckout(taskId: string, branch: string, branchIndex: number, branchTotal: number, accountTotal: number) {
    let eventCursor = 0;
    const processedEventIds = new Set<number>();
    let stream: EventSource | null = null;

    const applyEvents = (events: Session[]) => {
      const ordered = [...events].sort((left, right) => Number(left?.id || 0) - Number(right?.id || 0));
      const fresh = ordered.filter((item) => {
        const eventId = Number(item?.id || 0);
        if (eventId <= 0) return true;
        if (processedEventIds.has(eventId)) return false;
        processedEventIds.add(eventId);
        eventCursor = Math.max(eventCursor, eventId);
        return true;
      });
      if (!fresh.length) return;
      setChainProgress((current) => {
        const chains = [...current.chains];
        for (const item of fresh) {
          if (item.type !== "checkout_progress" && item.type !== "checkout_result") continue;
          const detail = checkoutEventDetail(item);
          const identity = checkoutEventIdentity(item);
          const chainIndex = chains.findIndex((chain) => chain.branch === branch && progressChainMatches(chain, identity));
          if (chainIndex < 0) continue;
          const previous = chains[chainIndex];
          if (item.type === "checkout_progress" && (previous.status === "succeeded" || previous.status === "failed")) continue;
          const result = detail.result && typeof detail.result === "object" ? detail.result : {};
          const resultStatus = String(result.status || "").trim().toLowerCase();
          const succeeded = ["succeeded", "success", "done"].includes(resultStatus);
          const eventProgress = item.type === "checkout_result" ? 100 : Math.max(0, Math.min(100, Number(detail.progress ?? item.progress ?? 0) || 0));
          const terminalLog = item.type === "checkout_result"
            ? String(result.error || result.reason || result.message || item.message || (succeeded ? "提链成功" : "提链失败"))
            : "";
          const currentLog = String(
            detail.current_log
              || terminalLog
              || item.message
              || previous.current_log
              || (succeeded ? "提链成功" : "提链处理中"),
          );
          chains[chainIndex] = {
            ...previous,
            progress: Math.max(previous.progress, eventProgress),
            current_log: currentLog,
            status: item.type === "checkout_result" ? (succeeded ? "succeeded" : "failed") : "running",
            reason: item.type === "checkout_result" && !succeeded ? currentLog : previous.reason,
          };
        }
        const summary = summarizeProgressChains(chains);
        return { ...current, ...summary, visible: true, branch, branchIndex, branchTotal, total: branchTotal * accountTotal, status: "running", chains };
      });
    };

    const readEvents = async () => {
      let readCursor = eventCursor;
      const collected: Session[] = [];
      for (let eventPage = 0; eventPage < 5; eventPage += 1) {
        const data = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/events?since=${readCursor}&limit=200`);
        const next = Array.isArray(data?.items) ? data.items as Session[] : [];
        if (!next.length) break;
        collected.push(...next);
        readCursor = Number(next[next.length - 1]?.id || readCursor);
        if (next.length < 200) break;
      }
      applyEvents(collected);
    };

    try {
      const apiBase = String(API_BASE || "/api").replace(/\/$/, "");
      stream = new EventSource(`${apiBase}/tasks/${encodeURIComponent(taskId)}/logs/stream?since=0`, { withCredentials: true });
      stream.onmessage = (message) => {
        try {
          const payload = JSON.parse(message.data || "{}");
          if (!payload.done) applyEvents([payload]);
        } catch { /* polling below remains the fallback */ }
      };
      stream.onerror = () => { stream?.close(); stream = null; };

      for (let attempt = 0; attempt < 300; attempt += 1) {
        const task = await apiFetch(`/tasks/${encodeURIComponent(taskId)}`);
        await readEvents().catch(() => {});
        if (isTerminalTask(task)) {
          await readEvents().catch(() => {});
          const results = Array.isArray(task?.result?.items) ? task.result.items as Session[] : [];
          applyEvents(results.map((result) => ({
            type: "checkout_result",
            email: result.email,
            account_id: result.account_id,
            detail: { email: result.email, account_id: result.account_id, index: result.index, progress: 100, result },
          })));
          setChainProgress((current) => {
            const chains = current.chains.map((chain) => chain.branch === branch && (chain.status === "pending" || chain.status === "running")
              ? { ...chain, progress: 100, current_log: String(task?.error || "任务结束但未返回账号结果"), status: "failed" as const, reason: String(task?.error || "任务结束但未返回账号结果") }
              : chain);
            return { ...current, ...summarizeProgressChains(chains), chains };
          });
          return task;
        }
        await wait(700);
      }
      throw new Error("提链任务状态等待超时，请到提链管理查看");
    } finally {
      stream?.close();
    }
  }

  async function startSelectedChains() {
    if (!selectedSessions.length && !selectedTokenIds.size) {
      setMessage("请先在账号 AT 区域勾选账号");
      return;
    }
    const projects = Array.from(selectedProjects);
    if (!projects.length) {
      setMessage("请先在上方项目卡片勾选提链项目");
      return;
    }
    const branches = Array.from(new Set(projects.map((project) => CHAIN_PROJECT_BRANCH[project]).filter((branch): branch is NonNullable<typeof branch> => Boolean(branch))));
    const unsupported = projects.filter((project) => !CHAIN_PROJECT_BRANCH[project]);
    if (!branches.length) {
      setMessage("当前勾选项目没有可直接提链的分支");
      return;
    }
    setBusy("chain-start");
    const failures: string[] = [];
    let started = 0;
    try {
      const checkoutSessions = selectedSessions.filter((session) => Number.isFinite(Number(session.id)) && Number(session.id) > 0);
      const sessionIds = checkoutSessions.map((session) => Number(session.id));
      if (!sessionIds.length) {
        setMessage("所选账号没有可用会话，无法启动提链");
        return;
      }
      const enabledProxies = runtime.proxies.filter((proxy) => proxy.enabled && proxy.address);
      if (!enabledProxies.length) {
        setMessage("代理池为空，请先在代理配置中启用代理");
        return;
      }
      const projectConfig = await api<Session>("/api/config");
      const initialChains = branches.flatMap((branch) => checkoutSessions.map((session, index) => ({
        chain_id: `${branch}:${String(session.id)}`,
        branch,
        email: String(session.email || session.mailbox || ""),
        account_id: Number(session.id || 0),
        index,
        progress: 0,
        current_log: "等待提链任务",
        status: "pending" as const,
      })));
      setChainProgress({
        visible: true,
        branch: branches[0],
        branchIndex: 0,
        branchTotal: branches.length,
        total: initialChains.length,
        done: 0,
        percent: 0,
        success: 0,
        failure: 0,
        status: "running",
        chains: initialChains,
      });
      setMessage(`正在启动 ${branches.length} 个提链项目（${sessionIds.length} 个账号）...`);
      for (const [branchIndex, branch] of branches.entries()) {
        try {
          const execution = chainExecutionSettings(projectConfig, branch);
          const checkoutProxies = enabledProxies.filter((proxy) => execution.checkoutCountries.includes(String(proxy.country || "").trim().toUpperCase()));
          const promotionProxies = enabledProxies.filter((proxy) => execution.promotionCountries.includes(String(proxy.country || "").trim().toUpperCase()));
          if (!checkoutProxies.length) {
            throw new Error(`${branch} 配置要求 Checkout 出口 ${execution.checkoutCountries.join("/")}，但代理池中没有对应国家的已启用代理`);
          }
          if (!promotionProxies.length) {
            throw new Error(`${branch} 配置要求 Promotion 出口 ${execution.promotionCountries.join("/")}，但代理池中没有对应国家的已启用代理`);
          }
          const checkoutPool = checkoutProxies.map((proxy) => proxy.address).join("\n");
          const promotionPool = promotionProxies.map((proxy) => proxy.address).join("\n");
          setChainProgress((current) => ({
            ...current,
            visible: true,
            branch,
            branchIndex,
            branchTotal: branches.length,
            status: "running",
            chains: current.chains.map((chain) => chain.branch === branch && chain.status === "pending" ? { ...chain, current_log: "正在创建提链任务" } : chain),
          }));
          const result = await apiFetch("/sunny/checkout", { method: "POST", body: JSON.stringify({
            system_at: true,
            session_ids: sessionIds,
            external_ats: [],
            checkout_kinds: [],
            checkout_proxies: checkoutPool,
            promotion_proxies: promotionPool,
            plan: "plus",
            link_type: branch,
            country: execution.country,
            currency: execution.currency,
            retry_count: execution.retry,
            concurrency: Math.min(10, sessionIds.length),
            use_promo: execution.usePromo,
            promo_country: execution.promotionCountries[0] || execution.country,
            checkout_mode: execution.checkoutMode,
            chain_config: execution.chainConfig,
          }) });
          if (result?.error) throw new Error(String(result.error));
          const taskId = String(result?.id || result?.task_id || "");
          if (!taskId) throw new Error("提链任务未返回任务编号");
          await waitForSunnyCheckout(taskId, branch, branchIndex, branches.length, sessionIds.length);
          started += 1;
          pushLog(`${branch} 提链启动：${sessionIds.length} 个账号`, "ok");
        } catch (error) {
          const reason = error instanceof Error ? error.message : String(error);
          failures.push(`${branch}: ${reason}`);
          setChainProgress((current) => {
            const chains = current.chains.map((chain) => chain.branch === branch && (chain.status === "pending" || chain.status === "running")
              ? { ...chain, progress: 100, current_log: reason, reason, status: "failed" as const }
              : chain);
            return { ...current, ...summarizeProgressChains(chains), chains };
          });
          pushLog(`${branch} 提链启动失败：${reason}`, "err");
        }
      }
      setChainProgress((current) => ({ ...current, status: current.failure > 0 ? "failed" : "success" }));
      const skipped = unsupported.length ? `，已跳过 ${unsupported.length} 个非提链项目` : "";
      setMessage(`已启动 ${started}/${branches.length} 个提链项目${skipped}${failures.length ? `；失败：${failures.join("；")}` : ""}`);
      if (unsupported.length) pushLog(`已跳过 ${unsupported.length} 个支付授权项目`, "info");
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      setMessage(`开始提链失败：${reason}`);
      pushLog(`开始提链失败：${reason}`, "err");
    } finally {
      setBusy(null);
    }
  }

  async function runTask(action: Exclude<BusyAction, null>, endpoint: string, ids = selected, body: Session = {}) {
    const sessionIds = Array.from(new Set(ids.map(String).map(Number).filter(Boolean)));
    if (!sessionIds.length) { setMessage("请先选择账号"); return; }
    setBusy(action);
    setMessage(`正在执行 ${action === "access-token-check" ? "AT 检测" : action === "refresh-at" ? "AT 续期" : action === "health-check" ? "测活" : action === "subscription-check" ? "订阅检测" : action === "trial-check" ? "试用检测" : action === "checkout-probe" ? "Checkout 探测" : action === "payment-probe" ? "支付探测" : action === "add-ls" ? "添加 LS" : action === "rebind" ? "邮箱换绑" : action}（${sessionIds.length} 个账号）...`);
    try {
      const created = await apiFetch(endpoint, { method: "POST", body: JSON.stringify({ session_ids: sessionIds, ...body }) });
      const taskId = String(created?.id || created?.task_id || created?.task?.id || "");
      if (taskId) await pollTask(taskId);
      await load();
      setMessage("任务完成，账号状态已刷新");
    } catch (error) {
      setMessage(`操作失败：${error instanceof Error ? error.message : String(error)}`);
    } finally { setBusy(null); }
  }

  async function runCountryTask(action: "trial-check" | "payment-probe", endpoint: string) {
    try {
      const response = await apiFetch(`/sunny/sessions/${action === "trial-check" ? "trial-check" : "payment-probe"}/countries`);
      const countries = Array.from(new Set((Array.isArray(response?.countries) ? response.countries : []).map((value: any) => String(value).trim().toUpperCase()).filter((value: string) => /^[A-Z]{2}$/.test(value))));
      if (!countries.length) throw new Error("没有可用的地区代理");
      await runTask(action, endpoint, selected, { countries });
    } catch (error) { setMessage(`地区列表加载失败：${error instanceof Error ? error.message : String(error)}`); }
  }

  async function exportAccounts() {
    if (!selected.length) { setMessage("请先选择账号"); return; }
    setBusy("export");
    try {
      const { blob, filename } = await apiDownload("/sunny/sessions/export", { method: "POST", body: JSON.stringify({ format: "all", session_ids: selected.map(Number) }) });
      triggerBrowserDownload(blob, filename);
      setMessage("账号检测记录已导出");
    } catch (error) { setMessage(`导出失败：${error instanceof Error ? error.message : String(error)}`); }
    finally { setBusy(null); }
  }

  async function copyField(session: Session, field: string) {
    try {
      const result = await apiFetch(`/sunny/sessions/${encodeURIComponent(String(session.id))}/field?name=${field}`);
      const value = String(result.value || "").trim();
      if (!value) throw new Error(`${field.toUpperCase()} 为空`);
      await navigator.clipboard.writeText(value);
      setMessage(`${field.toUpperCase()} 已复制`);
    } catch (error) { setMessage(`读取失败：${error instanceof Error ? error.message : String(error)}`); }
  }

  async function acquireRefreshToken(session: Session) {
    await runTask("acquire-rt", "/sunny/tasks/acquire-rt", [String(session.id)], { execution_mode: "background", concurrency: 1 });
  }

  async function openRebindDialog() {
    if (!selected.length) { setMessage("请先选择账号"); return; }
    setRebindOpen(true);
    setRebindSelected([]);
    setRebindLoading(true);
    try {
      const result = await apiFetch("/sunny/mailboxes?page=1&page_size=100");
      const rows = Array.isArray(result?.items) ? [...result.items] : [];
      const pages = Math.ceil(Math.min(Number(result?.total || rows.length), 5000) / 100);
      if (pages > 1) {
        const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, index) => apiFetch(`/sunny/mailboxes?page=${index + 2}&page_size=100`)));
        rest.forEach((pageResult) => { if (Array.isArray(pageResult?.items)) rows.push(...pageResult.items); });
      }
      const options = rows.map((item: Session) => {
        let mailboxApi = String(item.access_key || item.rebind_mailbox_api || "").trim();
        if (!mailboxApi && String(item.raw || "").includes("----")) mailboxApi = String(item.raw).split("----").slice(1).join("----").trim();
        return { ...item, mailbox_api: mailboxApi };
      }).filter((item: Session) => item.enabled !== false && String(item.email || "").includes("@") && String(item.mailbox_api || "").trim());
      setRebindMailboxes(options);
    } catch (error) {
      setRebindMailboxes([]);
      setMessage(`换绑邮箱池加载失败：${error instanceof Error ? error.message : String(error)}`);
    } finally { setRebindLoading(false); }
  }

  async function submitRebind() {
    const targets = rebindMailboxes.filter((item) => rebindSelected.includes(String(item.email)));
    const body = targets.length ? {
      rebind_source: "imported",
      target_email: String(targets[0].email),
      target_mailbox_api: String(targets[0].mailbox_api),
      target_mailbox_type: String(targets[0].mailbox_type || "domain"),
      target_mailbox_channel: String(targets[0].mailbox_channel || "domain_api"),
      target_mailboxes: targets.map((item) => ({ email: String(item.email), mailbox_api: String(item.mailbox_api), mailbox_type: String(item.mailbox_type || "domain"), mailbox_channel: String(item.mailbox_channel || "domain_api") })),
    } : { rebind_source: "self" };
    setRebindOpen(false);
    await runTask("rebind", "/sunny/sessions/rebind", selected, body);
  }

  const operationBusy = busy !== null;
  const selectedCount = Math.max(selected.length, selectedTokenIds.size);
  return (
    <section className="freepp-account-picker" aria-label="账号 AT">
      <div className="freepp-account-picker-head">
        <div>
          <h2>账号 AT</h2>
          <p>与 SunnyRegister 账号管理共用数据源，选择账号后直接用于提链。</p>
        </div>
        <div className="freepp-account-picker-actions">
          <span>已选 {selectedCount} 项</span>
          <span>提链项目 {selectedProjects.size} 项</span>
          <span className="freepp-runtime-summary">运行时：{runtime.accounts.filter((item) => item.accessToken).length} AT · {runtime.proxies.length} 代理</span>
          <button className="btn" type="button" onClick={() => void load()} disabled={loading || operationBusy} title="刷新账号列表"><RefreshCw className={loading ? "spin" : ""} />刷新</button>
          <button className="btn btn-primary" type="button" onClick={() => void startSelectedChains()} disabled={(!selected.length && !selectedTokenIds.size) || !selectedProjects.size || operationBusy}><Play />开始提链</button>
        </div>
      </div>
      <div className="freepp-account-filters">
        <label className="freepp-account-search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索邮箱或换绑邮箱..." /></label>
        <select value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}><option value="">全部分组</option>{groups.map((group) => <option key={String(group.id)} value={String(group.id)}>{String(group.name || group.group_name || `分组 ${group.id}`)}</option>)}</select>
        <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部状态</option>{STATUS_OPTIONS.map((status) => <option value={status} key={status}>{status}</option>)}</select>
        <select value={planFilter} onChange={(event) => setPlanFilter(event.target.value)}><option value="">套餐类型</option>{PLAN_OPTIONS.map((plan) => <option value={plan} key={plan}>{planLabel(plan)}</option>)}</select>
        <select value={rebindEmailFilter} onChange={(event) => setRebindEmailFilter(event.target.value)}><option value="">换绑邮箱</option>{BOOLEAN_FILTER_OPTIONS.map((value) => <option value={value} key={value}>{value === "present" ? "已设置" : "未设置"}</option>)}</select>
        <select value={loginSecretFilter} onChange={(event) => setLoginSecretFilter(event.target.value)}><option value="">录密钥</option>{BOOLEAN_FILTER_OPTIONS.map((value) => <option value={value} key={value}>{value === "present" ? "已录入" : "未录入"}</option>)}</select>
        <select value={trialFilter} onChange={(event) => setTrialFilter(event.target.value)}><option value="">全部试用资格</option>{TRIAL_OPTIONS.map((trial) => <option value={trial} key={trial}>{trialLabel(trial)}</option>)}</select>
        <select value={trialCountryFilter} onChange={(event) => setTrialCountryFilter(event.target.value)}><option value="">试用地区</option>{trialCountryOptions.map((country) => <option value={country} key={country}>{country}</option>)}</select>
        <select value={checkoutFilter} onChange={(event) => setCheckoutFilter(event.target.value)}><option value="">全部 Checkout</option>{CHECKOUT_OPTIONS.map((kind) => <option value={kind} key={kind}>{checkoutLabel(kind)}</option>)}</select>
        <select value={paymentFilter} onChange={(event) => setPaymentFilter(event.target.value)}><option value="">全部支付方式</option>{paymentOptions.map((method) => <option value={method} key={method}>{paymentLabel(method)}</option>)}</select>
      </div>
      <div className="freepp-account-toolbar">
        <button className="btn btn-sm" type="button" onClick={togglePage} disabled={!items.length || operationBusy}>{pageSelected ? "清除选择" : "全选"}</button>
        <span>选中 {selectedCount} 项</span>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runTask("access-token-check", "/sunny/sessions/access-token-check")} disabled={!selected.length || operationBusy}><RefreshCw />AT 检测</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runTask("refresh-at", "/sunny/tasks/refresh-session")} disabled={!selected.length || operationBusy}><RefreshCw />续期</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runTask("health-check", "/sunny/sessions/health-check")} disabled={!selected.length || operationBusy}><Activity />测活</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runTask("subscription-check", "/sunny/sessions/subscription-check")} disabled={!selected.length || operationBusy}><CheckCircle2 />订阅</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runCountryTask("trial-check", "/sunny/sessions/trial-check")} disabled={!selected.length || operationBusy}><Globe2 />试用</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runTask("checkout-probe", "/sunny/sessions/checkout-probe")} disabled={!selected.length || operationBusy}><Globe2 />Checkout 探测</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runCountryTask("payment-probe", "/sunny/sessions/payment-probe")} disabled={!selected.length || operationBusy}><CreditCard />支付探测</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runTask("sub2-import", "/sunny/tasks/sub2-import")} disabled={!selected.length || operationBusy}><Upload />反代</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void runTask("add-ls", "/sunny/tasks/add-ls", selected, { execution_mode: "protocol", protocol_challenge_strategy: "sentinel_protocol" })} disabled={!selected.length || operationBusy}><KeyRound />添加 LS</button>
        <button className="btn btn-sm btn-blue" type="button" onClick={() => void openRebindDialog()} disabled={!selected.length || operationBusy}><RotateCw />换绑</button>
        <button className="btn btn-sm" type="button" onClick={() => void exportAccounts()} disabled={!selected.length || operationBusy}><Download />导出</button>
      </div>
      {runtime.lastError && <div className="freepp-account-message">代理池同步提示：{runtime.lastError}</div>}
      {message && <div className="freepp-account-message">{message}</div>}
      {chainProgress.visible && <div className={`freepp-chain-progress ${chainProgress.status}`}>
        <div className="freepp-chain-progress-head"><span>{chainProgress.status === "running" ? "提链进行中" : chainProgress.status === "success" ? "提链完成" : "提链结束"} · {chainProgress.branch || "本地任务"}{chainProgress.branchTotal > 1 ? `（${chainProgress.branchIndex + 1}/${chainProgress.branchTotal}）` : ""}</span><strong>{chainProgress.percent}%</strong></div>
        <div className="freepp-chain-progress-track"><span style={{ width: `${Math.max(0, Math.min(100, chainProgress.percent))}%` }} /></div>
        <small>完成 {chainProgress.done} / {chainProgress.total} · 成功 {chainProgress.success} · 失败 {chainProgress.failure}</small>
      </div>}
      <div className="freepp-account-table-wrap">
        <table className="freepp-account-table">
          <thead><tr><th><input type="checkbox" aria-label="选择当前页账号" checked={pageSelected} onChange={togglePage} disabled={!items.length || operationBusy} /></th><th>邮箱</th><th>换绑邮箱</th><th>所属分组</th><th>状态</th><th>套餐类型</th><th>LS</th><th>SK</th><th>AT</th><th>RT</th><th>试用资格</th><th>Checkout</th><th>支付方式</th><th>AT 过期时间</th><th>最近测活</th><th>检测记录</th></tr></thead>
          <tbody>
            {items.map((session) => {
              const id = String(session.id);
              const selectedRow = selected.includes(id);
              const atAvailable = hasAccessToken(session);
              const payments = Array.isArray(session.payment_methods) ? session.payment_methods : [];
              const liveChain = chainProgress.visible ? sessionChainProgress(session, chainProgress) : null;
              return <tr key={id} className={selectedRow ? "selected" : ""}>
                <td><input type="checkbox" checked={selectedRow} disabled={operationBusy} onChange={() => toggle(session)} aria-label={`选择 ${session.email || id}`} /></td>
                <td className="freepp-email-column" title={String(session.email || "")}><div className="freepp-email-cell"><span className="freepp-email-value">{session.email || `会话 #${id}`}</span>{liveChain && <div className={`freepp-account-chain-live ${liveChain.status}`} title={liveChain.message}><div className="freepp-account-chain-label"><span>{liveChain.message}</span><strong>{liveChain.progress}%</strong></div><div className="freepp-account-chain-track"><span style={{ width: `${Math.max(0, Math.min(100, liveChain.progress))}%` }} /></div></div>}</div></td>
                <td title={String(session.rebind_email || "-")}>{session.rebind_email || "-"}</td>
                <td>{session.group_name || "默认分组"}</td>
                <td><span className={`freepp-status freepp-status-${statusLabel(session.status) === "已注册" ? "ok" : statusLabel(session.status) === "失败" ? "error" : "info"}`}>{statusLabel(session.status)}</span></td>
                <td><span className="freepp-plan">{planLabel(session.plan_type || session.account_type)}</span></td>
                <td><button className="freepp-field-button" type="button" disabled={!session.has_login_secret || operationBusy} onClick={() => void copyField(session, "login_secret")}>{session.has_login_secret ? "LS" : "-"}</button></td>
                <td><button className="freepp-field-button" type="button" disabled={!session.has_secret_key || operationBusy} onClick={() => void copyField(session, "secret_key")}>{session.has_secret_key ? "SK" : "-"}</button></td>
                <td><button className={`freepp-field-button ${atAvailable ? "present" : "missing"}`} type="button" disabled={!atAvailable || operationBusy} onClick={() => void copyField(session, "access_token")}>{atAvailable ? "AT" : "-"}</button><small className={`freepp-at-state ${atAvailable ? "available" : "missing"}`}>{atLabel(session)}</small></td>
                <td>{session.has_refresh_token ? <button className="freepp-field-button" type="button" disabled={operationBusy} onClick={() => void copyField(session, "refresh_token")}>RT</button> : <button className="freepp-field-button" type="button" disabled={operationBusy} onClick={() => void acquireRefreshToken(session)}>获取</button>}</td>
                <td><span className={`freepp-detection freepp-detection-${session.trial_eligibility === "eligible" ? "ok" : session.trial_eligibility === "ineligible" ? "error" : "muted"}`}>{trialLabel(session.trial_eligibility)}</span></td>
                <td>{checkoutLabel(session.checkout_kind)}</td>
                <td title={payments.map(paymentLabel).join("、") || "-"}>{payments.length ? payments.map(paymentLabel).join("、") : "-"}</td>
                <td className={session.access_token_status === "invalid" || session.access_token_status === "expired" ? "freepp-expired" : ""}>{session.access_token_status === "invalid" || session.access_token_status === "expired" ? "AT 无效或已过期" : formatDateTime(session.access_token_expires_at)}</td>
                <td title={session.health_check_error || ""}>{session.health_check_status === "failed" ? <span className="freepp-expired">测活失败</span> : formatDateTime(session.last_health_checked_at)}</td>
                <td className="freepp-record-cell" title={detectionRecord(session)}>{detectionRecord(session)}</td>
              </tr>;
            })}
            {!items.length && <tr><td colSpan={16} className="freepp-account-empty">{loading ? "加载中..." : "暂无账号"}</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="freepp-account-pagination">
        <span>第 {page} / {totalPages} 页 · 共 {total} 条</span>
        <button className="btn btn-sm" type="button" onClick={() => setPage(1)} disabled={page <= 1}>首页</button>
        <button className="btn btn-sm" type="button" onClick={() => setPage((value) => value - 1)} disabled={page <= 1}>上一页</button>
        {pageNumbers(page, totalPages).map((number) => <button className={`freepp-page-number ${number === page ? "active" : ""}`} type="button" key={number} onClick={() => setPage(number)}>{number}</button>)}
        <button className="btn btn-sm" type="button" onClick={() => setPage((value) => value + 1)} disabled={page >= totalPages}>下一页</button>
        <button className="btn btn-sm" type="button" onClick={() => setPage(totalPages)} disabled={page >= totalPages}>末页</button>
        <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))} aria-label="账号每页条数">{PAGE_SIZES.map((size) => <option value={size} key={size}>{size} 条/页</option>)}</select>
        <button className="btn btn-sm" type="button" onClick={() => setView("tokens")}>打开 Token 库</button>
      </div>
      {rebindOpen && <div className="freepp-rebind-overlay" role="dialog" aria-modal="true" aria-label="选择换绑邮箱"><div className="freepp-rebind-dialog"><div className="freepp-rebind-head"><strong>选择换绑邮箱</strong><button type="button" className="freepp-rebind-close" onClick={() => setRebindOpen(false)}>×</button></div><div className="freepp-rebind-body"><p>可从已导入邮箱池选择多个目标邮箱，系统按批次分配；不选择时使用自建域名邮箱池。</p>{rebindLoading ? <div className="freepp-account-empty">正在加载邮箱池...</div> : <div className="freepp-rebind-list">{rebindMailboxes.length ? rebindMailboxes.map((item) => <label key={String(item.email)}><input type="checkbox" checked={rebindSelected.includes(String(item.email))} onChange={(event) => setRebindSelected((current) => event.target.checked ? [...current, String(item.email)] : current.filter((value) => value !== String(item.email)))} /><span>{String(item.email)}</span></label>) : <div className="freepp-account-empty">暂无可用邮箱，提交后将尝试使用自建邮箱池</div>}</div>}</div><div className="freepp-rebind-foot"><button type="button" className="btn" onClick={() => setRebindOpen(false)}>取消</button><button type="button" className="btn btn-primary" disabled={rebindLoading || operationBusy} onClick={() => void submitRebind()}>开始换绑（{selected.length}）</button></div></div></div>}
    </section>
  );
}
