import { useEffect, useState, useCallback, useRef } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { RegEvent, RegAccount, RegStatus, PipelineConfig } from "../types";

// 渠道元信息 (name/label/hint) 由后端 /api/register/channels 动态返回,
// 前端不再硬编码固定列表 — 开源用户自建/启停渠道后下拉自动同步。
interface EmailChannel {
  name: string;
  label: string;
  hint: string;
}

interface ProxyOption {
  value: string;
  label: string;
  country?: string;
  source?: string;
}

function channelLabel(channels: EmailChannel[], c: string): string {
  const ch = channels.find((m) => m.name === c);
  if (ch) return ch.label;
  if (c.startsWith("imap:")) return "📧 IMAP 邮箱";
  if (c.startsWith("mailcom:")) return "mail.com 邮箱";
  if (c.startsWith("api798")) return "api798 卡密邮箱";
  return c;
}
function channelHint(channels: EmailChannel[], c: string): string {
  const ch = channels.find((m) => m.name === c);
  if (ch) return ch.hint;
  if (c.startsWith("imap:")) return "自建域名邮箱 · IMAP 取码 · 仅注册该域";
  if (c.startsWith("mailcom:")) return "mail.com 接码服务 · 注册与恢复共用";
  return "自定义渠道";
}

const OPERATION_CN: Record<string, string> = {
  account_import: "账号导入",
  account_export: "账号格式导出",
  account_check: "账号检测",
  plan_check: "套餐查询",
  token_recovery: "Token 恢复",
  token_import: "Token 导入",
  token_copy: "Token 复制",
  account_delete: "账号删除",
  two_factor_save: "2FA 设置",
  two_factor_setup: "服务端 2FA 设置",
  registration_stop: "停止注册",
  rebind: "账号换绑",
  commerce_probe: "Checkout/试用/支付探测",
};

function operationLabel(value: string | undefined): string {
  return OPERATION_CN[String(value || "")] || String(value || "账号操作");
}

function eventMessage(ev: RegEvent): string {
  if (ev.message) return ev.message;
  if (ev.type === "progress") return `第 ${ev.index || 0}/${ev.total || 0} 号完成`;
  if (ev.type === "op_start") return `${operationLabel(ev.operation)}开始${ev.total != null ? `，共 ${ev.total} 项` : ""}`;
  if (ev.type === "op_progress") {
    const count = ev.done != null && ev.total != null ? ` ${ev.done}/${ev.total}` : "";
    return `${operationLabel(ev.operation)}${count}${ev.ok === false ? " 失败" : ev.ok === true ? " 成功" : "处理中"}`;
  }
  if (ev.type === "op_complete") return `${operationLabel(ev.operation)}完成：成功 ${ev.success || 0}，失败 ${ev.failed || 0}`;
  if (ev.type === "op_error") return `${operationLabel(ev.operation)}异常：${ev.error || "未知错误"}`;
  return "";
}

const STATUS_CN: Record<string, string> = {
  active: "存活",
  pending: "待验证",
  expired: "过期",
  suspended: "冻结",
  deactivated: "停用",
  logout: "登出",
  disabled: "失效",
  revoked: "吊销",
  unknown: "未知",
};

const STATUS_BADGE: Record<string, string> = {
  active: "badge-success",
  pending: "badge-warn",
  expired: "badge-warn",
  suspended: "badge-warn",
  deactivated: "badge-muted",
  logout: "badge-muted",
  disabled: "badge-danger",
  revoked: "badge-danger",
  unknown: "badge-muted",
};

const MODE_BADGE: Record<string, string> = {
  "163": "badge-accent",
};

const MODE_LABEL: Record<string, string> = {
  imported: "导入账号",
  api798: "API 邮箱",
};

function modeLabel(value: string | null | undefined): string {
  const mode = String(value || "").trim();
  if (!mode) return "—";
  if (mode.startsWith("imap:")) return `IMAP · ${mode.slice(5)}`;
  if (mode.startsWith("mailcom:")) return `mail.com · ${mode.slice(8)}`;
  return MODE_LABEL[mode] || mode;
}

const PLAN_BADGE: Record<string, string> = {
  plus: "badge-accent",
  pro: "badge-accent",
  team: "badge-warn",
  business: "badge-warn",
  enterprise: "badge-warn",
  edu: "badge-warn",
  trial: "badge-warn",
  free: "badge-muted",
};

const PLAN_LABEL: Record<string, string> = {
  free: "基础免费",
  trial: "试用中",
  plus: "Plus",
  pro: "Pro",
  team: "Team",
  business: "Business",
  enterprise: "Enterprise",
  edu: "Edu",
  unknown: "未验证",
};

function planLabel(value: string | null | undefined): string {
  const plan = String(value || "").trim().toLowerCase();
  return PLAN_LABEL[plan] || value || "—";
}

function planStatusLabel(value: string | null | undefined): string {
  const status = String(value || "").trim().toLowerCase();
  const labels: Record<string, string> = {
    active: "订阅有效",
    queued: "排队中",
    running: "查询中",
    no_subscription: "无有效订阅",
    inactive: "未激活",
    unauthorized: "需重新登录",
    rate_limited: "稍后重试",
    missing_token: "缺少 Token",
    missing_account_id: "缺少账号 ID",
    network_error: "网络异常",
    server_error: "接口异常",
    http_error: "接口返回错误",
    probe_error: "检测异常",
    target_access_blocked: "出口被拦截",
  };
  return labels[status] || (status && status !== "unknown" ? status : "");
}

function planTitle(plan: string | null | undefined, status: string | null | undefined, detail: string | null | undefined): string {
  const normalizedPlan = String(plan || "").trim().toLowerCase();
  const normalizedStatus = String(status || "").trim().toLowerCase();
  if (normalizedPlan === "free" && normalizedStatus === "no_subscription") {
    return "基础免费：未检测到有效订阅";
  }
  if (normalizedPlan === "trial") return "0 元试用/试用套餐";
  const label = planLabel(normalizedPlan || plan);
  const statusText = planStatusLabel(normalizedStatus);
  return [label, statusText, detail].filter(Boolean).join("：") || "套餐尚未检测";
}

function errorLabel(value: string | null | undefined): string {
  const code = String(value || "").trim();
  const labels: Record<string, string> = {
    TOKEN_RECOVERY_ACCOUNT_DEACTIVATED: "账号已停用",
    TOKEN_RECOVERY_OTP_REQUIRED: "邮箱验证码校验失败",
    TOKEN_RECOVERY_CSRF_REJECTED: "登录会话被拒绝",
    TOKEN_RECOVERY_PASSWORD_REJECTED: "密码校验失败",
    TOKEN_RECOVERY_MAILBOX_UNAVAILABLE: "邮箱取码不可用",
    TOKEN_RECOVERY_EDGE_BLOCKED: "认证入口不可用，已跳过邮箱取码",
    TOKEN_RECOVERY_BIND_FLOW_ERROR: "登录链路异常",
    TOKEN_RECOVERY_TIMEOUT: "登录超时",
    TOKEN_RECOVERY_INVALID: "新 Token 校验失败",
    TOKEN_RECOVERY_SAVE_FAILED: "Token 写入失败",
    LOGIN_SESSION_TOKEN_EMPTY: "登录会话未生成",
    LOGIN_ACCESS_TOKEN_EMPTY: "登录后未获取 AccessToken",
    LOGIN_INPUT_INCOMPLETE: "登录资料不完整",
    INVALID_OPENAI_CREDENTIALS: "邮箱或密码错误",
    ACCOUNT_NOT_REGISTERED: "账号未注册",
    ACCOUNT_DELETED_OR_DEACTIVATED: "账号已停用",
    MFA_VERIFICATION_FAILED: "2FA 校验失败",
    EMAIL_OTP_UNAVAILABLE: "缺少邮箱验证码地址",
    ACCOUNT_CHECK_FAILED: "账号检测失败",
    ACCOUNT_CHECK_ERROR: "检测链路异常",
    PLAN_TOKEN_MISSING: "缺少 AccessToken",
    PLAN_ACCOUNT_ID_MISSING: "Token 缺少账号 ID",
    PLAN_TOKEN_INVALID: "Token 已失效",
    PLAN_RATE_LIMITED: "套餐接口限流",
    PLAN_RESPONSE_EMPTY: "套餐接口未返回数据",
    PLAN_NETWORK_ERROR: "套餐查询网络异常",
    PLAN_SERVER_ERROR: "套餐接口暂不可用",
    PLAN_HTTP_ERROR: "套餐接口返回错误",
    PLAN_PROBE_ERROR: "套餐检测异常",
    PLAN_TARGET_ACCESS_BLOCKED: "套餐出口被拦截",
    TOKEN_RECOVERY_WORKER_ERROR: "恢复任务异常",
  };
  return labels[code] || code || "—";
}

function tokenRecoveryLabel(value: string | null | undefined): string {
  const status = String(value || "idle").trim().toLowerCase();
  if (status === "running") return "恢复中";
  if (status === "success") return "Token 已更新";
  if (status === "failed") return "恢复失败";
  return "未执行";
}

function tokenRecoveryBadge(value: string | null | undefined): string {
  const status = String(value || "idle").trim().toLowerCase();
  if (status === "success") return "badge-success";
  if (status === "failed") return "badge-danger";
  if (status === "running") return "badge-warn";
  return "badge-muted";
}

interface RegDetail extends RegAccount {
  password?: string | null;
  access_token?: string | null;
  session_token?: string | null;
  refresh_token?: string | null;
  two_factor_secret?: string | null;
  two_factor_url?: string | null;
}
interface RebindMailbox { id: string; email: string; code_url: string; used_count?: number; last_error?: string; src?: "rebind" | "mailcom" }

function maskSecret(s: string | null | undefined): string {
  if (!s) return "—";
  if (s.length <= 24) return s;
  return s.slice(0, 12) + "…" + s.slice(-8);
}

// 详情弹层: 可点击复制的字段行 (复制完整值, 不截断)
function DetailCopyRow({ label, value, k, copied, onCopy }: {
  label: string;
  value: string | null | undefined;
  k: string;
  copied: boolean;
  onCopy: (key: string, val: string | null | undefined) => void;
}) {
  const empty = !value;
  return (
    <div
      className={`detail-row${empty ? "" : " dr-copy"}`}
      title={empty ? undefined : "点击复制完整值"}
      onClick={empty ? undefined : () => onCopy(k, value)}
    >
      <span className="dr-label">{label}</span>
      <span className="dr-value mono dr-clip">
        {empty ? "—" : maskSecret(value)}
        {!empty && <span className={`dr-copy-badge${copied ? " show" : ""}`}>{copied ? "✓ 已复制" : "⧉"}</span>}
      </span>
    </div>
  );
}

export function RegisterView() {
  const pushLog = useStore((s) => s.pushLog);

  const [status, setStatus] = useState<RegStatus | null>(null);
  const [channels, setChannels] = useState<string[]>([]);
  const [channelMeta, setChannelMeta] = useState<EmailChannel[]>([]);
  const [count, setCount] = useState(1);
  const [emailMode, setEmailMode] = useState<string>("");
  const [cooldown, setCooldown] = useState(30);
  const [proxy, setProxy] = useState("");
  const [proxyOptions, setProxyOptions] = useState<ProxyOption[]>([]);
  const [country, setCountry] = useState<string>("auto");
  const [countries, setCountries] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const [events, setEvents] = useState<RegEvent[]>([]);
  const [accounts, setAccounts] = useState<RegAccount[]>([]);
  const [accountPage, setAccountPage] = useState(1);
  const [accountPageSize, setAccountPageSize] = useState(50);
  const [accountTotal, setAccountTotal] = useState(0);
  const [accountPages, setAccountPages] = useState(1);
  const [stats, setStats] = useState<{ total: number; active: number; disabled: number } | null>(null);
  const [progress, setProgress] = useState<{ index: number; total: number; success: number; failed: number } | null>(null);
  const [operationProgress, setOperationProgress] = useState<{
    operation: string; done: number; total: number; success: number; failed: number; running: boolean;
  } | null>(null);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [detail, setDetail] = useState<RegDetail | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [importingTokens, setImportingTokens] = useState(false);
  const [exportingCredentials, setExportingCredentials] = useState(false);
  const [copyingTokens, setCopyingTokens] = useState(false);
  const [recoveringTokens, setRecoveringTokens] = useState(false);
  const [tokenRecoveryResult, setTokenRecoveryResult] = useState("");
  const [twoFactorSecret, setTwoFactorSecret] = useState("");
  const [savingTwoFactor, setSavingTwoFactor] = useState(false);
  const [settingTwoFactor, setSettingTwoFactor] = useState(false);
  const [rebindOpen, setRebindOpen] = useState(false);
  const [rebindMailboxes, setRebindMailboxes] = useState<RebindMailbox[]>([]);
  const [rebindSource, setRebindSource] = useState<"rebind" | "mailcom">("rebind");
  const [rebindSelected, setRebindSelected] = useState<Set<string>>(new Set());
  const [rebindBusy, setRebindBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  // Credentials import is intentionally side-effect free. Fetch at/st/token
  // only when the user explicitly runs account detection afterward.
  const [importCheckAfter, setImportCheckAfter] = useState(false);
  const [checkingAccounts, setCheckingAccounts] = useState(false);
  const [checkingPlans, setCheckingPlans] = useState(false);
  const [probingCommerce, setProbingCommerce] = useState(false);
  const [accountActionMenu, setAccountActionMenu] = useState<RegAccount | null>(null);
  const [rowRunningAction, setRowRunningAction] = useState<{ id: number; label: string } | null>(null);
  const mountedRef = useRef(true);
  const sinceRef = useRef(0);
  const pollBusyRef = useRef(false);
  const accountRefreshAtRef = useRef(0);

  const loadStatus = useCallback(async () => {
    try {
      const r = await api<RegStatus & { channels?: string[] }>("/api/register/status");
      if (r?.ok) {
        setStatus(r);
        if (Array.isArray(r.channels) && r.channels.length) {
          setChannels(r.channels);
          // 首次加载渠道后, 若 emailMode 仍为空则自动选第一个
          setEmailMode((prev) => prev && r.channels!.includes(prev) ? prev : (r.channels![0] || ""));
        }
      }
    } catch { /* ignore */ }
    // 拉取渠道元信息 (label/hint) — 与 status 分离, 启停渠道后下拉即时反映
    try {
      const c = await api<{ ok: boolean; channels: EmailChannel[] }>("/api/register/channels");
      if (c?.ok && Array.isArray(c.channels)) setChannelMeta(c.channels);
    } catch { /* ignore */ }
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("search", search.trim());
      if (filterStatus) params.set("status", filterStatus);
      params.set("page", String(accountPage));
      params.set("page_size", String(accountPageSize));
      const r = await api<{ ok: boolean; items: RegAccount[]; total?: number; pages?: number; page?: number; page_size?: number }>(
        "/api/register/accounts?" + params.toString()
      );
      if (r?.ok) {
        setAccounts(r.items || []);
        const total = Math.max(0, Number(r.total) || 0);
        const pages = Math.max(1, Number(r.pages) || Math.ceil(total / accountPageSize) || 1);
        setAccountTotal(total);
        setAccountPages(pages);
        // 删除最后一页后，自动回到仍然存在的最后一页。
        setAccountPage((prev) => Math.min(Math.max(1, prev), pages));
      }
    } catch { /* ignore */ }
  }, [search, filterStatus, accountPage, accountPageSize]);

  const loadStats = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; total: number; active: number; disabled: number }>("/api/register/stats");
      if (r?.ok) setStats(r);
    } catch { /* ignore */ }
  }, []);

  const loadRebindMailboxes = useCallback(async (source: "rebind" | "mailcom" = "rebind") => {
    try {
      const r = await api<{ ok: boolean; mailboxes?: RebindMailbox[] }>("/api/register/rebind/mailboxes?source=" + encodeURIComponent(source));
      if (r?.ok) setRebindMailboxes(r.mailboxes || []);
    } catch { /* ignore */ }
  }, []);

  const pollEvents = useCallback(async () => {
    if (!mountedRef.current || pollBusyRef.current) return;
    pollBusyRef.current = true;
    try {
      const cursor = sinceRef.current;
      const r = await api<{ ok: boolean; events: RegEvent[]; last_seq: number }>(
        "/api/register/events?since=" + cursor
      );
      if (r?.ok) {
        const incoming = Array.isArray(r.events) ? r.events : [];
        if (incoming.length) {
          setEvents((prev) => {
            const seen = new Set(prev.map((item) => item.seq));
            return [...prev, ...incoming.filter((item) => !seen.has(item.seq))].slice(-500);
          });
          for (const ev of incoming) {
            if (ev.type === "start") {
              setProgress({ index: 0, total: ev.total ?? 0, success: 0, failed: 0 });
            } else if (ev.type === "progress" && ev.index !== undefined) {
              setProgress({ index: ev.index, total: ev.total ?? 0, success: ev.success ?? 0, failed: ev.failed ?? 0 });
            } else if (ev.type === "complete") {
              // Keep the final task summary visible so the progress panel does
              // not disappear as soon as the worker finishes.
              setProgress((prev) => prev ? {
                ...prev,
                index: ev.total ?? prev.total,
                total: ev.total ?? prev.total,
                success: ev.success ?? prev.success,
                failed: ev.failed ?? prev.failed,
              } : null);
            } else if (ev.type === "op_start") {
              setOperationProgress({ operation: ev.operation || "account", done: 0, total: ev.total ?? 0, success: 0, failed: 0, running: true });
            } else if (ev.type === "op_progress") {
              setOperationProgress((prev) => ({
                operation: ev.operation || prev?.operation || "account",
                done: ev.done ?? prev?.done ?? 0,
                total: ev.total ?? prev?.total ?? 0,
                success: ev.success ?? prev?.success ?? 0,
                failed: ev.failed ?? prev?.failed ?? 0,
                running: true,
              }));
              // 账号操作逐项落库；同步刷新当前表格，避免进度已出而表格仍显示旧值。
              if (["account_check", "plan_check", "commerce_probe", "token_recovery", "token_import", "token_copy", "account_import", "account_export", "account_delete", "two_factor_setup", "two_factor_save", "rebind"].includes(String(ev.operation || ""))) {
                const now = Date.now();
                if (now - accountRefreshAtRef.current > 1200) {
                  accountRefreshAtRef.current = now;
                  void loadAccounts();
                  void loadStats();
                }
              }
            } else if (ev.type === "op_complete" || ev.type === "op_error") {
              setOperationProgress((prev) => ({
                operation: ev.operation || prev?.operation || "account",
                done: ev.done ?? ev.total ?? prev?.done ?? 0,
                total: ev.total ?? prev?.total ?? 0,
                success: ev.success ?? prev?.success ?? 0,
                failed: ev.failed ?? prev?.failed ?? 0,
                running: false,
              }));
              if (["account_check", "plan_check", "commerce_probe", "token_recovery", "token_import", "token_copy", "account_import", "account_export", "account_delete", "two_factor_setup", "two_factor_save", "rebind"].includes(String(ev.operation || ""))) {
                void loadAccounts();
                void loadStats();
              }
            }
          }
        }
        // Advance the cursor even when no events were returned. This keeps a
        // long-running page aligned after the server trims its ring buffer.
        const next = Math.max(cursor, Number(r.last_seq) || 0);
        sinceRef.current = next;
      }
    } catch { /* ignore */ }
    finally {
      pollBusyRef.current = false;
    }
  }, [loadAccounts, loadStats]);

  useEffect(() => {
    mountedRef.current = true;
    loadStatus();
    loadAccounts();
    loadStats();
    loadRebindMailboxes();
    void pollEvents();
    // 加载可选出口国家列表 (GEO 表 41 国)
    api<{ countries?: string[] }>("/api/register/countries").then((r) => {
      if (Array.isArray(r.countries) && r.countries.length) setCountries(r.countries);
    }).catch(() => { /* ignore */ });
    api<{ options?: ProxyOption[] }>("/api/proxy/options").then((r) => {
      if (Array.isArray(r.options)) setProxyOptions(r.options);
    }).catch(() => { /* ignore */ });
    // 从一键流程配置回填注册参数 (复用 pipeline config 的 reg_* 字段, 跨会话记忆)
    api<{ config?: Partial<PipelineConfig> }>("/api/pipeline/config").then((r) => {
      const c = r?.config;
      if (!c) return;
      if (c.reg_email_mode) setEmailMode(c.reg_email_mode);
      if (typeof c.reg_cooldown === "number") setCooldown(c.reg_cooldown);
      if (typeof c.reg_proxy === "string") {
        const poolChoice = ["", "__clash__", "__api_pool__", "__mixed_pool__"];
        setProxy(poolChoice.includes(c.reg_proxy) ? c.reg_proxy : "");
      }
      if (typeof c.reg_country === "string") setCountry(c.reg_country);
      // 回填完成后再放开回写 (延一拍, 避免上面的 setState 触发回写)
      setTimeout(() => { persistReady.current = true; }, 0);
    }).catch(() => {
      // 拉取失败也放开回写, 否则用户改了永远不落盘
      setTimeout(() => { persistReady.current = true; }, 0);
    });
    const t1 = setInterval(loadStatus, 3000);
    const t2 = setInterval(pollEvents, 3000);
    const t3 = setInterval(() => { loadAccounts(); loadStats(); }, 6000);
    return () => {
      mountedRef.current = false;
      clearInterval(t1); clearInterval(t2); clearInterval(t3);
    };
  }, [loadStatus, loadAccounts, loadStats, pollEvents, loadRebindMailboxes]);

  // 搜索、状态筛选或每页数量变化后，从第一页重新查看结果。
  useEffect(() => {
    setAccountPage(1);
  }, [search, filterStatus, accountPageSize]);

  // 注册参数变更 → 防抖回写 pipeline config (供下次打开记忆 + 一键流程共享)
  const persistTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const persistReady = useRef(false);  // 回填完成后才允许回写, 避免首渲染冲掉配置
  useEffect(() => {
    if (!persistReady.current) return;
    if (persistTimer.current) clearTimeout(persistTimer.current);
    persistTimer.current = setTimeout(() => {
      api("/api/pipeline/config", "POST", {
        reg_email_mode: emailMode,
        reg_cooldown: cooldown,
        reg_proxy: proxy,
        reg_country: country,
      }).catch(() => { /* ignore */ });
    }, 1200);
    return () => { if (persistTimer.current) clearTimeout(persistTimer.current); };
  }, [emailMode, cooldown, proxy, country]);

  // 详情弹层打开时支持 Esc 关闭
  useEffect(() => {
    if (!detail) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDetail(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [detail]);

  const handleStart = async () => {
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string }>("/api/register/start", "POST", {
        count: Number(count) || 1,
        email_mode: emailMode,
        cooldown: Number(cooldown) || 30,
        proxy: proxy.trim() || undefined,
        country: country || "auto",
      });
      if (r?.ok) {
        pushLog(`注册任务已启动: ${count} 个 (${channelLabel(channelMeta, emailMode)})`, "ok");
        await loadStatus();
      } else {
        pushLog(`启动失败: ${r?.error || "未知原因"}`, "err");
      }
    } catch (e) {
      pushLog("启动失败: " + (e as Error).message, "err");
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    try {
      const r = await api<{ ok: boolean; stopped: boolean }>("/api/register/stop", "POST");
      pushLog(r?.stopped ? "已请求停止（当前号跑完后停止）" : "当前无运行中任务", r?.stopped ? "warn" : "info");
    } catch (e) {
      pushLog("停止失败: " + (e as Error).message, "err");
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("确认删除该注册账号记录？")) return;
    pushLog(`正在删除账号 #${id}…`, "info");
    try {
      const r = await api(`/api/register/accounts/${id}`, "DELETE");
      if (r?.ok) {
        pushLog(`已删除账号 #${id}`, "ok");
        loadAccounts();
        loadStats();
      }
    } catch (e) {
      pushLog("删除失败: " + (e as Error).message, "err");
    }
  };

  const handleDetail = async (id: number) => {
    pushLog(`正在加载账号 #${id} 详情…`, "info");
    try {
      const r = await api<{ ok: boolean; account: RegDetail }>(`/api/register/accounts/${id}`);
      if (r?.ok) {
        setDetail(r.account);
        setTwoFactorSecret(r.account.two_factor_secret || "");
      }
    } catch { /* ignore */ }
  };

  const handleImportAccounts = async () => {
    if (importBusy || !importText.trim()) return;
    setImportBusy(true);
    pushLog("正在导入账号…", "info");
    try {
      const r = await api<{ ok: boolean; imported?: number; updated?: number; total?: number; errors?: string[]; account_ids?: number[]; error?: string }>(
        "/api/register/accounts/import", "POST", { text: importText },
      );
      if (!r?.ok) throw new Error(r?.error || "导入失败");
      const issue = r.errors?.length ? `，${r.errors.length} 行格式有误` : "";
      pushLog(`账号导入完成：新增 ${r.imported || 0}，更新 ${r.updated || 0}${issue}`, r.errors?.length ? "warn" : "ok");
      setImportText("");
      setImportOpen(false);
      await loadAccounts();
      await loadStats();
      if (importCheckAfter && r.account_ids?.length) {
        setSelected(new Set(r.account_ids));
        setTimeout(() => { void handleCheckAccounts(r.account_ids); }, 0);
      }
    } catch (e) {
      pushLog("账号导入失败: " + (e as Error).message, "err");
    } finally {
      setImportBusy(false);
    }
  };

  const handleImportFile = (file?: File) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImportText(String(reader.result || ""));
    reader.readAsText(file, "utf-8");
  };

  const handleCheckAccounts = async (idsOverride?: number[]) => {
    const ids = (idsOverride?.length ? idsOverride : [...selected]).slice().sort((a, b) => b - a);
    if (!ids.length || checkingAccounts || recoveringTokens) return;
    setCheckingAccounts(true);
    pushLog(`开始检测 ${ids.length} 个账号…`, "info");
    try {
      const r = await api<{ ok: boolean; checked?: number; failed?: number; results?: { account_id: number; ok: boolean; plan_type?: string; error?: string }[]; error?: string }>(
        "/api/register/accounts/check", "POST", { account_ids: ids, proxy: proxy || undefined, country: country === "auto" ? "JP" : country, timeout: 90, wait_timeout: 300, plan_timeout: 30 },
      );
      if (!r?.results) throw new Error(r?.error || "检测失败");
      const checked = r.checked || 0;
      const planCount = (r.results || []).filter((item) => item.ok && item.plan_type).length;
      pushLog(`账号检测完成：登录成功 ${checked}，套餐已更新 ${planCount}，失败 ${r.failed || 0}`, checked ? "ok" : "warn");
      await loadAccounts();
    } catch (e) {
      pushLog("账号检测失败: " + (e as Error).message, "err");
    } finally {
      setCheckingAccounts(false);
    }
  };

  const handleCheckPlans = async (idsOverride?: number[]) => {
    const ids = (idsOverride?.length ? idsOverride : [...selected]).slice().sort((a, b) => b - a);
    if (!ids.length || checkingPlans || checkingAccounts) return;
    setCheckingPlans(true);
    pushLog(`开始查询 ${ids.length} 个账号套餐…`, "info");
    try {
      const r = await api<{ ok: boolean; queried?: number; failed?: number; error?: string }>("/api/register/accounts/check_plans", "POST", {
        account_ids: ids, proxy: proxy || undefined, country: country === "auto" ? "US" : country,
      });
      pushLog(`套餐查询完成：成功 ${r.queried || 0}，失败 ${r.failed || 0}`, r.queried ? "ok" : "warn");
      await loadAccounts();
    } catch (e) { pushLog("套餐查询失败: " + (e as Error).message, "err"); }
    finally { setCheckingPlans(false); }
  };

  const handleProbeCommerce = async (idsOverride?: number[]) => {
    const ids = (idsOverride?.length ? idsOverride : [...selected]).slice().sort((a, b) => b - a);
    if (!ids.length || probingCommerce || checkingAccounts) return;
    setProbingCommerce(true);
    pushLog(`开始探测 ${ids.length} 个账号的 Checkout、试用和支付…`, "info");
    try {
      const r = await api<{ ok: boolean; probed?: number; failed?: number; error?: string }>("/api/register/accounts/probe_commerce", "POST", {
        account_ids: ids, proxy: proxy || undefined, country: country === "auto" ? "US" : country,
      });
      pushLog(`商业能力探测完成：成功 ${r.probed || 0}，失败 ${r.failed || 0}`, r.probed ? "ok" : "warn");
      await loadAccounts();
    } catch (e) { pushLog("商业能力探测失败: " + (e as Error).message, "err"); }
    finally { setProbingCommerce(false); }
  };

  const handleSaveTwoFactor = async (clear = false) => {
    if (!detail || savingTwoFactor) return;
    const secret = clear ? "" : twoFactorSecret.trim();
    setSavingTwoFactor(true);
    pushLog(`${clear ? "正在清除" : "正在保存"}账号 #${detail.id} 的 2FA…`, "info");
    try {
      const r = await api<{ ok: boolean; account?: RegDetail; configured?: boolean; error?: string }>(
        `/api/register/accounts/${detail.id}/2fa`, "POST", { two_factor_secret: secret },
      );
      if (!r?.ok || !r.account) {
        pushLog(`保存 2FA 失败: ${r?.error || "未知原因"}`, "err");
        return;
      }
      setDetail(r.account);
      setTwoFactorSecret(r.account.two_factor_secret || "");
      await loadAccounts();
      pushLog(r.configured ? `账号 #${detail.id} 已保存 2FA 密钥` : `账号 #${detail.id} 已清除 2FA 密钥`, "ok");
    } catch (e) {
      pushLog("保存 2FA 失败: " + (e as Error).message, "err");
    } finally {
      setSavingTwoFactor(false);
    }
  };

  const handleSetupSelectedTwoFactor = async () => {
    const ids = [...selected].sort((a, b) => b - a);
    if (!ids.length || settingTwoFactor) return;
    const missing = accounts.filter((item) => selected.has(item.id) && !item.has_two_factor).length;
    if (!missing) {
      pushLog("所选账号均已配置 2FA", "info");
      return;
    }
    setSettingTwoFactor(true);
    pushLog(`开始为 ${missing} 个账号启用服务端 2FA…`, "info");
    try {
      const r = await api<{
        ok: boolean; configured?: number; failed?: number; error?: string;
        results?: { account_id: number; ok: boolean; error_code?: string; error?: string }[];
      }>("/api/register/accounts/setup_2fa", "POST", {
        account_ids: ids,
        proxy: proxy || "__api_pool__",
        country: country === "auto" ? "JP" : country,
        concurrency: 1,
      });
      const firstError = r.results?.find((item) => !item.ok);
      pushLog(
        `2FA 设置完成：成功 ${r.configured || 0}，失败 ${r.failed || 0}`
          + (firstError ? `（${firstError.error_code || "MFA_SETUP_FAILED"}: ${firstError.error || "设置失败"}）` : ""),
        r.failed ? "warn" : "ok",
      );
      await loadAccounts();
    } catch (e) {
      pushLog("2FA 设置失败: " + (e as Error).message, "err");
    } finally {
      setSettingTwoFactor(false);
    }
  };

  const openRebind = async (accountId?: number) => {
    const picked = accountId ? new Set([accountId]) : selected;
    if (!picked.size) return;
    if (accountId) setSelected(picked);
    await loadRebindMailboxes(rebindSource);
    setRebindSelected(new Set());
    setRebindOpen(true);
  };
  const submitRebind = async () => {
    const ids = [...selected];
    const mailboxIds = [...rebindSelected];
    if (!ids.length || mailboxIds.length < ids.length) { pushLog(`请选择至少 ${ids.length} 个换绑邮箱`, "warn"); return; }
    setRebindBusy(true);
    pushLog(`开始换绑 ${ids.length} 个账号…`, "info");
    try {
      const r = await api<{ ok: boolean; queued?: number; error?: string }>("/api/register/rebind", "POST", { account_ids: ids, mailbox_ids: mailboxIds, source: rebindSource, country: country || "auto", proxy_source: proxy.trim() || undefined });
      if (!r?.ok) throw new Error(r?.error || "换绑启动失败");
      pushLog(`已启动 ${r.queued || ids.length} 个账号换绑`, "ok"); setRebindOpen(false); setSelected(new Set()); await loadAccounts();
    } catch (e) { pushLog("换绑启动失败: " + (e as Error).message, "err"); }
    finally { setRebindBusy(false); }
  };

  const [copiedKey, setCopiedKey] = useState<string>("");
  const handleCopyField = async (key: string, value: string | null | undefined) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(""), 1500);
    } catch { /* ignore */ }
  };

  // 批量管理
  const allSelected = accounts.length > 0 && accounts.every((a) => selected.has(a.id));
  const toggleSelect = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  const toggleSelectAll = () => {
    setSelected(allSelected ? new Set() : new Set(accounts.map((a) => a.id)));
  };
  const handleBulkDelete = async () => {
    if (selected.size === 0) return;
    if (!window.confirm(`确认删除选中的 ${selected.size} 个账号？此操作不可撤销。`)) return;
    pushLog(`正在删除 ${selected.size} 个账号…`, "info");
    try {
      const r = await api<{ ok: boolean; deleted?: number; error?: string }>("/api/register/accounts/bulk_delete", "POST", {
        account_ids: [...selected],
      });
      if (r?.ok) {
        pushLog(`批量删除 ${r.deleted || 0} 个账号`, "ok");
        setSelected(new Set());
        loadAccounts();
        loadStats();
      } else {
        pushLog(`批量删除失败: ${r?.error || "未知"}`, "err");
      }
    } catch (e) {
      pushLog("批量删除失败: " + (e as Error).message, "err");
    }
  };
  const handleBulkExport = () => {
    const picked = accounts.filter((a) => selected.has(a.id));
    if (picked.length === 0) return;
    const header = "ID,邮箱,渠道,套餐,状态,2FA配置\n";
    const rows = picked.map((a) =>
      [a.id, a.email, a.email_mode || "", a.plan_type || "", a.alive_status || "", a.has_two_factor ? "已配置" : "未配置"]
        .map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")
    ).join("\n");
    const blob = new Blob(["\ufeff" + header + rows], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `register_accounts_${selected.size}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };
  const handleExportCredentialFormat = async () => {
    if (selected.size === 0 || exportingCredentials) return;
    setExportingCredentials(true);
    pushLog(`正在导出 ${selected.size} 个账号的三段格式…`, "info");
    try {
      const r = await api<{
        ok: boolean; lines?: string[]; exported?: number;
        skipped_missing_credentials?: number; not_found?: number; error?: string;
      }>("/api/register/accounts/export_credentials", "POST", { account_ids: [...selected] });
      const lines = (r?.lines || []).filter(Boolean);
      if (!r?.ok || lines.length === 0) {
        pushLog(r?.error || "没有同时具备密码和 2FA 的账号", "warn");
        return;
      }
      const blob = new Blob([lines.join("\n") + "\n"], { type: "text/plain;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `accounts_credentials_${lines.length}.txt`;
      link.click();
      URL.revokeObjectURL(url);
      const skipped = Number(r.skipped_missing_credentials || 0) + Number(r.not_found || 0);
      pushLog(`账号格式导出完成：${lines.length} 个${skipped ? `，跳过 ${skipped} 个资料不完整账号` : ""}`, skipped ? "warn" : "ok");
    } catch (e) {
      pushLog("账号格式导出失败: " + (e as Error).message, "err");
    } finally {
      setExportingCredentials(false);
    }
  };
  const handleImportSelectedToTokens = async () => {
    if (selected.size === 0 || importingTokens) return;
    setImportingTokens(true);
    pushLog(`正在导入 ${selected.size} 个账号到 Token 库…`, "info");
    try {
      const r = await api<{
        ok: boolean; imported?: number; already_present?: number;
        metadata_updated?: number; skipped_no_token?: number; failed?: number; error?: string;
      }>("/api/register/accounts/import_tokens", "POST", { account_ids: [...selected] });
      if (r?.ok) {
        pushLog(
          `Token 导入完成：新增 ${r.imported || 0}，已存在 ${r.already_present || 0}，` +
          `补全 ${r.metadata_updated || 0}，缺少 at ${r.skipped_no_token || 0}` +
          ((r.failed || 0) ? `，失败 ${r.failed}` : ""),
          (r.failed || 0) ? "warn" : "ok",
        );
        await loadAccounts();
      } else {
        pushLog(`Token 导入失败: ${r?.error || "未知原因"}`, "err");
      }
    } catch (e) {
      pushLog("Token 导入失败: " + (e as Error).message, "err");
    } finally {
      setImportingTokens(false);
    }
  };
  const handleCopySelectedTokens = async () => {
    if (selected.size === 0 || copyingTokens) return;
    setCopyingTokens(true);
    pushLog(`正在复制 ${selected.size} 个账号的 Token…`, "info");
    try {
      const r = await api<{
        ok: boolean; copied?: number; skipped_no_token?: number; tokens?: string[]; error?: string;
      }>("/api/register/accounts/copy_tokens", "POST", { account_ids: [...selected] });
      const tokens = (r?.tokens || []).filter(Boolean);
      if (!r?.ok || tokens.length === 0) {
        pushLog(r?.error || "所选账号没有可复制的 AccessToken", "warn");
        return;
      }
      await navigator.clipboard.writeText(tokens.join("\n"));
      pushLog(`已复制 ${r.copied || tokens.length} 个 AccessToken` +
        ((r.skipped_no_token || 0) ? `，跳过 ${r.skipped_no_token} 个无 at 账号` : ""), "ok");
    } catch (e) {
      pushLog("复制 Token 失败: " + (e as Error).message, "err");
    } finally {
      setCopyingTokens(false);
    }
  };
  const handleRecoverSelectedTokens = async (idsOverride?: number[]) => {
    const ids = (idsOverride?.length ? idsOverride : [...selected]).slice().sort((a, b) => b - a);
    if (!ids.length || recoveringTokens || checkingAccounts) return;
    setRecoveringTokens(true);
    setTokenRecoveryResult("正在重新登录并校验 Token…");
    pushLog(`开始重新获取 ${ids.length} 个账号的 Token…`, "info");
    try {
      const r = await api<{
        ok: boolean; recovered?: number; failed?: number;
        results?: { account_id: number; email?: string; ok: boolean; error?: string }[];
        error?: string;
      }>("/api/register/accounts/refresh_tokens", "POST", {
        account_ids: ids, proxy: proxy || undefined, country,
      });
      const recovered = r?.recovered || 0;
      const failed = r?.failed || 0;
      const firstError = (r?.results || []).find((item) => !item.ok)?.error || r?.error || "";
      const message = `Token 恢复完成：成功 ${recovered}，失败 ${failed}` +
        (firstError ? ` · ${firstError}` : "");
      setTokenRecoveryResult(message);
      pushLog(message, recovered > 0 ? "ok" : "warn");
      await Promise.all([loadAccounts(), loadStats()]);
    } catch (e) {
      const message = "Token 恢复失败: " + (e as Error).message;
      setTokenRecoveryResult(message);
      pushLog(message, "err");
    } finally {
      setRecoveringTokens(false);
    }
  };

  const runRowAction = async (account: RegAccount, label: string, action: () => Promise<void>) => {
    setAccountActionMenu(null);
    setRowRunningAction({ id: account.id, label });
    try {
      await action();
    } finally {
      setRowRunningAction((current) => current?.id === account.id ? null : current);
    }
  };

  const successRate = stats && stats.total > 0 ? ((stats.active / stats.total) * 100).toFixed(0) : "—";
  const registrationTotal = Math.max(0, Number(progress?.total) || 0);
  const registrationDone = Math.min(registrationTotal, Math.max(0, Number(progress?.index) || 0));
  const registrationPercent = registrationTotal ? Math.round((registrationDone / registrationTotal) * 100) : 0;
  const registrationSuccess = Math.max(0, Number(progress?.success) || 0);
  const registrationFailed = Math.max(0, Number(progress?.failed) || 0);
  const registrationPending = Math.max(0, registrationTotal - registrationDone);
  const accountProgressEvents = events.filter((ev) => ev.email).slice(-6).reverse();

  const safeAccountPage = Math.min(Math.max(1, accountPage), Math.max(1, accountPages));
  const accountPageNumbers = (() => {
    const total = Math.max(1, accountPages);
    const windowSize = 5;
    let start = Math.max(1, safeAccountPage - Math.floor(windowSize / 2));
    const end = Math.min(total, start + windowSize - 1);
    start = Math.max(1, end - windowSize + 1);
    return Array.from({ length: end - start + 1 }, (_, i) => start + i);
  })();

  return (
    <div className="page page-wide">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <h2 className="page-title">账号注册</h2>
        <span className={`badge ${status?.running ? "badge-info" : "badge-muted"}`}>
          {status?.running ? "● 任务运行中" : "○ 空闲"}
        </span>
      </div>

      {/* 统计卡 */}
      <div className="stat-grid" style={{ marginBottom: 14 }}>
        <div className="stat-card">
          <div className="stat-label">累计注册</div>
          <div className="stat-value">{stats?.total ?? "—"}</div>
          <div className="stat-foot">全部渠道</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">存活</div>
          <div className="stat-value" style={{ color: "var(--ok)" }}>{stats?.active ?? "—"}</div>
          <div className="stat-foot">alive</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">失效</div>
          <div className="stat-value" style={{ color: "var(--danger)" }}>{stats?.disabled ?? "—"}</div>
          <div className="stat-foot">disabled</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">存活率</div>
          <div className="stat-value">{successRate}%</div>
          <div className="stat-foot">active / total</div>
        </div>
      </div>

      {/* 任务控制 */}
      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">批量注册</span>
          {progress && (
            <span className="running-chip" style={{ marginLeft: 8 }}>
              第 {progress.index}/{progress.total} 号 · 成功 {progress.success} · 失败 {progress.failed}
            </span>
          )}
        </div>
        <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", marginTop: 12 }}>
          <label className="field">
            <span className="field-label">注册数量</span>
            <input className="input" type="number" min={1} max={200} value={count}
              onChange={(e) => setCount(Math.min(Math.max(Number(e.target.value) || 1, 1), 200))} />
          </label>
          <label className="field">
            <span className="field-label">邮箱渠道</span>
            <select className="select" value={emailMode} onChange={(e) => setEmailMode(e.target.value)}>
              {channels.map((c) => (
                <option key={c} value={c}>{channelLabel(channelMeta, c)}</option>
              ))}
            </select>
            <span className="field-hint">{channelHint(channelMeta, emailMode)}</span>
          </label>
          <label className="field">
            <span className="field-label">号间冷却 (秒)</span>
            <input className="input" type="number" min={0} max={600} value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value) || 0)} />
          </label>
          <label className="field">
            <span className="field-label">出口国家</span>
            <select className="select" value={country}
              onChange={(e) => setCountry(e.target.value)}
              title="注册出口 IP 国家；代理由所选代理池提供">
              <option value="auto">auto (随机)</option>
              {countries.map((cc) => (
                <option key={cc} value={cc}>{cc}</option>
              ))}
            </select>
            <span className="field-hint">
              {country && country !== "auto" ? `按 ${country} 出口注册` : "随机选国家"}
            </span>
          </label>
          <label className="field">
            <span className="field-label">代理出口</span>
            <select className="select" value={proxy}
              onChange={(e) => setProxy(e.target.value)}
              title="按代理池类别选择出口，任务启动时轮换实际 IP">
              {proxyOptions.map((item) => (
                <option key={`${item.source || "proxy"}:${item.value}`} value={item.value}>{item.label}</option>
              ))}
            </select>
            <span className="field-hint">指定国家时只从同国家标签的代理轮换；在“密钥与凭据 → 住宅代理池”管理标签</span>
          </label>
        </div>
        {progress && (
          <div className="progress" style={{ marginTop: 14 }}>
            <div className="progress-bar" style={{ width: `${(progress.index / progress.total) * 100}%` }} />
          </div>
        )}
        <div className="btn-row" style={{ marginTop: 14 }}>
          <button className="btn btn-primary" onClick={() => setImportOpen(true)}>导入账号</button>
          <button className="btn btn-primary" disabled={busy || !!status?.running} onClick={handleStart}>
            {busy ? "启动中…" : "启动注册"}
          </button>
          <button className="btn btn-stop-live" disabled={!status?.running} onClick={handleStop}>
            停止任务
          </button>
          <span className="muted" style={{ alignSelf: "center" }}>
            导入格式：邮箱----GPT 登录密码----2FA；选中后点“检测账号”再获取 at/st/token
          </span>
        </div>
      </section>

      {operationProgress && operationProgress.total > 0 && (
        <div className="batch-bar" style={{ marginBottom: 14, border: "1px solid var(--border)" }}>
          <span className="running-chip">{operationLabel(operationProgress.operation)} · {operationProgress.running ? "进行中" : "已完成"} · {operationProgress.done}/{operationProgress.total}</span>
          <span className="muted">成功 {operationProgress.success} · 失败 {operationProgress.failed}</span>
        </div>
      )}

      {/* 保留任务进度；详细日志已收起，避免占据整块空白区域。 */}
      <section className="card" style={{ marginBottom: 14 }}>
        <div className="realtime-progress-grid">
          <div className="realtime-progress-panel">
            <div className="realtime-progress-head">
              <strong>注册任务进度</strong>
              <span>{registrationTotal ? `${registrationDone}/${registrationTotal}` : "等待任务"}</span>
            </div>
            <div className="realtime-progress-track" aria-label="注册任务完成进度">
              <div className="realtime-progress-fill" style={{ width: `${registrationPercent}%` }} />
            </div>
            <div className="realtime-progress-stats">
              <span><b>{registrationSuccess}</b> 已完成</span>
              <span><b>{registrationPending}</b> 未完成</span>
              <span className="realtime-progress-error"><b>{registrationFailed}</b> 异常</span>
            </div>
          </div>
          <div className="realtime-progress-panel realtime-account-panel">
            <div className="realtime-progress-head">
              <strong>当前账号进度</strong>
              {progress?.total ? <span>{registrationPercent}%</span> : <span>—</span>}
            </div>
            <div className="realtime-account-list">
              {accountProgressEvents.length === 0 ? (
                <span className="muted">启动任务后显示当前账号</span>
              ) : accountProgressEvents.map((ev, index) => {
                const accountPercent = ev.total
                  ? Math.round((Math.min(Number(ev.index) || 0, Number(ev.total)) / Number(ev.total)) * 100)
                  : registrationPercent;
                return (
                  <div className="realtime-account-item" key={`${ev.seq}-${index}`}>
                    <div className="realtime-account-meta">
                      <span title={ev.email || undefined}>{ev.email}</span>
                      <b>{accountPercent}%</b>
                    </div>
                    <div className="realtime-account-track"><div className="realtime-account-fill" style={{ width: `${accountPercent}%` }} /></div>
                    <div className="realtime-account-step">{eventMessage(ev)}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </section>

      {/* 账号表格 */}
      <section className="card">
        <div className="card-head">
          <span className="card-title">注册账号</span>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select className="select flex-field-sm" value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}>
              <option value="">全部状态</option>
              <option value="active">存活</option>
              <option value="disabled">失效</option>
            </select>
            <input className="input flex-field" placeholder="搜索邮箱…" value={search}
              onChange={(e) => setSearch(e.target.value)} />
            <button className="btn btn-sm" onClick={() => { loadAccounts(); loadStats(); }}>刷新</button>
          </div>
        </div>
        <div className="table-wrap" style={{ marginTop: 8 }}>
          <div className="batch-bar" style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
              <span className="tag">已选 {selected.size}</span>
              <button className="btn btn-sm" onClick={toggleSelectAll}>{allSelected ? "取消全选" : "全选"}</button>
              <button className="btn btn-sm btn-ghost" disabled={!selected.size} onClick={() => setSelected(new Set())}>取消选择</button>
              <button className="btn btn-sm btn-primary" disabled={!selected.size || importingTokens} onClick={handleImportSelectedToTokens}>
                {importingTokens ? "反代导入中…" : "反代导入"}
              </button>
              <button className="btn btn-sm" onClick={() => setImportOpen(true)} title="补全邮箱、密码和 2FA 等登录资料">添加 LS</button>
              <button className="btn btn-sm" title="登录校验、Token 更新并查询实时套餐" disabled={!selected.size || checkingAccounts || recoveringTokens} onClick={() => handleCheckAccounts()}>
                {checkingAccounts ? "检测中…" : "检测账号"}
              </button>
              <button className="btn btn-sm" disabled={!selected.size || checkingPlans || checkingAccounts} onClick={() => handleCheckPlans()}>{checkingPlans ? "套餐查询中…" : "订阅/套餐"}</button>
              <button className="btn btn-sm" disabled={!selected.size || probingCommerce || checkingAccounts} onClick={() => handleProbeCommerce()}>{probingCommerce ? "探测中…" : "试用探测"}</button>
              <button className="btn btn-sm" disabled={!selected.size || probingCommerce || checkingAccounts} onClick={() => handleProbeCommerce()}>Checkout</button>
              <button className="btn btn-sm" disabled={!selected.size || probingCommerce || checkingAccounts} onClick={() => handleProbeCommerce()}>支付探测</button>
              <button className="btn btn-sm btn-primary" disabled={!selected.size || rebindBusy || checkingAccounts} onClick={() => openRebind()}>换绑</button>
              <button className="btn btn-sm" disabled={!selected.size || recoveringTokens || checkingAccounts} onClick={() => handleRecoverSelectedTokens()}>
                {recoveringTokens ? "重新获取中…" : "重新获取 Token"}
              </button>
              <button className="btn btn-sm" disabled={!selected.size || settingTwoFactor || checkingAccounts || recoveringTokens} onClick={handleSetupSelectedTwoFactor}>
                {settingTwoFactor ? "设置 2FA 中…" : "设置服务端 2FA"}
              </button>
              <button className="btn btn-sm" disabled={!selected.size || copyingTokens} onClick={handleCopySelectedTokens}>
                {copyingTokens ? "复制中…" : "复制所选 Token"}
              </button>
              <button className="btn btn-sm btn-danger" disabled={!selected.size} onClick={handleBulkDelete}>删除所选</button>
              <button className="btn btn-sm" disabled={!selected.size} onClick={handleBulkExport}>导出 CSV</button>
              <button className="btn btn-sm" disabled={!selected.size || exportingCredentials} onClick={handleExportCredentialFormat}>
                {exportingCredentials ? "导出中…" : "导出账号格式"}
              </button>
              {tokenRecoveryResult && <span className="muted" style={{ fontSize: 12 }}>{tokenRecoveryResult}</span>}
            </div>
          <table className="table">
            <thead>
              <tr>
                <th className="account-select-col" style={{ width: 42 }}>
                  <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} />
                </th>
                <th className="num">ID</th>
                <th>邮箱</th>
                <th>渠道</th>
                <th>套餐</th>
                <th>状态</th>
                <th>Token 恢复</th>
                <th>Checkout</th>
                <th>支付</th>
                <th>错误</th>
                <th>Token</th>
                <th>2FA</th>
                <th>换绑</th>
                <th className="num">操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.length === 0 && (
                <tr>
                  <td colSpan={14}>
                    <div className="empty" style={{ padding: "24px 0" }}>
                      <div className="empty-title">暂无注册记录</div>
                      <div className="empty-hint">启动注册任务后，结果将在此展示</div>
                    </div>
                  </td>
                </tr>
              )}
              {accounts.map((a) => (
                <tr key={a.id} className={selected.has(a.id) ? "row-selected" : ""}>
                  <td className="account-select-col">
                    <input type="checkbox" checked={selected.has(a.id)} onChange={() => toggleSelect(a.id)} />
                  </td>
                  <td className="num mono">{a.id}</td>
                  <td className="mono">{a.email}</td>
                  <td>
                    <span className={`badge ${MODE_BADGE[a.email_mode || ""] || "badge-muted"}`}>
                      {modeLabel(a.email_mode)}
                    </span>
                  </td>
                  <td title={[planTitle(a.plan_type, a.plan_status, a.plan_detail), a.plus_trial_eligible ? (a.plus_trial_title || "检测到可用 Plus 试用资格") : ""].filter(Boolean).join("；")}>
                    <span className={`badge ${PLAN_BADGE[a.plan_type || ""] || "badge-muted"}`}>
                      {planLabel(a.plan_type)}
                    </span>
                    {a.plus_trial_eligible && (
                      <span className="badge badge-warn" style={{ marginLeft: 4 }} title={a.plus_trial_title || "检测到可用 Plus 试用资格"}>可试用</span>
                    )}
                    {a.plan_checked_at && planStatusLabel(a.plan_status) && <div className="muted" style={{ fontSize: 10, marginTop: 3 }}>{planStatusLabel(a.plan_status)}</div>}
                  </td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[a.alive_status] || "badge-muted"}`}>
                      {STATUS_CN[a.alive_status] || a.alive_status}
                    </span>
                  </td>
                  <td>
                    <span className={`badge ${tokenRecoveryBadge(a.token_recovery_status)}`}>
                      {tokenRecoveryLabel(a.token_recovery_status)}
                    </span>
                    {a.token_recovery_error_code && <span className="badge badge-danger" title={`${a.token_recovery_error_code}${a.token_recovery_error ? `: ${a.token_recovery_error}` : ""}`}>错误</span>}
                    {a.token_recovery_at && <div className="muted" style={{ fontSize: 10, marginTop: 3 }}>{a.token_recovery_at.slice(0, 19)}</div>}
                  </td>
                  <td>
                    {a.commerce_probe_status && a.commerce_probe_status !== "idle" ? (
                      <>
                        <span className={`badge ${a.commerce_probe_status === "success" ? "badge-success" : a.commerce_probe_status === "missing_token" ? "badge-warn" : "badge-danger"}`} title={a.commerce_probe_error || undefined}>
                          {a.commerce_session_type || (a.commerce_probe_status === "missing_token" ? "缺少 Token" : "探测失败")}
                        </span>
                        {a.commerce_promo && <div className="muted" style={{ fontSize: 10, marginTop: 3 }}>试用：{a.commerce_promo === "yes" ? "可用" : a.commerce_promo === "no" ? "不可用" : a.commerce_promo}</div>}
                      </>
                    ) : <span className="badge badge-muted">未探测</span>}
                  </td>
                  <td>
                    {a.commerce_probe_status === "success" ? (
                      (a.commerce_payment_methods || []).length ?
                        (a.commerce_payment_methods || []).map((method) => <span className="badge badge-info" key={method}>{method}</span>) :
                        <span className="badge badge-muted">未检测到</span>
                    ) : <span className="badge badge-muted">—</span>}
                  </td>
                  <td className="mono">
                    {(a.error_code || a.token_recovery_error_code) ? (
                      <span className="badge badge-danger" title={`${a.error_code || a.token_recovery_error_code}${a.error_detail || a.token_recovery_error ? `: ${a.error_detail || a.token_recovery_error}` : ""}`}>错误</span>
                    ) : null}
                  </td>
                  <td>
                    {a.has_access_token && <span className="badge badge-info">at</span>}
                    {a.has_session_token && <span className="badge badge-info" style={{ marginLeft: 4 }}>st</span>}
                    {!a.has_access_token && <span className="badge badge-muted">无</span>}
                  </td>
                  <td>
                    <span className={`badge ${a.has_two_factor ? "badge-success" : "badge-muted"}`}>
                      {a.has_two_factor ? "已设置" : "未设置"}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`badge ${a.rebind_status === "success" ? "badge-success" : a.rebind_status === "failed" ? "badge-danger" : a.rebind_status === "running" || a.rebind_status === "queued" ? "badge-warn" : "badge-muted"}`}
                      title={a.rebind_error ? `换绑错误：${a.rebind_error}` : undefined}
                    >
                      {a.rebind_status === "success" ? "换绑成功" : a.rebind_status === "failed" ? "换绑失败" : a.rebind_status === "running" ? "换绑中" : a.rebind_status === "queued" ? "排队中" : "未换绑"}
                    </span>
                    {a.rebind_target_email && <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{a.rebind_target_email}</div>}
                  </td>
                  <td className="num">
                    <button className={`btn btn-sm${rowRunningAction?.id === a.id ? " btn-primary" : ""}`} title="打开账号操作" aria-label={`打开账号 ${a.id} 操作`} onClick={() => setAccountActionMenu(a)}>
                      {rowRunningAction?.id === a.id ? "◌" : "⋯"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="pager" style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12, padding: "0 12px 12px", flexWrap: "wrap" }}>
          <span className="muted" style={{ fontSize: 12 }}>
            {accountTotal === 0
              ? "0 条"
              : `${(safeAccountPage - 1) * accountPageSize + 1}-${Math.min(safeAccountPage * accountPageSize, accountTotal)} / ${accountTotal} 条`}
          </span>
          <button className="btn btn-sm" onClick={() => setAccountPage(1)} disabled={safeAccountPage <= 1}>首页</button>
          <button className="btn btn-sm" onClick={() => setAccountPage((p) => Math.max(1, p - 1))} disabled={safeAccountPage <= 1}>上一页</button>
          {accountPageNumbers.map((n) => (
            <button key={n} className={`btn btn-sm${n === safeAccountPage ? " btn-primary" : ""}`} onClick={() => setAccountPage(n)}>{n}</button>
          ))}
          <button className="btn btn-sm" onClick={() => setAccountPage((p) => Math.min(accountPages, p + 1))} disabled={safeAccountPage >= accountPages}>下一页</button>
          <button className="btn btn-sm" onClick={() => setAccountPage(accountPages)} disabled={safeAccountPage >= accountPages}>末页</button>
          <label className="muted" style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, marginLeft: "auto" }}>
            每页
            <select className="select select-sm" style={{ width: 86 }} value={accountPageSize} onChange={(e) => setAccountPageSize(Number(e.target.value))}>
              {[20, 50, 100, 200].map((n) => <option key={n} value={n}>{n} 条</option>)}
            </select>
          </label>
        </div>
      </section>

      {accountActionMenu && (
        <div className="overlay" onClick={() => setAccountActionMenu(null)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label={`账号 ${accountActionMenu.id} 操作`} onClick={(e) => e.stopPropagation()} style={{ width: "min(420px, calc(100vw - 24px))" }}>
            <div className="sheet-head">
              <span className="sheet-title">账号操作 #{accountActionMenu.id}</span>
              <button className="btn btn-ghost" onClick={() => setAccountActionMenu(null)}>关闭</button>
            </div>
            <div className="sheet-body">
              <div className="muted mono" style={{ marginBottom: 14, overflowWrap: "anywhere" }}>{accountActionMenu.email}</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8 }}>
                <button className="btn" onClick={() => runRowAction(accountActionMenu, "测活", () => handleCheckAccounts([accountActionMenu.id]))}>测活</button>
                <button className="btn" onClick={() => runRowAction(accountActionMenu, "探测", () => handleProbeCommerce([accountActionMenu.id]))}>Checkout/试用/支付</button>
                <button className="btn" onClick={() => runRowAction(accountActionMenu, "订阅", () => handleCheckPlans([accountActionMenu.id]))}>订阅/套餐</button>
                <button className="btn" onClick={() => runRowAction(accountActionMenu, "续期", () => handleRecoverSelectedTokens([accountActionMenu.id]))}>续期 Token</button>
                <button className="btn btn-primary" onClick={() => runRowAction(accountActionMenu, "换绑", () => openRebind(accountActionMenu.id))}>换绑</button>
                <button className="btn" onClick={() => runRowAction(accountActionMenu, "详情", () => handleDetail(accountActionMenu.id))}>详情</button>
                <button className="btn btn-danger" onClick={() => runRowAction(accountActionMenu, "删除", () => handleDelete(accountActionMenu.id))}>删除</button>
              </div>
              {rowRunningAction?.id === accountActionMenu.id && <div className="muted" style={{ marginTop: 14 }}>正在执行：{rowRunningAction.label} ◌</div>}
            </div>
          </div>
        </div>
      )}

      {rebindOpen && (
        <div className="overlay" onClick={() => setRebindOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="换绑选择账号" onClick={(e) => e.stopPropagation()} style={{ padding: 22, width: "min(720px, calc(100vw - 24px))" }}>
            <div className="page-head" style={{ marginBottom: 14 }}><div><h3 className="page-title" style={{ fontSize: 20 }}>换绑选择账号</h3><p className="page-sub">已选 {selected.size} 个账号，请按顺序选择同数量目标邮箱</p></div><button className="btn btn-ghost" onClick={() => setRebindOpen(false)}>关闭</button></div>
            <div className="card"><div className="card-head"><span className="card-title">目标邮箱池 ({rebindMailboxes.length})</span><span className="card-hint">已选择 {rebindSelected.size} 条</span></div><div className="card-body"><div className="segmented" style={{ width: "fit-content", marginBottom: 10 }}><button className={rebindSource === "rebind" ? "active" : ""} onClick={() => { setRebindSource("rebind"); setRebindSelected(new Set()); loadRebindMailboxes("rebind"); }}>密钥与凭据</button><button className={rebindSource === "mailcom" ? "active" : ""} onClick={() => { setRebindSource("mailcom"); setRebindSelected(new Set()); loadRebindMailboxes("mailcom"); }}>邮箱管理 · mail.com</button></div><div style={{ maxHeight: 380, overflowY: "auto" }}>{!rebindMailboxes.length ? <div className="empty"><div className="empty-title">暂无换绑邮箱</div><div className="empty-hint">{rebindSource === "mailcom" ? "请先在“邮箱管理”导入账号或启用邮箱" : "请先在“密钥与凭据”导入目标邮箱"}</div></div> : rebindMailboxes.map((m) => <label key={m.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 4px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}><input type="checkbox" checked={rebindSelected.has(m.id)} onChange={(e) => setRebindSelected((prev) => { const next = new Set(prev); if (e.target.checked) next.add(m.id); else next.delete(m.id); return next; })} style={{ marginTop: 3 }} /><span style={{ minWidth: 0, flex: 1 }}><span style={{ display: "block" }}>{m.email}</span><span className="muted" style={{ display: "block", fontSize: 11, marginTop: 3, overflowWrap: "anywhere" }}>{m.code_url}</span></span></label>)}</div><div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}><button className="btn btn-ghost" onClick={() => setRebindOpen(false)}>取消</button><button className="btn btn-primary" disabled={rebindBusy || rebindSelected.size < selected.size} onClick={submitRebind}>{rebindBusy ? "启动中…" : `开始换绑 (${selected.size})`}</button></div></div></div>
          </div>
        </div>
      )}

      {importOpen && (
        <div className="overlay" onClick={() => setImportOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="导入账号" onClick={(e) => e.stopPropagation()} style={{ padding: 22, width: "min(720px, calc(100vw - 24px))" }}>
            <div className="page-head" style={{ marginBottom: 14 }}>
              <div><h3 className="page-title" style={{ fontSize: 20 }}>导入账号</h3><p className="page-sub">支持：邮箱----GPT 登录密码----2FA 密钥；也支持 2FA 地址与邮箱----密码交错粘贴</p></div>
              <button className="btn btn-ghost" onClick={() => setImportOpen(false)}>关闭</button>
            </div>
            <label className="field"><span className="field-label">账号内容</span>
              <textarea className="input mono" rows={10} value={importText} onChange={(e) => setImportText(e.target.value)} placeholder={'name@example.com----password----JBSWY3DPEHPK3PXP\nhttps://2fa.example/code/xxxxx\nname2@example.com----password2'} style={{ resize: "vertical", minHeight: 180 }} />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, fontSize: 13 }}>
              <input type="checkbox" checked={importCheckAfter} onChange={(e) => setImportCheckAfter(e.target.checked)} /> 导入后立即获取 at/st/token
            </label>
            <div className="btn-row" style={{ marginTop: 12, justifyContent: "space-between" }}>
              <label className="btn btn-ghost" style={{ cursor: "pointer" }}>读取 TXT/CSV<input type="file" accept=".txt,.csv,text/plain,text/csv" hidden onChange={(e) => handleImportFile(e.target.files?.[0])} /></label>
              <div style={{ display: "flex", gap: 8 }}><button className="btn btn-ghost" onClick={() => setImportOpen(false)}>取消</button><button className="btn btn-primary" disabled={importBusy || !importText.trim()} onClick={handleImportAccounts}>{importBusy ? "导入中…" : "开始导入"}</button></div>
            </div>
          </div>
        </div>
      )}

      {/* 详情弹层 */}
      {detail && (
        <div className="overlay" onClick={() => setDetail(null)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <span className="sheet-title">账号详情 #{detail.id}</span>
              <span className="muted" style={{ fontSize: 11, fontWeight: 400 }}>点击任意字段值可复制完整内容</span>
            </div>
            <div className="sheet-body">
              <div className="detail-list">
                <DetailCopyRow label="邮箱" value={detail.email} k="email" copied={copiedKey === "email"} onCopy={handleCopyField} />
                <DetailCopyRow label="密码" value={detail.password} k="password" copied={copiedKey === "password"} onCopy={handleCopyField} />
                <DetailCopyRow label="AccessToken" value={detail.access_token} k="access_token" copied={copiedKey === "access_token"} onCopy={handleCopyField} />
                <DetailCopyRow label="SessionToken" value={detail.session_token} k="session_token" copied={copiedKey === "session_token"} onCopy={handleCopyField} />
                <DetailCopyRow label="RefreshToken" value={detail.refresh_token} k="refresh_token" copied={copiedKey === "refresh_token"} onCopy={handleCopyField} />
                <DetailCopyRow label="2FA 地址" value={detail.two_factor_url} k="two_factor_url" copied={copiedKey === "two_factor_url"} onCopy={handleCopyField} />
                <div className="detail-row">
                  <label className="dr-label" htmlFor="account-two-factor">2FA 密钥</label>
                  <div className="dr-value" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      id="account-two-factor"
                      className="input mono"
                      type="password"
                      autoComplete="off"
                      placeholder="输入认证器密钥"
                      value={twoFactorSecret}
                      onChange={(e) => setTwoFactorSecret(e.target.value)}
                      style={{ minWidth: 0, flex: 1 }}
                    />
                    <button className="btn btn-sm btn-primary" disabled={savingTwoFactor} onClick={() => handleSaveTwoFactor()}>
                      {savingTwoFactor ? "保存中…" : "保存"}
                    </button>
                    {detail.has_two_factor && (
                      <button className="btn btn-sm btn-danger" disabled={savingTwoFactor} onClick={() => handleSaveTwoFactor(true)}>清除</button>
                    )}
                  </div>
                  <span className="field-hint" style={{ gridColumn: "2 / -1" }}>
                    导入账号与“检测账号”会使用密码 + TOTP；若登录流程额外要求邮箱验证码，需要先配置可用取码地址。
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">套餐</span>
                  <span className="dr-value" title={planTitle(detail.plan_type, detail.plan_status, detail.plan_detail)}>
                    {planLabel(detail.plan_type)}
                    {planStatusLabel(detail.plan_status) && <span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>({planStatusLabel(detail.plan_status)})</span>}
                  </span>
                </div>
                {detail.plan_checked_at && (
                  <div className="detail-row">
                    <span className="dr-label">套餐查询时间</span>
                    <span className="dr-value mono">{detail.plan_checked_at.slice(0, 19)}</span>
                  </div>
                )}
                {detail.plan_detail && (
                  <div className="detail-row">
                    <span className="dr-label">套餐说明</span>
                    <span className="dr-value" style={{ wordBreak: "break-word" }}>{detail.plan_detail}</span>
                  </div>
                )}
                {detail.subscription_plan && (
                  <div className="detail-row">
                    <span className="dr-label">订阅标识</span>
                    <span className="dr-value mono">{detail.subscription_plan}</span>
                  </div>
                )}
                {detail.has_active_subscription !== undefined && (
                  <div className="detail-row">
                    <span className="dr-label">订阅状态</span>
                    <span className="dr-value"><span className={`badge ${detail.has_active_subscription ? "badge-success" : "badge-muted"}`}>{detail.has_active_subscription ? "有效" : "无有效订阅"}</span></span>
                  </div>
                )}
                {detail.plus_trial_eligible && (
                  <div className="detail-row">
                    <span className="dr-label">试用资格</span>
                    <span className="dr-value">
                      <span className="badge badge-warn">可用</span>
                      {detail.plus_trial_title && <span className="muted" style={{ marginLeft: 8 }}>{detail.plus_trial_title}</span>}
                      {detail.plus_trial_discount_percentage != null && <span className="muted" style={{ marginLeft: 8 }}>{detail.plus_trial_discount_percentage}% · {detail.plus_trial_duration_num_periods || 1} {detail.plus_trial_duration_period || "期"}</span>}
                    </span>
                  </div>
                )}
                {detail.plan_expires_at && (
                  <div className="detail-row">
                    <span className="dr-label">套餐到期</span>
                    <span className="dr-value mono">{detail.plan_expires_at.slice(0, 19)}</span>
                  </div>
                )}
                {(detail.billing_period || detail.billing_currency) && (
                  <div className="detail-row">
                    <span className="dr-label">账期/币种</span>
                    <span className="dr-value">{[detail.billing_period, detail.billing_currency].filter(Boolean).join(" · ")}</span>
                  </div>
                )}
                {detail.plan_renews_at && (
                  <div className="detail-row">
                    <span className="dr-label">下次续费</span>
                    <span className="dr-value mono">{detail.plan_renews_at.slice(0, 19)}</span>
                  </div>
                )}
                {detail.plan_cancels_at && (
                  <div className="detail-row">
                    <span className="dr-label">取消时间</span>
                    <span className="dr-value mono">{detail.plan_cancels_at.slice(0, 19)}</span>
                  </div>
                )}
                {detail.is_delinquent && <div className="detail-row"><span className="dr-label">账单状态</span><span className="dr-value"><span className="badge badge-danger">逾期</span></span></div>}
                {detail.eligible_offer_ids && detail.eligible_offer_ids.length > 0 && (
                  <div className="detail-row">
                    <span className="dr-label">可用套餐选项</span>
                    <span className="dr-value" style={{ wordBreak: "break-word" }}>{detail.eligible_offer_ids.join(", ")}</span>
                  </div>
                )}
                <div className="detail-row">
                  <span className="dr-label">状态</span>
                  <span className="dr-value">
                    <span className={`badge ${STATUS_BADGE[detail.alive_status] || "badge-muted"}`}>
                      {STATUS_CN[detail.alive_status] || detail.alive_status}
                    </span>
                    {" "}
                    <span className={`badge ${detail.status === "active" ? "badge-success" : "badge-danger"}`}>
                      {detail.status}
                    </span>
                  </span>
                </div>
                <div className="detail-row">
                  <span className="dr-label">Token 恢复</span>
                  <span className="dr-value">
                    <span className={`badge ${tokenRecoveryBadge(detail.token_recovery_status)}`}>
                      {tokenRecoveryLabel(detail.token_recovery_status)}
                    </span>
                    {detail.token_recovery_error_code && (
                      <span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>
                        {errorLabel(detail.token_recovery_error_code)}
                      </span>
                    )}
                  </span>
                </div>
                {detail.token_recovery_error && (
                  <div className="detail-row">
                    <span className="dr-label">恢复原因</span>
                    <span className="dr-error" style={{ wordBreak: "break-word" }}>{detail.token_recovery_error}</span>
                  </div>
                )}
                <div className="detail-row">
                  <span className="dr-label">换绑状态</span>
                  <span className="dr-value">
                    <span
                      className={`badge ${detail.rebind_status === "success" ? "badge-success" : detail.rebind_status === "failed" ? "badge-danger" : detail.rebind_status === "running" || detail.rebind_status === "queued" ? "badge-warn" : "badge-muted"}`}
                      title={detail.rebind_error ? `换绑错误：${detail.rebind_error}` : undefined}
                    >
                      {detail.rebind_status === "success" ? "换绑成功" : detail.rebind_status === "failed" ? "换绑失败" : detail.rebind_status === "running" ? "换绑中" : detail.rebind_status === "queued" ? "排队中" : "未换绑"}
                    </span>
                    {detail.rebind_target_email && <span className="muted" style={{ marginLeft: 8, fontSize: 11 }}>{detail.rebind_target_email}</span>}
                  </span>
                </div>
                {detail.rebind_error && (
                  <div className="detail-row">
                    <span className="dr-label">换绑原因</span>
                    <span className="badge badge-danger" title={`换绑错误：${detail.rebind_error}`}>错误</span>
                  </div>
                )}
                <div className="detail-row">
                  <span className="dr-label">渠道</span>
                  <span className="dr-value">{modeLabel(detail.email_mode)}</span>
                </div>
                <DetailCopyRow label="来源邮箱" value={detail.source_email} k="source_email" copied={copiedKey === "source_email"} onCopy={handleCopyField} />
                {detail.error_detail && (
                  <div className="detail-row">
                    <span className="dr-label">失败原因</span>
                    <span className="dr-error" style={{ wordBreak: "break-all" }}>{detail.error_detail}</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
