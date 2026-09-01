import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

type Route = { address: string; url: string; is_primary?: boolean };
type Account = {
  id: number;
  email: string;
  password?: string;
  status?: string;
  last_error?: string;
  proxy_bound?: boolean;
  addresses?: Route[];
};
type ServiceStatus = { ok: boolean; base_url?: string; service?: any };
type SplitMode =
  "original" | "custom" | "multi" | "popular" | "common" | "all" | "new";

const EMPTY_STATUS: ServiceStatus = { ok: false };

export function MailComView({ embedded = false }: { embedded?: boolean } = {}) {
  const [status, setStatus] = useState<ServiceStatus>(EMPTY_STATUS);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [importText, setImportText] = useState("");
  const [verify, setVerify] = useState(true);
  const [syncOnImport, setSyncOnImport] = useState(true);
  const [importSplitCount, setImportSplitCount] = useState(0);
  const [importSplitMode, setImportSplitMode] = useState<SplitMode>("original");
  const [importDomain, setImportDomain] = useState("");
  const [importDomains, setImportDomains] = useState("");
  const [importResult, setImportResult] = useState<string[]>([]);
  const [queryText, setQueryText] = useState("");
  const [maxAge, setMaxAge] = useState(600);
  const [queryResults, setQueryResults] = useState<any[]>([]);
  const [splitFor, setSplitFor] = useState<Account | null>(null);
  const [splitMessage, setSplitMessage] = useState<string | null>(null);
  const [splitCount, setSplitCount] = useState(1);
  const [splitMode, setSplitMode] = useState<SplitMode>("original");
  const [splitDomain, setSplitDomain] = useState("");
  const [splitDomains, setSplitDomains] = useState("");
  const [domainLists, setDomainLists] = useState<{
    popular: any[];
    common: string[];
    new: string[];
  }>({ popular: [], common: [], new: [] });
  const [selectedAccount, setSelectedAccount] = useState<Account | null>(null);
  const [mailResult, setMailResult] = useState<any>(null);
  const [mailOperation, setMailOperation] = useState<
    "query" | "folders" | "quota" | "aliases" | "domains" | "body"
  >("query");
  const [mailKeyword, setMailKeyword] = useState("");
  const [mailFolder, setMailFolder] = useState("INBOX");
  const [mailAmount, setMailAmount] = useState(20);
  const [mailId, setMailId] = useState("");
  const [newAlias, setNewAlias] = useState("");

  async function load() {
    try {
      const [s, a] = await Promise.all([
        api<ServiceStatus>("/api/mail_pool/mailcom/status"),
        api<{ ok: boolean; accounts: Account[] }>(
          "/api/mail_pool/mailcom/accounts",
        ),
      ]);
      const nextAccounts = a.accounts || [];
      setStatus(s);
      setAccounts(nextAccounts);
      setSelectedAccount((current) => current ? nextAccounts.find((item) => item.email === current.email) || null : null);
      setMessage(null);
    } catch (e: any) {
      setStatus(EMPTY_STATUS);
      setMessage("加载 mail.com 服务失败: " + (e?.message || e));
    }
  }

  async function loadDomains() {
    const read = async <T,>(kind: string, fallback: T): Promise<T> => {
      try {
        const result = await api<any>(`/api/mail_pool/mailcom/domains/${kind}`);
        if (kind === "popular") return (result.domains || []) as T;
        return (result.domains || []) as T;
      } catch {
        return fallback;
      }
    };
    setDomainLists({
      popular: await read<any[]>("popular", []),
      common: await read<string[]>("common", []),
      new: await read<string[]>("new", []),
    });
  }

  useEffect(() => {
    void load();
    void loadDomains();
  }, []);

  function parseCredentialRows(text: string) {
    return text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith("#"))
      .map((line) => {
        const parts = line.split("----");
        return {
          email: (parts[0] || "").trim().toLowerCase(),
          password: (parts[1] || "").trim(),
        };
      })
      .filter((row) => row.email.includes("@") && row.password);
  }

  function splitPayload(mode: SplitMode, domain: string, domains: string) {
    const payload: Record<string, unknown> = {};
    if (mode === "custom") payload.domain = domain.trim();
    if (mode === "multi") payload.domains = domains;
    if (mode === "popular") payload.popular_random = true;
    if (mode === "common") payload.common_random = true;
    if (mode === "all") payload.all_random = true;
    if (mode === "new") payload.new_random = true;
    return payload;
  }

  async function importAccounts() {
    if (!importText.trim()) {
      setMessage("请先输入邮箱----密码");
      return;
    }
    const rows = parseCredentialRows(importText);
    if (!rows.length) {
      setMessage("没有识别到有效账号");
      return;
    }
    setBusy("import");
    setMessage("正在导入账号...");
    setImportResult([]);
    try {
      const result = await api<any>("/api/mail_pool/mailcom/import", "POST", {
        text: importText,
        verify,
        sync_aliases: syncOnImport,
      });
      const lines: string[] = result.lines || [];
      setImportResult(lines);
      let splitCreated = 0;
      if (importSplitCount > 0) {
        for (const row of rows) {
          try {
            const split = await api<any>(
              "/api/mail_pool/mailcom/split",
              "POST",
              {
                account: row.email,
                count: importSplitCount,
                ...splitPayload(importSplitMode, importDomain, importDomains),
              },
            );
            splitCreated += Number(split.created || 0);
            (split.routes || []).forEach((route: Route) =>
              lines.push(`${route.address}----${route.url}`),
            );
          } catch {
            /* row-level failures are reflected by the next sync */
          }
        }
        setImportResult([...lines]);
      }
      setMessage(
        `导入完成：${result.imported || 0} 个账号${splitCreated ? `，创建 ${splitCreated} 个别名` : ""}`,
      );
      if (result.verification_job?.job_id)
        void watchVerification(result.verification_job.job_id);
      setImportText("");
      await load();
    } catch (e: any) {
      setMessage("导入失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  async function watchVerification(jobId: string) {
    for (let i = 0; i < 60; i += 1) {
      try {
        const snapshot = await api<any>(
          `/api/mail_pool/mailcom/import-status/${encodeURIComponent(jobId)}`,
        );
        if (snapshot.status === "completed") {
          setMessage(
            `后台验证完成：${snapshot.completed || 0}/${snapshot.total || 0}`,
          );
          await load();
          return;
        }
        setMessage(
          `后台验证中：${snapshot.completed || 0}/${snapshot.total || 0}`,
        );
      } catch {
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  }

  async function sync(account: Account | null) {
    setBusy(account ? `sync:${account.email}` : "sync:all");
    try {
      const result = account
        ? await api<any>("/api/mail_pool/mailcom/sync", "POST", {
            account: account.email,
          })
        : await api<any>("/api/mail_pool/mailcom/sync_all", "POST", {});
      setMessage(
        account
          ? `同步完成：新增 ${result.saved?.added || 0}，移除 ${result.removed || 0}`
          : `全部同步完成：${result.accounts || 0} 个账号`,
      );
      await load();
    } catch (e: any) {
      setMessage("同步失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  async function splitAccount() {
    if (!splitFor) return;
    if (splitMode === "custom" && !splitDomain.trim()) {
      setMessage("请输入自定义域名");
      return;
    }
    if (splitMode === "multi" && !splitDomains.trim()) {
      setMessage("请输入域名列表");
      return;
    }
    setBusy("split");
    setSplitMessage("正在分裂，请等待 mail.com 返回结果...");
    try {
      const result = await api<any>("/api/mail_pool/mailcom/split", "POST", {
        account: splitFor.email,
        count: splitCount,
        ...splitPayload(splitMode, splitDomain, splitDomains),
      });
      const failed = Array.isArray(result.failed) ? result.failed : [];
      const detail = failed
        .map((item: any) => item?.detail || item?.error)
        .filter(Boolean)
        .join("；");
      if (result.ok === false) {
        setSplitMessage(
          `分裂失败：${result.detail || result.error || "服务端返回失败"}`,
        );
      } else if (!Number(result.created || 0) && detail) {
        setSplitMessage(`分裂失败：${detail}`);
      } else {
        setSplitMessage(
          `分裂完成：创建 ${result.created || 0} 个别名${detail ? `，${detail}` : ""}`,
        );
      }
      if (Number(result.created || 0) > 0) setSplitFor(null);
      await load();
    } catch (e: any) {
      setSplitMessage("分裂失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  async function removeAlias(account: Account, address: string) {
    if (!confirm(`删除别名 ${address}？`)) return;
    setBusy(`remove:${address}`);
    try {
      await api("/api/mail_pool/mailcom/remove_alias", "POST", {
        account: account.email,
        address,
      });
      setMessage("别名已删除");
      await load();
    } catch (e: any) {
      setMessage("删除失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  async function checkAccount(account: Account) {
    setBusy(`check:${account.email}`);
    try {
      const result = await api<any>("/api/mail_pool/mailcom/check", "POST", {
        account: account.email,
        sync_aliases: true,
      });
      setMessage(
        result.ok
          ? `${account.email} 验证通过`
          : `${account.email} 验证失败: ${result.detail || result.error || ""}`,
      );
      await load();
    } catch (e: any) {
      setMessage("检查失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  async function checkAll() {
    if (!accounts.length) return;
    setBusy("check:all");
    let ok = 0;
    for (const account of accounts) {
      try {
        const result = await api<any>("/api/mail_pool/mailcom/check", "POST", {
          account: account.email,
          sync_aliases: true,
        });
        if (result.ok) ok += 1;
      } catch {
        /* continue with remaining accounts */
      }
    }
    setMessage(`全部检查完成：${ok}/${accounts.length} 成功`);
    await load();
    setBusy(null);
  }

  async function addAlias() {
    if (!selectedAccount || !newAlias.trim()) {
      setMessage("请输入别名地址");
      return;
    }
    setBusy("alias:add");
    try {
      await api("/api/mail_pool/mailcom/aliases", "POST", {
        account: selectedAccount.email,
        address: newAlias.trim().toLowerCase(),
      });
      setNewAlias("");
      setMessage("别名已添加");
      await load();
    } catch (e: any) {
      setMessage("添加别名失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  async function queryCodes() {
    const emails = queryText
      .split(/[\n,]+/)
      .map((x) => x.trim().toLowerCase())
      .filter(Boolean);
    if (!emails.length) {
      setMessage("请输入要查询的邮箱");
      return;
    }
    setBusy("query");
    try {
      const result = await api<any>("/api/mail_pool/mailcom/query", "POST", {
        emails,
        max_age: maxAge,
      });
      setQueryResults(result.results || []);
      setMessage(`查询完成：${result.count || 0} 个邮箱`);
    } catch (e: any) {
      setMessage("查询失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  async function exportRoutes() {
    try {
      const result = await api<{ ok: boolean; text: string }>(
        "/api/mail_pool/mailcom/export",
      );
      const blob = new Blob([result.text || ""], {
        type: "text/plain;charset=utf-8",
      });
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = "邮箱----接码API.txt";
      link.click();
      URL.revokeObjectURL(href);
      setMessage("地址文件已下载");
    } catch (e: any) {
      setMessage("导出失败: " + (e?.message || e));
    }
  }

  async function runMailOperation() {
    if (!selectedAccount) {
      setMessage("请先选择账号");
      return;
    }
    setBusy("mail");
    try {
      const body: Record<string, unknown> = {
        email: selectedAccount.email,
        password: selectedAccount.password || "",
      };
      if (mailOperation === "query")
        Object.assign(body, {
          keyword: mailKeyword,
          folder: mailFolder,
          amount: mailAmount,
        });
      if (mailOperation === "body") body.mail_id = mailId;
      const result = await api<any>(
        `/api/mail_pool/mailcom/mail/${mailOperation}`,
        "POST",
        body,
      );
      setMailResult(result);
      setMessage("邮箱操作完成");
    } catch (e: any) {
      setMessage("邮箱操作失败: " + (e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  const routeCount = useMemo(
    () =>
      accounts.reduce(
        (sum, account) => sum + (account.addresses?.length || 0),
        0,
      ),
    [accounts],
  );
  const popular = domainLists.popular
    .map((item: any) => (typeof item === "string" ? item : item.domain))
    .filter(Boolean);

  return (
    <div className={embedded ? "mailcom-embedded" : "page"}>
      {!embedded && <div className="page-head">
        <div>
          <h2 className="page-title">邮箱管理 · mail.com 接码</h2>
          <p className="page-sub">
            账号导入、邮箱分裂、验证码查询与接码地址管理
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <span
            className={`badge ${status.ok ? "badge-success" : "badge-danger"}`}
          >
            {status.ok ? "服务在线" : "服务离线"}
          </span>
          <button
            className="btn"
            onClick={() => {
              void load();
              void loadDomains();
            }}
          >
            刷新
          </button>
          <button
            className="btn"
            onClick={() => void sync(null)}
            disabled={busy === "sync:all"}
          >
            {busy === "sync:all" ? "同步中..." : "同步全部别名"}
          </button>
          <button
            className="btn"
            onClick={() => void checkAll()}
            disabled={busy === "check:all"}
          >
            {busy === "check:all" ? "检查中..." : "全部检查"}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void exportRoutes()}
          >
            导出地址
          </button>
        </div>
      </div>}
      {embedded && <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head" style={{ gap: 10, flexWrap: "wrap" }}>
          <div>
            <span className="card-title">mail.com 接码专区</span>
            <span className="muted" style={{ marginLeft: 10, fontSize: 12 }}>账号、别名、验证码和邮件操作</span>
          </div>
          <div className="page-actions" style={{ display: "flex", gap: 6, marginLeft: "auto", flexWrap: "wrap" }}>
            <span className={`badge ${status.ok ? "badge-success" : "badge-danger"}`}>{status.ok ? "服务在线" : "服务离线"}</span>
            <button className="btn btn-sm" onClick={() => { void load(); void loadDomains(); }}>刷新</button>
            <button className="btn btn-sm" onClick={() => void sync(null)} disabled={busy === "sync:all"}>{busy === "sync:all" ? "同步中..." : "同步全部别名"}</button>
            <button className="btn btn-sm" onClick={() => void checkAll()} disabled={busy === "check:all"}>{busy === "check:all" ? "检查中..." : "全部检查"}</button>
            <button className="btn btn-sm btn-primary" onClick={() => void exportRoutes()}>导出地址</button>
          </div>
        </div>
      </section>}
      {message && (
        <div
          style={{
            marginBottom: 12,
            padding: "8px 12px",
            borderRadius: "var(--r-input)",
            background: "var(--info-soft)",
            color: "var(--fg-info)",
            fontSize: 13,
          }}
        >
          {message}
        </div>
      )}
      {!embedded && <div className="stat-grid" style={{ marginBottom: 14 }}>
        <div className="stat-card">
          <div className="stat-label">账号</div>
          <div className="stat-value">{accounts.length}</div>
          <div className="stat-foot">已导入</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">接码地址</div>
          <div className="stat-value">{routeCount}</div>
          <div className="stat-foot">主账号与别名</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">服务端点</div>
          <div className="stat-value" style={{ fontSize: 15 }}>
            {status.base_url || "—"}
          </div>
          <div className="stat-foot">mail-com-code-api</div>
        </div>
      </div>}

      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">批量导入账号</span>
          <span className="muted" style={{ fontSize: 12 }}>
            每行：邮箱----密码 或 邮箱----密码----HTTP代理
          </span>
        </div>
        <div className="card-body">
          <textarea
            className="input"
            rows={5}
            style={{ fontFamily: "var(--font-mono)" }}
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            placeholder="user@mail.com----password"
          />
          <div
            className="form-grid"
            style={{ gridTemplateColumns: "repeat(4, 1fr)", marginTop: 10 }}
          >
            <label className="field">
              <span className="field-label">导入后验证</span>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={verify}
                  onChange={(e) => setVerify(e.target.checked)}
                />
                <span className="switch-track" />
              </label>
            </label>
            <label className="field">
              <span className="field-label">同步已有别名</span>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={syncOnImport}
                  onChange={(e) => setSyncOnImport(e.target.checked)}
                />
                <span className="switch-track" />
              </label>
            </label>
            <label className="field">
              <span className="field-label">每个账号分裂</span>
              <select
                className="select"
                value={importSplitCount}
                onChange={(e) => setImportSplitCount(Number(e.target.value))}
              >
                {Array.from({ length: 10 }, (_, i) => (
                  <option key={i} value={i}>
                    {i ? `${i} 个` : "不分裂"}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">域名策略</span>
              <select
                className="select"
                value={importSplitMode}
                onChange={(e) =>
                  setImportSplitMode(e.target.value as SplitMode)
                }
              >
                <option value="original">原邮箱域名</option>
                <option value="custom">自定义域名</option>
                <option value="multi">多域名随机</option>
                <option value="popular">热门域名随机</option>
                <option value="common">常用域名随机</option>
                <option value="all">全部域名随机</option>
                <option value="new">新域名随机</option>
              </select>
            </label>
          </div>
          {importSplitMode === "custom" && (
            <>
              <input
                className="input"
                list="mailcom-common-domains"
                style={{ marginTop: 8 }}
                value={importDomain}
                onChange={(e) => setImportDomain(e.target.value)}
                placeholder="example.com"
              />
              <datalist id="mailcom-common-domains">
                {domainLists.common.map((domain) => (
                  <option key={domain} value={domain} />
                ))}
              </datalist>
            </>
          )}
          {importSplitMode === "multi" && (
            <textarea
              className="input"
              style={{ marginTop: 8 }}
              rows={2}
              value={importDomains}
              onChange={(e) => setImportDomains(e.target.value)}
              placeholder="example.com, mail.com"
            />
          )}
          <div
            style={{
              marginTop: 10,
              display: "flex",
              gap: 8,
              alignItems: "center",
            }}
          >
            <button
              className="btn btn-primary"
              onClick={() => void importAccounts()}
              disabled={busy === "import"}
            >
              {busy === "import" ? "导入中..." : "保存并导入"}
            </button>
            {importResult.length > 0 && (
              <button
                className="btn"
                onClick={() =>
                  navigator.clipboard.writeText(importResult.join("\n"))
                }
              >
                复制结果
              </button>
            )}
            <span className="muted" style={{ fontSize: 12 }}>
              热门：{popular.slice(0, 5).join(", ") || "暂无动态榜单"}
            </span>
          </div>
          {importResult.length > 0 && (
            <textarea
              className="input"
              rows={3}
              readOnly
              style={{ marginTop: 10, fontFamily: "var(--font-mono)" }}
              value={importResult.join("\n")}
            />
          )}
        </div>
      </section>

      <section className="card" style={{ marginBottom: 14 }}>
        <div className="card-head">
          <span className="card-title">批量查询验证码</span>
        </div>
        <div className="card-body">
          <textarea
            className="input"
            rows={3}
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            placeholder="输入邮箱，每行一个或逗号分隔"
          />
          <div
            style={{
              display: "flex",
              gap: 8,
              marginTop: 8,
              alignItems: "center",
            }}
          >
            <label
              className="field"
              style={{ flexDirection: "row", alignItems: "center", gap: 6 }}
            >
              <span className="field-label">最大邮件年龄</span>
              <input
                className="input"
                style={{ width: 100 }}
                type="number"
                value={maxAge}
                onChange={(e) => setMaxAge(Number(e.target.value) || 0)}
              />
              <span className="muted">秒</span>
            </label>
            <button
              className="btn btn-primary"
              onClick={() => void queryCodes()}
              disabled={busy === "query"}
            >
              {busy === "query" ? "查询中..." : "批量查询"}
            </button>
          </div>
          {queryResults.length > 0 && (
            <table className="table" style={{ marginTop: 10, width: "100%" }}>
              <thead>
                <tr>
                  <th>邮箱</th>
                  <th>验证码</th>
                  <th>主题</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {queryResults.map((item, i) => (
                  <tr key={`${item.email}-${i}`}>
                    <td>{item.email}</td>
                    <td className="mono">{item.code || "—"}</td>
                    <td>{item.mail?.subject || ""}</td>
                    <td>{item.error || (item.code ? "已识别" : "暂无新码")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <span className="card-title">账号与接码地址 ({accounts.length})</span>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          <table className="table" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th>账号</th>
                <th>状态</th>
                <th>地址数</th>
                <th>接码地址</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.length === 0 ? (
                <tr>
                  <td colSpan={5} className="empty">
                    暂无账号，请先批量导入
                  </td>
                </tr>
              ) : (
                accounts.map((account) => (
                  <tr key={account.email}>
                    <td style={{ textAlign: "left" }}>
                      <div className="cell-strong">{account.email}</div>
                      <div className="cell-sub">
                        {account.proxy_bound ? "已绑定代理" : "未绑定代理"}
                      </div>
                    </td>
                    <td>
                      <span
                        className={`badge ${account.status === "ready" ? "badge-success" : "badge-muted"}`}
                      >
                        {account.status || "已保存"}
                      </span>
                      {account.last_error && (
                        <div className="cell-sub" style={{ color: "var(--fg-danger)", marginTop: 4 }}>
                          {account.last_error}
                        </div>
                      )}
                    </td>
                    <td>{account.addresses?.length || 0}</td>
                    <td style={{ textAlign: "left", maxWidth: 360 }}>
                      {(account.addresses || []).slice(0, 3).map((route) => (
                        <div
                          key={route.address}
                          className="mono"
                          style={{ fontSize: 11 }}
                        >
                          {route.address}{" "}
                          <button
                            className="btn btn-sm btn-ghost"
                            onClick={() =>
                              navigator.clipboard.writeText(route.url)
                            }
                          >
                            复制
                          </button>
                        </div>
                      ))}
                      {(account.addresses?.length || 0) > 3 && (
                        <span className="muted">
                          还有 {(account.addresses?.length || 0) - 3} 个
                        </span>
                      )}
                    </td>
                    <td>
                      <div
                        style={{
                          display: "flex",
                          gap: 4,
                          flexWrap: "wrap",
                          justifyContent: "center",
                        }}
                      >
                        <button
                          className="btn btn-sm"
                          onClick={() => void checkAccount(account)}
                          disabled={busy === `check:${account.email}`}
                        >
                          检查
                        </button>
                        <button
                          className="btn btn-sm"
                          onClick={() => void sync(account)}
                          disabled={busy === `sync:${account.email}`}
                        >
                          同步
                        </button>
                        <button
                          className="btn btn-sm btn-primary"
                          onClick={() => {
                            setSplitFor(account);
                            setSplitCount(1);
                            setSplitMode("original");
                            setSplitDomain("");
                            setSplitDomains("");
                            setSplitMessage(null);
                          }}
                        >
                          分裂
                        </button>
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => {
                            setSelectedAccount(account);
                            setMailResult(null);
                          }}
                        >
                          邮箱操作
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      {splitFor && (
        <div
          className="overlay"
          onClick={() => busy !== "split" && setSplitFor(null)}
        >
          <div
            className="sheet"
            style={{ maxWidth: 620 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sheet-head">
              <h3 className="sheet-title">分裂 mail.com 别名</h3>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => setSplitFor(null)}
              >
                ✕
              </button>
            </div>
            <div className="sheet-body">
              <div className="field-hint" style={{ marginBottom: 12 }}>
                主账号：<code>{splitFor.email}</code>
              </div>
              {splitMessage && (
                <div
                  style={{
                    marginBottom: 12,
                    padding: "8px 10px",
                    borderRadius: "var(--r-input)",
                    background: "var(--info-soft)",
                    color: "var(--fg-info)",
                    fontSize: 12,
                  }}
                >
                  {splitMessage}
                </div>
              )}
              <div
                className="form-grid"
                style={{ gridTemplateColumns: "140px 1fr" }}
              >
                <label className="field">
                  <span className="field-label">数量</span>
                  <input
                    className="input"
                    type="number"
                    min={1}
                    max={9}
                    value={splitCount}
                    onChange={(e) =>
                      setSplitCount(
                        Math.max(1, Math.min(9, Number(e.target.value) || 1)),
                      )
                    }
                  />
                </label>
                <label className="field">
                  <span className="field-label">域名策略</span>
                  <select
                    className="select"
                    value={splitMode}
                    onChange={(e) => setSplitMode(e.target.value as SplitMode)}
                  >
                    <option value="original">原域名</option>
                    <option value="custom">自定义单域名</option>
                    <option value="multi">多域名随机</option>
                    <option value="popular">热门域名随机</option>
                    <option value="common">常用域名随机</option>
                    <option value="all">全部域名随机</option>
                    <option value="new">新域名随机</option>
                  </select>
                </label>
              </div>
              {splitMode === "custom" && (
                <input
                  className="input"
                  style={{ marginTop: 10 }}
                  value={splitDomain}
                  onChange={(e) => setSplitDomain(e.target.value)}
                  placeholder="example.com"
                />
              )}
              {splitMode === "multi" && (
                <textarea
                  className="input"
                  style={{ marginTop: 10 }}
                  rows={3}
                  value={splitDomains}
                  onChange={(e) => setSplitDomains(e.target.value)}
                  placeholder="example.com, mail.com"
                />
              )}
              <div className="field-hint" style={{ marginTop: 12 }}>
                每个别名都会同步到邮箱管理及注册渠道。
              </div>
            </div>
            <div className="sheet-foot">
              <button className="btn" onClick={() => setSplitFor(null)}>
                关闭
              </button>
              <button
                className="btn btn-primary"
                onClick={() => void splitAccount()}
                disabled={busy === "split"}
              >
                {busy === "split" ? "分裂中..." : "开始分裂"}
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedAccount && (
        <div className="overlay" onClick={() => setSelectedAccount(null)}>
          <div
            className="sheet"
            style={{ maxWidth: 760 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sheet-head">
              <h3 className="sheet-title">
                邮箱操作 · {selectedAccount.email}
              </h3>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => setSelectedAccount(null)}
              >
                ✕
              </button>
            </div>
            <div className="sheet-body">
              <div
                className="segmented"
                style={{ width: "fit-content", marginBottom: 12 }}
              >
                <button
                  className={mailOperation === "query" ? "active" : ""}
                  onClick={() => setMailOperation("query")}
                >
                  邮件查询
                </button>
                <button
                  className={mailOperation === "folders" ? "active" : ""}
                  onClick={() => setMailOperation("folders")}
                >
                  文件夹
                </button>
                <button
                  className={mailOperation === "quota" ? "active" : ""}
                  onClick={() => setMailOperation("quota")}
                >
                  配额
                </button>
                <button
                  className={mailOperation === "aliases" ? "active" : ""}
                  onClick={() => setMailOperation("aliases")}
                >
                  别名
                </button>
                <button
                  className={mailOperation === "domains" ? "active" : ""}
                  onClick={() => setMailOperation("domains")}
                >
                  域名
                </button>
                <button
                  className={mailOperation === "body" ? "active" : ""}
                  onClick={() => setMailOperation("body")}
                >
                  正文
                </button>
              </div>
              {mailOperation === "query" && (
                <div
                  className="form-grid"
                  style={{ gridTemplateColumns: "1fr 140px 100px" }}
                >
                  <label className="field">
                    <span className="field-label">关键词</span>
                    <input
                      className="input"
                      value={mailKeyword}
                      onChange={(e) => setMailKeyword(e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">文件夹</span>
                    <input
                      className="input"
                      value={mailFolder}
                      onChange={(e) => setMailFolder(e.target.value)}
                    />
                  </label>
                  <label className="field">
                    <span className="field-label">数量</span>
                    <input
                      className="input"
                      type="number"
                      value={mailAmount}
                      onChange={(e) =>
                        setMailAmount(Number(e.target.value) || 20)
                      }
                    />
                  </label>
                </div>
              )}
              {mailOperation === "body" && (
                <label className="field" style={{ marginTop: 10 }}>
                  <span className="field-label">邮件 ID</span>
                  <input
                    className="input mono"
                    value={mailId}
                    onChange={(e) => setMailId(e.target.value)}
                    placeholder="mailIdentifier"
                  />
                </label>
              )}
              {mailOperation === "aliases" && (
                <div className="card" style={{ marginTop: 10 }}>
                  <div className="card-head">
                    <span className="card-title">别名管理</span>
                    <span className="card-hint">当前账号 {selectedAccount.addresses?.length || 0} 个地址</span>
                  </div>
                  <div className="card-body">
                    {(selectedAccount.addresses || []).length ? (
                      <div style={{ display: "grid", gap: 6, marginBottom: 10 }}>
                        {(selectedAccount.addresses || []).map((route) => (
                          <div key={route.address} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span className="mono" style={{ minWidth: 0, flex: 1, overflowWrap: "anywhere" }}>{route.address}</span>
                            {route.is_primary ? <span className="badge badge-info">主地址</span> : null}
                            {!route.is_primary ? <button className="btn btn-sm btn-danger" onClick={() => void removeAlias(selectedAccount, route.address)} disabled={busy !== null}>删除</button> : null}
                          </div>
                        ))}
                      </div>
                    ) : <div className="muted" style={{ marginBottom: 10 }}>暂无别名</div>}
                    <div style={{ display: "flex", gap: 8 }}>
                      <input className="input" value={newAlias} onChange={(e) => setNewAlias(e.target.value)} placeholder="输入要添加的别名地址" />
                      <button className="btn btn-primary" onClick={() => void addAlias()} disabled={busy !== null || !newAlias.trim()}>添加别名</button>
                    </div>
                  </div>
                </div>
              )}
              <button
                className="btn btn-primary"
                style={{ marginTop: 12 }}
                onClick={() => void runMailOperation()}
                disabled={busy === "mail"}
              >
                {busy === "mail" ? "处理中..." : "执行"}
              </button>
              {mailResult && (
                <pre
                  style={{
                    marginTop: 12,
                    maxHeight: 300,
                    overflow: "auto",
                    whiteSpace: "pre-wrap",
                    fontSize: 12,
                  }}
                >
                  {JSON.stringify(mailResult, null, 2)}
                </pre>
              )}
            </div>
            <div className="sheet-foot">
              <button className="btn" onClick={() => setSelectedAccount(null)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
