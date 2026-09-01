import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../store/useStore";

/* ==========================================================================
   密钥与凭据页 — 把分散在 config.yaml / secrets.json / .env / 环境变量的
   API key、平台凭据、端点参数集中到前端可编辑, 写入后端落盘并热生效。
   离线开源项目: 无需鉴权, 凭据原值可见 (密码框 type=password 防肩窥)。
   ========================================================================== */

// ---- secrets.json 字段 (B 层: env 注入 + 热重载) ----
interface Seven11Secrets {
  PROXY_711_HOST: string;
  PROXY_711_PORT: string;
  PROXY_711_USER: string;
  PROXY_711_PASS: string;
  CLASH_PROXY: string;
  PROXY_711_RELAY_PORT: string;
  PROXY_711_CONNECT_REWRITE_HOSTS: string;
}
interface Api798Secrets {
  REG_API798_MAILBOXES: string;
  REG_API798_ENDPOINT: string;
  REG_API798_ENABLED: string;
}
interface Api798Mailbox {
  id: string;
  email: string;
  auth_url?: string;
  auth_code?: string;
}
interface RebindMailbox { id: string; email: string; code_url: string; enabled?: boolean; used_count?: number; last_used_at?: string; last_error?: string }
interface SmsSecrets { SMSBOWER_API_KEY: string; GRIZZLYSMS_API_KEY: string }
interface PaypalAntibotSecrets {
  PAYPAL_ROXY_API_KEY: string;
  PAYPAL_DATADOME_MODE: string;
  PAYPAL_MTR_RUNTIME: string;
  PAYPAL_MTR_CHANNEL: string;
  PAYPAL_MTR_API_KEY: string;
  PAYPAL_RISK_SIGNALS_MODE: string;
  PAYPAL_FINGERPRINT_SOURCE: string;
  PAYPAL_HCAPTCHA_TOKEN: string;
}
type SecretsData = {
  seven11: Seven11Secrets;
  api798: Api798Secrets;
  sms: SmsSecrets;
  paypal_antibot: PaypalAntibotSecrets;
};

// ---- config.yaml A 层标量 (POST /api/config/section) ----
interface ProxyPool { host: string; port: number; auth_key: string; auth_pwd: string }
interface ResidentialProxy { id: string; url: string; country?: string }
interface ClashNodeInfo { name: string; country: string; type: string; server: string; ip: string; port: number; source: string }
interface ClashCountryInfo { country: string; count: number; ips: string[]; nodes: ClashNodeInfo[] }
interface ConfigScalars {
  server: { host: string; port: number; max_concurrent_chains: number; thread_pool_size: number; chain_mode: string; mock_success_rate: number; mock_stage_min: number; mock_stage_max: number };
  stripe: Record<string, string>;
  tls: { impersonate: string; user_agent: string; accept_language: string };
  proxy: { default_pool: string; health_check_interval: number; max_concurrent_per_node: number; sess_time: number };
  register_pool: { base_url: string; timeout: number };
  storage: { db_path: string; samples_dir: string; runs_dir: string };
  geo: { enabled: boolean; timeout: number; sources: string[] };
  logging: { level: string; json_logs: boolean };
  momo: { enabled: boolean; connect_intercept: boolean; dns_fix: boolean; pm_inject: boolean; confirm_build: boolean; resolve_regex: boolean };
  proxyPools: { qg_super_pool: ProxyPool; qg_resi_pool: ProxyPool; default_pool: string };
}

type SecretSection = keyof SecretsData;
type ConfigSection = "server" | "stripe" | "tls" | "proxy" | "register_pool" | "storage" | "geo" | "logging" | "momo";

const EMPTY_SECRETS: SecretsData = {
  seven11: { PROXY_711_HOST: "", PROXY_711_PORT: "", PROXY_711_USER: "", PROXY_711_PASS: "", CLASH_PROXY: "", PROXY_711_RELAY_PORT: "", PROXY_711_CONNECT_REWRITE_HOSTS: "" },
  api798: { REG_API798_MAILBOXES: "", REG_API798_ENDPOINT: "", REG_API798_ENABLED: "1" },
  sms: { SMSBOWER_API_KEY: "", GRIZZLYSMS_API_KEY: "" },
  paypal_antibot: { PAYPAL_ROXY_API_KEY: "", PAYPAL_DATADOME_MODE: "", PAYPAL_MTR_RUNTIME: "", PAYPAL_MTR_CHANNEL: "", PAYPAL_MTR_API_KEY: "", PAYPAL_RISK_SIGNALS_MODE: "", PAYPAL_FINGERPRINT_SOURCE: "", PAYPAL_HCAPTCHA_TOKEN: "" },
};

export function SecretsView() {
  const setView = useStore((s) => s.setView);
  const [secrets, setSecrets] = useState<SecretsData>(EMPTY_SECRETS);
  const [cfg, setCfg] = useState<ConfigScalars | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [savedFlash, setSavedFlash] = useState("");
  const [mailboxOpen, setMailboxOpen] = useState(false);
  const [mailboxes, setMailboxes] = useState<Api798Mailbox[]>([]);
  const [selectedMailboxIds, setSelectedMailboxIds] = useState<Set<string>>(new Set());
  const [mailboxImportText, setMailboxImportText] = useState("");
  const [mailboxBusy, setMailboxBusy] = useState(false);
  const [mailboxMessage, setMailboxMessage] = useState("");
  const [mailboxTestId, setMailboxTestId] = useState("");
  const mailboxFileRef = useRef<HTMLInputElement | null>(null);
  const rebindFileRef = useRef<HTMLInputElement | null>(null);
  const [rebindOpen, setRebindOpen] = useState(false);
  const [rebindMailboxes, setRebindMailboxes] = useState<RebindMailbox[]>([]);
  const [rebindSelected, setRebindSelected] = useState<Set<string>>(new Set());
  const [rebindImportText, setRebindImportText] = useState("");
  const [rebindBusy, setRebindBusy] = useState(false);
  const [rebindMessage, setRebindMessage] = useState("");
  const [clashDetectBusy, setClashDetectBusy] = useState(false);
  const [clashDetectMessage, setClashDetectMessage] = useState("");
  const [clashCountries, setClashCountries] = useState<ClashCountryInfo[]>([]);
  const [clashResultOpen, setClashResultOpen] = useState(false);

  // ---- 加载 ----
  useEffect(() => {
    (async () => {
      try {
        const [secRes, cfgRes] = await Promise.all([
          api("/api/config/secrets", "GET"),
          api("/api/config", "GET"),
        ]);
        if (secRes?.secrets) setSecrets((prev) => ({ ...prev, ...secRes.secrets }));
        if (cfgRes) {
          setCfg({
            server: cfgRes.server,
            stripe: cfgRes.stripe || {},
            tls: cfgRes.tls,
            proxy: cfgRes.proxy,
            register_pool: cfgRes.register_pool,
            storage: cfgRes.storage,
            geo: cfgRes.geo,
            logging: cfgRes.logging,
            momo: cfgRes.momo,
            proxyPools: {
              qg_super_pool: secRes?.proxy_pools?.qg_super_pool || { host: "", port: 0, auth_key: "", auth_pwd: "" },
              qg_resi_pool: secRes?.proxy_pools?.qg_resi_pool || { host: "", port: 0, auth_key: "", auth_pwd: "" },
              default_pool: secRes?.proxy_pools?.default_pool || "",
            },
          });
        }
      } catch (e: any) {
        setErr(e?.message || "加载失败");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // ---- 自动保存 (1s 防抖, 复用 PayPalView 模式) ----
  const saveSecretsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (loading) return;
    if (saveSecretsTimer.current) clearTimeout(saveSecretsTimer.current);
    saveSecretsTimer.current = setTimeout(async () => {
      // 找出与上次不同的 section 整组提交 (后端 update 只改非空 diff)
      for (const sec of Object.keys(secrets) as SecretSection[]) {
        try {
          await api("/api/config/secrets", "POST", { section: sec, fields: secrets[sec] });
        } catch { /* ignore */ }
      }
      setSavedFlash("已保存 ✓");
      setTimeout(() => setSavedFlash(""), 1500);
    }, 1000);
    return () => { if (saveSecretsTimer.current) clearTimeout(saveSecretsTimer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secrets]);

  const saveCfgTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleCfgSave = (section: ConfigSection, fields: Record<string, unknown>) => {
    if (saveCfgTimer.current) clearTimeout(saveCfgTimer.current);
    saveCfgTimer.current = setTimeout(async () => {
      try {
        await api("/api/config/section", "POST", { section, fields });
        setSavedFlash("已保存 ✓");
        setTimeout(() => setSavedFlash(""), 1500);
      } catch { /* ignore */ }
    }, 1000);
  };

  // ---- helpers ----
  const updSecret = (sec: SecretSection, fld: string, val: string) =>
    setSecrets((prev) => ({ ...prev, [sec]: { ...prev[sec], [fld]: val } }));

  const detectClash = async () => {
    setClashDetectBusy(true);
    setClashDetectMessage("识别中...");
    try {
      const r = await api<{ ok: boolean; detected?: string; message?: string; countries?: ClashCountryInfo[] }>("/api/proxy/clash/detect", "POST");
      setClashCountries(r?.countries || []);
      if (r?.countries?.length) setClashResultOpen(true);
      if (r?.ok && r.detected) {
        updSecret("seven11", "CLASH_PROXY", r.detected);
        setClashCountries(r.countries || []);
        setClashDetectMessage(`${r.detected}（已保存）`);
      } else {
        setClashDetectMessage(r?.message || "未找到可用端口");
      }
    } catch (e: any) {
      setClashDetectMessage(e?.message || "识别失败");
    } finally {
      setClashDetectBusy(false);
    }
  };

  const loadMailboxes = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; mailboxes?: Api798Mailbox[] }>("/api/config/api798_mailboxes", "GET");
      if (r?.ok) setMailboxes(r.mailboxes || []);
    } catch (e: any) {
      setMailboxMessage(e?.message || "邮箱列表加载失败");
    }
  }, []);

  useEffect(() => { loadMailboxes(); }, [loadMailboxes]);

  const loadRebindMailboxes = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; mailboxes?: RebindMailbox[] }>("/api/config/rebind_mailboxes", "GET");
      if (r?.ok) setRebindMailboxes(r.mailboxes || []);
    } catch (e: any) { setRebindMessage(e?.message || "换绑邮箱列表加载失败"); }
  }, []);
  useEffect(() => { loadRebindMailboxes(); }, [loadRebindMailboxes]);

  const importRebindMailboxes = async () => {
    if (!rebindImportText.trim()) return;
    setRebindBusy(true);
    try {
      const r = await api<{ ok: boolean; imported?: number; mailboxes?: RebindMailbox[]; error?: string }>("/api/config/rebind_mailboxes/import", "POST", { text: rebindImportText });
      if (!r?.ok) throw new Error(r?.error || "导入失败");
      setRebindMailboxes(r.mailboxes || []); setRebindImportText("");
      setRebindMessage(`已导入 ${r.imported || 0} 条`);
    } catch (e: any) { setRebindMessage(e?.message || "导入失败"); }
    finally { setRebindBusy(false); }
  };
  const exportRebindMailboxes = async () => {
    setRebindBusy(true);
    try {
      const r = await api<{ content?: string; total?: number }>("/api/config/rebind_mailboxes/export", "POST", rebindSelected.size ? { ids: [...rebindSelected] } : {});
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([r.content || ""], { type: "text/plain" })); a.download = "rebind-mailboxes.txt"; a.click(); URL.revokeObjectURL(a.href);
      setRebindMessage(`已导出 ${r.total || 0} 条`);
    } catch (e: any) { setRebindMessage(e?.message || "导出失败"); }
    finally { setRebindBusy(false); }
  };
  const deleteRebindMailboxes = async (all = false) => {
    if (!all && !rebindSelected.size) return;
    if (!window.confirm(all ? "确定删除全部换绑邮箱？" : `确定删除选中的 ${rebindSelected.size} 条换绑邮箱？`)) return;
    setRebindBusy(true);
    try {
      const r = await api<{ mailboxes?: RebindMailbox[] }>("/api/config/rebind_mailboxes/delete", "POST", all ? { delete_all: true } : { ids: [...rebindSelected] });
      setRebindMailboxes(r.mailboxes || []); setRebindSelected(new Set());
    } catch (e: any) { setRebindMessage(e?.message || "删除失败"); }
    finally { setRebindBusy(false); }
  };
  const readRebindFile = (e: React.ChangeEvent<HTMLInputElement>) => { const file = e.target.files?.[0]; if (!file) return; const reader = new FileReader(); reader.onload = () => setRebindImportText(String(reader.result || "")); reader.readAsText(file); e.target.value = ""; };

  const openMailboxManager = async () => {
    setMailboxOpen(true);
    setMailboxMessage("");
    setSelectedMailboxIds(new Set());
    await loadMailboxes();
  };

  const importMailboxes = async () => {
    if (!mailboxImportText.trim()) return;
    setMailboxBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string; mailboxes?: Api798Mailbox[]; imported?: number }>(
        "/api/config/api798_mailboxes/import", "POST", { text: mailboxImportText }
      );
      if (!r?.ok) throw new Error(r?.error || "导入失败");
      setMailboxes(r.mailboxes || []);
      setMailboxImportText("");
      setSelectedMailboxIds(new Set());
      setMailboxMessage(`已导入 ${r.imported || 0} 条`);
    } catch (e: any) {
      setMailboxMessage(e?.message || "导入失败");
    } finally {
      setMailboxBusy(false);
    }
  };

  const exportMailboxes = async () => {
    setMailboxBusy(true);
    try {
      const ids = Array.from(selectedMailboxIds);
      const r = await api<{ ok: boolean; content?: string; total?: number }>(
        "/api/config/api798_mailboxes/export", "POST", ids.length ? { ids } : {}
      );
      const blob = new Blob([r?.content || ""], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `api798-mailboxes-${new Date().toISOString().slice(0, 10)}.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setMailboxMessage(`已导出 ${r?.total || 0} 条${ids.length ? "所选" : "全部"}`);
    } catch (e: any) {
      setMailboxMessage(e?.message || "导出失败");
    } finally {
      setMailboxBusy(false);
    }
  };

  const deleteMailboxes = async (deleteAll = false) => {
    const ids = Array.from(selectedMailboxIds);
    if (!deleteAll && !ids.length) return;
    const prompt = deleteAll ? "确定删除全部邮箱记录？" : `确定删除选中的 ${ids.length} 条邮箱？`;
    if (!window.confirm(prompt)) return;
    setMailboxBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string; mailboxes?: Api798Mailbox[] }>(
        "/api/config/api798_mailboxes/delete", "POST", deleteAll ? { delete_all: true } : { ids }
      );
      if (!r?.ok) throw new Error(r?.error || "删除失败");
      setMailboxes(r.mailboxes || []);
      setSelectedMailboxIds(new Set());
      setMailboxMessage(deleteAll ? "已删除全部" : `已删除 ${ids.length} 条`);
    } catch (e: any) {
      setMailboxMessage(e?.message || "删除失败");
    } finally {
      setMailboxBusy(false);
    }
  };

  const selectAllMailboxes = () => setSelectedMailboxIds(new Set(mailboxes.map((m) => m.id)));
  const clearMailboxSelection = () => setSelectedMailboxIds(new Set());

  const testMailbox = async (id: string) => {
    setMailboxTestId(id);
    setMailboxMessage("");
    try {
      const r = await api<{ ok: boolean; has_code?: boolean; message?: string; error?: string }>(
        "/api/config/api798_mailboxes/test", "POST", { id }
      );
      if (!r?.ok) throw new Error(r?.error || "取码地址测试失败");
      setMailboxMessage(r.has_code ? "地址正常，已读取到验证码" : (r.message || "地址正常，当前没有新验证码"));
    } catch (e: any) {
      setMailboxMessage(e?.message || "取码地址测试失败");
    } finally {
      setMailboxTestId("");
    }
  };

  const readMailboxFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setMailboxImportText(await file.text());
    setMailboxMessage(`已读取文件：${file.name}`);
  };

  const flash = () => savedFlash && (
    <span className="muted" style={{ fontSize: 11.5, color: "var(--ok)" }}>{savedFlash}</span>
  );

  if (loading) {
    return (
      <div className="page">
        <div className="page-head"><h2 className="page-title">密钥与凭据</h2><p className="page-sub">加载中…</p></div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">密钥与凭据</h2>
          <p className="page-sub">
            代理凭据 · 注册功能 · SMS 接码 · PayPal 反爬 · Stripe 端点 · TLS 指纹 · 服务器 · MoMo · 存储
            {err && <span style={{ color: "var(--warn)" }}> ({err})</span>}
          </p>
        </div>
        <div className="page-actions">
          {flash()}
          <button className="btn btn-ghost btn-sm" onClick={() => setView("settings")}>← 返回设置</button>
        </div>
      </div>

      {/* 1. 住宅代理池 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">住宅代理池</span>
          <span className="card-hint">按国家标签轮换；指定国家时只使用对应国家的代理</span>
        </div>
        <div className="card-body">
          <ResidentialProxyPoolCard pool="api" title="API 住宅代理池" hint="用于连接 API 的住宅代理" />
          <ResidentialProxyPoolCard pool="mixed" title="通用住宅代理池" hint="自动识别多种代理格式" />
          <div className="section-head" style={{ marginTop: 10 }}><span className="section-title">Clash 本地代理</span></div>
          <SecretRow label="Clash 代理地址" value={secrets.seven11.CLASH_PROXY} onChange={(v) => updSecret("seven11", "CLASH_PROXY", v)} placeholder="127.0.0.1:7897" hint="原有 Clash 节点池仍在“代理池”页面管理" action={<button className="btn btn-primary btn-sm" onClick={detectClash} disabled={clashDetectBusy}>{clashDetectBusy ? "识别中..." : "自动识别"}</button>} />
          {clashDetectMessage && <div className="setting-row" style={{ paddingTop: 0 }}><span className="setting-label" /><span className="setting-hint" style={{ color: clashDetectMessage.includes("已保存") ? "var(--ok)" : "var(--text-3)" }}>{clashDetectMessage}</span></div>}
          {clashCountries.length > 0 && <div className="setting-row">
            <span className="setting-label">Clash 识别结果</span>
            <div className="setting-control" style={{ flexWrap: "wrap" }}>
              <span className="setting-hint">已识别 {clashCountries.length} 个国家分类、{clashCountries.reduce((sum, group) => sum + group.count, 0)} 个节点</span>
              <button className="btn btn-ghost btn-sm" onClick={() => setClashResultOpen(true)}>查看识别结果</button>
            </div>
          </div>}
        </div>
      </div>

      {/* 2. 注册功能 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">注册功能</span>
          <span className="card-hint">邮箱渠道 · codex_register 注册池（全部可在前端配置，不再硬编码）</span>
        </div>
        <div className="card-body">
          <div className="section-head"><span className="section-title">api798 邮箱服务</span></div>
          <SecretSelectRow label="启用状态" value={secrets.api798.REG_API798_ENABLED || "1"} onChange={(v) => updSecret("api798", "REG_API798_ENABLED", v)} options={[["1", "启用 (默认)"], ["0", "禁用"]]} />
          <div className="setting-row">
            <span className="setting-label">卡密邮箱</span>
            <div className="setting-control">
              <button className="btn btn-primary" onClick={() => setView("mailpool")}>打开邮箱管理</button>
              <span className="setting-hint">当前 {mailboxes.length} 条 api798 记录；IMAP、mail.com、分裂、导入导出和测试统一在“邮箱管理”中</span>
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">取码端点</span>
            <div className="setting-control">
              <span className="muted" style={{ fontSize: 12.5 }}>自动识别每条邮箱记录中的 auth_code 地址</span>
            </div>
          </div>
          <div className="section-head" style={{ marginTop: 14 }}><span className="section-title">换绑邮箱池</span></div>
          <div className="setting-row">
            <span className="setting-label">目标邮箱</span>
            <div className="setting-control">
              <button className="btn btn-primary" onClick={() => { setRebindOpen(true); setRebindMessage(""); loadRebindMailboxes(); }}>导入换绑邮箱</button>
              <span className="setting-hint">当前 {rebindMailboxes.length} 条，格式：邮箱----HTTP(S)取码地址</span>
            </div>
          </div>
          <div className="section-head" style={{ marginTop: 8 }}><span className="section-title">codex_register 注册池</span></div>
          <div className="setting-row">
            <span className="setting-label">注册池地址</span>
            <div className="setting-control">
              <input className="input" value={cfg?.register_pool.base_url ?? ""} onChange={(e) => { const v = e.target.value; setCfg(c => c ? { ...c, register_pool: { ...c.register_pool, base_url: v } } : c); scheduleCfgSave("register_pool", { base_url: v }); }} placeholder="http://127.0.0.1:8780" style={{ width: 280 }} />
            </div>
          </div>
          <div className="setting-row">
            <span className="setting-label">超时</span>
            <div className="setting-control">
              <input className="input" type="number" value={cfg?.register_pool.timeout ?? ""} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, register_pool: { ...c.register_pool, timeout: v } } : c); scheduleCfgSave("register_pool", { timeout: v }); }} style={{ width: 100 }} /> <span className="muted" style={{ fontSize: 11.5 }}>秒</span>
            </div>
          </div>
        </div>
      </div>

      {/* 2b. 邮箱域名池 (PayPal 注册邮箱域名, 按国家配置) */}
      <EmailDomainsCard />

      {/* 3. SMS 接码 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">SMS 接码</span>
          <span className="card-hint">全局默认 key · PayPal 授权页留空时回落到这里</span>
        </div>
        <div className="card-body">
          <SecretRow label="SMSBower API Key" value={secrets.sms.SMSBOWER_API_KEY} onChange={(v) => updSecret("sms", "SMSBOWER_API_KEY", v)} password placeholder="留空使用 .env / 默认" />
          <SecretRow label="GrizzlySMS API Key" value={secrets.sms.GRIZZLYSMS_API_KEY} onChange={(v) => updSecret("sms", "GRIZZLYSMS_API_KEY", v)} password placeholder="留空使用 .env / 默认" />
        </div>
      </div>

      {/* 4. PayPal 反爬 / 指纹 */}
      <div className="card">
        <div className="card-head">
          <span className="card-title">PayPal 反爬 / 指纹</span>
          <span className="card-hint">Roxy · DataDome · MTR · Risk · hCaptcha</span>
        </div>
        <div className="card-body">
          <SecretRow label="Roxy API Key" value={secrets.paypal_antibot.PAYPAL_ROXY_API_KEY} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_ROXY_API_KEY", v)} password placeholder="本地 Roxy 浏览器 API key" />
          <SecretSelectRow label="指纹来源" value={secrets.paypal_antibot.PAYPAL_FINGERPRINT_SOURCE} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_FINGERPRINT_SOURCE", v)} options={[["random", "random (默认)"], ["roxy", "roxy (Roxy 浏览器)"], ["auto", "auto (有 key 用 roxy)"]]} />
          <SecretSelectRow label="DataDome 模式" value={secrets.paypal_antibot.PAYPAL_DATADOME_MODE} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_DATADOME_MODE", v)} options={[["protocol", "protocol (默认)"], ["roxy", "roxy"], ["headless", "headless"], ["auto", "auto"], ["off", "off"]]} />
          <SecretSelectRow label="MTR 来源" value={secrets.paypal_antibot.PAYPAL_MTR_RUNTIME} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_MTR_RUNTIME", v)} options={[["python_generated", "python_generated (默认)"], ["roxy", "roxy"]]} />
          <SecretRow label="MTR Channel" value={secrets.paypal_antibot.PAYPAL_MTR_CHANNEL} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_MTR_CHANNEL", v)} placeholder="iwc-mxo" />
          <SecretRow label="MTR API Key" value={secrets.paypal_antibot.PAYPAL_MTR_API_KEY} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_MTR_API_KEY", v)} password placeholder="留空使用默认" />
          <SecretSelectRow label="Risk Signals 模式" value={secrets.paypal_antibot.PAYPAL_RISK_SIGNALS_MODE} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_RISK_SIGNALS_MODE", v)} options={[["protocol", "protocol (默认)"], ["roxy", "roxy"]]} />
          <SecretRow label="hCaptcha Token" value={secrets.paypal_antibot.PAYPAL_HCAPTCHA_TOKEN} onChange={(v) => updSecret("paypal_antibot", "PAYPAL_HCAPTCHA_TOKEN", v)} password placeholder="留空使用默认" />
        </div>
      </div>

      {/* 5. Stripe 端点 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">Stripe 端点</span>
            <span className="card-hint">init / update / confirm / poll · chatgpt.com checkout</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="Checkout URL" section="stripe" field="checkout_url" value={cfg.stripe.checkout_url ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://chatgpt.com/..." />
            <CfgTextRow label="Approve URL" section="stripe" field="approve_url" value={cfg.stripe.approve_url ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://chatgpt.com/..." />
            <CfgTextRow label="Payment Methods URL" section="stripe" field="pm_url" value={cfg.stripe.pm_url ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/..." />
            <CfgTextRow label="Init URL 模板" section="stripe" field="init_url_tmpl" value={cfg.stripe.init_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/init" />
            <CfgTextRow label="Update URL 模板" section="stripe" field="update_url_tmpl" value={cfg.stripe.update_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/update" />
            <CfgTextRow label="Confirm URL 模板" section="stripe" field="confirm_url_tmpl" value={cfg.stripe.confirm_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/confirm" />
            <CfgTextRow label="Poll URL 模板" section="stripe" field="poll_url_tmpl" value={cfg.stripe.poll_url_tmpl ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="https://api.stripe.com/v1/invoices/{cs}/poll" />
            <CfgTextRow label="Init 版本" section="stripe" field="init_version" value={cfg.stripe.init_version ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="2025-08-27" />
            <CfgTextRow label="Runtime 版本" section="stripe" field="runtime_version" value={cfg.stripe.runtime_version ?? ""} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="2025-08-27" />
          </div>
        </div>
      )}

      {/* 6. TLS 指纹 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">TLS 指纹</span>
            <span className="card-hint">curl_cffi impersonate · UA · 语言</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="impersonate" section="tls" field="impersonate" value={cfg.tls.impersonate} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="chrome146" />
            <CfgTextRow label="User-Agent" section="tls" field="user_agent" value={cfg.tls.user_agent} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="Mozilla/5.0 ..." />
            <CfgTextRow label="Accept-Language" section="tls" field="accept_language" value={cfg.tls.accept_language} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="en-US,en;q=0.9" />
          </div>
        </div>
      )}

      {/* 7. 服务器配置 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">服务器配置</span>
            <span className="card-hint">uvicorn 监听 · 并发 · 链路模式</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="监听 Host" section="server" field="host" value={cfg.server.host} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="0.0.0.0" />
            <div className="setting-row">
              <span className="setting-label">监听端口</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.server.port} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, port: v } } : c); scheduleCfgSave("server", { port: v }); }} style={{ width: 100 }} />
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">最大并发链路</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.server.max_concurrent_chains} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, max_concurrent_chains: v } } : c); scheduleCfgSave("server", { max_concurrent_chains: v }); }} style={{ width: 100 }} />
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">线程池大小</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.server.thread_pool_size} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, thread_pool_size: v } } : c); scheduleCfgSave("server", { thread_pool_size: v }); }} style={{ width: 100 }} />
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">链路模式</span>
              <div className="setting-control">
                <select className="select" value={cfg.server.chain_mode} onChange={(e) => { const v = e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, chain_mode: v } } : c); scheduleCfgSave("server", { chain_mode: v }); }} style={{ width: 140 }}>
                  <option value="live">live (真实)</option>
                  <option value="mock">mock (模拟)</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">Mock 成功率</span>
              <div className="setting-control">
                <input className="input" type="number" step="0.1" value={cfg.server.mock_success_rate} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, server: { ...c.server, mock_success_rate: v } } : c); scheduleCfgSave("server", { mock_success_rate: v }); }} style={{ width: 100 }} />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 8. MoMo 补丁 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">MoMo 补丁</span>
            <span className="card-hint">五层 Patch 开关</span>
          </div>
          <div className="card-body">
            <MoMoToggle label="启用 MoMo" field="enabled" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L1 拦截 CONNECT" field="connect_intercept" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L2 Clash fake-ip 重解析" field="dns_fix" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L3 payment_method 注入" field="pm_inject" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L4 confirm payload 构造" field="confirm_build" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
            <MoMoToggle label="L5 MoMo 支付 URL 正则" field="resolve_regex" cfg={cfg} setCfg={setCfg} scheduleSave={scheduleCfgSave} />
          </div>
        </div>
      )}

      {/* 9. 存储 / 日志 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">存储 / 日志</span>
            <span className="card-hint">SQLite · 样本目录 · 日志级别</span>
          </div>
          <div className="card-body">
            <CfgTextRow label="Token 数据库" section="storage" field="db_path" value={cfg.storage.db_path} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="tokens.db" />
            <CfgTextRow label="样本目录" section="storage" field="samples_dir" value={cfg.storage.samples_dir} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="samples" />
            <CfgTextRow label="运行目录" section="storage" field="runs_dir" value={cfg.storage.runs_dir} onCfg={setCfg} scheduleSave={scheduleCfgSave} placeholder="runs" />
            <div className="setting-row">
              <span className="setting-label">日志级别</span>
              <div className="setting-control">
                <select className="select" value={cfg.logging.level} onChange={(e) => { const v = e.target.value; setCfg(c => c ? { ...c, logging: { ...c.logging, level: v } } : c); scheduleCfgSave("logging", { level: v }); }} style={{ width: 140 }}>
                  <option value="DEBUG">DEBUG</option>
                  <option value="INFO">INFO</option>
                  <option value="WARNING">WARNING</option>
                  <option value="ERROR">ERROR</option>
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">JSON 日志</span>
              <div className="setting-control">
                <label className="switch">
                  <input type="checkbox" checked={cfg.logging.json_logs} onChange={(e) => { const v = e.target.checked; setCfg(c => c ? { ...c, logging: { ...c.logging, json_logs: v } } : c); scheduleCfgSave("logging", { json_logs: v }); }} />
                  <span className="switch-track" />
                </label>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 10. IP 地理查询 */}
      {cfg && (
        <div className="card">
          <div className="card-head">
            <span className="card-title">IP 地理查询</span>
            <span className="card-hint">出口国探测 · 数据源 · 超时</span>
          </div>
          <div className="card-body">
            <div className="setting-row">
              <span className="setting-label">启用</span>
              <div className="setting-control">
                <label className="switch">
                  <input type="checkbox" checked={cfg.geo.enabled} onChange={(e) => { const v = e.target.checked; setCfg(c => c ? { ...c, geo: { ...c.geo, enabled: v } } : c); scheduleCfgSave("geo", { enabled: v }); }} />
                  <span className="switch-track" />
                </label>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">查询超时</span>
              <div className="setting-control">
                <input className="input" type="number" value={cfg.geo.timeout} onChange={(e) => { const v = +e.target.value; setCfg(c => c ? { ...c, geo: { ...c.geo, timeout: v } } : c); scheduleCfgSave("geo", { timeout: v }); }} style={{ width: 100 }} /> <span className="muted" style={{ fontSize: 11.5 }}>秒</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">数据源</span>
              <div className="setting-control">
                <input className="input" value={(cfg.geo.sources || []).join(", ")} onChange={(e) => { const v = e.target.value.split(",").map((s) => s.trim()).filter(Boolean); setCfg(c => c ? { ...c, geo: { ...c.geo, sources: v } } : c); scheduleCfgSave("geo", { sources: v }); }} placeholder="ip-api, ipwhois, ipinfo" style={{ width: 280 }} />
                <span className="setting-hint">逗号分隔</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {rebindOpen && (
        <div className="overlay" onClick={() => setRebindOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="换绑邮箱管理" onClick={(e) => e.stopPropagation()} style={{ padding: 22, width: "min(680px, calc(100vw - 24px))" }}>
            <div className="page-head" style={{ marginBottom: 14 }}><div><h3 className="page-title" style={{ fontSize: 20 }}>换绑邮箱管理</h3><p className="page-sub">每行格式：邮箱----HTTP(S)取码地址</p></div><button className="btn btn-ghost" onClick={() => setRebindOpen(false)}>关闭</button></div>
            <div className="card" style={{ marginBottom: 14 }}><div className="card-head"><span className="card-title">批量导入</span><span className="card-hint">目标邮箱验证码由地址自动读取</span></div><div className="card-body">
              <textarea className="textarea" rows={5} value={rebindImportText} onChange={(e) => setRebindImportText(e.target.value)} placeholder={'邮箱----https://example.com/code/TOKEN'} style={{ width: "100%", resize: "vertical", minHeight: 120 }} />
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}><button className="btn btn-primary" onClick={importRebindMailboxes} disabled={rebindBusy || !rebindImportText.trim()}>批量导入</button><button className="btn btn-ghost" onClick={() => rebindFileRef.current?.click()} disabled={rebindBusy}>读取文件</button><input ref={rebindFileRef} type="file" accept=".txt,.csv,text/plain" onChange={readRebindFile} style={{ display: "none" }} />{rebindMessage && <span className="muted" style={{ fontSize: 12, color: "var(--ok)" }}>{rebindMessage}</span>}</div>
            </div></div>
            <div className="card"><div className="card-head"><span className="card-title">邮箱列表 ({rebindMailboxes.length})</span><span className="card-hint">已选择 {rebindSelected.size} 条</span></div><div className="card-body"><div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}><button className="btn btn-ghost btn-sm" onClick={() => setRebindSelected(new Set(rebindMailboxes.map((m) => m.id)))} disabled={!rebindMailboxes.length}>全选</button><button className="btn btn-ghost btn-sm" onClick={() => setRebindSelected(new Set())}>取消选择</button><button className="btn btn-ghost btn-sm" onClick={exportRebindMailboxes} disabled={!rebindMailboxes.length || rebindBusy}>批量导出</button><button className="btn btn-danger btn-sm" onClick={() => deleteRebindMailboxes(false)} disabled={!rebindSelected.size || rebindBusy}>删除选择</button><button className="btn btn-danger btn-sm" onClick={() => deleteRebindMailboxes(true)} disabled={!rebindMailboxes.length || rebindBusy}>删除全部</button></div><div style={{ maxHeight: 360, overflowY: "auto", borderTop: "1px solid var(--border)" }}>{!rebindMailboxes.length ? <div className="muted" style={{ padding: "24px 8px", textAlign: "center" }}>暂无换绑邮箱</div> : rebindMailboxes.map((m) => <label key={m.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 4px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}><input type="checkbox" checked={rebindSelected.has(m.id)} onChange={(e) => setRebindSelected((prev) => { const next = new Set(prev); if (e.target.checked) next.add(m.id); else next.delete(m.id); return next; })} style={{ marginTop: 3 }} /><span style={{ minWidth: 0, flex: 1 }}><span style={{ display: "block", fontSize: 13, color: "var(--text-1)" }}>{m.email}</span><span style={{ display: "block", marginTop: 3, fontSize: 11, color: "var(--text-3)", overflowWrap: "anywhere" }}>{m.code_url}</span><span className="muted" style={{ fontSize: 11 }}>已使用 {m.used_count || 0} 次{m.last_error ? ` · ${m.last_error}` : ""}</span></span></label>)}</div></div></div>
          </div>
        </div>
      )}

      {mailboxOpen && (
        <div className="overlay" onClick={() => setMailboxOpen(false)}>
          <div
            className="sheet"
            role="dialog"
            aria-modal="true"
            aria-label="api798 邮箱管理"
            onClick={(e) => e.stopPropagation()}
            style={{ padding: 22, width: "min(680px, calc(100vw - 24px))" }}
          >
            <div className="page-head" style={{ marginBottom: 14 }}>
              <div>
                <h3 className="page-title" style={{ fontSize: 20 }}>api798 邮箱管理</h3>
                <p className="page-sub">每行格式：EMAIL----AUTH_URL，支持 Markdown 链接和旧卡密格式</p>
              </div>
              <button className="btn btn-ghost" onClick={() => setMailboxOpen(false)}>关闭</button>
            </div>

            <div className="card" style={{ marginBottom: 14 }}>
              <div className="card-head"><span className="card-title">批量导入邮箱</span><span className="card-hint">粘贴多行或读取 txt 文件</span></div>
              <div className="card-body">
                <textarea
                  className="textarea"
                  rows={5}
                  value={mailboxImportText}
                  onChange={(e) => setMailboxImportText(e.target.value)}
                  placeholder={'邮箱----auth_code 地址\n例如：name@example.com----https://email.example/m/TOKEN'}
                  style={{ width: "100%", resize: "vertical", minHeight: 120 }}
                />
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                  <button className="btn btn-primary" onClick={importMailboxes} disabled={mailboxBusy || !mailboxImportText.trim()}>批量导入邮箱</button>
                  <button className="btn btn-ghost" onClick={() => mailboxFileRef.current?.click()} disabled={mailboxBusy}>读取文件</button>
                  <input ref={mailboxFileRef} type="file" accept=".txt,.csv,text/plain" onChange={readMailboxFile} style={{ display: "none" }} />
                  {mailboxMessage && <span className="muted" style={{ fontSize: 12, color: "var(--ok)" }}>{mailboxMessage}</span>}
                </div>
              </div>
            </div>

            <div className="card">
              <div className="card-head">
                <span className="card-title">邮箱列表 ({mailboxes.length})</span>
                <span className="card-hint">已选择 {selectedMailboxIds.size} 条</span>
              </div>
              <div className="card-body">
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <button className="btn btn-ghost btn-sm" onClick={selectAllMailboxes} disabled={!mailboxes.length || mailboxBusy}>全选</button>
                  <button className="btn btn-ghost btn-sm" onClick={clearMailboxSelection} disabled={!selectedMailboxIds.size || mailboxBusy}>取消选择</button>
                  <button className="btn btn-ghost btn-sm" onClick={exportMailboxes} disabled={!mailboxes.length || mailboxBusy}>批量导出邮箱</button>
                  <button className="btn btn-danger btn-sm" onClick={() => deleteMailboxes(false)} disabled={!selectedMailboxIds.size || mailboxBusy}>删除选择</button>
                  <button className="btn btn-danger btn-sm" onClick={() => deleteMailboxes(true)} disabled={!mailboxes.length || mailboxBusy}>删除全部</button>
                </div>
                <div style={{ maxHeight: 360, overflowY: "auto", borderTop: "1px solid var(--border)" }}>
                  {!mailboxes.length ? (
                    <div className="muted" style={{ padding: "24px 8px", textAlign: "center" }}>暂无邮箱记录</div>
                  ) : mailboxes.map((m) => {
                    const address = m.auth_url || m.auth_code || "";
                    return (
                      <label key={m.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 4px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}>
                        <input type="checkbox" checked={selectedMailboxIds.has(m.id)} onChange={(e) => {
                          setSelectedMailboxIds((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(m.id); else next.delete(m.id);
                            return next;
                          });
                        }} style={{ marginTop: 3 }} />
                        <span style={{ minWidth: 0, flex: 1 }}>
                          <span style={{ display: "block", fontSize: 13, color: "var(--text-1)" }}>{m.email}</span>
                          <span style={{ display: "block", marginTop: 3, fontSize: 11, color: "var(--text-3)", overflowWrap: "anywhere" }}>{address}</span>
                        </span>
                        <button
                          className="btn btn-ghost btn-sm"
                          type="button"
                          onClick={(e) => { e.preventDefault(); void testMailbox(m.id); }}
                          disabled={mailboxBusy || mailboxTestId === m.id}
                        >{mailboxTestId === m.id ? "测试中" : "测试取码"}</button>
                      </label>
                    );
                  })}
                </div>
                {mailboxMessage && <div className="muted" style={{ marginTop: 10, fontSize: 12, color: "var(--ok)" }}>{mailboxMessage}</div>}
              </div>
            </div>
          </div>
        </div>
      )}

      {clashResultOpen && (
        <div className="overlay" onClick={() => setClashResultOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="Clash 识别结果" onClick={(e) => e.stopPropagation()} style={{ padding: 22, width: "min(980px, calc(100vw - 24px))" }}>
            <div className="page-head" style={{ marginBottom: 14 }}>
              <div>
                <h3 className="page-title" style={{ fontSize: 20 }}>Clash 识别结果</h3>
                <p className="page-sub">按国家分类显示节点、出口 IP 和节点详情</p>
              </div>
              <button className="btn btn-ghost" onClick={() => setClashResultOpen(false)}>关闭</button>
            </div>
            <div className="card" style={{ marginBottom: 14 }}>
              <div className="card-head"><span className="card-title">国家分类</span><span className="card-hint">{clashCountries.length} 个国家 · {clashCountries.reduce((sum, group) => sum + group.count, 0)} 个节点</span></div>
              <div className="card-body" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {clashCountries.map((group) => <span className="tag" key={group.country}>{group.country} · {group.count} 个{group.ips.length ? ` · ${group.ips.join(", ")}` : ""}</span>)}
              </div>
            </div>
            <div className="card">
              <div className="card-head"><span className="card-title">节点详情</span><span className="card-hint">共 {clashCountries.reduce((sum, group) => sum + group.nodes.length, 0)} 条</span></div>
              <div className="card-body" style={{ paddingTop: 0 }}>
                <div style={{ maxHeight: "min(52vh, 520px)", overflowY: "auto" }}>
                  {clashCountries.flatMap((group) => group.nodes).map((node) => <div key={`${node.country}:${node.name}:${node.server}:${node.port}`} style={{ display: "grid", gridTemplateColumns: "80px minmax(150px, 1fr) 150px 70px", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--border-faint)", fontSize: 12 }}><span>{node.country}</span><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={node.name}>{node.name}</span><span className="mono">{node.ip || node.server}:{node.port}</span><span className="muted">{node.type}</span></div>)}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── 子组件 ──────────────────────────────────────────────────────── */

// secrets.json 单字段文本/密码行
function SecretRow({ label, value, onChange, placeholder, password, hint, action }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; password?: boolean; hint?: string; action?: React.ReactNode;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <input className="input" type={password ? "password" : "text"} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} style={{ width: 300 }} />
        {action}
        {hint && <span className="setting-hint">{hint}</span>}
      </div>
    </div>
  );
}

// secrets.json 下拉行
function SecretSelectRow({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: [string, string][];
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <select className="select" value={value} onChange={(e) => onChange(e.target.value)} style={{ width: 220 }}>
          <option value="">(默认)</option>
          {options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
        </select>
      </div>
    </div>
  );
}

// QG 代理池凭据行 (host/port/auth_key/auth_pwd)
function PoolRow({ label, pool, onChange }: {
  label: string; pool?: ProxyPool; onChange: (p: ProxyPool) => void;
}) {
  const p = pool || { host: "", port: 0, auth_key: "", auth_pwd: "" };
  const upd = (k: keyof ProxyPool, v: string | number) => onChange({ ...p, [k]: v });
  return (
    <>
      <div className="setting-row">
        <span className="setting-label">{label} host</span>
        <div className="setting-control">
          <input className="input" value={p.host} onChange={(e) => upd("host", e.target.value)} placeholder="proxy.qg.example.com" style={{ width: 200 }} />
          <input className="input" type="number" value={p.port || ""} onChange={(e) => upd("port", +e.target.value)} placeholder="端口" style={{ width: 80 }} />
        </div>
      </div>
      <div className="setting-row">
        <span className="setting-label">{label} 凭据</span>
        <div className="setting-control">
          <input className="input" value={p.auth_key} onChange={(e) => upd("auth_key", e.target.value)} placeholder="auth_key" style={{ width: 180 }} />
          <input className="input" type="password" value={p.auth_pwd} onChange={(e) => upd("auth_pwd", e.target.value)} placeholder="auth_pwd" style={{ width: 180 }} />
        </div>
      </div>
    </>
  );
}

// config.yaml A 层文本行 (通用)
function CfgTextRow({ label, section, field, value, onCfg, scheduleSave, placeholder }: {
  label: string; section: ConfigSection; field: string; value: string; onCfg: React.Dispatch<React.SetStateAction<ConfigScalars | null>>; scheduleSave: (s: ConfigSection, f: Record<string, unknown>) => void; placeholder?: string;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <input className="input" value={value} placeholder={placeholder} onChange={(e) => {
          const v = e.target.value;
          onCfg((c) => {
            if (!c) return c;
            if (section === "stripe") return { ...c, stripe: { ...c.stripe, [field]: v } };
            if (section === "tls") return { ...c, tls: { ...c.tls, [field]: v } };
            if (section === "storage") return { ...c, storage: { ...c.storage, [field]: v } };
            return c;
          });
          scheduleSave(section, { [field]: v });
        }} style={{ width: 320 }} />
      </div>
    </div>
  );
}
// 可批量维护的住宅代理池。两种池共用管理界面，但数据相互隔离。
function ResidentialProxyPoolCard({ pool, title, hint }: { pool: "api" | "mixed"; title: string; hint: string }) {
  const [items, setItems] = useState<ResidentialProxy[]>([]);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [text, setText] = useState("");
  const [importCountry, setImportCountry] = useState("");
  const [selectedCountry, setSelectedCountry] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<{ ok: boolean; proxies?: ResidentialProxy[] }>(`/api/config/residential_proxies/${pool}`, "GET");
      if (r?.ok) setItems(r.proxies || []);
    } catch (e: any) { setMessage(e?.message || "代理列表加载失败"); }
  }, [pool]);
  useEffect(() => { load(); }, [load]);

  const openManager = async () => { setOpen(true); setMessage(""); setSelected(new Set()); await load(); };
  const importItems = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string; proxies?: ResidentialProxy[]; imported?: number }>(
        `/api/config/residential_proxies/${pool}/import`, "POST", {
          text, country: importCountry.trim().toUpperCase() || undefined,
        }
      );
      if (!r?.ok) throw new Error(r?.error || "导入失败");
      setItems(r.proxies || []); setText(""); setSelected(new Set()); setMessage(`已导入 ${r.imported || 0} 条`);
    } catch (e: any) { setMessage(e?.message || "导入失败"); }
    finally { setBusy(false); }
  };
  const tagSelectedCountry = async () => {
    const ids = Array.from(selected);
    if (!ids.length) return;
    const country = selectedCountry.trim().toUpperCase();
    if (country && !/^[A-Z]{2}$/.test(country)) {
      setMessage("国家请填写两位 ISO 代码，例如 PL");
      return;
    }
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string; proxies?: ResidentialProxy[]; updated?: number }>(
        `/api/config/residential_proxies/${pool}/country`, "POST", { ids, country }
      );
      if (!r?.ok) throw new Error(r?.error || "国家标记失败");
      setItems(r.proxies || []);
      setMessage(country ? `已将 ${r.updated || 0} 条标记为 ${country}` : `已清除 ${r.updated || 0} 条国家标记`);
    } catch (e: any) { setMessage(e?.message || "国家标记失败"); }
    finally { setBusy(false); }
  };
  const exportItems = async () => {
    setBusy(true);
    try {
      const ids = Array.from(selected);
      const r = await api<{ ok: boolean; content?: string; total?: number }>(
        `/api/config/residential_proxies/${pool}/export`, "POST", ids.length ? { ids } : {}
      );
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([r?.content || ""], { type: "text/plain;charset=utf-8" }));
      a.download = `${pool}-residential-proxies-${new Date().toISOString().slice(0, 10)}.txt`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
      setMessage(`已导出 ${r?.total || 0} 条`);
    } catch (e: any) { setMessage(e?.message || "导出失败"); }
    finally { setBusy(false); }
  };
  const deleteItems = async (all = false) => {
    const ids = Array.from(selected);
    if (!all && !ids.length) return;
    if (!window.confirm(all ? `确定删除${title}全部记录？` : `确定删除选中的 ${ids.length} 条代理？`)) return;
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; error?: string; proxies?: ResidentialProxy[] }>(
        `/api/config/residential_proxies/${pool}/delete`, "POST", all ? { delete_all: true } : { ids }
      );
      if (!r?.ok) throw new Error(r?.error || "删除失败");
      setItems(r.proxies || []); setSelected(new Set()); setMessage(all ? "已删除全部" : `已删除 ${ids.length} 条`);
    } catch (e: any) { setMessage(e?.message || "删除失败"); }
    finally { setBusy(false); }
  };
  const readFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; e.target.value = "";
    if (file) { setText(await file.text()); setMessage(`已读取文件：${file.name}`); }
  };

  return (
    <div className="setting-row" style={{ alignItems: "center", paddingTop: 12, paddingBottom: 12 }}>
      <span className="setting-label">{title}</span>
      <div className="setting-control" style={{ flexWrap: "wrap" }}>
        <button className="btn btn-primary" onClick={openManager}>导入 / 管理</button>
        <span className="setting-hint">{hint} · 当前 {items.length} 条</span>
      </div>
      {open && (
        <div className="overlay" onClick={() => setOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label={`${title}管理`} onClick={(e) => e.stopPropagation()} style={{ padding: 22, width: "min(760px, calc(100vw - 24px))" }}>
            <div className="page-head" style={{ marginBottom: 14 }}>
              <div><h3 className="page-title" style={{ fontSize: 20 }}>{title}</h3><p className="page-sub">{hint}</p></div>
              <button className="btn btn-ghost" onClick={() => setOpen(false)}>关闭</button>
            </div>
            <div className="card" style={{ marginBottom: 14 }}>
              <div className="card-head"><span className="card-title">批量导入</span><span className="card-hint">自动识别 Country=PL、g-PL 等国家标记；无法识别时可指定本批国家</span></div>
              <div className="card-body">
                <textarea className="textarea" rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="http://user:pass@host:port 或 https://HOST/api/ProxyLogic/Generate?...&GenType=socks5" style={{ width: "100%", resize: "vertical", minHeight: 120 }} />
                <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap", alignItems: "center" }}>
                  <input className="input" value={importCountry} maxLength={2} onChange={(e) => setImportCountry(e.target.value.toUpperCase())} placeholder="本批国家（自动）" title="留空时从代理内容自动识别；例如 PL" style={{ width: 150 }} />
                  <button className="btn btn-primary" onClick={importItems} disabled={busy || !text.trim()}>批量导入</button>
                  <button className="btn btn-ghost" onClick={() => fileRef.current?.click()} disabled={busy}>读取文件</button>
                  <input ref={fileRef} type="file" accept=".txt,.csv,text/plain" onChange={readFile} style={{ display: "none" }} />
                  {message && <span className="muted" style={{ fontSize: 12, color: "var(--ok)" }}>{message}</span>}
                </div>
              </div>
            </div>
            <div className="card">
              <div className="card-head"><span className="card-title">代理列表 ({items.length})</span><span className="card-hint">已选择 {selected.size} 条</span></div>
              <div className="card-body">
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set(items.map((x) => x.id)))} disabled={busy || !items.length}>全选</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setSelected(new Set())} disabled={busy || !selected.size}>取消选择</button>
                  <button className="btn btn-ghost btn-sm" onClick={exportItems} disabled={busy || !items.length}>批量导出</button>
                  <button className="btn btn-danger btn-sm" onClick={() => deleteItems(false)} disabled={busy || !selected.size}>删除选择</button>
                  <button className="btn btn-danger btn-sm" onClick={() => deleteItems(true)} disabled={busy || !items.length}>删除全部</button>
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10, alignItems: "center" }}>
                  <input className="input" value={selectedCountry} maxLength={2} onChange={(e) => setSelectedCountry(e.target.value.toUpperCase())} placeholder="所选国家（PL）" title="给所选代理设置国家标签；留空可清除标签" style={{ width: 150 }} />
                  <button className="btn btn-ghost btn-sm" onClick={tagSelectedCountry} disabled={busy || !selected.size}>标记所选国家</button>
                  <span className="card-hint">指定注册或提链国家时，只有同国家标签的代理会被选中</span>
                </div>
                <div style={{ maxHeight: 360, overflowY: "auto", borderTop: "1px solid var(--border)" }}>
                  {!items.length ? <div className="muted" style={{ padding: "24px 8px", textAlign: "center" }}>暂无代理记录</div> : items.map((item) => (
                    <label key={item.id} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 4px", borderBottom: "1px solid var(--border)", cursor: "pointer" }}>
                      <input type="checkbox" checked={selected.has(item.id)} onChange={(e) => setSelected((prev) => { const next = new Set(prev); e.target.checked ? next.add(item.id) : next.delete(item.id); return next; })} style={{ marginTop: 3 }} />
                      <span style={{ minWidth: 0, flex: 1, overflowWrap: "anywhere", fontSize: 12.5 }}>{item.url}</span>
                      <span className={`badge ${item.country ? "badge-info" : "badge-muted"}`} style={{ flex: "0 0 auto" }}>{item.country || "未标记"}</span>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
// MoMo bool 开关行
function MoMoToggle({ label, field, cfg, setCfg, scheduleSave }: {
  label: string; field: keyof ConfigScalars["momo"]; cfg: ConfigScalars; setCfg: React.Dispatch<React.SetStateAction<ConfigScalars | null>>; scheduleSave: (s: ConfigSection, f: Record<string, unknown>) => void;
}) {
  return (
    <div className="setting-row">
      <span className="setting-label">{label}</span>
      <div className="setting-control">
        <label className="switch">
          <input type="checkbox" checked={cfg.momo[field]} onChange={(e) => { const v = e.target.checked; setCfg((c) => c ? { ...c, momo: { ...c.momo, [field]: v } } : c); scheduleSave("momo", { [field]: v }); }} />
          <span className="switch-track" />
        </label>
      </div>
    </div>
  );
}

// 邮箱域名池卡片 (PayPal 注册邮箱域名, 按国家可配置, 不再硬编码)
function EmailDomainsCard() {
  const [byCountry, setByCountry] = useState<Record<string, string[]>>({});
  // 后端会同时返回用户自定义域名和内置默认域名。首次使用时
  // by_country 为空，仍应使用 defaults.by_country 填充国家下拉框。
  const [defaultByCountry, setDefaultByCountry] = useState<Record<string, string[]>>({});
  const [fallback, setFallback] = useState<string[]>([]);
  const [country, setCountry] = useState("US");
  const [savedFlash, setSavedFlash] = useState("");
  const [loading, setLoading] = useState(true);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<{
        ok: boolean;
        by_country?: Record<string, string[]>;
        fallback?: string[];
        defaults?: { by_country?: Record<string, string[]>; fallback?: string[] };
      }>("/api/config/email_domains", "GET");
      if (r?.ok) {
        setByCountry(r.by_country || {});
        setDefaultByCountry(r.defaults?.by_country || {});
        setFallback(r.fallback || []);
        const keys = Object.keys({ ...(r.defaults?.by_country || {}), ...(r.by_country || {}) });
        if (keys.length && !keys.includes(country)) setCountry(keys[0]);
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [country]);

  useEffect(() => { load(); }, [load]);

  const scheduleSave = (nextBy: Record<string, string[]>, nextFallback: string[]) => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try {
        await api("/api/config/email_domains", "POST", { by_country: nextBy, fallback: nextFallback });
        setSavedFlash("已保存 ✓");
        setTimeout(() => setSavedFlash(""), 1500);
      } catch { /* ignore */ }
    }, 800);
  };

  const updCountry = (val: string) => {
    const domains = val.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    const next = { ...byCountry, [country]: domains };
    setByCountry(next);
    scheduleSave(next, fallback);
  };
  const updFallback = (val: string) => {
    const domains = val.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    setFallback(domains);
    scheduleSave(byCountry, domains);
  };
  const reset = async () => {
    if (!window.confirm("重置为内置默认域名池？用户自定义将丢失。")) return;
    try {
      const r = await api<{ ok: boolean; by_country?: Record<string, string[]>; fallback?: string[] }>("/api/config/email_domains", "POST", { reset: true });
      if (r?.ok) {
        setByCountry(r.by_country || {});
        setFallback(r.fallback || []);
        setSavedFlash("已重置 ✓");
        setTimeout(() => setSavedFlash(""), 1500);
      }
    } catch { /* ignore */ }
  };

  const availableCountries = Object.keys({ ...defaultByCountry, ...byCountry }).sort();
  const currentDomains = (Object.prototype.hasOwnProperty.call(byCountry, country)
    ? byCountry[country]
    : defaultByCountry[country] || []).join(", ");
  const fallbackStr = fallback.join(", ");

  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">邮箱域名池</span>
        <span className="card-hint">PayPal 注册邮箱域名 · 按国家配置（留空回落内置默认）</span>
      </div>
      <div className="card-body">
        {loading ? (
          <div className="muted" style={{ fontSize: 12.5 }}>加载中…</div>
        ) : (
          <>
            <div className="setting-row">
              <span className="setting-label">国家</span>
              <div className="setting-control">
                <select className="select" value={country} onChange={(e) => setCountry(e.target.value)} style={{ width: 120 }}>
                  {availableCountries.map((c) => (<option key={c} value={c}>{c}</option>))}
                </select>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">{country} 域名</span>
              <div className="setting-control">
                <input className="input" value={currentDomains} onChange={(e) => updCountry(e.target.value)} placeholder="例: gmail.com, outlook.com" style={{ width: 320 }} />
                <span className="muted" style={{ fontSize: 11.5 }}>逗号分隔</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label">通用 fallback</span>
              <div className="setting-control">
                <input className="input" value={fallbackStr} onChange={(e) => updFallback(e.target.value)} placeholder="例: gmail.com, yahoo.com" style={{ width: 320 }} />
                <span className="muted" style={{ fontSize: 11.5 }}>无国家匹配时使用 · 逗号分隔</span>
              </div>
            </div>
            <div className="setting-row">
              <span className="setting-label"></span>
              <div className="setting-control">
                <button className="btn btn-ghost" onClick={reset} style={{ fontSize: 12.5 }}>↺ 重置为默认</button>
                {savedFlash && <span className="muted" style={{ fontSize: 11.5, color: "var(--ok)", marginLeft: 8 }}>{savedFlash}</span>}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
