import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { toDataURL } from "qrcode";
import jsQR from "jsqr";
import {
  Activity, CheckCircle2, Clock3, KeyRound, LayoutDashboard, Loader2,
  MessageSquareText, Phone, Plus, QrCode, Search, Settings2,
  ShieldCheck, Smartphone, Trash2, UserRound, UsersRound, WalletCards,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiFetch, cn } from "@/lib/utils";

type Row = Record<string, any>;
type MomoView = "overview" | "register" | "pool" | "accounts" | "payment" | "settings";
type SmsSource = "pool" | "smsbower" | "smspool" | "grizzlysms" | "hero_sms";

const api = (path: string, options?: RequestInit) => apiFetch(`/payments/momo${path}`, options);
const post = (path: string, body: Row = {}) => api(path, { method: "POST", body: JSON.stringify(body) });
const smsLabels: Record<Exclude<SmsSource, "pool">, string> = { smsbower: "SMSBower", smspool: "SMSPool", grizzlysms: "GrizzlySMS", hero_sms: "HeroSMS" };

function statusLabel(value: unknown) {
  const key = String(value || "unknown");
  return ({ queued: "排队中", running: "进行中", waiting_otp: "等待 OTP", awaiting_confirmation: "等待确认", success: "成功", failed: "失败", cancelled: "已取消", available: "可用", registered: "已注册", reserved: "已占用", used: "已使用", unknown: "未检测" } as Record<string, string>)[key] || key;
}

function Status({ value }: { value: unknown }) {
  const key = String(value || "unknown");
  return <span className={cn("gopay-status", `is-${key}`)}>{statusLabel(key)}</span>;
}

function Panel({ title, action, children, className }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={cn("gopay-panel", className)}><header><h3>{title}</h3>{action}</header>{children}</section>;
}

function Empty({ title, detail }: { title: string; detail?: string }) {
  return <div className="gopay-empty"><WalletCards /><strong>{title}</strong>{detail && <span>{detail}</span>}</div>;
}

function formatTime(value: unknown) {
  if (!value) return "-";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
}

export default function MomoPayment() {
  const [view, setView] = useState<MomoView>("overview");
  const [accounts, setAccounts] = useState<Row[]>([]);
  const [phones, setPhones] = useState<Row[]>([]);
  const [registerJobs, setRegisterJobs] = useState<Row[]>([]);
  const [paymentJobs, setPaymentJobs] = useState<Row[]>([]);
  const [settings, setSettings] = useState<Row>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<{ type: "ok" | "error"; text: string } | null>(null);

  const toast = useCallback((text: string, type: "ok" | "error" = "ok") => {
    setNotice({ text, type });
    window.setTimeout(() => setNotice((current) => current?.text === text ? null : current), 3200);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [accountData, phoneData, registerData, paymentData, config] = await Promise.all([
        api("/accounts"), api("/phone-pool"), api("/register-jobs"), api("/payment-jobs"), api("/settings"),
      ]);
      setAccounts(accountData.accounts || []);
      setPhones(phoneData.phones || []);
      setRegisterJobs(registerData.jobs || []);
      setPaymentJobs(paymentData.jobs || []);
      setSettings(config || {});
    } catch (error) {
      toast(error instanceof Error ? error.message : "MoMo 数据加载失败", "error");
    } finally { setLoading(false); }
  }, [toast]);

  useEffect(() => { const timer = window.setTimeout(() => void refresh(), 0); return () => window.clearTimeout(timer); }, [refresh]);
  useEffect(() => { const timer = window.setInterval(() => void refresh(), 3000); return () => window.clearInterval(timer); }, [refresh]);

  const run = useCallback(async (key: string, action: () => Promise<any>, success: string) => {
    setBusy(key);
    try { await action(); toast(success); await refresh(); }
    catch (error) { toast(error instanceof Error ? error.message : "操作失败", "error"); }
    finally { setBusy(""); }
  }, [refresh, toast]);

  const stats = useMemo(() => ({
    accounts: accounts.length,
    phones: Number(settings.phone_pool_available_count ?? phones.filter((item) => String(item.status || "available") === "available").length),
    otp: [...registerJobs, ...paymentJobs].filter((job) => job.status === "waiting_otp").length,
    running: [...registerJobs, ...paymentJobs].filter((job) => ["running", "awaiting_confirmation"].includes(String(job.status))).length,
  }), [accounts, phones, registerJobs, paymentJobs, settings]);

  const nav: Array<[MomoView, string, ReactNode]> = [
    ["overview", "总览", <LayoutDashboard />], ["register", "注册与登录", <UsersRound />],
    ["pool", "号码池", <Smartphone />], ["accounts", "MoMo 账号", <WalletCards />],
    ["payment", "扫码支付", <QrCode />], ["settings", "系统配置", <Settings2 />],
  ];

  return <div className="momo-payment gopay-view">
    {notice && <div className={cn("gopay-toast", notice.type === "error" && "is-error")}><CheckCircle2 />{notice.text}</div>}
    <div className="gopay-workspace">
      <nav className="gopay-nav" aria-label="MoMo 功能">{nav.map(([key, label, icon]) => <button key={key} type="button" className={view === key ? "active" : ""} onClick={() => setView(key)}>{icon}<span>{label}</span></button>)}</nav>
      <main className="gopay-content">
        <section className="gopay-summary" aria-label="MoMo 实时概览">
          <div><span><UserRound />MoMo 账号</span><strong>{stats.accounts}</strong></div><div><span><Phone />可用号码</span><strong>{stats.phones}</strong></div><div><span><MessageSquareText />等待 OTP</span><strong>{stats.otp}</strong></div><div><span><Activity />进行中</span><strong>{stats.running}</strong></div>
        </section>
        {loading ? <div className="gopay-loading"><Loader2 className="animate-spin" />正在加载 MoMo 模块...</div> : <>
          {view === "overview" && <MomoOverview registerJobs={registerJobs} paymentJobs={paymentJobs} onView={setView} />}
          {view === "register" && <MomoRegister jobs={registerJobs} phones={phones} settings={settings} busy={busy} run={run} />}
          {view === "pool" && <MomoPool phones={phones} busy={busy} run={run} />}
          {view === "accounts" && <MomoAccounts accounts={accounts} busy={busy} run={run} onRegister={() => setView("register")} />}
          {view === "payment" && <MomoQrPayment accounts={accounts} jobs={paymentJobs} settings={settings} busy={busy} run={run} />}
          {view === "settings" && <MomoSettings settings={settings} busy={busy} run={run} />}
        </>}
      </main>
    </div>
  </div>;
}

function MomoOverview({ registerJobs, paymentJobs, onView }: { registerJobs: Row[]; paymentJobs: Row[]; onView: (view: MomoView) => void }) {
  const activity: Row[] = [...registerJobs.map((row): Row => ({ ...row, kind: row.login_existing ? "登录" : "注册" })), ...paymentJobs.map((row): Row => ({ ...row, kind: "扫码支付" }))].sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || ""))).slice(0, 8);
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 运行总览</h2><p>越南号码、账号和扫码任务的实时状态</p></div></div><div className="gopay-two-column"><Panel title="最近活动">{activity.length ? <div className="gopay-activity">{activity.map((job, index) => <div key={`${job.kind}-${job.id || index}`}><Clock3 /><span><strong>{job.kind} · {job.phone || job.id || "-"}</strong><small>{job.message || "等待状态更新"}</small></span><Status value={job.status} /></div>)}</div> : <Empty title="暂无任务" detail="从注册与登录或扫码支付创建任务" />}</Panel><Panel title="快速操作"><div className="gopay-quick-actions"><button onClick={() => onView("register")}><UsersRound /><span><strong>注册或登录 MoMo</strong><small>使用 +84 手机号创建任务</small></span></button><button onClick={() => onView("pool")}><Smartphone /><span><strong>维护越南号码</strong><small>导入短信接口或号码池</small></span></button><button onClick={() => onView("accounts")}><WalletCards /><span><strong>管理 MoMo 账号</strong><small>查看登录和设备状态</small></span></button><button onClick={() => onView("payment")}><QrCode /><span><strong>发起扫码支付</strong><small>上传商户二维码并登录付款</small></span></button></div></Panel></div></div>;
}

function MomoRegister({ jobs, phones, settings, busy, run }: { jobs: Row[]; phones: Row[]; settings: Row; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [mode, setMode] = useState<"register" | "login">("register");
  const [skipKyc, setSkipKyc] = useState(true);
  const source = String(settings.phone_source || "pool") as SmsSource;
  const sourceLabel = source === "pool" ? "系统号码池" : (smsLabels[source as Exclude<SmsSource, "pool">] || source);
  const automaticSourceReady = source === "pool" ? Number(settings.phone_pool_available_count ?? phones.filter((item) => item.status === "available" || !item.status).length) > 0 : Boolean(settings.sms_api_key_configured);
  useEffect(() => { const timer = window.setTimeout(() => setSkipKyc(settings.skip_kyc_default !== false), 0); return () => window.clearTimeout(timer); }, [settings.skip_kyc_default]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const phone = String(data.get("phone") || "").trim();
    const pin = String(data.get("pin") || "").trim();
    const profile = {
      display_name: String(data.get("display_name") || "").trim(),
      email: String(data.get("email") || "").trim(),
      date_of_birth: String(data.get("date_of_birth") || "").trim(),
      address: String(data.get("address") || "").trim(),
    };
    if (!phone && mode === "register" && !automaticSourceReady) return void window.alert(source === "pool" ? "请先在 MoMo 系统配置导入可用的 +84 号码" : "请先在 MoMo 系统配置保存短信平台 API Key");
    if (pin && !/^\d{4,8}$/.test(pin)) return void window.alert("支付密码请输入 4 到 8 位数字");
    await run("momo-register", () => post("/register", { phone, source, pin, login_existing: mode === "login", skip_kyc: skipKyc, profile, count: Number(data.get("count") || 1) }), mode === "login" ? "MoMo 登录任务已创建" : "MoMo 注册任务已创建");
    event.currentTarget.reset();
  }
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 注册与登录</h2><p>使用越南 +84 手机号完成 OTP、登录和账号初始化</p></div></div><Panel title="新建任务"><form className="gopay-form" onSubmit={submit}><div className="gopay-form-grid"><label><span>号码来源（系统配置）</span><input value={sourceLabel} readOnly /><small className="gopay-field-hint">注册时自动从系统配置取号，不在此页覆盖</small></label><label><span>任务模式</span><select value={mode} onChange={(event) => setMode(event.target.value as "register" | "login")}><option value="register">注册新号</option><option value="login">登录已有号</option></select></label><label><span>手机号（+84，可选）</span><input name="phone" placeholder={mode === "login" ? "+849xxxxxxxx" : "留空自动从系统取号"} required={mode === "login"} /></label><label><span>数量</span><input name="count" type="number" min="1" max="100" defaultValue="1" /></label><label><span>{mode === "login" ? "登录密码 / PIN" : "设置支付密码"}</span><input name="pin" type="password" inputMode="numeric" maxLength={8} autoComplete="off" placeholder="4 到 8 位数字（可选）" /></label><label><span>姓名 / 昵称（可选）</span><input name="display_name" maxLength={80} placeholder="提交给直连协议的资料" /></label><label><span>邮箱（可选）</span><input name="email" type="email" maxLength={160} placeholder="name@example.com" /></label><label><span>出生日期（可选）</span><input name="date_of_birth" type="date" /></label><label className="wide"><span>地址（可选）</span><input name="address" maxLength={240} placeholder="提交给直连协议的资料" /></label><label className="gopay-check wide"><input type="checkbox" checked={skipKyc} onChange={(event) => setSkipKyc(event.target.checked)} /><span><strong>默认跳过实名认证 / KYC</strong><small>由系统配置控制目标 APP 的流程分支</small></span></label></div><div className="gopay-warning"><ShieldCheck />代理、手机号来源、OTP 轮询均由 MoMo 系统配置统一管理；注册完成后会写入账号列表。</div><Button type="submit" disabled={busy !== ""}><Plus className="mr-2 h-4 w-4" />创建 MoMo 任务</Button></form></Panel><Panel title={`最近任务 · ${jobs.length}`} action={<Status value={jobs.some((job) => job.status === "running") ? "running" : "idle"} />}><div className="gopay-table-wrap"><table><thead><tr><th>任务 ID</th><th>手机号</th><th>模式</th><th>状态</th><th>消息</th><th>验证码</th></tr></thead><tbody>{jobs.length ? jobs.map((job) => <tr key={job.id}><td className="mono">{job.id}</td><td>{job.phone || "-"}</td><td>{job.login_existing ? "登录" : "注册"}</td><td><Status value={job.status} /></td><td className="gopay-message">{job.message || "-"}</td><td>{job.status === "waiting_otp" ? <MomoOtp jobId={job.id} run={run} /> : "-"}{["running", "waiting_otp"].includes(String(job.status)) && <MomoCancel jobId={job.id} kind="register" run={run} />}</td></tr>) : <tr><td colSpan={6}><Empty title="暂无注册任务" /></td></tr>}</tbody></table></div></Panel></div>;
}

function MomoOtp({ jobId, kind = "register", run }: { jobId: string; kind?: "register" | "payment"; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) { const [code, setCode] = useState(""); const path = kind === "payment" ? "/payment-jobs" : "/register-jobs"; return <span className="gopay-otp-inline"><input value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 8))} inputMode="numeric" placeholder="OTP" /><Button size="sm" disabled={!/^\d{4,8}$/.test(code)} onClick={() => void run(`momo-otp-${kind}-${jobId}`, () => post(`${path}/${encodeURIComponent(jobId)}/otp`, { code }), "MoMo OTP 已提交")}>提交</Button></span>; }

function MomoCancel({ jobId, kind, run }: { jobId: string; kind: "register" | "payment"; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) { const path = kind === "payment" ? "/payment-jobs" : "/register-jobs"; return <Button size="sm" variant="outline" onClick={() => void run(`momo-cancel-${kind}-${jobId}`, () => post(`${path}/${encodeURIComponent(jobId)}/cancel`), "MoMo 任务已取消")}>取消</Button>; }

function MomoPool({ phones, busy, run }: { phones: Row[]; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) { const [text, setText] = useState(""); const [search, setSearch] = useState(""); const rows = phones.filter((row) => !search || `${row.phone} ${row.sms_url || ""}`.toLowerCase().includes(search.toLowerCase())); async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); await run("momo-pool", () => post("/phone-pool/import", { text }), "MoMo 号码池已导入"); setText(""); } return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 越南号码池</h2><p>号码格式：+84 手机号----短信读取 URL</p></div><Button size="sm" variant="destructive" disabled={!phones.length || busy !== ""} onClick={() => window.confirm("确定清空 MoMo 号码池吗？") && void run("momo-pool-clear", () => post("/phone-pool/clear"), "MoMo 号码池已清空")}><Trash2 className="mr-1 h-3.5 w-3.5" />清空</Button></div><div className="gopay-two-column"><Panel title="导入号码"><form className="gopay-form" onSubmit={submit}><label><span>号码与短信接口</span><textarea value={text} onChange={(event) => setText(event.target.value)} rows={8} required placeholder={"+84901234567----https://example.test/sms/123\n每行一个号码"} /></label><Button type="submit" disabled={busy !== ""}><Plus className="mr-2 h-4 w-4" />导入号码</Button></form></Panel><Panel title={`号码明细 · ${rows.length}`}><div className="gopay-search"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索手机号或短信接口" /></div><div className="gopay-table-wrap"><table><thead><tr><th>手机号</th><th>短信接口</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows.length ? rows.map((row) => <tr key={row.phone}><td className="mono">{row.phone}</td><td className="gopay-message">{row.sms_url || "-"}</td><td><Status value={row.status || "available"} /></td><td><Button size="sm" variant="ghost" onClick={() => void run(`momo-phone-${row.phone}`, () => post("/phone-pool/delete", { phone: row.phone }), "号码已删除")}><Trash2 className="h-4 w-4 text-red-500" /></Button></td></tr>) : <tr><td colSpan={4}><Empty title="号码池为空" /></td></tr>}</tbody></table></div></Panel></div></div>; }

function MomoAccounts({ accounts, busy, run, onRegister }: { accounts: Row[]; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void>; onRegister: () => void }) { const [search, setSearch] = useState(""); const rows = accounts.filter((row) => !search || String(row.phone || "").includes(search)); return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 账号</h2><p>登录状态、支付密码和设备会话</p></div><Button size="sm" onClick={onRegister}><Plus className="mr-1 h-3.5 w-3.5" />注册或登录</Button></div><Panel title={`账号列表 · ${rows.length}`}><div className="gopay-search"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 +84 手机号" /></div><div className="gopay-table-wrap"><table><thead><tr><th>手机号</th><th>状态</th><th>PIN</th><th>KYC</th><th>设备会话</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{rows.length ? rows.map((row) => <tr key={row.phone}><td className="mono">{row.phone}</td><td><Status value={row.status || "registered"} /></td><td>{row.pin_set ? "已设置" : "未设置"}</td><td>{row.kyc_status || "skipped"}</td><td>{row.session_ready ? "已登录" : "待登录"}</td><td>{formatTime(row.updated_at || row.created_at)}</td><td><div className="gopay-row-actions"><Button size="sm" variant="outline" disabled={busy !== ""} onClick={() => void run(`momo-relogin-${row.phone}`, () => post(`/accounts/${encodeURIComponent(row.phone)}/relogin`), "MoMo 重新登录任务已创建")}><KeyRound className="mr-1 h-3.5 w-3.5" />重新登录</Button><Button size="sm" variant="ghost" disabled={busy !== ""} onClick={() => window.confirm(`确定删除 ${row.phone} 吗？`) && void run(`momo-delete-${row.phone}`, () => post(`/accounts/${encodeURIComponent(row.phone)}/delete`), "账号已删除")}><Trash2 className="h-4 w-4 text-red-500" /></Button></div></td></tr>) : <tr><td colSpan={7}><Empty title="暂无 MoMo 账号" detail="完成注册或登录后，账号会显示在这里" /></td></tr>}</tbody></table></div></Panel></div>; }

function MomoQrPayment({ accounts, jobs, settings, busy, run }: { accounts: Row[]; jobs: Row[]; settings: Row; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [selectedPhones, setSelectedPhones] = useState<string[]>([]); const [pin, setPin] = useState(""); const [amount, setAmount] = useState(""); const [qrPayload, setQrPayload] = useState(""); const [qrImage, setQrImage] = useState(""); const [selectedId, setSelectedId] = useState(""); const selected = jobs.find((job) => String(job.id) === selectedId) || null;
  useEffect(() => { let active = true; if (!qrPayload) { const timer = window.setTimeout(() => { if (active) setQrImage(""); }, 0); return () => { active = false; window.clearTimeout(timer); }; } void toDataURL(qrPayload, { width: 240, margin: 2 }).then((value) => { if (active) setQrImage(value); }).catch(() => { if (active) setQrImage(""); }); return () => { active = false; }; }, [qrPayload]);
  useEffect(() => () => { if (qrImage.startsWith("blob:")) URL.revokeObjectURL(qrImage); }, [qrImage]);
  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setQrImage(URL.createObjectURL(file));
    try {
      const bitmap = await createImageBitmap(file);
      try {
        const Detector = (window as any).BarcodeDetector;
        let decoded = "";
        if (Detector) {
          const codes = await new Detector({ formats: ["qr_code"] }).detect(bitmap);
          decoded = String(codes[0]?.rawValue || "");
        }
        if (!decoded) {
          const maxDimension = 2048;
          const scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
          const canvas = document.createElement("canvas");
          canvas.width = Math.max(1, Math.round(bitmap.width * scale));
          canvas.height = Math.max(1, Math.round(bitmap.height * scale));
          const context = canvas.getContext("2d", { willReadFrequently: true });
          if (!context) throw new Error("二维码图像上下文不可用");
          context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
          const image = context.getImageData(0, 0, canvas.width, canvas.height);
          decoded = String(jsQR(image.data, image.width, image.height, { inversionAttempts: "attemptBoth" })?.data || "");
        }
        if (decoded) setQrPayload(decoded);
      } finally {
        bitmap.close();
      }
    } catch {
      /* Manual QR payload input remains available when image decoding fails. */
    }
  }
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (selectedPhones.length !== 1 || !qrPayload.trim()) return void window.alert("请勾选一个 MoMo 账号，并填写或识别二维码内容"); await run("momo-payment", async () => { const result = await post("/payment", { phone: selectedPhones[0], pin, amount, qr_payload: qrPayload.trim() }); setSelectedId(String(result.id || "")); return result; }, "MoMo 已登录并创建扫码支付任务"); }
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 扫码支付</h2><p>选择账号后，点击按钮会先登录账号，再识别并提交上传的商户二维码</p></div></div><div className="momo-qr-layout"><div className="gopay-payment-main"><Panel title="发起扫码支付"><form className="gopay-form" onSubmit={submit}><div className="gopay-form-grid"><fieldset className="wide momo-account-picker"><legend>选择 MoMo 账号（单选）</legend>{accounts.length ? accounts.map((row) => { const accountPhone = String(row.phone || ""); return <label key={accountPhone}><input type="checkbox" checked={selectedPhones.includes(accountPhone)} onChange={() => setSelectedPhones((current) => current.includes(accountPhone) ? current.filter((item) => item !== accountPhone) : [accountPhone])} /><span><strong>{accountPhone}</strong><small>{row.session_ready ? "已登录，支付前会重新登录" : "支付前登录"}</small></span></label>; }) : <span className="momo-account-empty">暂无已注册账号，请先完成注册与登录</span>}</fieldset><label><span>支付密码（可选）</span><input value={pin} onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 8))} type="password" inputMode="numeric" placeholder="留空使用账号设置" /></label><label><span>金额 VND（可选）</span><input value={amount} onChange={(event) => setAmount(event.target.value.replace(/\D/g, ""))} inputMode="numeric" placeholder="从二维码读取时可留空" /></label><label className="wide"><span>系统代理池</span><input value={settings.proxy_count ? `${settings.proxy_count} 条 · ${settings.proxy_mode === "random" ? "随机" : "轮换"}` : "未配置（按系统策略）"} readOnly /></label><label className="wide"><span>二维码内容 / 深链</span><textarea value={qrPayload} onChange={(event) => setQrPayload(event.target.value)} rows={4} required placeholder="粘贴二维码解析内容，或上传二维码图片自动识别" /></label><label className="wide momo-upload"><span>上传商户二维码</span><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void handleFile(event)} /></label></div><div className="gopay-warning"><ShieldCheck />点击后先登录勾选的账号，再识别二维码并提交扫码支付；代理由系统配置统一选择。</div><Button type="submit" disabled={busy !== "" || selectedPhones.length !== 1 || !qrPayload.trim()}><QrCode className="mr-2 h-4 w-4" />登录账号并开始扫码支付</Button></form></Panel><Panel title={`支付任务 · ${jobs.length}`}><div className="gopay-table-wrap"><table><thead><tr><th>任务 ID</th><th>账号</th><th>金额</th><th>状态</th><th>消息</th><th>时间</th><th>操作</th></tr></thead><tbody>{jobs.length ? jobs.map((job) => <tr key={job.id} className={String(job.id) === selectedId ? "selected" : ""} onClick={() => setSelectedId(String(job.id))}><td className="mono">{job.id}</td><td>{job.phone || "-"}</td><td>{job.amount ? `${job.amount} VND` : "-"}</td><td><Status value={job.status} /></td><td className="gopay-message">{job.message || "-"}</td><td>{formatTime(job.updated_at || job.created_at)}</td><td>{["running", "waiting_otp", "awaiting_confirmation"].includes(String(job.status)) && <MomoCancel jobId={job.id} kind="payment" run={run} />}</td></tr>) : <tr><td colSpan={7}><Empty title="暂无扫码支付任务" /></td></tr>}</tbody></table></div></Panel></div><aside className="gopay-panel momo-qr-preview"><header><h3>二维码预览</h3>{selected && <Status value={selected.status} />}</header>{qrImage ? <img src={qrImage} alt="MoMo 商户二维码" /> : <Empty title="上传二维码" detail="识别结果和支付任务详情会显示在这里" />}{selected && <div className="gopay-detail"><dl><dt>任务 ID</dt><dd className="mono">{selected.id}</dd><dt>账号</dt><dd>{selected.phone || "-"}</dd><dt>消息</dt><dd>{selected.message || "-"}</dd></dl>{selected.status === "waiting_otp" && <div className="gopay-otp-box"><KeyRound /><strong>输入支付 OTP</strong><p>验证码发送至 {selected.phone}</p><MomoOtp jobId={selected.id} kind="payment" run={run} /></div>}{selected.status === "awaiting_confirmation" && <div className="gopay-otp-box"><ShieldCheck /><strong>确认支付</strong><Button size="sm" onClick={() => void run(`momo-confirm-${selected.id}`, () => post(`/payment-jobs/${encodeURIComponent(selected.id)}/confirm`), "MoMo 支付确认已提交")}>确认</Button></div>}<div className="gopay-row-actions">{["running", "waiting_otp", "awaiting_confirmation"].includes(String(selected.status)) && <MomoCancel jobId={selected.id} kind="payment" run={run} />}</div><h4>流程日志</h4><ol className="gopay-logs">{(selected.logs || []).map((entry: Row, index: number) => <li key={index}><time>{formatTime(entry.at)}</time><span>{entry.message || "-"}</span></li>)}</ol></div>}</aside></div></div>;
}

function MomoSettings({ settings, busy, run }: { settings: Row; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [values, setValues] = useState<Row>({});
  const [smsApiKey, setSmsApiKey] = useState("");
  const [proxyPool, setProxyPool] = useState("");
  const [proxyPoolTouched, setProxyPoolTouched] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [check, setCheck] = useState<Row | null>(null);
  useEffect(() => {
    if (dirty) return;
    const timer = window.setTimeout(() => {
      const next = { ...settings };
      delete next.sms_api_key;
      delete next.protocol_token;
      delete next.protocol_access_key;
      setValues(next);
      setSmsApiKey("");
      setProxyPool("");
      setProxyPoolTouched(false);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [settings, dirty]);
  const set = (key: string, value: any) => { setDirty(true); setValues((current) => ({ ...current, [key]: value })); };
  function buildPayload() {
    const payload: Row = { ...values, mock_mode: false, skip_kyc_default: Boolean(values.skip_kyc_default) };
    delete payload.proxy_pool;
    delete payload.sms_api_key;
    delete payload.protocol_token;
    delete payload.protocol_access_key;
    delete payload.protocol_secret_key;
    if (smsApiKey.trim()) payload.sms_api_key = smsApiKey.trim();
    if (proxyPoolTouched) payload.proxy_pool = proxyPool;
    return payload;
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const payload = buildPayload();
    await run("momo-settings", async () => {
      const result = await post("/settings", payload);
      setValues({ ...result });
      setSmsApiKey("");
      setProxyPool("");
      setProxyPoolTouched(false);
      setDirty(false);
      return result;
    }, "MoMo 系统配置已保存");
  }
  async function checkSettings() {
    await run("momo-settings-check", async () => {
      const result = await post("/settings/check", buildPayload());
      setCheck(result);
      return result;
    }, "MoMo 配置检测完成");
  }
  const legacyWorker = Boolean(settings && !settings.runtime_version);
  const protocolReady = Boolean(settings.live_protocol_ready);
  const phoneSource = String(settings.phone_source || "pool") === "pool" ? "系统号码池" : "短信平台（系统默认）";
  return <div className="gopay-view">
    <div className="gopay-section-title"><div><h2>MoMo 系统配置</h2><p>Worker 内置协议直连手机号、短信、账号与支付接口，不再经过外置适配器</p>{legacyWorker && <div className="gopay-warning"><Activity />当前 Worker 返回的是旧版配置接口；磁盘代码已更新为直连协议，重启 Worker 后检测项会显示为 momo_protocol。</div>}</div></div>
    <Panel title="MoMo 默认配置">
      <form className="gopay-form" onSubmit={submit}>
        <div className="gopay-defaults" aria-label="MoMo 默认配置状态">
          <div><span>运行协议</span><strong>{protocolReady ? "系统默认已就绪" : "正在初始化"}</strong><small>自动选择内置协议或部署配置</small></div>
          <div><span>号码来源</span><strong>{phoneSource}</strong><small>注册任务自动选择可用号码</small></div>
          <div><span>手机号格式</span><strong>越南 +84</strong><small>国家代码和前缀由系统固定</small></div>
          <div><span>KYC 策略</span><strong>{settings.skip_kyc_default !== false ? "默认跳过" : "按协议处理"}</strong><small>不需要手动设置</small></div>
        </div>
        <h4>短信自动取号</h4>
        <div className="gopay-form-grid">
          <label><span>SMS 服务代码</span><input value={String(values.sms_service_code || "momo")} onChange={(event) => set("sms_service_code", event.target.value)} /></label>
          <label><span>SMS 国家代码</span><input value={String(values.sms_country_code || "84")} onChange={(event) => set("sms_country_code", event.target.value)} /></label>
          <label className="wide"><span>SMS API Base URL（可选）</span><input value={String(values.sms_api_base_url || "")} onChange={(event) => set("sms_api_base_url", event.target.value)} type="url" /></label>
          <label className="wide"><span>SMS API Key</span><input value={smsApiKey} onChange={(event) => { setSmsApiKey(event.target.value); setDirty(true); }} type="password" autoComplete="new-password" placeholder={settings.sms_api_key_configured ? `已配置 ${settings.sms_api_key || ""}，留空保持不变` : "请输入 API Key"} /></label>
          <label><span>最高价格（可选）</span><input value={String(values.sms_max_price || "")} onChange={(event) => set("sms_max_price", event.target.value)} type="number" min="0" step="any" /></label>
          <label><span>SMSPool 号码池（可选）</span><input value={String(values.sms_pool || "")} onChange={(event) => set("sms_pool", event.target.value)} /></label>
        </div>
        <h4>OTP 与代理策略</h4>
        <div className="gopay-form-grid">
          <label><span>OTP 超时（秒）</span><input value={String(values.otp_timeout_sec || 300)} onChange={(event) => set("otp_timeout_sec", Number(event.target.value) || 300)} type="number" min="30" max="900" /></label>
          <label><span>轮询间隔（秒）</span><input value={String(values.otp_poll_interval_sec || 3)} onChange={(event) => set("otp_poll_interval_sec", Number(event.target.value) || 3)} type="number" min="1" max="30" /></label>
          <label><span>OTP 最大重发</span><input value={String(values.otp_max_resends ?? 2)} onChange={(event) => set("otp_max_resends", Number(event.target.value) || 0)} type="number" min="0" max="5" /></label>
          <label><span>API 超时（秒）</span><input value={String(values.api_timeout_sec || 60)} onChange={(event) => set("api_timeout_sec", Number(event.target.value) || 60)} type="number" min="5" max="300" /></label>
          <label className="wide"><span>代理池（每行一条）</span><textarea value={proxyPool} onChange={(event) => { setProxyPool(event.target.value); setProxyPoolTouched(true); setDirty(true); }} rows={5} placeholder={settings.proxy_count ? `${settings.proxy_count} 条已配置，输入新内容将覆盖` : "http://user:pass@host:port"} /></label>
          <label><span>代理轮换方式</span><select value={String(values.proxy_mode || "round_robin")} onChange={(event) => set("proxy_mode", event.target.value)}><option value="round_robin">轮换</option><option value="random">随机</option></select></label>
          <label className="gopay-check"><input type="checkbox" checked={Boolean(values.proxy_required)} onChange={(event) => set("proxy_required", event.target.checked)} /><span><strong>强制使用代理</strong><small>代理池为空时拒绝创建任务</small></span></label>
        </div>
        <div className="gopay-warning"><ShieldCheck />当前为 Worker 内置直连协议模式；注册、登录、支付和短信请求均使用系统选择的代理。</div>
        <div className="gopay-row-actions"><Button type="submit" disabled={busy !== ""}><Settings2 className="mr-2 h-4 w-4" />保存全部配置</Button><Button type="button" variant="outline" disabled={busy !== ""} onClick={() => void checkSettings()}><Activity className="mr-2 h-4 w-4" />检测配置</Button></div>
      </form>
    </Panel>
    {check && <Panel title="配置检测结果"><div className="gopay-activity">{(check.checks || []).map((item: Row) => <div key={item.name}><Status value={item.ok ? "success" : "failed"} /><span><strong>{item.name}</strong><small>{item.message}</small></span></div>)}{!check.runtime_version && <div className="gopay-warning"><Activity /><span><strong>旧版 Worker 响应</strong><small>检测结果来自未加载直连协议代码的 Worker；请重启 Worker 后再检测。</small></span></div>}</div></Panel>}
  </div>;
}
