import { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ChevronLeft, ChevronRight, Download, Eye, FileArchive, FilterX, Loader2, RefreshCw, Save, Search, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmBubble } from "@/components/ui/confirm-bubble";
import { apiDownload, apiFetch, cn, triggerBrowserDownload } from "@/lib/utils";
import { useI18n } from "@/lib/i18n-context";

type AuditFilters = Record<string, string>;
type AuditRow = Record<string, any>;

const emptyFilters: AuditFilters = {
  search: "", log_type: "", category: "", action: "", actor: "", ip: "", level: "", status: "",
  source: "", entity_type: "", task_id: "", request_id: "", date_from: "", date_to: "",
};

const copy = {
  "zh-CN": {
    title: "日志管理", desc: "集中审计系统登录、配置变更、资源增删、任务执行、定时任务与运行指标。查询类操作默认不记录。",
    total: "日志总量", today: "今日新增", failed: "异常/失败", system: "系统事件", search: "搜索摘要、对象、路径、任务或请求 ID...",
    all: "全部", type: "日志类型", category: "操作类别", action: "操作行为", actor: "操作人", ip: "IP 地址", level: "级别", status: "结果",
    source: "来源", entity: "对象类型", task: "任务 ID", request: "请求 ID", from: "开始时间", to: "结束时间", reset: "重置筛选",
    refresh: "刷新", retention: "保留天数", save: "保存策略", selected: "已选 {count} 项", clear: "清除选择", delete: "删除日志",
    deleteSelected: "确认删除选中的日志？", deleteFiltered: "确认删除当前筛选结果？", deleteHint: "删除后不可恢复。",
    exportFormat: "导出格式", export: "异步导出", exportRunning: "正在后台整理并打包日志，请耐心等待...", exportReady: "日志导出完成，已开始下载",
    time: "时间", kind: "类型 / 类别", behavior: "行为", operator: "操作人 / IP", target: "对象 / 任务", result: "结果", summary: "日志摘要", operation: "操作",
    detail: "日志详情", close: "关闭", noData: "没有符合筛选条件的日志", loading: "正在加载日志...", deleted: "日志删除完成", saved: "日志保留策略已保存",
    prev: "上一页", next: "下一页", page: "第 {page} / {pages} 页", range: "显示 {from} 至 {to}，共 {total} 条", perPage: "每页",
  },
  "en-US": {
    title: "Audit Logs", desc: "Audit sign-ins, configuration changes, resource mutations, tasks, schedules, and runtime metrics. Read-only queries are not recorded.",
    total: "Total Logs", today: "Today", failed: "Failed / Error", system: "System Events", search: "Search summary, target, path, task, or request ID...",
    all: "All", type: "Log Type", category: "Category", action: "Action", actor: "Actor", ip: "IP Address", level: "Level", status: "Result",
    source: "Source", entity: "Entity Type", task: "Task ID", request: "Request ID", from: "From", to: "To", reset: "Reset Filters",
    refresh: "Refresh", retention: "Retention", save: "Save Policy", selected: "{count} selected", clear: "Clear", delete: "Delete Logs",
    deleteSelected: "Delete selected logs?", deleteFiltered: "Delete all filtered logs?", deleteHint: "This action cannot be undone.",
    exportFormat: "Export Format", export: "Export Async", exportRunning: "Preparing and packaging logs in the background...", exportReady: "Export completed and download started",
    time: "Time", kind: "Type / Category", behavior: "Action", operator: "Actor / IP", target: "Entity / Task", result: "Result", summary: "Summary", operation: "Action",
    detail: "Log Details", close: "Close", noData: "No logs match the current filters", loading: "Loading audit logs...", deleted: "Logs deleted", saved: "Retention policy saved",
    prev: "Previous", next: "Next", page: "Page {page} / {pages}", range: "Showing {from}-{to} of {total}", perPage: "Per page",
  },
};

function interpolate(value: string, params: Record<string, string | number>) {
  return value.replace(/\{(\w+)\}/g, (_, key) => String(params[key] ?? ""));
}

function displayTime(value: string) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { hour12: false });
}

function AuditSelect({ label, value, values, all, onChange }: { label: string; value: string; values: string[]; all: string; onChange: (value: string) => void }) {
  return <label className="audit-filter-field"><span>{label}</span><select value={value} onChange={(event)=>onChange(event.target.value)}><option value="">{all}</option>{values.map((item)=><option key={item} value={item}>{item}</option>)}</select></label>;
}

export default function AuditLogPage() {
  const { language } = useI18n();
  const c = copy[language];
  const [filters, setFilters] = useState<AuditFilters>(emptyFilters);
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [options, setOptions] = useState<Record<string, string[]>>({});
  const [stats, setStats] = useState<Record<string, number>>({ total: 0, today: 0, failed: 0, system: 0 });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number[]>([]);
  const [detail, setDetail] = useState<AuditRow | null>(null);
  const [retention, setRetention] = useState(7);
  const [savedRetention, setSavedRetention] = useState(7);
  const [format, setFormat] = useState("csv");
  const [exporting, setExporting] = useState(false);
  const [notice, setNotice] = useState<{type:"ok"|"fail"; text:string}|null>(null);
  const pages = Math.max(1, Math.ceil(total / pageSize));

  const activeFilters = useMemo(() => Object.fromEntries(Object.entries(filters).filter(([, value])=>value.trim() !== "")), [filters]);
  const query = useMemo(() => {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize), sort_order: "desc" });
    Object.entries(activeFilters).forEach(([key, value])=>params.set(key, value));
    return params.toString();
  }, [activeFilters, page, pageSize]);

  const loadLogs = useCallback(async (showNotice = false) => {
    setLoading(true);
    try {
      const list = await apiFetch(`/audit/logs?${query}`);
      const nextTotal = Number(list.total || 0);
      setRows(list.items || []); setTotal(nextTotal);
      setPage((current) => Math.min(current, Math.max(1, Math.ceil(nextTotal / pageSize))));
      if (showNotice) setNotice({type:"ok", text:c.refresh});
    } catch (error: any) {
      setNotice({type:"fail", text:error.message || String(error)});
    } finally { setLoading(false); }
  }, [c.refresh, pageSize, query]);

  const loadMeta = useCallback(async () => {
    try {
      const [optionData, statData, setting] = await Promise.all([apiFetch("/audit/options"), apiFetch("/audit/stats"), apiFetch("/audit/settings")]);
      setOptions(optionData || {}); setStats(statData || {});
      setRetention(Number(setting.retention_days || 7)); setSavedRetention(Number(setting.retention_days || 7));
    } catch (error: any) { setNotice({type:"fail", text:error.message || String(error)}); }
  }, []);

  useEffect(()=>{ queueMicrotask(() => { void loadLogs(); }); }, [loadLogs]);
  useEffect(()=>{ queueMicrotask(() => { void loadMeta(); }); }, [loadMeta]);
  useEffect(()=>{ if (!notice) return; const timer = window.setTimeout(()=>setNotice(null), 2600); return ()=>window.clearTimeout(timer); }, [notice]);

  const updateFilter = (key: string, value: string) => { setFilters((current)=>({...current,[key]:value})); setPage(1); setSelected([]); };
  const allCurrentSelected = rows.length > 0 && rows.every((row)=>selected.includes(row.id));

  async function saveRetention() {
    try {
      await apiFetch("/audit/settings", {method:"PUT", body:JSON.stringify({retention_days:retention, enabled:true})});
      setSavedRetention(retention); setNotice({type:"ok",text:c.saved});
    } catch(error:any) { setNotice({type:"fail",text:error.message||String(error)}); }
  }

  async function deleteLogs() {
    try {
      await apiFetch("/audit/logs", {method:"DELETE", body:JSON.stringify({ids:selected, filters:selected.length ? {} : activeFilters})});
      setSelected([]); setNotice({type:"ok", text:c.deleted}); await Promise.all([loadLogs(), loadMeta()]);
    } catch(error:any) { setNotice({type:"fail",text:error.message||String(error)}); }
  }

  async function exportLogs() {
    setExporting(true);
    try {
      const job = await apiFetch("/audit/exports", {method:"POST", body:JSON.stringify({format, ids:selected, filters:selected.length ? {} : activeFilters})});
      const deadline = Date.now() + 5 * 60 * 1000;
      let state = job;
      while (Date.now() < deadline && state.status !== "completed") {
        if (state.status === "failed") throw new Error(state.error || "Export failed");
        await new Promise((resolve)=>window.setTimeout(resolve, 1000));
        state = await apiFetch(`/audit/exports/${job.id}`);
      }
      if (state.status !== "completed") throw new Error("Export timed out");
      const download = await apiDownload(`/audit/exports/${job.id}/download`);
      triggerBrowserDownload(download.blob, download.filename);
      setNotice({type:"ok",text:c.exportReady});
    } catch(error:any) { setNotice({type:"fail",text:error.message||String(error)}); }
    finally { setExporting(false); }
  }

  const selectOptions = (key: string) => options[key] || [];
  const rangeFrom = total ? (page - 1) * pageSize + 1 : 0;
  const rangeTo = Math.min(page * pageSize, total);

  return <section className="audit-page">
    {notice && <div className={cn("audit-toast", notice.type)}>{notice.type === "ok" ? <CheckCircle2/> : <X/>}<span>{notice.text}</span></div>}
    {exporting && <div className="audit-export-progress"><Loader2 className="animate-spin"/><b>{c.exportRunning}</b></div>}
    <div className="audit-heading"><div><h1>{c.title}</h1><p>{c.desc}</p></div><div className="audit-retention"><label><span>{c.retention}</span><select value={retention} onChange={(e)=>setRetention(Number(e.target.value))}>{[1,3,7,14,30].map((day)=><option key={day} value={day}>{day}</option>)}</select></label><Button disabled={retention===savedRetention} onClick={saveRetention}><Save/>{c.save}</Button></div></div>
    <div className="audit-stats">{[[c.total,stats.total],[c.today,stats.today],[c.failed,stats.failed],[c.system,stats.system]].map(([label,value])=><div key={String(label)}><span>{label}</span><strong>{Number(value||0).toLocaleString()}</strong></div>)}</div>
    <div className="audit-toolbar">
      <div className="audit-search"><Search/><input value={filters.search} onChange={(e)=>updateFilter("search",e.target.value)} placeholder={c.search}/></div>
      <AuditSelect label={c.type} value={filters.log_type} values={selectOptions("log_type")} all={c.all} onChange={(v)=>updateFilter("log_type",v)}/>
      <AuditSelect label={c.category} value={filters.category} values={selectOptions("category")} all={c.all} onChange={(v)=>updateFilter("category",v)}/>
      <AuditSelect label={c.action} value={filters.action} values={selectOptions("action")} all={c.all} onChange={(v)=>updateFilter("action",v)}/>
      <AuditSelect label={c.actor} value={filters.actor} values={selectOptions("actor")} all={c.all} onChange={(v)=>updateFilter("actor",v)}/>
      <AuditSelect label={c.ip} value={filters.ip} values={selectOptions("ip")} all={c.all} onChange={(v)=>updateFilter("ip",v)}/>
      <AuditSelect label={c.status} value={filters.status} values={selectOptions("status")} all={c.all} onChange={(v)=>updateFilter("status",v)}/>
      <AuditSelect label={c.level} value={filters.level} values={selectOptions("level")} all={c.all} onChange={(v)=>updateFilter("level",v)}/>
      <AuditSelect label={c.source} value={filters.source} values={selectOptions("source")} all={c.all} onChange={(v)=>updateFilter("source",v)}/>
      <AuditSelect label={c.entity} value={filters.entity_type} values={selectOptions("entity_type")} all={c.all} onChange={(v)=>updateFilter("entity_type",v)}/>
      <label className="audit-filter-field"><span>{c.from}</span><input type="datetime-local" value={filters.date_from} onChange={(e)=>updateFilter("date_from",e.target.value)}/></label>
      <label className="audit-filter-field"><span>{c.to}</span><input type="datetime-local" value={filters.date_to} onChange={(e)=>updateFilter("date_to",e.target.value)}/></label>
      <label className="audit-filter-field"><span>{c.task}</span><input value={filters.task_id} onChange={(e)=>updateFilter("task_id",e.target.value)}/></label>
      <label className="audit-filter-field"><span>{c.request}</span><input value={filters.request_id} onChange={(e)=>updateFilter("request_id",e.target.value)}/></label>
      <button className="audit-reset" onClick={()=>{setFilters(emptyFilters);setPage(1);setSelected([])}}><FilterX/>{c.reset}</button>
    </div>
    <div className="audit-actions">
      <div>{selected.length>0 && <span className="audit-selection">{interpolate(c.selected,{count:selected.length})}<button onClick={()=>setSelected([])}>{c.clear}</button></span>}</div>
      <div className="audit-actions-right">
        <button className="audit-icon-action" onClick={()=>void Promise.all([loadLogs(true),loadMeta()])}><RefreshCw/>{c.refresh}</button>
        <select value={format} onChange={(e)=>setFormat(e.target.value)} aria-label={c.exportFormat}><option value="csv">CSV</option><option value="txt">TXT</option></select>
        <Button variant="outline" disabled={exporting} onClick={exportLogs}><Download/>{c.export}</Button>
        {(selected.length>0 || Object.keys(activeFilters).length>0) && <ConfirmBubble message={selected.length?c.deleteSelected:c.deleteFiltered} detail={c.deleteHint} onConfirm={deleteLogs} confirmLabel={c.delete} cancelLabel={c.close}><Button variant="outline" className="audit-delete"><Trash2/>{c.delete}</Button></ConfirmBubble>}
      </div>
    </div>
    <div className="audit-table-wrap" aria-busy={loading}>
      {loading && <div className="audit-loading"><Loader2 className="animate-spin"/>{c.loading}</div>}
      <table className="audit-table"><thead><tr><th><input type="checkbox" checked={allCurrentSelected} onChange={(e)=>setSelected(e.target.checked?Array.from(new Set([...selected,...rows.map((row)=>row.id)])):selected.filter((id)=>!rows.some((row)=>row.id===id)))}/></th><th>{c.time}</th><th>{c.kind}</th><th>{c.behavior}</th><th>{c.operator}</th><th>{c.target}</th><th>{c.result}</th><th>{c.summary}</th><th>{c.operation}</th></tr></thead>
      <tbody>{rows.length ? rows.map((row)=><tr key={row.id}><td><input type="checkbox" checked={selected.includes(row.id)} onChange={(e)=>setSelected(e.target.checked?[...selected,row.id]:selected.filter((id)=>id!==row.id))}/></td><td className="audit-time">{displayTime(row.occurred_at)}</td><td><b>{row.log_type}</b><small>{row.category}</small></td><td><b>{row.action}</b><small>{row.source}</small></td><td><b>{row.actor}</b><small>{row.ip||"-"}</small></td><td><b>{row.entity_name||row.entity_type||"-"}</b><small>{row.task_id||row.entity_id||"-"}</small></td><td><span className={cn("audit-result",row.status,row.level)}>{row.status}</span><small>HTTP {row.http_status||"-"} · {row.duration_ms||0}ms</small></td><td className="audit-summary" title={row.summary}>{row.summary}</td><td><button className="audit-view" onClick={()=>setDetail(row)} title={c.detail}><Eye/></button></td></tr>) : <tr><td colSpan={9}><div className="audit-empty"><FileArchive/><span>{c.noData}</span></div></td></tr>}</tbody></table>
      <div className="audit-pagination"><div>{interpolate(c.range,{from:rangeFrom,to:rangeTo,total})}<label>{c.perPage}<select value={pageSize} onChange={(e)=>{setPageSize(Number(e.target.value));setPage(1)}}>{[10,20,50,100].map((size)=><option key={size}>{size}</option>)}</select></label></div><div><button disabled={page<=1} onClick={()=>setPage((value)=>value-1)} title={c.prev}><ChevronLeft/></button><span>{interpolate(c.page,{page,pages})}</span><button disabled={page>=pages} onClick={()=>setPage((value)=>value+1)} title={c.next}><ChevronRight/></button></div></div>
    </div>
    {detail && <div className="audit-modal-mask" onMouseDown={(e)=>{if(e.target===e.currentTarget)setDetail(null)}}><div className="audit-modal"><header><h2>{c.detail}</h2><button onClick={()=>setDetail(null)}><X/></button></header><div className="audit-detail-grid"><span>ID</span><b>{detail.id}</b><span>{c.time}</span><b>{displayTime(detail.occurred_at)}</b><span>{c.operator}</span><b>{detail.actor} · {detail.ip||"-"}</b><span>{c.behavior}</span><b>{detail.category} / {detail.action}</b><span>{c.target}</span><b>{detail.entity_type||"-"} · {detail.entity_name||detail.entity_id||"-"}</b><span>{c.task}</span><b>{detail.task_id||"-"}</b><span>{c.request}</span><b>{detail.request_id||"-"}</b></div><pre>{JSON.stringify({summary:detail.summary, path:detail.path, method:detail.method, status:detail.status, details:detail.details},null,2)}</pre><footer><Button variant="outline" onClick={()=>setDetail(null)}>{c.close}</Button></footer></div></div>}
  </section>;
}
