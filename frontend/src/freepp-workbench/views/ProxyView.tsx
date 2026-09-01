import { useState, useCallback } from "react";
import { useStore } from "../store/useStore";
import { api } from "../api/client";
import type { ProxyNode } from "../types";

interface ClashCountryInfo { country: string; count: number; ips: string[]; nodes?: Array<{ name: string; server: string; ip?: string; port: number; type: string; country: string }> }

function flag(cc: string): string {
  if (!cc || cc.length !== 2) return "";
  const cp = 0x1f1e6 + (cc.charCodeAt(0) - 65) * 0x100 + (cc.charCodeAt(1) - 65);
  return String.fromCodePoint(cp);
}

export function ProxyView() {
  const nodes = useStore((s) => s.nodes);
  const qgPool = useStore((s) => s.qgPool);
  const pushLog = useStore((s) => s.pushLog);

  const [subUrl, setSubUrl] = useState("");
  const [subRaw, setSubRaw] = useState("");
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [clashDetectBusy, setClashDetectBusy] = useState(false);
  const [clashDetectResult, setClashDetectResult] = useState("");
  const [clashCountries, setClashCountries] = useState<ClashCountryInfo[]>([]);
  const [clashResultOpen, setClashResultOpen] = useState(false);

  const handleDetectClash = async () => {
    setClashDetectBusy(true);
    setClashDetectResult("识别中...");
    try {
      const r = await api<{ ok: boolean; detected?: string; message?: string; countries?: ClashCountryInfo[] }>("/api/proxy/clash/detect", "POST");
      setClashCountries(r?.countries || []);
      if (r?.ok && r.detected) {
        setClashDetectResult(`${r.detected}（已保存）`);
        setClashCountries(r.countries || []);
        if (r?.countries?.length) setClashResultOpen(true);
        pushLog(`Clash 自动识别: ${r.detected}`, "ok");
      } else {
        setClashDetectResult(r?.message || "未找到可用端口");
        pushLog("Clash 自动识别未找到可用端口", "err");
      }
    } catch (e) {
      setClashDetectResult("识别失败: " + (e as Error).message);
      pushLog("Clash 自动识别失败", "err");
    } finally {
      setClashDetectBusy(false);
    }
  };

  const handleFetchSub = async () => {
    if (!subUrl.trim()) {
      setResult("请输入订阅 URL");
      return;
    }
    setBusy(true);
    try {
      const r = await api("/api/proxy/fetch-sub", "POST", { url: subUrl });
      if (r && typeof r.raw === "string") {
        setSubRaw(r.raw);
        setResult(`已获取 ${r.length ?? r.raw.length} 字节`);
      } else {
        setResult("未返回内容");
      }
    } catch (e) {
      setResult("拉取失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleParse = async () => {
    if (!subRaw.trim()) {
      setResult("请粘贴或拉取订阅内容");
      return;
    }
    setBusy(true);
    try {
      const r = await api("/api/proxy/parse", "POST", { raw: subRaw });
      if (r && Array.isArray(r.nodes)) {
        useStore.setState({ nodes: r.nodes });
        setResult(`解析完成: ${r.count ?? r.nodes.length} 个节点`);
      } else {
        setResult("解析失败");
      }
    } catch (e) {
      setResult("解析失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleHealth = async () => {
    setBusy(true);
    try {
      const r = await api("/api/proxy/health");
      if (r && Array.isArray(r.nodes)) {
        useStore.setState({ nodes: r.nodes });
        setResult("健康检查完成");
      }
    } catch (e) {
      setResult("健康检查失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleStart = async (name: string) => {
    try {
      const r = await api("/api/proxy/start", "POST", { name });
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      pushLog(`启动节点: ${name}`, "info");
    } catch (e) {
      pushLog("启动失败: " + (e as Error).message, "err");
    }
  };

  const handleStop = async (name: string) => {
    try {
      const r = await api("/api/proxy/stop", "POST", { name });
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      pushLog(`停止节点: ${name}`, "info");
    } catch (e) {
      pushLog("停止失败: " + (e as Error).message, "err");
    }
  };

  const handleStartAll = async () => {
    setBusy(true);
    try {
      const r = await api("/api/proxy/start-all", "POST");
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      setResult(`已启动 ${r.started ?? 0} 个节点`);
    } catch (e) {
      setResult("启动失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleStopAll = async () => {
    setBusy(true);
    try {
      const r = await api("/api/proxy/stop-all", "POST");
      if (r && Array.isArray(r.nodes)) useStore.setState({ nodes: r.nodes });
      setResult(`已停止 ${r.stopped ?? 0} 个节点`);
    } catch (e) {
      setResult("停止失败: " + (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const nodeByCountry = nodes.reduce<Record<string, number>>((acc, n) => {
    const c = n.country_hint || "?";
    acc[c] = (acc[c] || 0) + 1;
    return acc;
  }, {});
  const healthyCount = nodes.filter((n) => n.healthy === true).length;
  const runningCount = nodes.filter((n) => n.running).length;
  const authReadyCount = nodes.filter((n) => n.auth_entry_ok === true).length;

  return (
    <div className="page page-wide">
      <div className="page-head">
        <div>
          <h2 className="page-title">代理池</h2>
          <p className="page-sub">API 住宅代理池 · 通用住宅代理池 · Clash / sing-box 节点 · QG 隧道</p>
        </div>
        <div className="page-actions">
          <button className="btn" onClick={handleHealth} disabled={busy}>
            健康检查
          </button>
          <button className="btn btn-primary" onClick={handleStartAll} disabled={busy}>
            全部启动
          </button>
          <button className="btn btn-danger" onClick={handleStopAll} disabled={busy}>
            全部停止
          </button>
        </div>
      </div>

      {/* ===== Clash 本地代理池 ===== */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">Clash 本地代理池</span>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <span className="card-hint">自动识别本机端口并按国家汇总节点</span>
            <button className="btn btn-sm btn-primary" onClick={handleDetectClash} disabled={clashDetectBusy}>
              {clashDetectBusy ? "识别中..." : "自动识别 Clash"}
            </button>
            {clashDetectResult && (
              <span style={{ color: clashDetectResult.includes("已保存") ? "var(--ok)" : "var(--text-3)", fontSize: 11 }}>
                {clashDetectResult}
              </span>
            )}
            {clashCountries.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setClashResultOpen(true)}>查看识别结果</button>}
          </div>
        </div>
        <div className="card-body">
          {clashCountries.length > 0 ? <span className="setting-hint">已识别 {clashCountries.length} 个国家分类、{clashCountries.reduce((sum, group) => sum + group.count, 0)} 个节点</span> : <span className="muted">尚未识别本机 Clash 节点</span>}
        </div>
      </div>

      {/* ===== QG 隧道池 (备代理) ===== */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">QG 隧道池</span>
          <span className="card-hint">备代理 · 青果隧道 · 超级池(机房) + 住宅池</span>
        </div>
        <div className="card-body">
          <div className="grid grid-3">
            <div className="mini-card">
              <div className="mini-card-label">Super 隧道</div>
              <div className="mini-card-value">
                <span className={`badge ${qgPool.superState === "active" ? "badge-success" : "badge-muted"}`}>
                  {qgPool.superState || "unknown"}
                </span>
              </div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">Resi 隧道</div>
              <div className="mini-card-value">
                <span className={`badge ${qgPool.resiState === "active" ? "badge-success" : "badge-muted"}`}>
                  {qgPool.resiState || "unknown"}
                </span>
              </div>
            </div>
            <div className="mini-card">
              <div className="mini-card-label">默认池</div>
              <div className="mini-card-value">{qgPool.defaultPool || "unknown"}</div>
            </div>
          </div>
        </div>
      </div>

      {/* ===== sing-box 节点订阅 ===== */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">sing-box 节点</span>
          <span className="card-hint">
            {nodes.length} 节点 · 节点在线 {healthyCount} · 认证入口可用 {authReadyCount} · 运行 {runningCount} ·{" "}
            {Object.entries(nodeByCountry).map(([c, n]) => `${c}×${n}`).join(" ")}
          </span>
        </div>
        <div className="inline-fields">
          <input
            className="input"
            style={{ flex: 1, minWidth: 200 }}
            placeholder="订阅 URL"
            value={subUrl}
            onChange={(e) => setSubUrl(e.target.value)}
          />
          <button className="btn" onClick={handleFetchSub} disabled={busy}>
            拉取
          </button>
        </div>
        <div style={{ padding: "0 16px 12px" }}>
          <textarea
            className="textarea"
            rows={3}
            placeholder="订阅原始内容 (base64 / JSON / 列表)"
            value={subRaw}
            onChange={(e) => setSubRaw(e.target.value)}
          />
        </div>
        <div className="btn-row">
          <button className="btn btn-primary btn-sm" onClick={handleParse} disabled={busy}>
            解析
          </button>
          {result && <span className="muted">{result}</span>}
        </div>
      </div>

      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>国家</th>
              <th>端口</th>
              <th className="num">延迟</th>
              <th>节点状态</th>
              <th>认证入口</th>
              <th className="num">并发</th>
              <th style={{ textAlign: "right" }}>操作</th>
            </tr>
          </thead>
          <tbody>
            {nodes.length === 0 && (
              <tr>
                <td colSpan={9} className="muted" style={{ textAlign: "center" }}>
                  暂无节点
                </td>
              </tr>
            )}
            {nodes.map((n) => (
              <tr key={n.name}>
                <td className="cell-strong">{n.name}</td>
                <td>
                  <span className="tag">{n.type || "-"}</span>
                </td>
                <td>{flag(n.country_hint)} {n.country_hint || "-"}</td>
                <td className="mono">{n.port ?? "-"}</td>
                <td className="num">{n.latency != null ? `${n.latency} ms` : "-"}</td>
                <td>
                  <span className={`health-dot ${
                    n.healthy === true ? "healthy" : n.healthy === false ? "unhealthy" : ""
                  }`} />
                  <span className="muted" style={{ marginLeft: 6 }}>{n.node_status || (n.running ? "待检查" : "已停止")}</span>
                </td>
                <td title={n.auth_entry_detail || undefined}>
                  <span className={`badge ${n.auth_entry_ok === true ? "badge-success" : n.auth_entry_ok === false ? "badge-danger" : "badge-muted"}`}>
                    {n.auth_entry_ok === true ? "可用" : n.auth_entry_ok === false ? (n.auth_entry_status || "不可用") : "未检查"}
                  </span>
                  {n.auth_entry_ok === true && n.auth_entry_latency_ms ? <span className="muted" style={{ marginLeft: 6 }}>{n.auth_entry_latency_ms} ms</span> : null}
                </td>
                <td className="num">
                  {n.concurrent ?? 0}/{n.max_concurrent ?? 0}
                </td>
                <td style={{ textAlign: "right" }}>
                  {n.running ? (
                    <button className="btn btn-ghost btn-sm" onClick={() => handleStop(n.name)}>
                      停止
                    </button>
                  ) : (
                    <button className="btn btn-ghost btn-sm" onClick={() => handleStart(n.name)}>
                      启动
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {clashResultOpen && (
        <div className="overlay" onClick={() => setClashResultOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-label="Clash 识别结果" onClick={(e) => e.stopPropagation()} style={{ padding: 22, width: "min(980px, calc(100vw - 24px))" }}>
            <div className="page-head" style={{ marginBottom: 14 }}>
              <div><h3 className="page-title" style={{ fontSize: 20 }}>Clash 识别结果</h3><p className="page-sub">按国家分类查看代理 IP 和节点详情</p></div>
              <button className="btn btn-ghost" onClick={() => setClashResultOpen(false)}>关闭</button>
            </div>
            <div className="card" style={{ marginBottom: 14 }}>
              <div className="card-head"><span className="card-title">国家分类</span><span className="card-hint">{clashCountries.length} 个国家 · {clashCountries.reduce((sum, group) => sum + group.count, 0)} 个节点</span></div>
              <div className="card-body" style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>{clashCountries.map((group) => <span className="tag" key={group.country}>{group.country} · {group.count} 个{group.ips.length ? ` · ${group.ips.join(", ")}` : ""}</span>)}</div>
            </div>
            <div className="card">
              <div className="card-head"><span className="card-title">节点详情</span><span className="card-hint">共 {clashCountries.reduce((sum, group) => sum + group.count, 0)} 条</span></div>
              <div className="card-body" style={{ paddingTop: 0 }}><div style={{ maxHeight: "min(52vh, 520px)", overflowY: "auto" }}>{clashCountries.flatMap((group) => group.nodes || []).map((node: any) => <div key={`${node.country}:${node.name}:${node.server}:${node.port}`} style={{ display: "grid", gridTemplateColumns: "80px minmax(150px, 1fr) 150px 70px", gap: 8, padding: "8px 0", borderBottom: "1px solid var(--border-faint)", fontSize: 12 }}><span>{node.country}</span><span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={node.name}>{node.name}</span><span className="mono">{node.ip || node.server}:{node.port}</span><span className="muted">{node.type}</span></div>)}</div></div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
