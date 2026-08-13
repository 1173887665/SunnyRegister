import { useEffect, useMemo, useRef, useState } from "react";
import QRCode from "qrcode";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Clipboard, Download, ExternalLink, Loader2, Play, RefreshCw, ScrollText, Trash2, X } from "lucide-react";
import { apiFetch, triggerBrowserDownload } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type AnyRow = Record<string, any>;
type Provider = { value: string; label: string; hint: string; country: string; currency: string };

const fallbackProviders: Provider[] = [
  ["hosted", "Hosted", "官方支付长链", "US", "USD"], ["ph_short", "菲律宾短链", "US Checkout / TR 优惠", "PH", "PHP"], ["paypal", "PayPal", "Approve 跳转", "US", "USD"],
  ["ideal", "iDEAL", "荷兰银行支付", "NL", "EUR"], ["upi", "UPI", "印度二维码", "IN", "INR"], ["pix", "PIX", "巴西即时支付", "BR", "BRL"],
  ["twint", "TWINT", "瑞士移动支付", "CH", "CHF"], ["momo", "MoMo", "越南电子钱包", "VN", "VND"], ["gcash", "GCash", "菲律宾电子钱包", "PH", "PHP"], ["kakao", "Kakao Pay", "韩国 Nicepay 跳转", "KR", "KRW"],
].map(([value, label, hint, country, currency]) => ({ value, label, hint, country, currency }));

const planOptions = [{ value: "plus", label: "Plus", hint: "个人订阅" }, { value: "pro", label: "Pro", hint: "专业计划" }, { value: "team", label: "Team", hint: "工作空间" }, { value: "codex_low", label: "Codex", hint: "低价空间" }];
const countryNames: Record<string, string> = { US: "美国", DE: "德国", FR: "法国", NL: "荷兰", IN: "印度", BR: "巴西", VN: "越南", GB: "英国", JP: "日本", KR: "韩国", PH: "菲律宾", AU: "澳大利亚", CA: "加拿大", CH: "瑞士" };
const currencyByCountry: Record<string, string> = { US: "USD", DE: "EUR", FR: "EUR", NL: "EUR", IN: "INR", BR: "BRL", VN: "VND", GB: "GBP", JP: "JPY", KR: "KRW", PH: "PHP", AU: "AUD", CA: "CAD", CH: "CHF" };
const sessionStatuses = ["未注册", "已注册", "已接码", "已反代", "已封禁", "需二验", "登录刷新", "失败"];
const sessionPlans = ["free", "plus", "k12", "team", "pro"];

const statusLabels: Record<string, string> = {
  unregistered: "未注册", registered: "已注册", phone_bound: "已接码", reverse_proxied: "已反代",
  banned: "已封禁", needs_2fa: "需二验", refreshing: "登录刷新", failed: "失败",
  pending: "待处理", valid: "有效", invalid: "无效", expired: "已过期", "待检测": "待检测", "格式无效": "格式无效",
};
const planLabels: Record<string, string> = { free: "Free", plus: "Plus", k12: "K12", team: "Team", pro: "Pro" };
const trialLabels: Record<string, string> = { eligible: "有0元试用", ineligible: "无0元试用", unknown: "未检测" };
const checkoutLabels: Record<string, string> = { oaics: "OAICS", cs_live: "CS Live", cs_test: "CS Test", unknown: "未检测" };
const pathLabels: Record<string, string> = {
  hosted: "官方长链", ph_short: "菲律宾短链", paypal: "PayPal", ideal: "iDEAL", upi: "UPI",
  pix: "PIX", twint: "TWINT", momo: "MoMo", gcash: "GCash", kakao: "Kakao Pay",
};

type BadgeTone = "slate" | "blue" | "green" | "cyan" | "red" | "amber" | "violet" | "rose";
const badgeTones: Record<BadgeTone, string> = {
  slate: "border-slate-200 bg-slate-100 text-slate-600 dark:border-slate-500/20 dark:bg-slate-400/10 dark:text-slate-300",
  blue: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-400/20 dark:bg-blue-400/10 dark:text-blue-300",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-400/20 dark:bg-emerald-400/10 dark:text-emerald-300",
  cyan: "border-cyan-200 bg-cyan-50 text-cyan-700 dark:border-cyan-400/20 dark:bg-cyan-400/10 dark:text-cyan-300",
  red: "border-red-200 bg-red-50 text-red-700 dark:border-red-400/20 dark:bg-red-400/10 dark:text-red-300",
  amber: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-400/20 dark:bg-amber-400/10 dark:text-amber-300",
  violet: "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-400/20 dark:bg-violet-400/10 dark:text-violet-300",
  rose: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-400/20 dark:bg-rose-400/10 dark:text-rose-300",
};

function normalized(value: unknown) { return String(value || "").trim().toLowerCase(); }
function labelFor(value: unknown, labels: Record<string, string>, fallback = "-") { const key = normalized(value); return labels[key] || (key ? String(value) : fallback); }
function CompactBadge({ label, tone = "slate" }: { label: string; tone?: BadgeTone }) {
  return <span className={`inline-flex whitespace-nowrap rounded-md border px-2 py-0.5 text-[11px] font-semibold ${badgeTones[tone]}`}>{label}</span>;
}
function accountStatusTone(value: unknown) {
  const key = normalized(value);
  if (["已注册", "registered", "valid"].includes(key)) return "blue";
  if (["已接码", "phone_bound"].includes(key)) return "green";
  if (["已反代", "reverse_proxied"].includes(key)) return "cyan";
  if (["已封禁", "banned", "失败", "failed", "invalid", "expired", "已过期", "格式无效"].includes(key)) return "red";
  if (["需二验", "登录刷新", "refreshing", "pending", "待检测"].includes(key)) return "amber";
  return "gray";
}
function AccountStatusBadge({ value }: { value: unknown }) { return <span className={`sr-status sr-status-${accountStatusTone(value)}`}>{labelFor(value, statusLabels)}</span>; }
function AccountPlanBadge({ value }: { value: unknown }) { const key = normalized(value); return key ? <span className={`sr-plan-badge sr-plan-${sessionPlans.includes(key) ? key : "default"}`}>{labelFor(value, planLabels)}</span> : <span className="text-slate-400">-</span>; }
function accountCommerceCheckable(row: AnyRow) { return Boolean(row.token) || (["已注册", "registered"].includes(String(row.status || "")) && normalized(row.plan_type) === "free"); }
function AccountTrialValue({ row }: { row: AnyRow }) { const key = normalized(row.trial_eligibility); if (!accountCommerceCheckable(row)) return <span className="text-slate-400">-</span>; if (key === "eligible") return <span className="font-semibold text-emerald-600 dark:text-emerald-400">{trialLabels.eligible}</span>; if (key === "ineligible") return <span className="font-semibold text-red-500">{trialLabels.ineligible}</span>; return <span className="text-slate-400">-</span>; }
function AccountCheckoutValue({ row }: { row: AnyRow }) { const key = normalized(row.checkout_kind); return accountCommerceCheckable(row) && key && key !== "unknown" ? <span className="font-semibold text-sky-600 dark:text-sky-400">{labelFor(row.checkout_kind, checkoutLabels)}</span> : <span className="text-slate-400">-</span>; }
function pathTone(value: unknown): BadgeTone { return ({ hosted: "blue", ph_short: "cyan", paypal: "violet", ideal: "green", upi: "amber", pix: "green", twint: "red", momo: "rose", gcash: "blue", kakao: "amber" } as Record<string, BadgeTone>)[normalized(value)] || "slate"; }

function taskStatusLabel(value: unknown) { return ({ pending: "等待中", claimed: "已领取", running: "运行中", succeeded: "已完成", failed: "失败", cancelled: "已停止", cancel_requested: "停止中" } as Record<string, string>)[normalized(value)] || String(value || "未开始"); }

function CheckoutLogFloat({ open, onToggle, task, logs, scrollRef }: { open: boolean; onToggle: () => void; task: AnyRow | null; logs: AnyRow[]; scrollRef: React.RefObject<HTMLDivElement | null> }) {
  const progress = task?.progress_detail || {};
  const total = Number(progress.total || 0);
  const current = Number(progress.current || 0);
  const percent = total > 0 ? Math.min(100, Math.round(current * 100 / total)) : 0;
  return <div className="fixed bottom-5 right-5 z-[450] flex flex-col items-end gap-2">
    {open && <div className="w-[min(430px,calc(100vw-2rem))] overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] shadow-2xl">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2.5"><div className="flex min-w-0 items-center gap-2"><ScrollText className="h-4 w-4 shrink-0 text-[var(--accent)]" /><span className="text-sm font-bold">提链日志</span><span className="truncate text-[11px] text-[var(--text-muted)]">{taskStatusLabel(task?.status)}</span></div><button className="round-tool h-7 w-7" title="隐藏日志" onClick={onToggle}><ChevronDown className="h-4 w-4" /></button></div>
      {task && <div className="border-b border-[var(--border)] px-3 py-2"><div className="mb-1.5 flex justify-between text-[11px] text-[var(--text-muted)]"><span>{task.progress || "0/0"}</span><span>{percent}%</span></div><div className="h-1.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"><div className="h-full rounded-full bg-[var(--accent)] transition-[width]" style={{ width: `${percent}%` }} /></div></div>}
      <div ref={scrollRef} className="h-64 overflow-y-auto bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-300">{logs.length ? logs.map((item, index) => <div key={item.id || index} className="grid grid-cols-[62px_8px_minmax(0,1fr)] gap-2"><span className="text-slate-500">{String(item.created_at || "").slice(11, 19) || "--:--:--"}</span><span className={item.level === "error" ? "text-red-400" : item.level === "warning" ? "text-amber-400" : "text-emerald-400"}>●</span><span className="break-words">{item.message || item.line}</span></div>) : <div className="flex h-full items-center justify-center text-slate-500">暂无提链日志</div>}</div>
    </div>}
    <button className="inline-flex h-10 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] px-3 text-sm font-semibold shadow-lg hover:border-[var(--accent)]" title={open ? "隐藏提链日志" : "显示提链日志"} onClick={onToggle}><ScrollText className="h-4 w-4 text-[var(--accent)]" />日志{open ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}</button>
  </div>;
}

function splitLines(value: string) { return value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean); }
function resultError(item: AnyRow) { return String(item.error || item.checkout_error || item.message || "").trim(); }
function resultDisplayLink(item: AnyRow) { return String(item.payment_link || item.short_link || item.verification_url || item.provider_redirect_url || item.paypal_link || item.checkout_url || "").trim(); }
function resultQrImage(item: AnyRow) { return String(item.qr_image || item.qr_image_png || item.qr_image_svg || "").trim(); }
function externalATInfo(token: string) {
  const parts = token.split(".");
  if (parts.length < 2) return { status: "格式无效", expires_at: "", email: "" };
  try {
    const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(parts[1].length / 4) * 4, "=")));
    const profile = payload["https://api.openai.com/profile"] || {};
    const exp = Number(payload.exp || 0);
    return { status: exp > 0 && exp * 1000 <= Date.now() ? "已过期" : "待检测", expires_at: exp ? new Date(exp * 1000).toISOString() : "", email: payload.email || profile.email || "" };
  } catch { return { status: "待检测", expires_at: "", email: "" }; }
}

function parseExternalRows(value: string) {
  return splitLines(value).map((token, index) => {
    const info = externalATInfo(token);
    return { index, token, email: (token.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i) || [info.email || ""])[0], at_status: token.startsWith("eyJ") || token.includes(".") ? info.status : "格式无效", expires_at: info.expires_at, selected: false };
  });
}

function QRThumb({ value, onClick }: { value: string; onClick: () => void }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let active = true;
    void QRCode.toDataURL(value, { width: 96, margin: 1, errorCorrectionLevel: "M" }).then((data) => { if (active) setSrc(data); }).catch(() => setSrc(""));
    return () => { active = false; };
  }, [value]);
  return <button className="rounded-lg border border-[var(--border)] bg-white p-1" title="查看二维码" onClick={onClick}>{src ? <img className="h-10 w-10" src={src} alt="支付二维码" /> : <span className="block h-10 w-10 animate-pulse bg-slate-100" />}</button>;
}

function QRImageThumb({ src, onClick }: { src: string; onClick: () => void }) {
  return <button className="rounded-lg border border-[var(--border)] bg-white p-1" title="查看二维码" onClick={onClick}><img className="h-10 w-10 object-contain" src={src} alt="支付二维码" /></button>;
}

function QRModal({ value, image, onClose }: { value: string; image: string; onClose: () => void }) {
  const [src, setSrc] = useState("");
  useEffect(() => { let active = true; if (image) { setSrc(image); return () => { active = false; }; } void QRCode.toDataURL(value, { width: 520, margin: 2, errorCorrectionLevel: "M" }).then((data) => { if (active) setSrc(data); }); return () => { active = false; }; }, [image, value]);
  async function copy() { if (image && navigator.clipboard?.write && typeof ClipboardItem !== "undefined") { const blob = await fetch(src).then((response) => response.blob()); await navigator.clipboard.write([new ClipboardItem({ [blob.type || "image/png"]: blob })]); return; } await navigator.clipboard?.writeText(value); }
  function download() { if (!src) return; const byte = atob(src.split(",")[1]); const arr = Uint8Array.from(byte, (c) => c.charCodeAt(0)); triggerBrowserDownload(new Blob([arr], { type: "image/png" }), "payment-qr.png"); }
  return <div className="fixed inset-0 z-[600] flex items-center justify-center bg-black/60 p-4" onClick={onClose}><div className="w-full max-w-md rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}><div className="mb-4 flex items-center justify-between"><h3 className="text-lg font-bold">支付二维码</h3><button className="round-tool" onClick={onClose}><X className="h-4 w-4" /></button></div>{src ? <img className="mx-auto aspect-square w-full max-w-[320px] rounded-xl bg-white p-3 object-contain" src={src} alt="支付二维码" /> : <div className="flex h-80 items-center justify-center"><Loader2 className="animate-spin" /></div>}{value && <p className="mt-3 break-all text-xs text-[var(--text-muted)]">{value}</p>}<div className="mt-4 flex justify-end gap-2"><Button variant="outline" onClick={() => void copy()}><Clipboard className="mr-2 h-4 w-4" />{image ? "复制图片" : "复制内容"}</Button><Button onClick={download} disabled={!src}><Download className="mr-2 h-4 w-4" />下载二维码</Button></div></div></div>;
}

export default function CheckoutManager() {
  const [providers, setProviders] = useState<Provider[]>(fallbackProviders);
  const [countries, setCountries] = useState<Record<string, string>>(currencyByCountry);
  const [checkoutProxies, setCheckoutProxies] = useState("");
  const [promotionProxies, setPromotionProxies] = useState("");
  const [systemAT, setSystemAT] = useState(true);
  const [sessions, setSessions] = useState<AnyRow[]>([]);
  const [groups, setGroups] = useState<AnyRow[]>([]);
  const [externalText, setExternalText] = useState("");
  const [externalRows, setExternalRows] = useState<AnyRow[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [status, setStatus] = useState("");
  const [planFilter, setPlanFilter] = useState("");
  const [trialFilter, setTrialFilter] = useState("");
  const [checkoutFilter, setCheckoutFilter] = useState("");
  const [plan, setPlan] = useState("plus");
  const [linkType, setLinkType] = useState("hosted");
  const [country, setCountry] = useState("US");
  const [currency, setCurrency] = useState("USD");
  const [retryCount, setRetryCount] = useState(10);
  const [concurrency, setConcurrency] = useState(3);
  const [usePromo, setUsePromo] = useState(true);
  const [promoCampaign, setPromoCampaign] = useState("plus-1-month-free");
  const [promoCode, setPromoCode] = useState("");
  const [promoCountry, setPromoCountry] = useState("");
  const [idealBank, setIdealBank] = useState("");
  const [workspaceName, setWorkspaceName] = useState("Codex Workspace");
  const [workspaceId, setWorkspaceId] = useState("");
  const [seatQuantity, setSeatQuantity] = useState(5);
  const [priceInterval, setPriceInterval] = useState("month");
  const [creditQuantity, setCreditQuantity] = useState(13);
  const [pixTaxID, setPixTaxID] = useState("");
  const [pixAutoKind, setPixAutoKind] = useState("cpf");
  const [precheckBusy, setPrecheckBusy] = useState(false);
  const [checkoutBusy, setCheckoutBusy] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [task, setTask] = useState<AnyRow | null>(null);
  const [notice, setNotice] = useState("");
  const [qrValue, setQrValue] = useState("");
  const [qrImage, setQrImage] = useState("");
  const [logOpen, setLogOpen] = useState(false);
  const [taskLogs, setTaskLogs] = useState<AnyRow[]>([]);
  const [cancelBusy, setCancelBusy] = useState(false);
  const logScrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { void apiFetch("/sunny/checkout/providers").then((data) => { if (data.items?.length) setProviders(data.items); if (data.countries) setCountries(data.countries); }).catch(() => {}); }, []);
  useEffect(() => { void apiFetch("/sunny/mailbox-groups").then((data) => setGroups(data.items || [])).catch(() => setGroups([])); }, []);
  useEffect(() => {
    if (!systemAT) return;
    let active = true;
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (query) params.set("q", query); if (group) params.set("group_id", group); if (status) params.set("status", status); if (planFilter) params.set("plan_type", planFilter); if (trialFilter) params.set("trial_eligibility", trialFilter); if (checkoutFilter) params.set("checkout_kind", checkoutFilter);
    setListLoading(true);
    void apiFetch(`/sunny/sessions?${params}`).then((data) => {
      if (!active) return;
      const nextTotal = Number(data.total || 0);
      const lastPage = Math.max(1, Math.ceil(nextTotal / pageSize));
      if (page > lastPage) {
        setSelected([]);
        setPage(lastPage);
        return;
      }
      setSessions(data.items || []);
      setTotal(nextTotal);
    }).catch(() => { if (active) { setSessions([]); setTotal(0); } }).finally(() => { if (active) setListLoading(false); });
    return () => { active = false; };
  }, [systemAT, query, group, status, planFilter, trialFilter, checkoutFilter, page, pageSize, refreshKey]);
  useEffect(() => { const provider = providers.find((x) => x.value === linkType); if (provider) { setCountry(provider.country); setCurrency(provider.currency); } }, [linkType, providers]);
  useEffect(() => { setExternalRows(parseExternalRows(externalText)); }, [externalText]);
  useEffect(() => { if (logOpen && logScrollRef.current) logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight; }, [logOpen, taskLogs]);
  const visibleExternalRows = useMemo(() => externalRows.slice((page - 1) * pageSize, page * pageSize), [externalRows, page, pageSize]);
  const rows = systemAT ? sessions : visibleExternalRows;
  const listTotal = systemAT ? total : externalRows.length;
  const pageCount = Math.max(1, Math.ceil(listTotal / pageSize));
  const pageFrom = listTotal ? (page - 1) * pageSize + 1 : 0;
  const pageTo = Math.min(page * pageSize, listTotal);
  const selectedCount = selected.length;
  const allCurrentSelected = rows.length > 0 && selected.length === rows.length;
  function switchMode(value: boolean) { setSystemAT(value); setSelected([]); setTask(null); setPage(1); }
  function changeFilter(setter: (value: string) => void, value: string) { setter(value); setSelected([]); setPage(1); }
  function toggleRow(index: number) { setSelected((old) => old.includes(index) ? old.filter((x) => x !== index) : [...old, index]); }
  function toggleCurrentPage() { setSelected(allCurrentSelected ? [] : rows.map((_, index) => index)); }
  function changePage(value: number) { setPage(Math.min(pageCount, Math.max(1, value))); setSelected([]); }
  function changePageSize(value: number) { setPageSize(value); setPage(1); setSelected([]); }
  function refreshRows() { setSelected([]); if (systemAT) setRefreshKey((value) => value + 1); else setExternalRows(parseExternalRows(externalText)); setNotice("账户列表已刷新"); window.setTimeout(() => setNotice(""), 1800); }
  function updatePath(value: string) { setLinkType(value); const provider = providers.find((x) => x.value === value); if (provider) { setCountry(provider.country); setCurrency(provider.currency); } }
  async function precheck() {
    if (!splitLines(checkoutProxies).length || !splitLines(promotionProxies).length || !selected.length) { setNotice("请先填写两个代理池并勾选账户"); return; }
    const selectedRows = rows.filter((_, index) => selected.includes(index));
    setPrecheckBusy(true);
    try {
      const data = await apiFetch("/sunny/checkout/precheck", { method: "POST", body: JSON.stringify({ system_at: systemAT, session_ids: systemAT ? selectedRows.map((x) => Number(x.id)) : [], external_ats: systemAT ? [] : selectedRows.map((x) => x.token), checkout_proxies: checkoutProxies, promotion_proxies: promotionProxies }) });
      const byEmail = new Map((data.items || []).map((item: AnyRow) => [item.email, item]));
      const apply = (old: AnyRow[]) => old.map((row) => { const found = byEmail.get(row.email) as AnyRow | undefined; return found ? { ...row, ...found } : row; });
      if (systemAT) setSessions(apply); else setExternalRows(apply);
      setNotice("试用资格与 Checkout 检测完成");
    } catch (error: any) { setNotice(error.message || String(error)); } finally { setPrecheckBusy(false); }
  }
  async function copy(value: string) { await navigator.clipboard?.writeText(value); setNotice("支付链接已复制"); window.setTimeout(() => setNotice(""), 1800); }
  async function cancelTask() {
    if (!task?.id || task.terminal || cancelBusy) return;
    setCancelBusy(true);
    try {
      await apiFetch(`/tasks/${encodeURIComponent(String(task.id))}/cancel`, { method: "POST" });
      setNotice("已请求停止提链任务");
      const current = await apiFetch(`/tasks/${encodeURIComponent(String(task.id))}`);
      setTask(current);
    } catch (error: any) { setNotice(error.message || String(error)); } finally { setCancelBusy(false); }
  }
  async function start() {
    if (!splitLines(checkoutProxies).length || !splitLines(promotionProxies).length) { setNotice("Checkout 代理池和 Promotion 代理池都必须填写"); return; }
    if (!selected.length) { setNotice("请先勾选需要提链的账户"); return; }
    setCheckoutBusy(true); setTask(null); setTaskLogs([]); setLogOpen(true);
    const selectedRows = rows.filter((_, index) => selected.includes(index));
    try {
      const response = await apiFetch("/sunny/checkout", { method: "POST", body: JSON.stringify({ system_at: systemAT, session_ids: systemAT ? selectedRows.map((x) => Number(x.id)) : [], external_ats: systemAT ? [] : selectedRows.map((x) => x.token), checkout_proxies: checkoutProxies, promotion_proxies: promotionProxies, plan, link_type: linkType, country, currency, retry_count: retryCount, concurrency, use_promo: usePromo, promo_campaign: promoCampaign, promo_country: promoCountry, promo_code: promoCode, ideal_bank: idealBank, workspace_name: workspaceName, workspace_id: workspaceId, seat_quantity: seatQuantity, price_interval: priceInterval, credit_quantity: creditQuantity, pix_tax_id: pixTaxID, pix_auto_kind: pixAutoKind }) });
      const taskID = String(response.id || response.task_id);
      let current = response;
      let eventCursor = 0;
      setTask(response);
      const readEvents = async () => {
        const collected: AnyRow[] = [];
        for (let page = 0; page < 5; page += 1) {
          const data = await apiFetch(`/tasks/${encodeURIComponent(taskID)}/events?since=${eventCursor}&limit=200`);
          const next = data.items || [];
          if (!next.length) break;
          eventCursor = Number(next[next.length - 1].id || eventCursor);
          collected.push(...next);
          if (next.length < 200) break;
        }
        if (collected.length) setTaskLogs((old) => [...old, ...collected]);
      };
      await readEvents().catch(() => {});
      for (let i = 0; i < 240; i += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        current = await apiFetch(`/tasks/${encodeURIComponent(taskID)}`);
        await readEvents().catch(() => {});
        setTask(current);
        if (current.terminal) break;
      }
      await readEvents().catch(() => {});
      const items = current.result?.items || [];
      if (systemAT) setSessions((old) => old.map((row) => { const found = items.find((item: AnyRow) => item.email === row.email); return found ? { ...row, checkout_result: found } : row; }));
      else setExternalRows((old) => old.map((row) => { const found = items.find((item: AnyRow) => item.email === row.email); return found ? { ...row, checkout_result: found } : row; }));
      setNotice(current.status === "succeeded" ? "提链任务完成" : "提链任务结束，请查看结果");
    } catch (error: any) { setNotice(error.message || String(error)); } finally { setCheckoutBusy(false); }
  }
  const statusLabel = task ? `${task.status} · ${task.progress || ""}` : selectedCount ? `已选择 ${selectedCount} 个账户` : "未选择账户";
  return <div className="space-y-5">
    {notice && <div className="fixed right-5 top-20 z-[500] rounded-xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white shadow-xl">{notice}</div>}
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--accent)]">PAYMENT ROUTER</p><h1 className="mt-1 text-2xl font-black">提链管理</h1><p className="mt-2 text-sm text-[var(--text-secondary)]">为已注册 ChatGPT 账户批量提取支付链接、跳转地址和支付二维码。</p></div><div className="rounded-full border border-[var(--border)] px-3 py-1 text-xs text-[var(--text-muted)]">{statusLabel}</div></div></section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5"><div className="grid gap-4 md:grid-cols-2"><label><span className="mb-2 block text-sm font-semibold">Checkout 代理池 <b className="text-red-500">*</b></span><textarea className="min-h-28 w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]" value={checkoutProxies} onChange={(e) => setCheckoutProxies(e.target.value)} placeholder="每行一个代理，支持 http://、https://、socks5://" /></label><label><span className="mb-2 block text-sm font-semibold">Promotion 代理池 <b className="text-red-500">*</b></span><textarea className="min-h-28 w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]" value={promotionProxies} onChange={(e) => setPromotionProxies(e.target.value)} placeholder="每行一个代理，支持 http://、https://、socks5://" /></label></div><p className="mt-3 text-xs text-[var(--text-muted)]">每个代理池最多 500 条。每轮重试会重新选择代理组合；PIX 与 MoMo 按参考路径复用 Checkout 线路。</p></section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5"><h2 className="mb-3 text-sm font-semibold">订阅 / 空间</h2><div className="grid grid-cols-2 gap-2 md:grid-cols-4">{planOptions.map((item) => <button key={item.value} type="button" onClick={() => setPlan(item.value)} className={`rounded-xl border p-3 text-left transition ${plan === item.value ? "border-[var(--accent)] bg-[var(--accent)]/10" : "border-[var(--border)] hover:border-[var(--accent)]/60"}`}><b className="block">{item.label}</b><small className="text-xs text-[var(--text-muted)]">{item.hint}</small></button>)}</div>{plan === "team" && <div className="mt-4 grid gap-3 md:grid-cols-4"><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={workspaceName} onChange={(e) => setWorkspaceName(e.target.value)} placeholder="空间名称" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)} placeholder="已有空间 ID" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" type="number" min={2} value={seatQuantity} onChange={(e) => setSeatQuantity(Number(e.target.value))} placeholder="席位数量" /><select className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={priceInterval} onChange={(e) => setPriceInterval(e.target.value)}><option value="month">按月</option><option value="year">按年</option></select></div>}{plan === "codex_low" && <div className="mt-4 grid gap-3 md:grid-cols-2"><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={workspaceName} onChange={(e) => setWorkspaceName(e.target.value)} placeholder="Codex 空间名称" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" type="number" min={1} value={creditQuantity} onChange={(e) => setCreditQuantity(Number(e.target.value))} placeholder="积分数量" /></div>}</section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5">
      <h2 className="mb-3 text-sm font-semibold">支付路径</h2>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">{providers.map((item) => <button key={item.value} type="button" onClick={() => updatePath(item.value)} className={`rounded-xl border px-3 py-2 text-left ${linkType === item.value ? "border-[var(--accent)] bg-[var(--accent)]/10" : "border-[var(--border)]"}`}><b className="block text-sm">{item.label}</b><small className="block truncate text-[11px] text-[var(--text-muted)]">{item.hint}</small></button>)}</div>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="w-56 max-w-full"><span className="mb-1 block text-xs text-[var(--text-muted)]">国家 / 地区</span><select className="h-10 w-full rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" value={country} onChange={(e) => { setCountry(e.target.value); setCurrency(countries[e.target.value] || currencyByCountry[e.target.value] || "USD"); }}>{Object.keys(countries).map((key) => <option key={key} value={key}>{key} · {countryNames[key] || key}</option>)}</select></label>
        <label className="w-36 max-w-full"><span className="mb-1 block text-xs text-[var(--text-muted)]">币种</span><select className="h-10 w-full rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" value={currency} onChange={(e) => setCurrency(e.target.value)}>{Object.values(countries).filter((x, i, a) => a.indexOf(x) === i).map((value) => <option key={value}>{value}</option>)}</select></label>
        <label className="w-56 max-w-full"><span className="mb-1 block text-xs text-[var(--text-muted)]">Promotion 国家 / 地区</span><select className="h-10 w-full rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" value={promoCountry} onChange={(e) => setPromoCountry(e.target.value)}><option value="">按支付路径默认</option>{Object.keys(countries).map((key) => <option key={key} value={key}>{key} · {countryNames[key] || key}</option>)}<option value="TR">TR · 土耳其</option></select></label>
        {linkType === "ideal" && <label className="w-60 max-w-full"><span className="mb-1 block text-xs text-[var(--text-muted)]">iDEAL 银行</span><select className="h-10 w-full rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" value={idealBank} onChange={(e) => setIdealBank(e.target.value)}><option value="">在 iDEAL 支付页面选择</option></select></label>}
      </div>
      {linkType === "pix" && <div className="mt-3 grid gap-3 md:grid-cols-2"><select className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={pixAutoKind} onChange={(e) => setPixAutoKind(e.target.value)}><option value="cpf">主要生成 CPF</option><option value="mixed">CPF / CNPJ 交替</option><option value="cnpj">仅生成 CNPJ</option></select><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={pixTaxID} onChange={(e) => setPixTaxID(e.target.value)} placeholder="固定 CPF / CNPJ（选填）" /></div>}
    </section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5">
      <div className="flex flex-wrap items-end gap-3">
        <label className="w-64 max-w-full"><span className="mb-1 flex h-5 items-center gap-2 text-xs text-[var(--text-muted)]"><span>优惠配置</span><button type="button" className={`sr-switch-only scale-90 ${usePromo ? "on" : ""}`} aria-label="启用优惠配置" onClick={(event) => { event.preventDefault(); setUsePromo(!usePromo); }}><span /></button></span><input className="h-10 w-full rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" value={promoCampaign} onChange={(e) => setPromoCampaign(e.target.value)} disabled={!usePromo || plan !== "plus"} placeholder="Plus Campaign" /></label>
        <label className="w-52 max-w-full"><span className="mb-1 flex h-5 items-center text-xs text-[var(--text-muted)]">优惠码（Team）</span><input className="h-10 w-full rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" value={promoCode} onChange={(e) => setPromoCode(e.target.value)} disabled={plan !== "team"} placeholder="选填" /></label>
        <label><span className="mb-1 flex h-5 items-center text-xs text-[var(--text-muted)]">失败重试次数</span><input className="h-10 w-24 rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" type="number" min={1} max={50} value={retryCount} onChange={(e) => setRetryCount(Number(e.target.value))} /></label>
        <label><span className="mb-1 flex h-5 items-center text-xs text-[var(--text-muted)]">提链并发</span><input className="h-10 w-24 rounded-xl border border-[var(--border)] bg-transparent px-3 text-sm" type="number" min={1} max={20} value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value))} /></label>
        <Button variant="outline" disabled={precheckBusy || selectedCount === 0} onClick={() => void precheck()}>{precheckBusy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}{precheckBusy ? "检测中..." : "检测资格 / Checkout"}</Button>
        <Button className="ml-auto" disabled={checkoutBusy || selectedCount === 0} onClick={() => void start()}>{checkoutBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}{checkoutBusy ? "提链中..." : "开始提链"}</Button>
      </div>
    </section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><span className="text-sm font-semibold">账户 AT</span><button type="button" className={`sr-switch-only ${systemAT ? "on" : ""}`} onClick={() => switchMode(!systemAT)}><span /></button><span className="text-xs text-[var(--text-muted)]">使用系统 AT</span></div>{!systemAT && <div className="flex gap-2"><button className="sr-text-btn" disabled={!selected.length} onClick={() => { const offset = (page - 1) * pageSize; const doomed = new Set(selected.map((index) => offset + index)); const tokens = splitLines(externalText).filter((_, index) => !doomed.has(index)); setExternalText(tokens.join("\n")); setSelected([]); setPage(Math.min(page, Math.max(1, Math.ceil(tokens.length / pageSize)))); }}><Trash2 className="h-4 w-4" />删除选中</button><button className="sr-text-btn" onClick={() => { setExternalText(""); setExternalRows([]); setSelected([]); setPage(1); }}><Trash2 className="h-4 w-4" />全部清空</button></div>}</div>
      {systemAT ? <div className="mb-3 flex flex-nowrap gap-2 overflow-x-auto pb-1">
        <input className="h-9 w-52 shrink-0 rounded-lg border border-[var(--border)] bg-transparent px-3 text-xs outline-none focus:border-[var(--accent)]" value={query} onChange={(e) => changeFilter(setQuery, e.target.value)} placeholder="搜索邮箱" />
        <select aria-label="分组筛选" className="h-9 w-40 shrink-0 rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] px-2 text-xs" value={group} onChange={(e) => changeFilter(setGroup, e.target.value)}><option value="">全部分组</option>{groups.map((item) => <option key={item.id} value={String(item.id)}>{item.name || `分组 ${item.id}`}</option>)}</select>
        <select aria-label="状态筛选" className="h-9 w-32 shrink-0 rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] px-2 text-xs" value={status} onChange={(e) => changeFilter(setStatus, e.target.value)}><option value="">全部状态</option>{sessionStatuses.map((value) => <option key={value} value={value}>{value}</option>)}</select>
        <select aria-label="套餐筛选" className="h-9 w-32 shrink-0 rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] px-2 text-xs" value={planFilter} onChange={(e) => changeFilter(setPlanFilter, e.target.value)}><option value="">全部套餐</option>{sessionPlans.map((value) => <option key={value} value={value}>{planLabels[value]}</option>)}</select>
        <select aria-label="试用资格筛选" className="h-9 w-36 shrink-0 rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] px-2 text-xs" value={trialFilter} onChange={(e) => changeFilter(setTrialFilter, e.target.value)}><option value="">全部试用资格</option><option value="eligible">有0元试用</option><option value="ineligible">无0元试用</option><option value="unknown">未检测</option></select>
        <select aria-label="Checkout 类型筛选" className="h-9 w-40 shrink-0 rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] px-2 text-xs" value={checkoutFilter} onChange={(e) => changeFilter(setCheckoutFilter, e.target.value)}><option value="">全部 Checkout</option><option value="oaics">OAICS</option><option value="cs_live">CS Live</option><option value="cs_test">CS Test</option><option value="unknown">未检测</option></select>
      </div> : <textarea className="mb-4 min-h-28 w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm" value={externalText} onChange={(e) => { setExternalText(e.target.value); setSelected([]); setPage(1); }} placeholder="每行一个 AT，支持任意数量；仅本次任务临时使用，不会长期保存" />}
      {task && <div className="mb-3 rounded-lg border border-[var(--border)] bg-[var(--bg-main)]/40 p-3"><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="text-sm font-semibold">最近提链任务</div><div className="mt-1 text-xs text-[var(--text-muted)]">状态：{taskStatusLabel(task.status)} · 进度：{task.progress || "0/0"} · 成功 {task.success_count || 0} · 失败 {task.error_count || 0}</div></div>{!task.terminal && <Button variant="outline" disabled={cancelBusy || task.status === "cancel_requested"} onClick={() => void cancelTask()}>{cancelBusy || task.status === "cancel_requested" ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <X className="mr-2 h-4 w-4" />}{cancelBusy || task.status === "cancel_requested" ? "停止中..." : "停止提链"}</Button>}</div>{task.terminal && <div className={`mt-2 text-xs ${task.status === "succeeded" ? "text-emerald-600" : "text-red-500"}`}>{task.status === "succeeded" ? `任务完成：成功 ${task.success_count || 0}，失败 ${task.error_count || 0}` : `任务结束：${task.error || "请查看下方账户结果"}`}</div>}</div>}
      <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg-main)]/40 p-2">
        <button type="button" className="sr-text-btn" disabled={!rows.length} onClick={toggleCurrentPage}><input className="pointer-events-none" type="checkbox" tabIndex={-1} checked={allCurrentSelected} readOnly />{allCurrentSelected ? "取消本页全选" : "本页全选"}</button>
        <span className="text-xs text-[var(--text-muted)]">已选择 {selectedCount} 项</span>
        {selectedCount > 0 && <button type="button" className="sr-link" onClick={() => setSelected([])}>清除选择</button>}
        <button type="button" className="sr-text-btn ml-auto" disabled={listLoading} onClick={refreshRows}>{listLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}刷新列表</button>
      </div>
      <div className="relative overflow-x-auto">{listLoading && <div className="absolute inset-0 z-10 flex items-center justify-center bg-[var(--bg-shell)]/70"><Loader2 className="h-5 w-5 animate-spin text-[var(--accent)]" /></div>}<table className="sr-account-table w-full min-w-[1420px] table-fixed text-left text-xs"><colgroup><col className="w-[58px]" /><col className="w-[190px]" /><col className="w-[130px]" /><col className="w-[100px]" /><col className="w-[86px]" /><col className="w-[112px]" /><col className="w-[128px]" /><col className="w-[118px]" /><col className="w-[330px]" /><col className="w-[86px]" /><col className="w-[150px]" /></colgroup><thead className="border-b border-[var(--border)] text-[var(--text-muted)]"><tr><th className="p-2"><input type="checkbox" aria-label="本页全选" checked={allCurrentSelected} disabled={!rows.length} onChange={toggleCurrentPage} /></th><th className="p-2">邮箱</th><th className="p-2">分组</th><th className="p-2">状态</th><th className="p-2">套餐</th><th className="p-2">试用资格</th><th className="p-2">Checkout 类型</th><th className="p-2">支付路径</th><th className="p-2">支付链接</th><th className="p-2">支付二维码</th><th className="p-2">操作</th></tr></thead><tbody>{rows.length ? rows.map((row, index) => { const result = row.checkout_result || {}; const link = resultDisplayLink(result); const error = resultError(result); const failed = normalized(result.status) === "failed"; const statusValue = row.at_status || row.status; const planValue = row.plan_type; const pathValue = result.link_type; const detail = [result.plan && `套餐 ${labelFor(result.plan, planLabels)}`, result.country && `地区 ${result.country}`, result.currency && `币种 ${result.currency}`, result.checkout_amount != null && `金额 ${result.checkout_amount}`, result.payment_methods?.length && `支付方式 ${result.payment_methods.join(", ")}`, result.checkout_session_id && `会话 ${result.checkout_session_id}`, result.promo_requested != null && `优惠 ${result.promo_applied === true ? "已生效" : result.promo_applied === false ? "未生效" : "待确认"}`].filter(Boolean).join(" · "); const qrImage = resultQrImage(result); return <tr key={`${row.id || row.index || index}`} className="border-b border-[var(--border)]/60"><td className="p-2"><input type="checkbox" checked={selected.includes(index)} onChange={() => toggleRow(index)} /></td><td className="p-2"><div className="truncate" title={row.email}>{row.email || "未知邮箱"}</div></td><td className="p-2"><div className="truncate" title={row.group_name || "-"}>{row.group_name || "-"}</div></td><td className="p-2"><AccountStatusBadge value={statusValue} /></td><td className="p-2"><AccountPlanBadge value={planValue} /></td><td className="p-2"><AccountTrialValue row={row} /></td><td className="p-2"><AccountCheckoutValue row={row} /></td><td className="p-2">{pathValue ? <CompactBadge label={labelFor(pathValue, pathLabels)} tone={pathTone(pathValue)} /> : <span className="text-[var(--text-muted)]">-</span>}</td><td className="p-2">{link ? <button className="block w-full truncate text-left font-medium text-[var(--accent)] underline decoration-[var(--accent)]/40 underline-offset-2 hover:decoration-[var(--accent)]" title={`${link}\n${detail}\n点击复制支付链接`} onClick={() => void copy(link)}>{link}</button> : error || failed ? <div className="truncate font-medium text-red-600 dark:text-red-400" title={`提链失败：${error || "任务未返回支付链接"}`}>提链失败：{error || "任务未返回支付链接"}</div> : <span className="text-[var(--text-muted)]">-</span>}</td><td className="p-2">{qrImage ? <QRImageThumb src={qrImage} onClick={() => { setQrValue(result.qr_data || ""); setQrImage(qrImage); }} /> : result.qr_data ? <QRThumb value={result.qr_data} onClick={() => { setQrValue(result.qr_data); setQrImage(""); }} /> : <span className="text-[var(--text-muted)]">-</span>}</td><td className="p-2">{link ? <div className="flex items-center gap-1"><button className="sr-link inline-flex items-center gap-1 whitespace-nowrap" title="复制支付链接" onClick={() => void copy(link)}><Clipboard className="h-3 w-3" />复制</button><button className="sr-link inline-flex items-center gap-1 whitespace-nowrap" title="在新窗口打开支付链接" onClick={() => window.open(link, "_blank", "noopener,noreferrer")}><ExternalLink className="h-3 w-3" />打开</button></div> : <span className="text-[var(--text-muted)]">-</span>}</td></tr>; }) : <tr><td colSpan={11} className="p-10 text-center text-sm text-[var(--text-muted)]">暂无账户，请先导入或选择 AT。</td></tr>}</tbody></table></div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] pt-3 text-xs text-[var(--text-muted)]">
        <div className="flex items-center gap-3"><span>显示 {pageFrom} 至 {pageTo}，共 {listTotal} 条</span><label className="flex items-center gap-1.5">每页<select className="h-8 rounded-lg border border-[var(--border)] bg-[var(--bg-shell)] px-2" value={pageSize} onChange={(e) => changePageSize(Number(e.target.value))}>{[10, 20, 50, 100].map((value) => <option key={value} value={value}>{value}</option>)}</select></label></div>
        <div className="flex items-center gap-2"><button type="button" className="round-tool h-8 w-8" title="上一页" disabled={page <= 1 || listLoading} onClick={() => changePage(page - 1)}><ChevronLeft className="h-4 w-4" /></button><span className="min-w-20 text-center">第 {page} / {pageCount} 页</span><button type="button" className="round-tool h-8 w-8" title="下一页" disabled={page >= pageCount || listLoading} onClick={() => changePage(page + 1)}><ChevronRight className="h-4 w-4" /></button></div>
      </div>
    </section>
    {(qrValue || qrImage) && <QRModal value={qrValue} image={qrImage} onClose={() => { setQrValue(""); setQrImage(""); }} />}
    <CheckoutLogFloat open={logOpen} onToggle={() => setLogOpen((value) => !value)} task={task} logs={taskLogs} scrollRef={logScrollRef} />
  </div>;
}
