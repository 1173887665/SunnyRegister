import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";
import type { Mailbox, MailPoolData, MailPoolRules, MailPoolStats, ImapPreset, AliasMode, MailPoolSettings } from "../types";

type ProviderFilter = "all" | "imap" | "api798";

const EMPTY_MBOX: Omit<Mailbox, "id" | "created_at" | "status" | "last_check" | "last_error" | "used_count"> = {
  provider: "imap",
  label: "",
  imap_host: "",
  imap_port: 993,
  imap_ssl: true,
  username: "",
  password: "",
  code_api_url: "",
  alias_mode: "direct",
  catchall_domain: "",
  sender_whitelist: [],
  subject_whitelist: [],
  code_regex: "",
  enabled: true,
};

export function MailPoolView() {
  const [data, setData] = useState<MailPoolData | null>(null);
  const [stats, setStats] = useState<MailPoolStats | null>(null);
  const [editing, setEditing] = useState<Partial<Mailbox> | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null); // null = 新建
  const [showPw, setShowPw] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkText, setBulkText] = useState("");
  const [bulkResult, setBulkResult] = useState<string | null>(null);
  const [splitOpen, setSplitOpen] = useState(false);
  const [splitCount, setSplitCount] = useState(10);
  const [splitResult, setSplitResult] = useState<string | null>(null);
  const [settings, setSettings] = useState<MailPoolSettings>({ enabled: true, split_enabled: false, split_count: 10 });
  const [busy, setBusy] = useState<string | null>(null); // 测试中 id
  const [msg, setMsg] = useState<string | null>(null);
  const [providerFilter, setProviderFilter] = useState<ProviderFilter>("all");

  const rulesTimer = useRef<number | undefined>(undefined);
  const [rules, setRules] = useState<MailPoolRules>({ sender_whitelist: [], subject_whitelist: [], code_regex: "" });

  async function loadAll() {
    try {
      const d = await api<MailPoolData>("/api/mail_pool");
      setData(d);
      setRules(d.rules);
      setSettings(d.settings || { enabled: true, split_enabled: false, split_count: 10 });
      const all = d.mailboxes;
      setStats({
        total: all.length,
        enabled: all.filter((m) => m.enabled).length,
        disabled: all.filter((m) => !m.enabled).length,
        ok_count: all.filter((m) => m.status === "ok").length,
        fail: all.filter((m) => m.status === "fail").length,
        used_total: all.reduce((sum, m) => sum + (m.used_count || 0), 0),
      });
    } catch (e: any) {
      setMsg("加载失败: " + (e?.message || e));
    }
  }

  useEffect(() => { loadAll(); }, []);

  // 规则 1s 防抖自动保存
  function scheduleRulesSave(next: MailPoolRules) {
    setRules(next);
    if (rulesTimer.current) window.clearTimeout(rulesTimer.current);
    rulesTimer.current = window.setTimeout(async () => {
      try { await api("/api/mail_pool/rules", "PUT", next); } catch (e: any) { setMsg("规则保存失败: " + (e?.message || e)); }
    }, 1000);
  }

  async function saveMailbox() {
    if (!editing) return;
    const provider = editing.provider || "imap";
    if (!editing.username || (provider === "imap" ? !editing.imap_host : !editing.code_api_url)) {
      setMsg(provider === "imap" ? "主机和用户名必填" : "邮箱地址和接码 URL 必填");
      return;
    }
    try {
      if (editingId) {
        await api(`/api/mail_pool/${editingId}`, "PUT", editing);
      } else {
        await api("/api/mail_pool", "POST", editing);
      }
      setEditing(null); setEditingId(null); setShowPw(false);
      await loadAll();
      setMsg(null);
    } catch (e: any) { setMsg("保存失败: " + (e?.message || e)); }
  }

  async function delMailbox(m: Mailbox) {
    if (!confirm("删除该邮箱？")) return;
    try {
      await api(`/api/mail_pool/${m.id}`, "DELETE");
      await loadAll();
    } catch (e: any) { setMsg("删除失败: " + (e?.message || e)); }
  }

  async function toggleEnabled(m: Mailbox) {
    try {
      await api(`/api/mail_pool/${m.id}/${m.enabled ? "disable" : "enable"}`, "POST");
      await loadAll();
    } catch (e: any) { setMsg("切换失败: " + (e?.message || e)); }
  }

  async function testOne(id: string) {
    setBusy(id);
    try {
      const r = await api<{ ok: boolean; status: string; last_error: string }>(`/api/mail_pool/${id}/test`, "POST");
      setMsg(r.ok ? "连接成功 ✓" : `连接失败: ${r.last_error || ""}`);
      await loadAll();
    } catch (e: any) { setMsg("测试失败: " + (e?.message || e)); }
    finally { setBusy(null); }
  }

  async function testAll() {
    setBusy("__all__");
    setMsg("正在逐个测试...");
    try {
      const r = await api<{ ok: boolean; results: { id: string; ok: boolean; last_error: string }[] }>("/api/mail_pool/test_all", "POST", { ids: mboxes.map((m) => m.id) });
      const ok = r.results.filter((x) => x.ok).length;
      setMsg(`全部测试完成: ${ok}/${r.results.length} 成功`);
      await loadAll();
    } catch (e: any) { setMsg("批量测试失败: " + (e?.message || e)); }
    finally { setBusy(null); }
  }

  /* ── 批量管理 ── */
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const mboxes = data?.mailboxes ?? [];
  const visibleMboxes = providerFilter === "all"
    ? mboxes
    : mboxes.filter((m) => m.provider === providerFilter);
  const toggleSelect = (id: string) =>
    setSelected((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  async function bulkSetEnabled(enabled: boolean) {
    const ids = Array.from(selected);
    if (!ids.length) return;
    try {
      await api("/api/mail_pool/bulk_enable", "POST", { mbox_ids: ids, enabled });
      setSelected(new Set());
      await loadAll();
      setMsg(`已${enabled ? "启用" : "禁用"} ${ids.length} 个邮箱`);
    } catch (e: any) { setMsg("批量操作失败: " + (e?.message || e)); }
  }

  async function bulkTest() {
    const ids = Array.from(selected);
    if (!ids.length) return;
    setBusy("__all__");
    setMsg(`正在测试 ${ids.length} 个邮箱...`);
    try {
      const r = await api<{ ok: boolean; results: { id: string; ok: boolean; last_error: string }[] }>("/api/mail_pool/test_all", "POST", { ids });
      const ok = r.results.filter((x) => x.ok).length;
      setMsg(`批量测试完成: ${ok}/${r.results.length} 成功`);
      await loadAll();
    } catch (e: any) { setMsg("批量测试失败: " + (e?.message || e)); }
    finally { setBusy(null); }
  }

  async function bulkDelete() {
    const ids = Array.from(selected);
    if (!ids.length) return;
    if (!confirm(`确认删除选中的 ${ids.length} 个邮箱？此操作不可撤销。`)) return;
    try {
      await api("/api/mail_pool/bulk_delete", "POST", { mbox_ids: ids });
      setSelected(new Set());
      await loadAll();
      setMsg(`已删除 ${ids.length} 个邮箱`);
    } catch (e: any) { setMsg("批量删除失败: " + (e?.message || e)); }
  }

  async function bulkImport() {
    setBulkResult(null);
    try {
      const r = await api<{ ok: boolean; added: number; skipped: number; errors: string[] }>("/api/mail_pool/bulk", "POST", { text: bulkText });
      setBulkResult(`成功 ${r.added} 条, 跳过 ${r.skipped} 条${r.errors.length ? ` (${r.errors.slice(0, 3).join("; ")})` : ""}`);
      if (r.added > 0) { await loadAll(); setBulkText(""); }
    } catch (e: any) { setBulkResult("导入失败: " + (e?.message || e)); }
  }

  async function exportMailboxes(scope: "all" | "unused" | "used" = "all") {
    try {
      const r = await api<{ ok: boolean; text?: string; count?: number }>("/api/mail_pool/export", "POST", { scope });
      const blob = new Blob([r.text || ""], { type: "text/plain;charset=utf-8" });
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = `邮箱管理-${scope}-${new Date().toISOString().slice(0, 10)}.txt`;
      link.click();
      URL.revokeObjectURL(href);
      setMsg(`已导出 ${r.count || 0} 条邮箱`);
    } catch (e: any) { setMsg("导出失败: " + (e?.message || e)); }
  }

  async function clearPool() {
    if (!mboxes.length || !window.confirm("确认清空全部邮箱？此操作不可撤销。")) return;
    try {
      const r = await api<{ removed: number }>("/api/mail_pool/clear", "POST", { provider: "all" });
      setSelected(new Set());
      await loadAll();
      setMsg(`已清空 ${r.removed || 0} 条邮箱`);
    } catch (e: any) { setMsg("清空失败: " + (e?.message || e)); }
  }

  async function savePoolSettings(next: MailPoolSettings) {
    setSettings(next);
    try {
      await api("/api/mail_pool/settings", "PUT", next);
      setMsg("邮箱管理设置已保存");
    } catch (e: any) { setMsg("设置保存失败: " + (e?.message || e)); }
  }

  async function splitAliases() {
    const ids = Array.from(selected).filter((id) => mboxes.some((m) => m.id === id && m.provider === "imap"));
    setBusy("split");
    setSplitResult(null);
    try {
      const r = await api<{ ok: boolean; added?: number; parents?: number; error?: string }>("/api/mail_pool/split_generate", "POST", {
        count: Math.max(1, Math.min(200, Number(splitCount) || 10)),
        ...(ids.length ? { ids } : {}),
      });
      if (!r.ok) throw new Error(r.error || "分裂失败");
      setSplitResult(`分裂完成：${r.parents || 0} 个主邮箱新增 ${r.added || 0} 个别名`);
      setSelected(new Set());
      await loadAll();
    } catch (e: any) {
      setSplitResult("分裂失败: " + (e?.message || e));
    } finally { setBusy(null); }
  }

  function startEdit(m: Mailbox) {
    setEditing({ ...m }); setEditingId(m.id); setShowPw(false);
  }
  function startAdd() {
    setEditing({ ...EMPTY_MBOX }); setEditingId(null); setShowPw(false);
  }
  function applyPreset(p: ImapPreset) {
    if (!editing) return;
    setEditing({ ...editing, imap_host: p.imap_host, imap_port: p.imap_port, imap_ssl: p.imap_ssl });
  }

  // ── 预设管理: 用户可增删自定义预设主机 (不再硬编码, 落盘 mail_presets.json) ──
  const [presetOpen, setPresetOpen] = useState(false);
  const [presetForm, setPresetForm] = useState<ImapPreset>({ label: "", imap_host: "", imap_port: 993, imap_ssl: true });

  async function addPreset() {
    const label = presetForm.label.trim();
    const host = presetForm.imap_host.trim();
    if (!label || !host) { setMsg("预设标签和主机不能为空"); return; }
    try {
      await api("/api/mail_pool/presets", "POST", {
        label, imap_host: host, imap_port: presetForm.imap_port || 993, imap_ssl: presetForm.imap_ssl,
      });
      setPresetForm({ label: "", imap_host: "", imap_port: 993, imap_ssl: true });
      setPresetOpen(false);
      await loadAll();
    } catch (e: any) { setMsg("添加预设失败: " + (e?.message || e)); }
  }
  async function delPreset(label: string) {
    if (!window.confirm(`删除预设「${label}」？`)) return;
    try {
      await api(`/api/mail_pool/presets/${encodeURIComponent(label)}`, "DELETE");
      await loadAll();
    } catch (e: any) { setMsg("删除预设失败: " + (e?.message || e)); }
  }

  const unusedMboxes = visibleMboxes.filter((m) => !m.used_count);
  const usedMboxes = visibleMboxes.filter((m) => Boolean(m.used_count));
  const renderMailboxTable = (rows: Mailbox[]) => (
    <table className="table" style={{ width: "100%" }}>
      <thead>
        <tr>
          <th style={{ width: 32 }}><input type="checkbox" checked={rows.length > 0 && rows.every((m) => selected.has(m.id))} onChange={() => setSelected((prev) => {
            const next = new Set(prev);
            const allChecked = rows.length > 0 && rows.every((m) => next.has(m.id));
            rows.forEach((m) => allChecked ? next.delete(m.id) : next.add(m.id));
            return next;
          })} /></th>
          <th>来源</th>
          <th style={{ textAlign: "left" }}>标签 / 接入点</th>
          <th>用户名</th>
          <th>地址模式 / 来源</th>
          <th>状态</th>
          <th>已用</th>
          <th>最近检查</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m.id} className={selected.has(m.id) ? "row-selected" : ""}>
            <td><input type="checkbox" checked={selected.has(m.id)} onChange={() => toggleSelect(m.id)} /></td>
            <td><span className={`badge ${m.provider !== "imap" ? "badge-info" : "badge-muted"}`}>{m.provider === "mailcom_api" ? "mail.com API" : m.provider === "api798" ? "api798" : "IMAP"}</span></td>
            <td style={{ textAlign: "left" }}>
              <div className="cell-strong">{m.label || "—"}</div>
              <div className="cell-sub">{m.provider === "mailcom_api" ? (m.mailcom_account && m.mailcom_account !== m.username ? `主账号：${m.mailcom_account}` : (m.code_api_url || "接码地址")) : m.provider === "api798" ? (m.code_api_url || m.auth_code || "接码地址") : `${m.imap_host}:${m.imap_port}${m.imap_ssl ? "" : " (明文)"}`}</div>
            </td>
            <td><span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{m.username}</span></td>
            <td>{m.provider === "mailcom_api" ? (m.mailcom_primary ? <span className="badge badge-info">主账号</span> : <span className="badge badge-muted">别名</span>) : m.provider === "api798" ? <span className="badge badge-info">取码地址</span> : m.alias_of ? <span className="badge badge-info">分裂别名</span> : m.alias_mode === "catchall" ? <span className="badge badge-info">catch-all{m.catchall_domain ? ` ${m.catchall_domain}` : ""}</span> : <span className="badge badge-muted">原地址</span>}</td>
            <td><StatusBadge status={m.status} enabled={m.enabled} /></td>
            <td>{m.used_count}</td>
            <td className="muted" style={{ fontSize: 11 }}>{m.last_check ? m.last_check.slice(5, 16).replace("T", " ") : "—"}</td>
            <td>
              <div style={{ display: "flex", gap: 4, justifyContent: "center" }}>
                <button className="btn btn-sm" onClick={() => testOne(m.id)} disabled={busy === m.id}>{busy === m.id ? "..." : "测试"}</button>
                <button className="btn btn-sm btn-ghost" onClick={() => toggleEnabled(m)}>{m.enabled ? "禁用" : "启用"}</button>
                <button className="btn btn-sm btn-ghost" onClick={() => startEdit(m)}>编辑</button>
                <button className="btn btn-sm btn-ghost" style={{ color: "var(--danger)" }} onClick={() => delMailbox(m)}>删</button>
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2 className="page-title">邮箱管理</h2>
          <p className="page-sub">IMAP、接码 API、邮箱分裂与验证码规则 · 注册及 Token 恢复共用</p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={loadAll}>刷新</button>
          <button className="btn" onClick={testAll} disabled={busy === "__all__" || !mboxes.length}>
            {busy === "__all__" ? "测试中..." : "全部测试"}
          </button>
          <button className="btn" onClick={() => setBulkOpen((v) => !v)}>批量导入</button>
          <button className="btn" onClick={() => void exportMailboxes("all")} disabled={!mboxes.length}>批量导出</button>
          <button className="btn" onClick={() => { setSplitCount(settings.split_count || 10); setSplitOpen(true); }} disabled={!mboxes.some((m) => m.provider === "imap")}>分裂邮箱</button>
          <button className="btn btn-ghost" onClick={clearPool} disabled={!mboxes.length}>清空</button>
          <button className="btn btn-primary" onClick={startAdd}>+ 添加邮箱</button>
        </div>
      </div>

      {msg && (
        <div style={{ marginBottom: 12, padding: "8px 12px", borderRadius: "var(--r-input)",
          background: "var(--info-soft)", color: "var(--fg-info)", fontSize: 13 }}>
          {msg}
        </div>
      )}

      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head"><span className="card-title">邮箱管理开关与分裂策略</span><span className="muted" style={{ fontSize: 12 }}>设置会持久化并作用于注册链路</span></div>
        <div className="card-body" style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
          <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <span className="field-label">启用邮箱池</span>
            <label className="switch"><input type="checkbox" checked={settings.enabled} onChange={(e) => void savePoolSettings({ ...settings, enabled: e.target.checked })} /><span className="switch-track" /></label>
          </label>
          <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <span className="field-label">启用分裂策略</span>
            <label className="switch"><input type="checkbox" checked={settings.split_enabled} onChange={(e) => void savePoolSettings({ ...settings, split_enabled: e.target.checked })} /><span className="switch-track" /></label>
          </label>
          <label className="field" style={{ width: 170 }}>
            <span className="field-label">默认别名数量</span>
            <input className="input" type="number" min={1} max={200} value={settings.split_count} onChange={(e) => { const split_count = Math.max(1, Math.min(200, Number(e.target.value) || 1)); setSettings({ ...settings, split_count }); }} onBlur={() => void savePoolSettings(settings)} />
          </label>
          <span className="muted" style={{ fontSize: 12 }}>总数 {mboxes.length} · 未使用 {mboxes.filter((m) => !m.used_count).length}</span>
        </div>
      </section>

      {/* 统计卡 */}
      <div className="stat-grid" style={{ marginBottom: 14 }}>
        <div className="stat-card"><div className="stat-label">邮箱总数</div><div className="stat-value">{stats?.total ?? "—"}</div><div className="stat-foot">全部</div></div>
        <div className="stat-card"><div className="stat-label">可用</div><div className="stat-value" style={{ color: "var(--ok)" }}>{stats?.enabled ?? "—"}</div><div className="stat-foot">已启用</div></div>
        <div className="stat-card"><div className="stat-label">连接正常</div><div className="stat-value" style={{ color: "var(--ok)" }}>{stats?.ok_count ?? "—"}</div><div className="stat-foot">测试通过</div></div>
        <div className="stat-card"><div className="stat-label">连接失败</div><div className="stat-value" style={{ color: "var(--danger)" }}>{stats?.fail ?? "—"}</div><div className="stat-foot">需检查凭据</div></div>
        <div className="stat-card"><div className="stat-label">已用次数</div><div className="stat-value">{stats?.used_total ?? "—"}</div><div className="stat-foot">direct 领用累计</div></div>
      </div>

      {/* 全局取码规则 */}
      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head"><span className="card-title">取码规则（全局默认，单邮箱可覆盖）</span></div>
        <div className="card-body">
          <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr" }}>
            <label className="field">
              <span className="field-label">发件人白名单</span>
              <input className="input" placeholder="openai.com, noreply, auth0"
                value={rules.sender_whitelist.join(", ")}
                onChange={(e) => scheduleRulesSave({ ...rules, sender_whitelist: splitCsv(e.target.value) })} />
              <span className="field-hint">逗号分隔，发件人含其一即通过</span>
            </label>
            <label className="field">
              <span className="field-label">主题白名单</span>
              <input className="input" placeholder="verification, verify, code"
                value={rules.subject_whitelist.join(", ")}
                onChange={(e) => scheduleRulesSave({ ...rules, subject_whitelist: splitCsv(e.target.value) })} />
              <span className="field-hint">逗号分隔，主题含其一即通过</span>
            </label>
            <label className="field">
              <span className="field-label">验证码正则</span>
              <input className="input" placeholder="\b(\d{4,8})\b" value={rules.code_regex}
                onChange={(e) => scheduleRulesSave({ ...rules, code_regex: e.target.value })} />
              <span className="field-hint">第一个捕获组即验证码</span>
            </label>
          </div>
        </div>
      </section>

      {/* 批量导入 */}
      {bulkOpen && (
        <section className="card" style={{ marginBottom: 14 }}>
          <div className="card-head"><span className="card-title">批量导入</span></div>
          <div className="card-body">
            <p className="muted" style={{ fontSize: 12, marginTop: 0 }}>
              支持：<code>邮箱----专用密码</code>、<code>邮箱----接码API地址</code>，或 <code>imap_host|port|邮箱|密码|direct|域名</code>
            </p>
            <textarea className="input" rows={6} style={{ fontFamily: "var(--font-mono)" }}
              placeholder={'user@gmail.com----应用专用密码\nuser@mail.com----http://127.0.0.1:8788/code/ACCESS_KEY'}
              value={bulkText} onChange={(e) => setBulkText(e.target.value)} />
            <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
              <button className="btn btn-primary" onClick={bulkImport} disabled={!bulkText.trim()}>导入</button>
              <button className="btn" onClick={() => { setBulkOpen(false); setBulkText(""); setBulkResult(null); }}>关闭</button>
              {bulkResult && <span className="muted" style={{ alignSelf: "center", fontSize: 12 }}>{bulkResult}</span>}
            </div>
          </div>
        </section>
      )}

      {/* 邮箱列表 */}
      <section className="card">
        <div className="card-head" style={{ gap: 12, flexWrap: "wrap" }}>
          <span className="card-title">统一邮箱列表 ({visibleMboxes.length}{providerFilter !== "all" ? ` / ${mboxes.length}` : ""})</span>
          <div className="segmented mail-provider-filter" aria-label="邮箱来源筛选">
            {(["all", "imap", "api798"] as ProviderFilter[]).map((filter) => (
              <button key={filter} className={providerFilter === filter ? "active" : ""} onClick={() => { setProviderFilter(filter); setSelected(new Set()); }}>
                {filter === "all" ? "全部" : filter === "imap" ? "IMAP" : "api798"}
                <span className="muted" style={{ marginLeft: 4, fontSize: 11 }}>{filter === "all" ? mboxes.length : mboxes.filter((m) => m.provider === filter).length}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          {!mboxes.length ? (
            <div className="empty" style={{ padding: 32, textAlign: "center" }}>
              <p className="muted">暂无邮箱。点击「+ 添加邮箱」或「批量导入」开始。</p>
              <p className="muted" style={{ fontSize: 12 }}>支持 IMAP 与 mail.com 接码地址，注册及 Token 恢复共用。</p>
            </div>
          ) : (
            <>
            {selected.size > 0 && (
              <div className="batch-bar">
                <span className="tag">已选 {selected.size}</span>
                <button className="btn btn-sm" onClick={() => bulkSetEnabled(true)}>批量启用</button>
                <button className="btn btn-sm" onClick={() => bulkSetEnabled(false)}>批量禁用</button>
                <button className="btn btn-sm" onClick={bulkTest} disabled={busy === "__all__"}>{busy === "__all__" ? "测试中..." : "批量测试"}</button>
                <button className="btn btn-sm btn-danger" onClick={bulkDelete}>批量删除</button>
                <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())}>取消选择</button>
              </div>
            )}
            <MailboxZone title="未使用邮箱" count={unusedMboxes.length} hint="已用次数为 0" rows={unusedMboxes} renderTable={renderMailboxTable} />
            <MailboxZone
              title="已使用邮箱"
              count={usedMboxes.length}
              hint="已用次数大于 0，单独归档显示"
              rows={usedMboxes}
              renderTable={renderMailboxTable}
              actions={(
                <>
                  <button className="btn btn-sm" onClick={() => setSelected((prev) => new Set([...prev, ...usedMboxes.map((m) => m.id)]))} disabled={!usedMboxes.length}>选择</button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setSelected(new Set())} disabled={!selected.size}>取消选择</button>
                  <button className="btn btn-sm btn-danger" onClick={() => void bulkDelete()} disabled={!usedMboxes.some((m) => selected.has(m.id))}>删除</button>
                </>
              )}
            />
            </>
          )}
        </div>
      </section>

      {splitOpen && (
        <div className="overlay" onClick={() => busy !== "split" && setSplitOpen(false)}>
          <div className="sheet" style={{ maxWidth: 520 }} onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <h3 className="sheet-title">分裂 IMAP 邮箱</h3>
              <button className="btn btn-sm btn-ghost" onClick={() => setSplitOpen(false)}>✕</button>
            </div>
            <div className="sheet-body">
              <p className="muted" style={{ marginTop: 0 }}>为 IMAP 主邮箱生成 `local+随机@域名` 别名，共用原邮箱凭证。{selected.size ? `当前选中 ${selected.size} 条，将只处理其中的 IMAP 邮箱。` : "未选择时处理全部 IMAP 主邮箱。"}</p>
              <label className="field" style={{ maxWidth: 180 }}>
                <span className="field-label">每个主邮箱数量</span>
                <input className="input" type="number" min={1} max={200} value={splitCount} onChange={(e) => setSplitCount(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
              </label>
              {splitResult && <div style={{ marginTop: 12, padding: "8px 10px", borderRadius: "var(--r-input)", background: "var(--info-soft)", color: "var(--fg-info)", fontSize: 12 }}>{splitResult}</div>}
            </div>
            <div className="sheet-foot">
              <button className="btn" onClick={() => setSplitOpen(false)}>关闭</button>
              <button className="btn btn-primary" onClick={() => void splitAliases()} disabled={busy === "split"}>{busy === "split" ? "分裂中..." : "开始分裂"}</button>
            </div>
          </div>
        </div>
      )}

      {/* 添加/编辑 sheet */}
      {editing && (
        <div className="overlay" onClick={() => setEditing(null)}>
          <div className="sheet" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <h3 className="sheet-title">{editingId ? "编辑邮箱" : "添加邮箱"}</h3>
              <button className="btn btn-sm btn-ghost" onClick={() => setEditing(null)}>✕</button>
            </div>
            <div className="sheet-body">
              <div className="segmented" style={{ marginBottom: 14, width: "fit-content" }}>
                <button className={(editing.provider || "imap") === "imap" ? "active" : ""} onClick={() => setEditing({ ...editing, provider: "imap", code_api_url: "" })}>IMAP</button>
                <button className={(editing.provider || "imap") === "mailcom_api" ? "active" : ""} onClick={() => setEditing({ ...editing, provider: "mailcom_api", imap_host: "", password: "" })}>mail.com API</button>
                <button className={(editing.provider || "imap") === "api798" ? "active" : ""} onClick={() => setEditing({ ...editing, provider: "api798", imap_host: "", password: "" })}>api798</button>
              </div>
              {/* 预设主机（可增删, 不再硬编码） */}
              {(editing.provider || "imap") === "imap" && (
              <div style={{ marginBottom: 12 }}>
                <span className="field-label">
                  预设主机（一键填充）
                  <button className="btn btn-sm btn-ghost" type="button" style={{ marginLeft: 8, fontSize: 11.5 }} onClick={() => setPresetOpen((v) => !v)}>{presetOpen ? "取消" : "+ 添加预设"}</button>
                </span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
                  {data?.presets.map((p) => (
                    <span key={p.label} style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
                      <button className="btn btn-sm" disabled={!p.imap_host} onClick={() => applyPreset(p)}>{p.label}</button>
                      <button className="btn btn-sm btn-ghost" type="button" title="删除预设" style={{ fontSize: 11, padding: "2px 6px" }} onClick={() => delPreset(p.label)}>✕</button>
                    </span>
                  ))}
                </div>
                {presetOpen && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8, alignItems: "end" }}>
                    <label className="field" style={{ flex: "0 0 110px" }}>
                      <span className="field-label">标签</span>
                      <input className="input" value={presetForm.label} onChange={(e) => setPresetForm({ ...presetForm, label: e.target.value })} placeholder="Gmail" />
                    </label>
                    <label className="field" style={{ flex: "1 1 180px" }}>
                      <span className="field-label">IMAP 主机</span>
                      <input className="input" value={presetForm.imap_host} onChange={(e) => setPresetForm({ ...presetForm, imap_host: e.target.value })} placeholder="imap.gmail.com" />
                    </label>
                    <label className="field" style={{ flex: "0 0 90px" }}>
                      <span className="field-label">端口</span>
                      <input className="input" type="number" value={presetForm.imap_port} onChange={(e) => setPresetForm({ ...presetForm, imap_port: Number(e.target.value) || 993 })} />
                    </label>
                    <label className="field" style={{ flex: "0 0 70px" }}>
                      <span className="field-label">SSL</span>
                      <label className="switch" style={{ marginTop: 6 }}>
                        <input type="checkbox" checked={presetForm.imap_ssl} onChange={(e) => setPresetForm({ ...presetForm, imap_ssl: e.target.checked })} />
                        <span className="switch-track" />
                      </label>
                    </label>
                    <button className="btn btn-sm" type="button" onClick={addPreset}>保存</button>
                  </div>
                )}
              </div>
              )}
              <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <label className="field">
                  <span className="field-label">标签（可选）</span>
                  <input className="input" value={editing.label || ""} onChange={(e) => setEditing({ ...editing, label: e.target.value })} />
                </label>
                {(editing.provider || "imap") === "imap" ? <>
                <label className="field">
                  <span className="field-label">IMAP 主机</span>
                  <input className="input" placeholder="imap.gmail.com" value={editing.imap_host || ""} onChange={(e) => setEditing({ ...editing, imap_host: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">端口</span>
                  <input className="input" type="number" value={editing.imap_port ?? 993} onChange={(e) => setEditing({ ...editing, imap_port: Number(e.target.value) || 993 })} />
                </label>
                <label className="field">
                  <span className="field-label">SSL</span>
                  <label className="switch" style={{ marginTop: 6 }}>
                    <input type="checkbox" checked={!!editing.imap_ssl} onChange={(e) => setEditing({ ...editing, imap_ssl: e.target.checked })} />
                    <span className="switch-track" />
                  </label>
                </label>
                <label className="field">
                  <span className="field-label">用户名（邮箱地址）</span>
                  <input className="input" placeholder="user@gmail.com" value={editing.username || ""} onChange={(e) => setEditing({ ...editing, username: e.target.value })} />
                </label>
                <label className="field">
                  <span className="field-label">密码 / 应用专用密码</span>
                  <div style={{ display: "flex", gap: 4 }}>
                    <input className="input" type={showPw ? "text" : "password"} placeholder="app-specific password" value={editing.password || ""}
                      onChange={(e) => setEditing({ ...editing, password: e.target.value })} />
                    <button className="btn btn-sm btn-ghost" type="button" onClick={() => setShowPw((v) => !v)}>{showPw ? "隐" : "显"}</button>
                  </div>
                  <span className="field-hint">Gmail/Outlook 需用应用专用密码，非账号密码</span>
                </label>
                <label className="field">
                  <span className="field-label">地址模式</span>
                  <select className="select" value={editing.alias_mode} onChange={(e) => setEditing({ ...editing, alias_mode: e.target.value as AliasMode })}>
                    <option value="direct">direct（用原地址注册）</option>
                    <option value="catchall">catchall（生成别名，共用收件箱）</option>
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">catch-all 域（仅 catchall 模式）</span>
                  <input className="input" placeholder="@domain.com" value={editing.catchall_domain || ""} onChange={(e) => setEditing({ ...editing, catchall_domain: e.target.value })} />
                  <span className="field-hint">catchall 模式自动生成 oai+随机@该域</span>
                </label>
                </> : <>
                <label className="field">
                  <span className="field-label">邮箱地址</span>
                  <input className="input" placeholder="user@mail.com" value={editing.username || ""} onChange={(e) => setEditing({ ...editing, username: e.target.value })} />
                </label>
                <label className="field" style={{ gridColumn: "1 / -1" }}>
                  <span className="field-label">{(editing.provider || "imap") === "api798" ? "auth_url / auth_code" : "接码 URL"}</span>
                  <input className="input mono" type={showPw ? "text" : "password"} placeholder={(editing.provider || "imap") === "api798" ? "https://email.example/m/TOKEN 或 AUTH_CODE" : "http://127.0.0.1:8788/code/ACCESS_KEY"} value={editing.code_api_url || editing.auth_url || editing.auth_code || ""} onChange={(e) => setEditing({ ...editing, code_api_url: e.target.value, ...((editing.provider || "imap") === "api798" ? { auth_url: e.target.value, auth_code: e.target.value } : {}) })} />
                  <span className="field-hint">{(editing.provider || "imap") === "api798" ? "支持普通取码链接、Markdown 链接或卡密文本" : "由 mail.com 接码服务生成的 /code/&lt;access_key&gt; 地址"}</span>
                </label>
                </>}
              </div>
              {/* 单邮箱覆盖规则 */}
              {(editing.provider || "imap") === "imap" && (
              <div style={{ marginTop: 14, borderTop: "1px solid var(--border-faint)", paddingTop: 12 }}>
                <span className="field-label">覆盖取码规则（留空用全局默认）</span>
                <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr 1fr", marginTop: 8 }}>
                  <label className="field">
                    <span className="field-label">发件人白名单</span>
                    <input className="input" placeholder="留空=用全局" value={(editing.sender_whitelist || []).join(", ")} onChange={(e) => setEditing({ ...editing, sender_whitelist: splitCsv(e.target.value) })} />
                  </label>
                  <label className="field">
                    <span className="field-label">主题白名单</span>
                    <input className="input" placeholder="留空=用全局" value={(editing.subject_whitelist || []).join(", ")} onChange={(e) => setEditing({ ...editing, subject_whitelist: splitCsv(e.target.value) })} />
                  </label>
                  <label className="field">
                    <span className="field-label">验证码正则</span>
                    <input className="input" placeholder="留空=用全局" value={editing.code_regex || ""} onChange={(e) => setEditing({ ...editing, code_regex: e.target.value })} />
                  </label>
                </div>
              </div>
              )}
            </div>
            <div className="sheet-foot">
              <button className="btn" onClick={() => setEditing(null)}>取消</button>
              <button className="btn btn-primary" onClick={saveMailbox}>保存</button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

/* ── helpers ──────────────────────────────────────────────────── */
function splitCsv(s: string): string[] {
  return s.split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
}

function MailboxZone({
  title,
  count,
  hint,
  rows,
  renderTable,
  actions,
}: {
  title: string;
  count: number;
  hint: string;
  rows: Mailbox[];
  renderTable: (rows: Mailbox[]) => ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="mailbox-zone">
      <div className="mailbox-zone-head">
        <div className="mailbox-zone-label"><strong>{title} ({count})</strong><span className="muted">{hint}</span></div>
        {actions && <div className="mailbox-zone-actions">{actions}</div>}
      </div>
      {rows.length ? renderTable(rows) : <div className="empty mailbox-zone-empty">暂无{title}</div>}
    </section>
  );
}

function StatusBadge({ status, enabled }: { status: Mailbox["status"]; enabled: boolean }) {
  if (!enabled) return <span className="badge badge-muted">已禁用</span>;
  if (status === "ok") return <span className="badge badge-success">● 正常</span>;
  if (status === "fail") return <span className="badge badge-danger">● 失败</span>;
  return <span className="badge badge-muted">○ 未测</span>;
}
