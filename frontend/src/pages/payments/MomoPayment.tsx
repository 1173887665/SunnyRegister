import { useCallback, useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from "react";
import { toDataURL } from "qrcode";
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
  return ({ queued: "排队中", running: "进行中", waiting_otp: "等待 OTP", awaiting_confirmation: "等待确认", success: "成功", failed: "失败", cancelled: "已取消", available: "可用", registered: "已注册", reserved: "已占用", unknown: "未检测" } as Record<string, string>)[key] || key;
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
    phones: phones.length,
    otp: [...registerJobs, ...paymentJobs].filter((job) => job.status === "waiting_otp").length,
    running: [...registerJobs, ...paymentJobs].filter((job) => ["running", "awaiting_confirmation"].includes(String(job.status))).length,
  }), [accounts, phones, registerJobs, paymentJobs]);

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
          <div><span><UserRound />MoMo 账号</span><strong>{stats.accounts}</strong></div>
          <div><span><Phone />越南号码</span><strong>{stats.phones}</strong></div>
          <div><span><MessageSquareText />等待 OTP</span><strong>{stats.otp}</strong></div>
          <div><span><Activity />进行中</span><strong>{stats.running}</strong></div>
        </section>
        {loading ? <div className="gopay-loading"><Loader2 className="animate-spin" />正在加载 MoMo 模块...</div> : <>
          {view === "overview" && <MomoOverview registerJobs={registerJobs} paymentJobs={paymentJobs} onView={setView} />}
          {view === "register" && <MomoRegister jobs={registerJobs} phones={phones} busy={busy} run={run} />}
          {view === "pool" && <MomoPool phones={phones} busy={busy} run={run} />}
          {view === "accounts" && <MomoAccounts accounts={accounts} busy={busy} run={run} onRegister={() => setView("register")} />}
          {view === "payment" && <MomoQrPayment accounts={accounts} jobs={paymentJobs} busy={busy} run={run} />}
          {view === "settings" && <MomoSettings settings={settings} busy={busy} run={run} />}
        </>}
      </main>
    </div>
  </div>;
}

function MomoOverview({ registerJobs, paymentJobs, onView }: { registerJobs: Row[]; paymentJobs: Row[]; onView: (view: MomoView) => void }) {
  const activity: Row[] = [...registerJobs.map((row): Row => ({ ...row, kind: row.login_existing ? "登录" : "注册" })), ...paymentJobs.map((row): Row => ({ ...row, kind: "扫码支付" }))]
    .sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || ""))).slice(0, 8);
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 运行总览</h2><p>越南号码、账号和扫码任务的实时状态</p></div></div><div className="gopay-two-column">
    <Panel title="最近活动">{activity.length ? <div className="gopay-activity">{activity.map((job, index) => <div key={`${job.kind}-${job.id || index}`}><Clock3 /><span><strong>{job.kind} · {job.phone || job.id || "-"}</strong><small>{job.message || "等待状态更新"}</small></span><Status value={job.status} /></div>)}</div> : <Empty title="暂无任务" detail="从注册与登录或扫码支付创建任务" />}</Panel>
    <Panel title="快速操作"><div className="gopay-quick-actions"><button onClick={() => onView("register")}><UsersRound /><span><strong>注册或登录 MoMo</strong><small>使用 +84 手机号创建任务</small></span></button><button onClick={() => onView("pool")}><Smartphone /><span><strong>维护越南号码</strong><small>导入短信接口或号码池</small></span></button><button onClick={() => onView("accounts")}><WalletCards /><span><strong>管理 MoMo 账号</strong><small>查看登录和设备状态</small></span></button><button onClick={() => onView("payment")}><QrCode /><span><strong>发起扫码支付</strong><small>上传商户二维码并登录付款</small></span></button></div></Panel>
  </div></div>;
}

function MomoRegister({ jobs, phones, busy, run }: { jobs: Row[]; phones: Row[]; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [mode, setMode] = useState<"register" | "login">("register");
  const [source, setSource] = useState<SmsSource>("pool");
  const [skipKyc, setSkipKyc] = useState(true);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const phone = String(data.get("phone") || "").trim();
    const pin = String(data.get("pin") || "").trim();
    if (!phone && source === "pool" && !phones.some((item) => item.status === "available" || !item.status)) return void window.alert("请先导入可用的 +84 号码");
    if (pin && !/^\d{4,8}$/.test(pin)) return void window.alert("支付密码请输入 4 到 8 位数字");
    await run("momo-register", () => post("/register", { phone, source, pin, login_existing: mode === "login", skip_kyc: skipKyc, count: Number(data.get("count") || 1), workers: Number(data.get("workers") || 1), proxy: String(data.get("proxy") || "") }), mode === "login" ? "MoMo 登录任务已创建" : "MoMo 注册任务已创建");
    event.currentTarget.reset();
  }
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 注册与登录</h2><p>使用越南 +84 手机号完成 OTP、登录和账号初始化</p></div></div>
    <Panel title="新建任务"><form className="gopay-form" onSubmit={submit}><div className="gopay-form-grid">
      <label><span>号码来源</span><select value={source} onChange={(event) => setSource(event.target.value as SmsSource)}><option value="pool">自建号码池</option>{(Object.keys(smsLabels) as Array<Exclude<SmsSource, "pool">>).map((item) => <option key={item} value={item}>{smsLabels[item]}</option>)}</select></label>
      <label><span>任务模式</span><select value={mode} onChange={(event) => setMode(event.target.value as "register" | "login")}><option value="register">注册新号</option><option value="login">登录已有号</option></select></label>
      <label><span>手机号（+84）</span><input name="phone" placeholder="+849xxxxxxxx" /></label>
      <label><span>数量</span><input name="count" type="number" min="1" max="100" defaultValue="1" /></label>
      <label><span>线程数</span><input name="workers" type="number" min="1" max="20" defaultValue="1" /></label>
      <label><span>{mode === "login" ? "登录密码 / PIN" : "设置支付密码"}</span><input name="pin" type="password" inputMode="numeric" maxLength={8} autoComplete="off" placeholder="4 到 8 位数字（可选）" /></label>
      <label className="gopay-check wide"><input type="checkbox" checked={skipKyc} onChange={(event) => setSkipKyc(event.target.checked)} /><span><strong>跳过实名认证 / KYC</strong><small>仅在目标 APP 流程允许时继续下一步</small></span></label>
      <label className="wide"><span>代理（可选）</span><input name="proxy" placeholder="http://user:pass@host:port" /></label>
    </div><div className="gopay-warning"><ShieldCheck />注册完成后会写入 MoMo 账号列表；KYC 跳过开关只控制本地任务分支。</div><Button type="submit" disabled={busy !== ""}><Plus className="mr-2 h-4 w-4" />创建 MoMo 任务</Button></form></Panel>
    <Panel title={`最近任务 · ${jobs.length}`} action={<Status value={jobs.some((job) => job.status === "running") ? "running" : "idle"} />}><div className="gopay-table-wrap"><table><thead><tr><th>任务 ID</th><th>手机号</th><th>模式</th><th>状态</th><th>消息</th><th>验证码</th></tr></thead><tbody>{jobs.length ? jobs.map((job) => <tr key={job.id}><td className="mono">{job.id}</td><td>{job.phone || "-"}</td><td>{job.login_existing ? "登录" : "注册"}</td><td><Status value={job.status} /></td><td className="gopay-message">{job.message || "-"}</td><td>{job.status === "waiting_otp" ? <MomoOtp jobId={job.id} run={run} /> : "-"}</td></tr>) : <tr><td colSpan={6}><Empty title="暂无注册任务" /></td></tr>}</tbody></table></div></Panel>
  </div>;
}

function MomoOtp({ jobId, run }: { jobId: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [code, setCode] = useState("");
  return <span className="gopay-otp-inline"><input value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 8))} inputMode="numeric" placeholder="OTP" /><Button size="sm" disabled={!/^\d{4,8}$/.test(code)} onClick={() => void run(`momo-otp-${jobId}`, () => post(`/register-jobs/${encodeURIComponent(jobId)}/otp`, { code }), "MoMo OTP 已提交")}>提交</Button></span>;
}

function MomoPool({ phones, busy, run }: { phones: Row[]; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const rows = phones.filter((row) => !search || `${row.phone} ${row.sms_url || ""}`.toLowerCase().includes(search.toLowerCase()));
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); await run("momo-pool", () => post("/phone-pool/import", { text }), "MoMo 号码池已导入"); setText(""); }
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 越南号码池</h2><p>号码格式：+84 手机号----短信读取 URL</p></div><Button size="sm" variant="destructive" disabled={!phones.length || busy !== ""} onClick={() => window.confirm("确定清空 MoMo 号码池吗？") && void run("momo-pool-clear", () => post("/phone-pool/clear"), "MoMo 号码池已清空")}><Trash2 className="mr-1 h-3.5 w-3.5" />清空</Button></div><div className="gopay-two-column"><Panel title="导入号码"><form className="gopay-form" onSubmit={submit}><label><span>号码与短信接口</span><textarea value={text} onChange={(event) => setText(event.target.value)} rows={8} required placeholder={"+84901234567----https://example.test/sms/123\n每行一个号码"} /></label><Button type="submit" disabled={busy !== ""}><Plus className="mr-2 h-4 w-4" />导入号码</Button></form></Panel><Panel title={`号码明细 · ${rows.length}`}><div className="gopay-search"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索手机号或短信接口" /></div><div className="gopay-table-wrap"><table><thead><tr><th>手机号</th><th>短信接口</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows.length ? rows.map((row) => <tr key={row.phone}><td className="mono">{row.phone}</td><td className="gopay-message">{row.sms_url || "-"}</td><td><Status value={row.status || "available"} /></td><td><Button size="sm" variant="ghost" onClick={() => void run(`momo-phone-${row.phone}`, () => post("/phone-pool/delete", { phone: row.phone }), "号码已删除")}><Trash2 className="h-4 w-4 text-red-500" /></Button></td></tr>) : <tr><td colSpan={4}><Empty title="号码池为空" /></td></tr>}</tbody></table></div></Panel></div></div>;
}

function MomoAccounts({ accounts, busy, run, onRegister }: { accounts: Row[]; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void>; onRegister: () => void }) {
  const [search, setSearch] = useState("");
  const rows = accounts.filter((row) => !search || String(row.phone || "").includes(search));
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 账号</h2><p>登录状态、支付密码和设备会话</p></div><Button size="sm" onClick={onRegister}><Plus className="mr-1 h-3.5 w-3.5" />注册或登录</Button></div><Panel title={`账号列表 · ${rows.length}`}><div className="gopay-search"><Search /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 +84 手机号" /></div><div className="gopay-table-wrap"><table><thead><tr><th>手机号</th><th>状态</th><th>PIN</th><th>KYC</th><th>设备会话</th><th>更新时间</th><th>操作</th></tr></thead><tbody>{rows.length ? rows.map((row) => <tr key={row.phone}><td className="mono">{row.phone}</td><td><Status value={row.status || "registered"} /></td><td>{row.pin_set ? "已设置" : "未设置"}</td><td>{row.kyc_status || "skipped"}</td><td>{row.session_ready ? "已登录" : "待登录"}</td><td>{formatTime(row.updated_at || row.created_at)}</td><td><div className="gopay-row-actions"><Button size="sm" variant="outline" disabled={busy !== ""} onClick={() => void run(`momo-relogin-${row.phone}`, () => post(`/accounts/${encodeURIComponent(row.phone)}/relogin`), "MoMo 重新登录任务已创建")}><KeyRound className="mr-1 h-3.5 w-3.5" />重新登录</Button><Button size="sm" variant="ghost" disabled={busy !== ""} onClick={() => window.confirm(`确定删除 ${row.phone} 吗？`) && void run(`momo-delete-${row.phone}`, () => post(`/accounts/${encodeURIComponent(row.phone)}/delete`), "账号已删除")}><Trash2 className="h-4 w-4 text-red-500" /></Button></div></td></tr>) : <tr><td colSpan={7}><Empty title="暂无 MoMo 账号" detail="完成注册或登录后，账号会显示在这里" /></td></tr>}</tbody></table></div></Panel></div>;
}

function MomoQrPayment({ accounts, jobs, busy, run }: { accounts: Row[]; jobs: Row[]; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [selectedPhones, setSelectedPhones] = useState<string[]>([]);
  const [pin, setPin] = useState("");
  const [proxy, setProxy] = useState("");
  const [amount, setAmount] = useState("");
  const [qrPayload, setQrPayload] = useState("");
  const [qrImage, setQrImage] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const selected = jobs.find((job) => String(job.id) === selectedId) || null;
  useEffect(() => { if (!qrPayload) { setQrImage(""); return; } void toDataURL(qrPayload, { width: 240, margin: 2 }).then(setQrImage).catch(() => setQrImage("")); }, [qrPayload]);
  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setQrImage(URL.createObjectURL(file));
    const Detector = (window as any).BarcodeDetector;
    if (!Detector) return;
    try {
      const bitmap = await createImageBitmap(file);
      const codes = await new Detector({ formats: ["qr_code"] }).detect(bitmap);
      if (codes[0]?.rawValue) setQrPayload(String(codes[0].rawValue));
      bitmap.close();
    } catch { /* Manual QR payload input remains available. */ }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (selectedPhones.length !== 1 || !qrPayload.trim()) return void window.alert("请勾选一个 MoMo 账号，并填写或识别二维码内容");
    await run("momo-payment", async () => { const result = await post("/payment", { phone: selectedPhones[0], pin, proxy, amount, qr_payload: qrPayload.trim() }); setSelectedId(String(result.id || "")); return result; }, "MoMo 已登录并创建扫码支付任务");
  }
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 扫码支付</h2><p>选择账号后，点击按钮会先登录账号，再识别并提交上传的商户二维码</p></div></div><div className="momo-qr-layout"><div className="gopay-payment-main"><Panel title="发起扫码支付"><form className="gopay-form" onSubmit={submit}><div className="gopay-form-grid"><fieldset className="wide momo-account-picker"><legend>选择 MoMo 账号（单选）</legend>{accounts.length ? accounts.map((row) => { const accountPhone = String(row.phone || ""); return <label key={accountPhone}><input type="checkbox" checked={selectedPhones.includes(accountPhone)} onChange={() => setSelectedPhones((current) => current.includes(accountPhone) ? current.filter((item) => item !== accountPhone) : [accountPhone])} /><span><strong>{accountPhone}</strong><small>{row.session_ready ? "已登录，支付前会重新登录" : "支付前登录"}</small></span></label>; }) : <span className="momo-account-empty">暂无已注册账号，请先完成注册与登录</span>}</fieldset><label><span>支付密码（可选）</span><input value={pin} onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 8))} type="password" inputMode="numeric" placeholder="留空使用账号设置" /></label><label><span>金额 VND（可选）</span><input value={amount} onChange={(event) => setAmount(event.target.value.replace(/\D/g, ""))} inputMode="numeric" placeholder="从二维码读取时可留空" /></label><label className="wide"><span>代理（可选）</span><input value={proxy} onChange={(event) => setProxy(event.target.value)} placeholder="留空使用账号代理" /></label><label className="wide"><span>二维码内容 / 深链</span><textarea value={qrPayload} onChange={(event) => setQrPayload(event.target.value)} rows={4} required placeholder="粘贴二维码解析内容，或上传二维码图片自动识别" /></label><label className="wide momo-upload"><span>上传商户二维码</span><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => void handleFile(event)} /></label></div><div className="gopay-warning"><ShieldCheck />点击后先登录勾选的账号，再识别二维码并提交扫码支付。</div><Button type="submit" disabled={busy !== "" || selectedPhones.length !== 1 || !qrPayload.trim()}><QrCode className="mr-2 h-4 w-4" />登录账号并开始扫码支付</Button></form></Panel><Panel title={`支付任务 · ${jobs.length}`}><div className="gopay-table-wrap"><table><thead><tr><th>任务 ID</th><th>账号</th><th>金额</th><th>状态</th><th>消息</th><th>时间</th></tr></thead><tbody>{jobs.length ? jobs.map((job) => <tr key={job.id} className={String(job.id) === selectedId ? "selected" : ""} onClick={() => setSelectedId(String(job.id))}><td className="mono">{job.id}</td><td>{job.phone || "-"}</td><td>{job.amount ? `${job.amount} VND` : "-"}</td><td><Status value={job.status} /></td><td className="gopay-message">{job.message || "-"}</td><td>{formatTime(job.updated_at || job.created_at)}</td></tr>) : <tr><td colSpan={6}><Empty title="暂无扫码支付任务" /></td></tr>}</tbody></table></div></Panel></div><aside className="gopay-panel momo-qr-preview"><header><h3>二维码预览</h3>{selected && <Status value={selected.status} />}</header>{qrImage ? <img src={qrImage} alt="MoMo 商户二维码" /> : <Empty title="上传二维码" detail="识别结果和支付任务详情会显示在这里" />}{selected && <div className="gopay-detail"><dl><dt>任务 ID</dt><dd className="mono">{selected.id}</dd><dt>账号</dt><dd>{selected.phone || "-"}</dd><dt>消息</dt><dd>{selected.message || "-"}</dd></dl>{selected.status === "waiting_otp" && <div className="gopay-otp-box"><KeyRound /><strong>输入支付 OTP</strong><p>验证码发送至 {selected.phone}</p><MomoOtp jobId={selected.id} run={run} /></div>}<h4>流程日志</h4><ol className="gopay-logs">{(selected.logs || []).map((entry: Row, index: number) => <li key={index}><time>{formatTime(entry.at)}</time><span>{entry.message || "-"}</span></li>)}</ol></div>}</aside></div></div>;
}

function MomoSettings({ settings, busy, run }: { settings: Row; busy: string; run: (key: string, action: () => Promise<any>, success: string) => Promise<void> }) {
  const [baseUrl, setBaseUrl] = useState(String(settings.api_base_url || ""));
  const [mockMode, setMockMode] = useState(settings.mock_mode !== false);
  const [skipKyc, setSkipKyc] = useState(settings.skip_kyc_default !== false);
  useEffect(() => { setBaseUrl(String(settings.api_base_url || "")); setMockMode(settings.mock_mode !== false); setSkipKyc(settings.skip_kyc_default !== false); }, [settings]);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); await run("momo-settings", () => post("/settings", { api_base_url: baseUrl, mock_mode: mockMode, skip_kyc_default: skipKyc }), "MoMo 配置已保存"); }
  return <div className="gopay-view"><div className="gopay-section-title"><div><h2>MoMo 系统配置</h2><p>维护目标 APP 适配器、扫码支付和实名认证分支</p></div></div><Panel title="适配器配置"><form className="gopay-form" onSubmit={submit}><div className="gopay-form-grid"><label className="wide"><span>MoMo API Base URL（可选）</span><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} type="url" placeholder="留空使用本地任务适配器" /></label><label className="gopay-check"><input type="checkbox" checked={mockMode} onChange={(event) => setMockMode(event.target.checked)} /><span><strong>本地演练模式</strong><small>验证界面、OTP、任务和二维码流程</small></span></label><label className="gopay-check"><input type="checkbox" checked={skipKyc} onChange={(event) => setSkipKyc(event.target.checked)} /><span><strong>默认跳过实名认证 / KYC</strong><small>仅作为任务分支开关</small></span></label></div><div className="gopay-warning"><ShieldCheck />关闭本地演练模式后，需要填入与目标 APP 匹配的官方接口适配器和测试环境配置。</div><Button type="submit" disabled={busy !== ""}><Settings2 className="mr-2 h-4 w-4" />保存配置</Button></form></Panel></div>;
}
