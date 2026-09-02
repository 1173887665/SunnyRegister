import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Plus, RefreshCw, RotateCcw, Send, Trash2, X } from "lucide-react";
import { apiFetch } from "@/lib/utils";
import { PagePortal } from "@/lib/page-cache";

type Webhook = {
  id: number; name: string; url: string; enabled: boolean; scope: "global" | "group" | "account";
  scope_value: string; events: string[]; timeout_sec: number; max_attempts: number; secret_configured: boolean;
};
type Delivery = { id: number; webhook_id: number; delivery_id: string; event_type: string; account_id: number; status: string; attempts: number; response_status: number; response_body: string; error: string; created_at: string; last_attempt_at?: string };
type Draft = Omit<Webhook, "id" | "secret_configured"> & { id?: number; secret: string };

const defaultEvents = [
  "account.registered", "account.updated", "account.status_changed", "account.token_refreshed",
  "account.trial_changed", "account.subscription_changed", "account.payment_changed",
];

function newDraft(events: string[]): Draft {
  return { name: "", url: "", enabled: true, scope: "global", scope_value: "", events: events.length ? events : defaultEvents, timeout_sec: 15, max_attempts: 5, secret: "" };
}

export function AccountWebhookDialog({ onClose, notify }: { onClose: () => void; notify: (type: "ok" | "fail", text: string) => void }) {
  const [items, setItems] = useState<Webhook[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [events, setEvents] = useState<string[]>(defaultEvents);
  const [draft, setDraft] = useState<Draft>(() => newDraft(defaultEvents));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [revealedSecret, setRevealedSecret] = useState("");
  const editing = useMemo(() => Boolean(draft.id), [draft.id]);
  const load = async () => {
    setLoading(true);
    try {
      const [config, log] = await Promise.all([apiFetch("/sunny/webhooks"), apiFetch("/sunny/webhook-deliveries?limit=80")]);
      setItems(Array.isArray(config?.items) ? config.items : []);
      setEvents(Array.isArray(config?.events) && config.events.length ? config.events : defaultEvents);
      setDeliveries(Array.isArray(log?.items) ? log.items : []);
    } catch (error: any) { notify("fail", error?.message || String(error)); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, []);
  const reset = (clearSecret = true) => { setDraft(newDraft(events)); if (clearSecret) setRevealedSecret(""); };
  const beginEdit = (item: Webhook) => { setRevealedSecret(""); setDraft({ ...item, events: item.events?.length ? item.events : events, secret: "" }); };
  const toggleEvent = (event: string) => setDraft((old) => ({ ...old, events: old.events.includes(event) ? old.events.filter((value) => value !== event) : [...old.events, event] }));
  const save = async () => {
    if (!draft.name.trim() || !draft.url.trim()) { notify("fail", "请填写名称和回调 URL"); return; }
    if (!draft.events.length) { notify("fail", "至少选择一个事件"); return; }
    setSaving(true);
    try {
      const payload = { ...draft, name: draft.name.trim(), url: draft.url.trim(), scope_value: draft.scope === "global" ? "" : draft.scope_value.trim() };
      const result = await apiFetch(editing ? `/sunny/webhooks/${draft.id}` : "/sunny/webhooks", { method: editing ? "PUT" : "POST", body: JSON.stringify(payload) });
      if (result?.secret) setRevealedSecret(String(result.secret));
      notify("ok", editing ? "回调已保存" : "回调已创建");
      reset(false);
      await load();
    } catch (error: any) { notify("fail", error?.message || String(error)); }
    finally { setSaving(false); }
  };
  const remove = async (id: number) => {
    if (!window.confirm("删除此回调配置及其投递记录？")) return;
    try { await apiFetch(`/sunny/webhooks/${id}`, { method: "DELETE" }); notify("ok", "回调已删除"); if (draft.id === id) reset(); await load(); }
    catch (error: any) { notify("fail", error?.message || String(error)); }
  };
  const test = async (id: number) => { try { const result = await apiFetch(`/sunny/webhooks/${id}/test`, { method: "POST" }); notify("ok", `测试投递成功（HTTP ${result.status}）`); await load(); } catch (error: any) { notify("fail", error?.message || String(error)); await load(); } };
  const retry = async (id: number) => { try { await apiFetch(`/sunny/webhook-deliveries/${id}/retry`, { method: "POST" }); notify("ok", "已加入重试队列"); await load(); } catch (error: any) { notify("fail", error?.message || String(error)); } };
  const scopeHint = draft.scope === "group" ? "填写账户分组名称" : draft.scope === "account" ? "填写账户 ID" : "全局范围会投递所有账户事件";
  return <PagePortal><div className="sr-modal-mask" role="dialog" aria-modal="true" aria-label="账号回调设置">
    <div className="sr-modal sr-mailbox-modal max-w-5xl">
      <div className="sr-modal-head"><div><h3>账号回调设置</h3><p className="mt-1 text-xs text-[var(--text-muted)]">独立 Webhook 模块。事件正文不会包含密码、Cookie、SK、AT、RT 或 Session。</p></div><button type="button" onClick={onClose} title="关闭"><X className="h-5 w-5" /></button></div>
      <div className="sr-modal-body grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section className="space-y-4">
          <div className="flex items-center justify-between gap-3"><b className="text-sm">{editing ? "编辑回调" : "新建回调"}</b><button type="button" className="sr-text-btn" onClick={() => reset()}><Plus className="h-4 w-4" />新建</button></div>
          <label className="block text-sm font-medium">名称<input className="sr-search mt-1 w-full" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="账户状态通知" /></label>
          <label className="block text-sm font-medium">回调 URL<input className="sr-search mt-1 w-full" value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} placeholder="https://example.com/webhooks/sunny" /></label>
          <label className="block text-sm font-medium">签名 Secret{editing && <span className="ml-2 text-xs font-normal text-[var(--text-muted)]">留空则保留原值</span>}<input className="sr-search mt-1 w-full" type="password" autoComplete="new-password" value={draft.secret} onChange={(e) => setDraft({ ...draft, secret: e.target.value })} placeholder={editing ? "保留现有 Secret" : "留空将自动生成"} /></label>
          <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm font-medium">范围<select className="sr-search mt-1 w-full" value={draft.scope} onChange={(e) => setDraft({ ...draft, scope: e.target.value as Draft["scope"] })}><option value="global">全局</option><option value="group">账户分组</option><option value="account">指定账户</option></select></label><label className="block text-sm font-medium">范围值<input className="sr-search mt-1 w-full" disabled={draft.scope === "global"} value={draft.scope_value} onChange={(e) => setDraft({ ...draft, scope_value: e.target.value })} placeholder={scopeHint} /></label></div>
          <div className="grid gap-3 sm:grid-cols-2"><label className="block text-sm font-medium">超时（秒）<input className="sr-search mt-1 w-full" min={1} max={120} type="number" value={draft.timeout_sec} onChange={(e) => setDraft({ ...draft, timeout_sec: Number(e.target.value) || 15 })} /></label><label className="block text-sm font-medium">最多投递次数<input className="sr-search mt-1 w-full" min={1} max={10} type="number" value={draft.max_attempts} onChange={(e) => setDraft({ ...draft, max_attempts: Number(e.target.value) || 5 })} /></label></div>
          <label className="flex items-center gap-2 text-sm font-medium"><input type="checkbox" checked={draft.enabled} onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })} />启用回调</label>
          <div><div className="mb-2 text-sm font-medium">事件</div><div className="grid gap-2 sm:grid-cols-2">{events.map((event) => <label key={event} className="flex items-center gap-2 text-xs"><input type="checkbox" checked={draft.events.includes(event)} onChange={() => toggleEvent(event)} />{event}</label>)}</div></div>
          {revealedSecret && <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-950"><b>新 Secret（仅本次显示）</b><code className="mt-1 block break-all select-all">{revealedSecret}</code></div>}
          <div className="flex flex-wrap gap-2"><button type="button" className="sr-btn" disabled={saving} onClick={() => void save()}>{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}{editing ? "保存" : "创建回调"}</button><button type="button" className="sr-btn" disabled={loading} onClick={() => void load()}><RefreshCw className="h-4 w-4" />刷新</button></div>
        </section>
        <section className="min-w-0 space-y-5">
          <div><div className="mb-2 flex items-center justify-between"><b className="text-sm">已配置回调</b><span className="text-xs text-[var(--text-muted)]">{items.length} 个</span></div><div className="max-h-56 space-y-2 overflow-y-auto">{items.length ? items.map((item) => <div key={item.id} className="rounded-md border border-[var(--border)] p-3"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><b className="block truncate text-sm">{item.name}</b><span className="block truncate text-xs text-[var(--text-muted)]">{item.url}</span><span className="mt-1 inline-block text-xs">{item.enabled ? "已启用" : "已停用"} · {item.scope === "global" ? "全局" : `${item.scope}: ${item.scope_value}`}</span></div><div className="flex shrink-0 gap-1"><button className="round-tool h-7 w-7" title="测试投递" onClick={() => void test(item.id)}><Send className="h-3.5 w-3.5" /></button><button className="round-tool h-7 w-7" title="编辑" onClick={() => beginEdit(item)}><RotateCcw className="h-3.5 w-3.5" /></button><button className="round-tool h-7 w-7 text-red-500" title="删除" onClick={() => void remove(item.id)}><Trash2 className="h-3.5 w-3.5" /></button></div></div></div>) : <div className="py-6 text-center text-sm text-[var(--text-muted)]">暂无回调配置</div>}</div></div>
          <div><div className="mb-2 flex items-center justify-between"><b className="text-sm">最近投递</b><button className="sr-text-btn" onClick={() => void load()}><RefreshCw className="h-3.5 w-3.5" />刷新</button></div><div className="max-h-64 overflow-auto rounded-md border border-[var(--border)]"><table className="w-full text-left text-xs"><thead><tr className="border-b border-[var(--border)]"><th className="p-2">事件</th><th className="p-2">状态</th><th className="p-2">尝试</th><th className="p-2">结果</th><th className="p-2" /></tr></thead><tbody>{deliveries.length ? deliveries.map((delivery) => <tr key={delivery.id} className="border-b border-[var(--border)] last:border-0"><td className="p-2" title={delivery.delivery_id}>{delivery.event_type}</td><td className="p-2">{delivery.status}</td><td className="p-2">{delivery.attempts}</td><td className="max-w-44 truncate p-2" title={delivery.error || delivery.response_body}>{delivery.error || (delivery.response_status ? `HTTP ${delivery.response_status}` : "-")}</td><td className="p-2">{delivery.status === "failed" && <button className="round-tool h-7 w-7" title="重试" onClick={() => void retry(delivery.id)}><RotateCcw className="h-3.5 w-3.5" /></button>}</td></tr>) : <tr><td className="p-5 text-center text-[var(--text-muted)]" colSpan={5}>暂无投递记录</td></tr>}</tbody></table></div></div>
        </section>
      </div>
    </div>
  </div></PagePortal>;
}
