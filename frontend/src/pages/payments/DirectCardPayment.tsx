import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import {
  CheckCircle2, CreditCard, Download, FileUp, Link2, Loader2, Play,
  RefreshCw, ShieldCheck, Square, Trash2, UserRound, XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiFetch, cn, triggerBrowserDownload } from "@/lib/utils";

type Row = Record<string, any>;
type FlowMode = "full" | "bind_only" | "link_pay" | "link_only";
type CardElement = { mount: (target: HTMLElement) => void; unmount: () => void; on: (event: string, callback: (value: Row) => void) => void };
type StripeClient = {
  elements: () => { create: (type: string, options?: Row) => CardElement };
  createPaymentMethod: (options: Row) => Promise<Row>;
};

declare global {
  interface Window { Stripe?: (key: string) => StripeClient }
}

type DirectAccount = {
  id: string;
  token: string;
  email: string;
  accountId: string;
  selected: boolean;
  status: "idle" | "running" | "success" | "error";
  stage: string;
  error: string;
  link: string;
  taskId: string;
  billing?: Row;
  fingerprint?: string;
  fingerprintId?: string;
  steps?: Row[];
};

const ACCOUNT_KEY = "sunny:direct-card:accounts:v1";
const SETTINGS_KEY = "sunny:direct-card:settings:v1";
const ACTIVE_TASKS = new Set(["queued", "running"]);
const api = (path: string, body?: Row) => apiFetch(`/payments/gopay/direct-card${path}`, body === undefined ? undefined : { method: "POST", body: JSON.stringify(body) });

function decodeAccount(token: string) {
  try {
    const part = token.split(".")[1];
    const raw = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const claims = JSON.parse(decodeURIComponent(Array.from(atob(raw), (char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`).join("")));
    const profile = claims["https://api.openai.com/profile"] || {};
    const auth = claims["https://api.openai.com/auth"] || {};
    return { email: String(profile.email || claims.email || ""), accountId: String(auth.chatgpt_account_id || auth.account_id || claims.chatgpt_account_id || claims.account_id || "") };
  } catch { return { email: "", accountId: "" }; }
}

function extractTokens(value: string) {
  const named = [...value.matchAll(/["']access_token["']\s*:\s*["'](eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*)["']/gi)].map((match) => match[1]);
  const candidates = named.length ? named : value.match(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*/g) || value.split(/[\r\n,;]+/);
  return [...new Set(candidates.map((item) => item.trim().replace(/^["']|["']$/g, "")).filter((token) => decodeAccount(token).accountId))];
}

function parseProxyPool(value: string) {
  const source = value.trim();
  if (!source) return [];
  let items: unknown[] = [];
  if (source.startsWith("[")) try { const parsed = JSON.parse(source); if (Array.isArray(parsed)) items = parsed; } catch { /* use text parsing */ }
  if (!items.length) items = source.split(/[\s,;，；]+/);
  return [...new Set(items.map((item) => String(item || "").trim().replace(/^["']|["']$/g, "")).filter(Boolean))].slice(0, 500);
}

function loadAccounts(): DirectAccount[] {
  try {
    const items = JSON.parse(localStorage.getItem(ACCOUNT_KEY) || "[]");
    return Array.isArray(items) ? items : [];
  } catch { return []; }
}

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}"); } catch { return {}; }
}

function hasBilling(value?: Row) {
  return Boolean(value?.name && value?.line1 && value?.city && value?.postal_code && value?.country);
}

async function fetchBillingBatch(items: DirectAccount[]) {
  const results = new Map<string, Row>();
  let cursor = 0;
  async function worker() {
    while (cursor < items.length) {
      const account = items[cursor++];
      const response = await api("/address", {});
      results.set(account.id, response.billing || {});
    }
  }
  await Promise.all(Array.from({ length: Math.min(8, items.length) }, worker));
  return results;
}

let stripePromise: Promise<(key: string) => StripeClient> | null = null;
function ensureStripe() {
  if (window.Stripe) return Promise.resolve(window.Stripe);
  if (stripePromise) return stripePromise;
  stripePromise = new Promise<(key: string) => StripeClient>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://js.stripe.com/v3/";
    script.async = true;
    script.dataset.directCardStripe = "1";
    const timer = window.setTimeout(() => { script.remove(); reject(new Error("Stripe 安全组件加载超时")); }, 20000);
    script.onload = () => {
      window.clearTimeout(timer);
      if (window.Stripe) resolve(window.Stripe);
      else reject(new Error("Stripe 安全组件初始化失败"));
    };
    script.onerror = () => { window.clearTimeout(timer); reject(new Error("Stripe 安全组件加载失败")); };
    document.head.appendChild(script);
  }).catch((error) => { stripePromise = null; throw error; });
  return stripePromise;
}

function csvCell(value: unknown) { return `"${String(value ?? "").replace(/"/g, "\"\"")}"`; }

export default function DirectCardPayment() {
  const settings = useMemo(loadSettings, []);
  const [accounts, setAccounts] = useState<DirectAccount[]>(loadAccounts);
  const [importText, setImportText] = useState("");
  const [bindProxies, setBindProxies] = useState(String(settings.bindProxies || ""));
  const [promoProxies, setPromoProxies] = useState(String(settings.promoProxies || ""));
  const [concurrency, setConcurrency] = useState(Math.max(1, Math.min(50, Math.trunc(Number(settings.concurrency) || 1))));
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [cardReady, setCardReady] = useState(false);
  const [cardComplete, setCardComplete] = useState({ number: false, expiry: false, cvc: false });
  const [message, setMessage] = useState("等待导入账号");
  const [jobs, setJobs] = useState<Row[]>([]);
  const stripeRef = useRef<StripeClient | null>(null);
  const cardElements = useRef<Record<string, CardElement>>({});
  const numberHost = useRef<HTMLDivElement>(null);
  const expiryHost = useRef<HTMLDivElement>(null);
  const cvcHost = useRef<HTMLDivElement>(null);
  const stopRef = useRef(false);

  const selected = accounts.filter((item) => item.selected);
  const bindPool = parseProxyPool(bindProxies);
  const promoPool = parseProxyPool(promoProxies);
  const activeCount = jobs.filter((item) => ACTIVE_TASKS.has(String(item.status))).length;

  useEffect(() => { localStorage.setItem(ACCOUNT_KEY, JSON.stringify(accounts)); }, [accounts]);
  useEffect(() => { localStorage.setItem(SETTINGS_KEY, JSON.stringify({ bindProxies, promoProxies, concurrency })); }, [bindProxies, promoProxies, concurrency]);
  useEffect(() => {
    void refreshJobs();
    const timer = window.setInterval(() => { void refreshJobs(); }, 3000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => () => Object.values(cardElements.current).forEach((element) => element.unmount()), []);

  function patchAccount(id: string, patch: Partial<DirectAccount>) {
    setAccounts((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));
  }

  async function refreshJobs() {
    try { const result = await api("/jobs"); setJobs(result.jobs || []); } catch { /* worker may be offline during page load */ }
  }

  async function importAccounts(value: string) {
    const tokens = extractTokens(value);
    if (!tokens.length) return setMessage("没有识别到包含账号 ID 的 Access Token");
    const known = new Set(accounts.map((item) => item.accountId));
    const additions = tokens.map((token, index) => {
      const decoded = decodeAccount(token);
      return { id: `${Date.now()}-${index}`, token, ...decoded, selected: true, status: "idle" as const, stage: "正在分配环境指纹", error: "", link: "", taskId: "" };
    }).filter((item) => !known.has(item.accountId));
    if (!additions.length) return setMessage("导入账号已存在于列表中");
    setAccounts((current) => [...current, ...additions]);
    setImportText("");
    setMessage(`正在为 ${additions.length} 个账号分配环境指纹`);
    try {
      const result = await api("/fingerprints", { accounts: additions.map((item) => ({ client_id: item.id, access_token: item.token })) });
      const profiles = new Map<string, Row>((result.items || []).map((item: Row) => [String(item.client_id), item]));
      setAccounts((current) => current.map((item) => {
        const profile = profiles.get(item.id);
        return profile ? { ...item, email: profile.email || item.email, fingerprint: profile.fingerprint || "", fingerprintId: profile.fingerprint_id || "", stage: profile.ok ? "环境已就绪" : "指纹分配失败", error: profile.ok ? "" : String(profile.error || "指纹分配失败") } : item;
      }));
      if (cardReady) {
        const billing = await fetchBillingBatch(additions);
        setAccounts((current) => current.map((item) => billing.has(item.id) ? { ...item, billing: billing.get(item.id), stage: "环境与账单地址已就绪" } : item));
      }
      setMessage(`已导入 ${additions.length} 个账号`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "指纹分配失败"); }
  }

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) await importAccounts(await file.text());
    event.target.value = "";
  }

  async function loadCard() {
    if (!selected.length || !bindPool.length || !promoPool.length || cardReady) return;
    setBusy(true);
    setMessage("正在加载 Checkout、账单地址与 Stripe 安全组件");
    try {
      const missingBilling = accounts.filter((item) => !hasBilling(item.billing));
      const [StripeFactory, preflight, billing] = await Promise.all([
        ensureStripe(),
        api("/preflight", { access_token: selected[0].token, bind_proxy_pool: [bindPool[0]], promo_proxy_pool: [promoPool[0]] }),
        fetchBillingBatch(missingBilling),
      ]);
      setAccounts((current) => current.map((item) => billing.has(item.id) ? { ...item, billing: billing.get(item.id), stage: "环境与账单地址已就绪" } : item));
      stripeRef.current = StripeFactory(String(preflight.publishable_key));
      const elements = stripeRef.current.elements();
      const style = { base: { fontSize: "14px", color: "#dbe8f5", "::placeholder": { color: "#73869a" } }, invalid: { color: "#ef6474" } };
      const hosts = { number: numberHost.current, expiry: expiryHost.current, cvc: cvcHost.current };
      for (const [name, type] of [["number", "cardNumber"], ["expiry", "cardExpiry"], ["cvc", "cardCvc"]] as const) {
        const element = elements.create(type, { showIcon: name === "number", style });
        if (!hosts[name]) throw new Error("卡片输入容器未就绪");
        element.mount(hosts[name]!);
        element.on("change", (event) => { setCardComplete((current) => ({ ...current, [name]: Boolean(event.complete) })); if (event.error?.message) setMessage(String(event.error.message)); });
        cardElements.current[name] = element;
      }
      setCardReady(true);
      setMessage(`安全卡片组件已加载 · ${preflight.country || "PH"}/${preflight.currency || "PHP"}`);
    } catch (error) {
      Object.values(cardElements.current).forEach((element) => element.unmount());
      cardElements.current = {};
      stripeRef.current = null;
      setCardReady(false);
      setCardComplete({ number: false, expiry: false, cvc: false });
      setMessage(error instanceof Error ? error.message : "卡片组件加载失败");
    }
    finally { setBusy(false); }
  }

  async function createPaymentMethod(account: DirectAccount) {
    if (!stripeRef.current || !cardElements.current.number) throw new Error("Stripe 安全卡片组件尚未加载");
    const billing = account.billing || {};
    const result = await stripeRef.current.createPaymentMethod({ type: "card", card: cardElements.current.number, billing_details: { name: billing.name, email: account.email || undefined, address: { line1: billing.line1, line2: billing.line2 || undefined, city: billing.city, state: billing.state, postal_code: billing.postal_code, country: billing.country } } });
    if (result.error) throw new Error(result.error.message || "PaymentMethod 创建失败");
    if (!result.paymentMethod?.id) throw new Error("PaymentMethod 创建失败");
    return { id: result.paymentMethod.id, last4: result.paymentMethod.card?.last4 || "", billing };
  }

  async function pollTask(account: DirectAccount, taskId: string) {
    for (let attempt = 0; attempt < 4500; attempt += 1) {
      if (stopRef.current) throw new Error("任务已停止");
      await new Promise((resolve) => window.setTimeout(resolve, 400));
      const response = await api(`/jobs/${encodeURIComponent(taskId)}`);
      const task = response.task || {};
      patchAccount(account.id, { taskId, stage: task.stage || "任务处理中", steps: task.steps || [] });
      if (task.status === "done") { patchAccount(account.id, { status: "success", taskId: "", stage: task.stage || "任务完成", link: task.result?.checkout_link || "", error: "", steps: task.steps || [] }); return; }
      if (task.status === "error") throw new Error(task.error || "直卡协议任务失败");
    }
    throw new Error("任务轮询超时");
  }

  async function run(mode: FlowMode) {
    const chosen = accounts.filter((item) => item.selected);
    if (!chosen.length || !promoPool.length) return setMessage("请选择账号并填写优惠地区代理池");
    if (mode !== "link_only" && (!bindPool.length || !cardReady || !Object.values(cardComplete).every(Boolean))) return setMessage("请填写双代理池并完成安全卡片输入");
    setBusy(true); setStopping(false); stopRef.current = false;
    let completed = 0; let succeeded = 0;
    try {
      for (let offset = 0; offset < chosen.length && !stopRef.current; offset += concurrency) {
        const wave = chosen.slice(offset, offset + concurrency);
        const prepared: Array<{ account: DirectAccount; card: Row | null }> = [];
        for (let index = 0; index < wave.length && !stopRef.current; index += 1) {
          const account = wave[index];
          patchAccount(account.id, { status: "running", stage: mode === "link_only" ? "提链代理已就绪" : "正在生成安全支付方式", error: "" });
          try { prepared.push({ account, card: mode === "link_only" ? null : await createPaymentMethod(account) }); }
          catch (error) { completed += 1; patchAccount(account.id, { status: "error", stage: "生成支付方式失败", error: error instanceof Error ? error.message : String(error) }); }
        }
        if (!prepared.length || stopRef.current) continue;
        const batch = await api("/jobs/batch", { start_delay_ms: 250, tasks: prepared.map(({ account, card }, index) => ({ client_id: account.id, payload: { access_token: account.token, flow_mode: mode, promo_proxy_pool: [promoPool[(offset + index) % promoPool.length]], ...(mode === "link_only" ? {} : { payment_method_id: card?.id, card_last4: card?.last4, billing: card?.billing, bind_proxy_pool: [bindPool[(offset + index) % bindPool.length]] }) } })) });
        const ids = new Map<string, string>((batch.items || []).map((item: Row) => [String(item.client_id), String(item.task_id)]));
        const results = await Promise.allSettled(prepared.map(({ account }) => pollTask(account, ids.get(account.id) || "")));
        results.forEach((result, index) => { completed += 1; if (result.status === "fulfilled") succeeded += 1; else patchAccount(prepared[index].account.id, { status: "error", taskId: "", stage: "任务执行失败", error: result.reason instanceof Error ? result.reason.message : String(result.reason) }); });
        setMessage(`已完成 ${completed}/${chosen.length}，成功 ${succeeded}`);
      }
      setMessage(stopRef.current ? `已停止后续任务 · 完成 ${completed}/${chosen.length}` : `批次完成 · 成功 ${succeeded}/${chosen.length}`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "批次执行失败"); }
    finally { setBusy(false); setStopping(false); void refreshJobs(); }
  }

  function stop() { stopRef.current = true; setStopping(true); setMessage("正在停止后续账号任务"); }
  function exportCsv() {
    const rows = [["账号", "账号ID", "状态", "当前步骤", "Checkout链接", "错误"], ...accounts.map((item) => [item.email, item.accountId, item.status, item.stage, item.link, item.error])];
    const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(",")).join("\r\n")}`;
    triggerBrowserDownload(new Blob([csv], { type: "text/csv;charset=utf-8" }), `直卡协议任务-${new Date().toISOString().replace(/[:.]/g, "-")}.csv`);
  }

  return <div className="gopay-view direct-card-view">
    <div className="gopay-section-title"><div><h2>直卡协议支付</h2><p>直卡绑卡、提链与协议支付任务</p></div><span className="paypal-active-count">{activeCount} 个运行中</span></div>
    <div className="direct-card-setup">
      <section className="gopay-panel"><header><h3>安全卡片</h3><span className={cn("direct-card-health", cardReady && "is-ready")}><ShieldCheck />{cardReady ? "Stripe 已连接" : "尚未加载"}</span></header><div className="direct-card-fields"><label className="wide"><span>卡号</span><div ref={numberHost} className="direct-card-element" /></label><label><span>有效期</span><div ref={expiryHost} className="direct-card-element" /></label><label><span>CVC</span><div ref={cvcHost} className="direct-card-element" /></label></div><div className="gopay-field-foot"><small>每个账号自动获取独立账单地址</small><Button size="sm" onClick={() => void loadCard()} disabled={busy || cardReady || !selected.length || !bindPool.length || !promoPool.length}><CreditCard className="mr-1 h-3.5 w-3.5" />加载安全卡片</Button></div></section>
      <section className="gopay-panel"><header><h3>导入账号</h3><span className="direct-card-count">{accounts.length} 个账号</span></header><textarea className="direct-card-import" value={importText} onChange={(event) => setImportText(event.target.value)} rows={5} spellCheck={false} placeholder="每行一个 Access Token" /><div className="gopay-row-actions"><Button size="sm" onClick={() => void importAccounts(importText)} disabled={!importText.trim() || busy}><UserRound className="mr-1 h-3.5 w-3.5" />导入列表</Button><label className="direct-card-file"><FileUp />从文件导入<input type="file" accept=".txt,.json" onChange={(event) => void handleFile(event)} /></label></div></section>
    </div>
    <section className="gopay-panel"><header><h3>双代理池</h3><span className="direct-card-count">绑卡 {bindPool.length} · 提链 {promoPool.length}</span></header><div className="direct-card-proxies"><label><span>绑卡与支付 · US 节点</span><textarea value={bindProxies} onChange={(event) => setBindProxies(event.target.value)} rows={4} placeholder="每行一个 HTTP、HTTPS 或 SOCKS 代理" /></label><label><span>提链 · 优惠地区节点</span><textarea value={promoProxies} onChange={(event) => setPromoProxies(event.target.value)} rows={4} placeholder="每行一个 HTTP、HTTPS 或 SOCKS 代理" /></label></div></section>
    <div className="direct-card-toolbar"><label><span>并发</span><input type="number" min={1} max={50} value={concurrency} onChange={(event) => setConcurrency(Math.max(1, Math.min(50, Math.trunc(Number(event.target.value) || 1))))} /></label><Button onClick={() => void run("full")} disabled={busy}><Play className="mr-1 h-4 w-4" />提链 + 绑卡 + 提链 + 支付</Button><Button variant="outline" onClick={() => void run("bind_only")} disabled={busy}>提链 + 绑卡</Button><Button variant="outline" onClick={() => void run("link_pay")} disabled={busy}>提链 + 支付</Button><Button variant="outline" onClick={() => void run("link_only")} disabled={busy}>仅提链</Button><Button variant="outline" onClick={stop} disabled={!busy || stopping}><Square className="mr-1 h-3.5 w-3.5" />停止</Button></div>
    <div className={cn("direct-card-message", busy && "is-running")}>{busy && <Loader2 className="animate-spin" />}<span>{message}</span></div>
    <section className="gopay-panel"><header><h3>账号任务 · {accounts.length}</h3><div className="gopay-row-actions"><Button size="sm" variant="outline" onClick={exportCsv} disabled={!accounts.length}><Download className="mr-1 h-3.5 w-3.5" />导出 CSV</Button><Button size="sm" variant="outline" onClick={() => setAccounts((current) => current.filter((item) => !item.selected))} disabled={busy || !selected.length}><Trash2 className="mr-1 h-3.5 w-3.5" />删除选中</Button><Button size="sm" variant="outline" onClick={() => { if (window.confirm("确定清空直卡协议账号列表吗？")) setAccounts([]); }} disabled={busy || !accounts.length}>清空列表</Button><Button size="sm" variant="outline" onClick={() => void refreshJobs()}><RefreshCw className="mr-1 h-3.5 w-3.5" />刷新</Button></div></header><div className="gopay-table-wrap"><table><thead><tr><th><input type="checkbox" checked={accounts.length > 0 && selected.length === accounts.length} onChange={(event) => setAccounts((current) => current.map((item) => ({ ...item, selected: event.target.checked })))} /></th><th>账号</th><th>环境</th><th>当前步骤</th><th>状态</th><th>Checkout</th><th>错误</th></tr></thead><tbody>{accounts.length ? accounts.map((account) => <tr key={account.id}><td><input type="checkbox" checked={account.selected} onChange={(event) => patchAccount(account.id, { selected: event.target.checked })} disabled={busy} /></td><td><strong>{account.email || "未命名账号"}</strong><small className="direct-card-subtext">{account.accountId}</small></td><td>{account.fingerprintId ? <span className="direct-card-fingerprint"><ShieldCheck />{account.fingerprintId}</span> : "分配中"}</td><td className="gopay-message">{account.stage || "等待执行"}</td><td><span className={cn("gopay-status", `is-${account.status}`)}>{account.status === "success" ? "成功" : account.status === "error" ? "失败" : account.status === "running" ? "进行中" : "待执行"}</span></td><td>{account.link ? <button className="direct-card-link" type="button" onClick={() => void navigator.clipboard.writeText(account.link)} title="复制 Checkout 链接"><Link2 />复制链接</button> : "-"}</td><td className="gopay-message">{account.error || "-"}</td></tr>) : <tr><td colSpan={7}><div className="gopay-empty"><CreditCard /><strong>暂无直卡协议账号</strong><span>导入账号后即可分配环境并创建任务</span></div></td></tr>}</tbody></table></div></section>
    <section className="direct-card-runtime"><div><CheckCircle2 /><span><strong>协议核心已隔离</strong><small>直卡任务使用独立运行时与数据文件</small></span></div><div><XCircle /><span><strong>原始卡片数据不落库</strong><small>卡片字段由 Stripe Elements 托管</small></span></div></section>
  </div>;
}
