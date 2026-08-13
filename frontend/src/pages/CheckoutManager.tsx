import { useEffect, useMemo, useState } from "react";
import QRCode from "qrcode";
import { Clipboard, Download, ExternalLink, Loader2, Play, Trash2, X } from "lucide-react";
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

function splitLines(value: string) { return value.split(/\r?\n/).map((x) => x.trim()).filter(Boolean); }
function resultValue(item: AnyRow) { return item.payment_link || ""; }
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
  const [busy, setBusy] = useState(false);
  const [task, setTask] = useState<AnyRow | null>(null);
  const [notice, setNotice] = useState("");
  const [qrValue, setQrValue] = useState("");
  const [qrImage, setQrImage] = useState("");

  useEffect(() => { void apiFetch("/sunny/checkout/providers").then((data) => { if (data.items?.length) setProviders(data.items); if (data.countries) setCountries(data.countries); }).catch(() => {}); }, []);
  useEffect(() => { if (!systemAT) return; const params = new URLSearchParams({ page: "1", page_size: "500" }); if (query) params.set("q", query); if (group) params.set("group_id", group); if (status) params.set("status", status); if (planFilter) params.set("plan_type", planFilter); if (trialFilter) params.set("trial_eligibility", trialFilter); if (checkoutFilter) params.set("checkout_kind", checkoutFilter); void apiFetch(`/sunny/sessions?${params}`).then((data) => setSessions(data.items || [])).catch(() => setSessions([])); }, [systemAT, query, group, status, planFilter, trialFilter, checkoutFilter]);
  useEffect(() => { const provider = providers.find((x) => x.value === linkType); if (provider) { setCountry(provider.country); setCurrency(provider.currency); } }, [linkType, providers]);
  useEffect(() => { const tokens = splitLines(externalText); setExternalRows(tokens.map((token, index) => { const info = externalATInfo(token); return { index, token, email: (token.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i) || [info.email || ""])[0], at_status: token.startsWith("eyJ") || token.includes(".") ? info.status : "格式无效", expires_at: info.expires_at, selected: false }; })); }, [externalText]);
  const visibleSessions = useMemo(() => sessions, [sessions]);
  const rows = systemAT ? visibleSessions : externalRows;
  const selectedCount = selected.length;
  function switchMode(value: boolean) { setSystemAT(value); setSelected([]); setTask(null); }
  function toggleRow(index: number) { setSelected((old) => old.includes(index) ? old.filter((x) => x !== index) : [...old, index]); }
  function updatePath(value: string) { setLinkType(value); const provider = providers.find((x) => x.value === value); if (provider) { setCountry(provider.country); setCurrency(provider.currency); } }
  async function precheck() {
    if (!splitLines(checkoutProxies).length || !splitLines(promotionProxies).length || !selected.length) { setNotice("请先填写两个代理池并勾选账户"); return; }
    const selectedRows = rows.filter((_, index) => selected.includes(index));
    setBusy(true);
    try {
      const data = await apiFetch("/sunny/checkout/precheck", { method: "POST", body: JSON.stringify({ system_at: systemAT, session_ids: systemAT ? selectedRows.map((x) => Number(x.id)) : [], external_ats: systemAT ? [] : selectedRows.map((x) => x.token), checkout_proxies: checkoutProxies, promotion_proxies: promotionProxies }) });
      const byEmail = new Map((data.items || []).map((item: AnyRow) => [item.email, item]));
      const apply = (old: AnyRow[]) => old.map((row) => { const found = byEmail.get(row.email) as AnyRow | undefined; return found ? { ...row, ...found } : row; });
      if (systemAT) setSessions(apply); else setExternalRows(apply);
      setNotice("试用资格与 Checkout 检测完成");
    } catch (error: any) { setNotice(error.message || String(error)); } finally { setBusy(false); }
  }
  async function copy(value: string) { await navigator.clipboard?.writeText(value); setNotice("支付链接已复制"); window.setTimeout(() => setNotice(""), 1800); }
  async function start() {
    if (!splitLines(checkoutProxies).length || !splitLines(promotionProxies).length) { setNotice("Checkout 代理池和 Promotion 代理池都必须填写"); return; }
    if (!selected.length) { setNotice("请先勾选需要提链的账户"); return; }
    setBusy(true); setTask(null);
    const selectedRows = rows.filter((_, index) => selected.includes(index));
    try {
      const response = await apiFetch("/sunny/checkout", { method: "POST", body: JSON.stringify({ system_at: systemAT, session_ids: systemAT ? selectedRows.map((x) => Number(x.id)) : [], external_ats: systemAT ? [] : selectedRows.map((x) => x.token), checkout_proxies: checkoutProxies, promotion_proxies: promotionProxies, plan, link_type: linkType, country, currency, retry_count: retryCount, concurrency, use_promo: usePromo, promo_campaign: promoCampaign, promo_country: promoCountry, promo_code: promoCode, ideal_bank: idealBank, workspace_name: workspaceName, workspace_id: workspaceId, seat_quantity: seatQuantity, price_interval: priceInterval, credit_quantity: creditQuantity, pix_tax_id: pixTaxID, pix_auto_kind: pixAutoKind }) });
      const taskID = String(response.id || response.task_id); let current = response; for (let i = 0; i < 240; i += 1) { await new Promise((resolve) => window.setTimeout(resolve, 1000)); current = await apiFetch(`/tasks/${encodeURIComponent(taskID)}`); setTask(current); if (current.terminal) break; } const items = current.result?.items || []; if (systemAT) setSessions((old) => old.map((row) => { const found = items.find((item: AnyRow) => item.email === row.email); return found ? { ...row, checkout_result: found } : row; })); else setExternalRows((old) => old.map((row) => { const found = items.find((item: AnyRow) => item.email === row.email); return found ? { ...row, checkout_result: found } : row; })); setNotice(current.status === "succeeded" ? "提链任务完成" : "提链任务结束，请查看结果");
    } catch (error: any) { setNotice(error.message || String(error)); } finally { setBusy(false); }
  }
  const statusLabel = task ? `${task.status} · ${task.progress || ""}` : selectedCount ? `已选择 ${selectedCount} 个账户` : "未选择账户";
  return <div className="space-y-5">
    {notice && <div className="fixed right-5 top-20 z-[500] rounded-xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-white shadow-xl">{notice}</div>}
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5 shadow-sm"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="text-xs font-bold uppercase tracking-[0.16em] text-[var(--accent)]">PAYMENT ROUTER</p><h1 className="mt-1 text-2xl font-black">提链管理</h1><p className="mt-2 text-sm text-[var(--text-secondary)]">为已注册 ChatGPT 账户批量提取支付链接、跳转地址和支付二维码。</p></div><div className="rounded-full border border-[var(--border)] px-3 py-1 text-xs text-[var(--text-muted)]">{statusLabel}</div></div></section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5"><div className="grid gap-4 md:grid-cols-2"><label><span className="mb-2 block text-sm font-semibold">Checkout 代理池 <b className="text-red-500">*</b></span><textarea className="min-h-28 w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]" value={checkoutProxies} onChange={(e) => setCheckoutProxies(e.target.value)} placeholder="每行一个代理，支持 http://、https://、socks5://" /></label><label><span className="mb-2 block text-sm font-semibold">Promotion 代理池 <b className="text-red-500">*</b></span><textarea className="min-h-28 w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm outline-none focus:border-[var(--accent)]" value={promotionProxies} onChange={(e) => setPromotionProxies(e.target.value)} placeholder="每行一个代理，支持 http://、https://、socks5://" /></label></div><p className="mt-3 text-xs text-[var(--text-muted)]">每个代理池最多 500 条。每轮重试会重新选择代理组合；PIX 与 MoMo 按参考路径复用 Checkout 线路。</p></section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5"><div className="grid gap-4 md:grid-cols-2"><div><div className="mb-2 flex items-center justify-between"><span className="text-sm font-semibold">优惠配置</span><button type="button" className={`sr-switch-only ${usePromo ? "on" : ""}`} onClick={() => setUsePromo(!usePromo)}><span /></button></div><input className="w-full rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={promoCampaign} onChange={(e) => setPromoCampaign(e.target.value)} disabled={!usePromo || plan !== "plus"} placeholder="Plus Campaign" /></div><div><span className="mb-2 block text-sm font-semibold">优惠码（Team）</span><input className="w-full rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={promoCode} onChange={(e) => setPromoCode(e.target.value)} disabled={plan !== "team"} placeholder="选填" /></div></div></section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5"><h2 className="mb-3 text-sm font-semibold">订阅 / 空间</h2><div className="grid grid-cols-2 gap-2 md:grid-cols-4">{planOptions.map((item) => <button key={item.value} type="button" onClick={() => setPlan(item.value)} className={`rounded-xl border p-3 text-left transition ${plan === item.value ? "border-[var(--accent)] bg-[var(--accent)]/10" : "border-[var(--border)] hover:border-[var(--accent)]/60"}`}><b className="block">{item.label}</b><small className="text-xs text-[var(--text-muted)]">{item.hint}</small></button>)}</div>{plan === "team" && <div className="mt-4 grid gap-3 md:grid-cols-4"><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={workspaceName} onChange={(e) => setWorkspaceName(e.target.value)} placeholder="空间名称" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)} placeholder="已有空间 ID" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" type="number" min={2} value={seatQuantity} onChange={(e) => setSeatQuantity(Number(e.target.value))} placeholder="席位数量" /><select className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={priceInterval} onChange={(e) => setPriceInterval(e.target.value)}><option value="month">按月</option><option value="year">按年</option></select></div>}{plan === "codex_low" && <div className="mt-4 grid gap-3 md:grid-cols-2"><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={workspaceName} onChange={(e) => setWorkspaceName(e.target.value)} placeholder="Codex 空间名称" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" type="number" min={1} value={creditQuantity} onChange={(e) => setCreditQuantity(Number(e.target.value))} placeholder="积分数量" /></div>}</section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5">
      <h2 className="mb-3 text-sm font-semibold">支付路径</h2>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">{providers.map((item) => <button key={item.value} type="button" onClick={() => updatePath(item.value)} className={`rounded-xl border px-3 py-2 text-left ${linkType === item.value ? "border-[var(--accent)] bg-[var(--accent)]/10" : "border-[var(--border)]"}`}><b className="block text-sm">{item.label}</b><small className="block truncate text-[11px] text-[var(--text-muted)]">{item.hint}</small></button>)}</div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label><span className="mb-1 block text-xs text-[var(--text-muted)]">国家 / 地区</span><select className="w-full rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={country} onChange={(e) => { setCountry(e.target.value); setCurrency(countries[e.target.value] || currencyByCountry[e.target.value] || "USD"); }}>{Object.keys(countries).map((key) => <option key={key} value={key}>{key} · {countryNames[key] || key}</option>)}</select></label>
        <label><span className="mb-1 block text-xs text-[var(--text-muted)]">币种</span><select className="w-full rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={currency} onChange={(e) => setCurrency(e.target.value)}>{Object.values(countries).filter((x, i, a) => a.indexOf(x) === i).map((value) => <option key={value}>{value}</option>)}</select></label>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label><span className="mb-1 block text-xs text-[var(--text-muted)]">Promotion 国家 / 地区</span><select className="w-full rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={promoCountry} onChange={(e) => setPromoCountry(e.target.value)}><option value="">按支付路径默认</option>{Object.keys(countries).map((key) => <option key={key} value={key}>{key} · {countryNames[key] || key}</option>)}<option value="TR">TR · 土耳其</option></select></label>
        {linkType === "ideal" ? <label><span className="mb-1 block text-xs text-[var(--text-muted)]">iDEAL 银行</span><select className="w-full rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={idealBank} onChange={(e) => setIdealBank(e.target.value)}><option value="">在 iDEAL 支付页面选择</option></select></label> : <div />}
      </div>
      {linkType === "pix" && <div className="mt-3 grid gap-3 md:grid-cols-2"><select className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={pixAutoKind} onChange={(e) => setPixAutoKind(e.target.value)}><option value="cpf">主要生成 CPF</option><option value="mixed">CPF / CNPJ 交替</option><option value="cnpj">仅生成 CNPJ</option></select><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={pixTaxID} onChange={(e) => setPixTaxID(e.target.value)} placeholder="固定 CPF / CNPJ（选填）" /></div>}
    </section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5"><div className="flex flex-wrap items-end gap-3"><label><span className="mb-1 block text-xs text-[var(--text-muted)]">失败重试次数</span><input className="w-28 rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" type="number" min={1} max={50} value={retryCount} onChange={(e) => setRetryCount(Number(e.target.value))} /></label><label><span className="mb-1 block text-xs text-[var(--text-muted)]">提链并发</span><input className="w-28 rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" type="number" min={1} max={20} value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value))} /></label><Button variant="outline" disabled={busy || selectedCount === 0} onClick={() => void precheck()}>检测资格 / Checkout</Button><Button className="ml-auto" disabled={busy || selectedCount === 0} onClick={() => void start()}>{busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}{busy ? "提链中..." : "开始提链"}</Button></div></section>
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--bg-shell)] p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><span className="text-sm font-semibold">账户 AT</span><button type="button" className={`sr-switch-only ${systemAT ? "on" : ""}`} onClick={() => switchMode(!systemAT)}><span /></button><span className="text-xs text-[var(--text-muted)]">使用系统 AT</span></div>{!systemAT && <div className="flex gap-2"><button className="sr-text-btn" disabled={!selected.length} onClick={() => { const doomed = new Set(selected); setExternalRows((old) => old.filter((_, index) => !doomed.has(index))); setSelected([]); }}><Trash2 className="h-4 w-4" />删除选中</button><button className="sr-text-btn" onClick={() => { setExternalText(""); setExternalRows([]); setSelected([]); }}><Trash2 className="h-4 w-4" />全部清空</button></div>}</div>
      {systemAT ? <div className="mb-3 grid gap-2 md:grid-cols-6"><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm md:col-span-2" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索邮箱" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={group} onChange={(e) => setGroup(e.target.value)} placeholder="分组 ID" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={status} onChange={(e) => setStatus(e.target.value)} placeholder="状态" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={planFilter} onChange={(e) => setPlanFilter(e.target.value)} placeholder="套餐" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={trialFilter} onChange={(e) => setTrialFilter(e.target.value)} placeholder="试用资格" /><input className="rounded-xl border border-[var(--border)] bg-transparent px-3 py-2 text-sm" value={checkoutFilter} onChange={(e) => setCheckoutFilter(e.target.value)} placeholder="Checkout 类型" /></div> : <textarea className="mb-4 min-h-28 w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm" value={externalText} onChange={(e) => setExternalText(e.target.value)} placeholder="每行一个 AT，支持任意数量；仅本次任务临时使用，不会长期保存" />}
      <div className="overflow-x-auto"><table className="w-full min-w-[1050px] text-left text-xs"><thead className="border-b border-[var(--border)] text-[var(--text-muted)]"><tr><th className="p-2">选择</th><th className="p-2">邮箱</th><th className="p-2">分组</th><th className="p-2">状态</th><th className="p-2">套餐</th><th className="p-2">试用资格</th><th className="p-2">Checkout 类型</th><th className="p-2">支付路径</th><th className="p-2">支付链接</th><th className="p-2">支付二维码</th><th className="p-2">操作</th></tr></thead><tbody>{rows.length ? rows.map((row, index) => { const result = row.checkout_result || {}; const link = resultValue(result); return <tr key={`${row.id || row.index || index}`} className="border-b border-[var(--border)]/60"><td className="p-2"><input type="checkbox" checked={selected.includes(index)} onChange={() => toggleRow(index)} /></td><td className="p-2 font-medium" title={row.email}>{row.email || "未知邮箱"}</td><td className="p-2">{row.group_name || "-"}</td><td className="p-2">{row.at_status || row.status || "-"}</td><td className="p-2">{row.plan_type || "-"}</td><td className="p-2">{row.trial_eligibility || "未检测"}</td><td className="p-2">{row.checkout_kind || "未检测"}</td><td className="p-2">{result.link_type || "-"}</td><td className="max-w-[220px] p-2">{link ? <button className="max-w-[210px] truncate text-left text-[var(--accent)] underline" title={link} onClick={() => void copy(link)}>{link}</button> : <span className="text-[var(--text-muted)]">-</span>}</td><td className="p-2">{result.qr_image ? <QRImageThumb src={result.qr_image} onClick={() => { setQrValue(result.qr_data || ""); setQrImage(result.qr_image); }} /> : result.qr_data ? <QRThumb value={result.qr_data} onClick={() => { setQrValue(result.qr_data); setQrImage(""); }} /> : <span className="text-[var(--text-muted)]">-</span>}</td><td className="p-2">{link && <button className="sr-link inline-flex items-center gap-1" onClick={() => window.open(link, "_blank", "noopener,noreferrer")}><ExternalLink className="h-3 w-3" />打开</button>}</td></tr>; }) : <tr><td colSpan={11} className="p-10 text-center text-sm text-[var(--text-muted)]">暂无账户，请先导入或选择 AT。</td></tr>}</tbody></table></div>
    </section>
    {(qrValue || qrImage) && <QRModal value={qrValue} image={qrImage} onClose={() => { setQrValue(""); setQrImage(""); }} />}
  </div>;
}
