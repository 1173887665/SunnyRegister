import { BrowserRouter, Route, Routes, NavLink, useLocation } from "react-router-dom";
import { useCallback, useEffect, useRef, useState } from "react";
import { App as AntApp, ConfigProvider, theme as antdTheme } from "antd";
import enUS from "antd/locale/en_US";
import zhCN from "antd/locale/zh_CN";
import { Languages, Link2, LogOut, Moon, Sun } from "lucide-react";
import { API, cn } from "@/lib/utils";
import { I18nProvider, useI18n } from "@/lib/i18n-context";
import { useTopBarGsap } from "@/lib/useSunnyGsap";
import SunnyRegister, { clearSunnyRegisterTaskHistory } from "@/pages/SunnyRegister";
import PublicLanding from "@/pages/PublicLanding";
import AuditLogPage from "@/pages/AuditLogPage";

function words(language: string) {
  return language === "en-US"
    ? { app: "SunnyRegister", sub: "GPT account registration manager", home: "Studio", settings: "Settings", loginTitle: "Welcome back", loginDesc: "Enter your administrator credentials.", user: "Username", pass: "Password", submit: "Sign in", checking: "Checking...", failed: "Login failed", loading: "Loading...", logout: "Sign out" }
    : { app: "SunnyRegister", sub: "GPT 账号注册与管理", home: "工作台", settings: "设置", loginTitle: "欢迎回来", loginDesc: "请输入管理员账号与密码。", user: "用户名", pass: "密码", submit: "登录", checking: "验证中...", failed: "登录失败", loading: "加载中...", logout: "退出登录" };
}

function TopBar({ theme, setTheme, onLogout }: { theme: string; setTheme: (v: string) => void; onLogout: () => Promise<void> }) {
  const { language, toggleLanguage } = useI18n();
  const c = words(language);
  const location = useLocation();
  const headerRef = useRef<HTMLElement | null>(null);
  useTopBarGsap(headerRef, `${location.pathname}:${language}`);
  const menus = language === "en-US"
    ? [["/", "Workbench"], ["/mailbox", "Mailbox"], ["/phone", "SMS"], ["/sub2api", "Reverse"], ["/proxy", "Proxy"], ["/session", "Account Management"], ["/audit", "Audit Logs"]]
    : [["/", "工作台"], ["/mailbox", "邮箱配置"], ["/phone", "接码配置"], ["/sub2api", "反代配置"], ["/proxy", "代理配置"], ["/session", "账户管理"], ["/audit", "日志管理"]];
  const navClass = (active: boolean) => cn("inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition-all", active ? "bg-[var(--accent)] text-white shadow-[var(--shadow-glow)]" : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]");
  return (
    <header ref={headerRef} className="sticky top-0 z-[300] border-b border-[var(--border)] bg-[var(--bg-shell)]/80 backdrop-blur-2xl">
      <div className="app-shell mx-auto grid grid-cols-[1fr_auto] items-center gap-4 py-3 lg:grid-cols-[280px_minmax(0,1fr)_160px]">
        <div className="flex min-w-0 shrink-0 items-center gap-3 justify-self-start">
          <div className="brand-mark"><Link2 className="h-5 w-5" /></div>
          <div className="hidden sm:block"><div className="text-sm font-black tracking-tight text-[var(--text-primary)]">{c.app}</div><div className="text-xs text-[var(--text-muted)]">{c.sub}</div></div>
        </div>
        <nav className="hidden w-fit max-w-full justify-center overflow-x-auto rounded-full border border-[var(--border)] bg-[var(--chip-bg)] p-1 justify-self-center lg:flex">
          {menus.map(([to, label]) => {
            const active = to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);
            return <NavLink key={to} to={to} data-sunny-nav-active={active ? "true" : undefined} className={() => navClass(active)}>{label}</NavLink>;
          })}
        </nav>
        <div className="flex shrink-0 items-center justify-end gap-2 justify-self-end">
          <button className="round-tool" onClick={() => setTheme(theme === "light" ? "dark" : "light")} title={theme}>{theme === "light" ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}</button>
          <button className="round-tool min-w-12 px-3 text-xs font-bold" onClick={toggleLanguage}><Languages className="h-4 w-4" />{language === "zh-CN" ? "中" : "EN"}</button>
          <button className="round-tool" title={c.logout} aria-label={c.logout} onClick={onLogout}><LogOut className="h-4 w-4" /></button>
        </div>
      </div>
    </header>
  );
}

function Shell({ theme, setTheme, onLogout }: { theme: string; setTheme: (v: string) => void; onLogout: () => Promise<void> }) {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[var(--bg-base)]">
        <TopBar theme={theme} setTheme={setTheme} onLogout={onLogout} />
        <main className="app-shell mx-auto py-6 md:py-8">
          <Routes>
            <Route path="/audit" element={<AuditLogPage />} />
            <Route path="*" element={<SunnyRegister />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

function AppContent() {
  const { language } = useI18n();
  const c = words(language);
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") === "dark" ? "dark" : "light");
  const [authState, setAuthState] = useState<"loading" | "open" | "locked" | "authed">("loading");
  const [logoutNotice, setLogoutNotice] = useState(false);
  useEffect(() => { document.documentElement.classList.toggle("light", theme === "light"); localStorage.setItem("theme", theme); }, [theme]);
  useEffect(() => { fetch(API + "/auth/check", { credentials: "include", cache: "no-store" }).then((r) => r.json()).then((data) => { if (!data.required) setAuthState("open"); else if (data.authenticated) setAuthState("authed"); else setAuthState("locked"); }).catch(() => setAuthState("locked")); }, []);
  const logout = useCallback(async () => {
    let completed = false;
    try {
      const response = await fetch(API + "/auth/logout", { method: "POST", credentials: "include", cache: "no-store" });
      completed = response.ok;
    } finally {
      window.history.replaceState(null, "", "/");
      setAuthState("locked");
      setLogoutNotice(completed);
    }
  }, []);
  let content;
  if (authState === "loading") content = <div className="flex h-screen items-center justify-center bg-[var(--bg-base)] text-sm text-[var(--text-muted)]">{c.loading}</div>;
  else if (authState === "locked") content = <PublicLanding onLogin={() => { clearSunnyRegisterTaskHistory(); setLogoutNotice(false); setAuthState("authed"); }} logoutNotice={logoutNotice} onNoticeDone={() => setLogoutNotice(false)} />;
  else content = <Shell theme={theme} setTheme={setTheme} onLogout={logout} />;
  return <ConfigProvider
    locale={language === "en-US" ? enUS : zhCN}
    theme={{
      algorithm: theme === "dark" ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
      token: {
        colorPrimary: "#10b981", colorInfo: "#0ea5e9", colorSuccess: "#10b981",
        colorWarning: "#f59e0b", colorError: "#ef4444", borderRadius: 8,
        controlHeight: 40, fontFamily: "Inter, 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif",
        zIndexPopupBase: 220,
      },
      components: {
        Button: { borderRadius: 8, fontWeight: 700, controlHeight: 40 },
        Card: { borderRadiusLG: 18 },
        Input: { borderRadius: 8, activeBorderColor: "#14b8a6", hoverBorderColor: "#2dd4bf" },
        Select: { borderRadius: 8, optionSelectedBg: "rgba(20,184,166,.12)" },
        Table: { headerBg: theme === "dark" ? "#10242d" : "#f8fafc", headerColor: theme === "dark" ? "#94a3b8" : "#64748b", borderColor: theme === "dark" ? "rgba(148,163,184,.16)" : "rgba(15,23,42,.08)", rowHoverBg: theme === "dark" ? "rgba(20,184,166,.08)" : "rgba(20,184,166,.045)" },
      },
    }}
  ><AntApp className="sunny-antd-app">{content}</AntApp></ConfigProvider>;
}

export default function App() { return <I18nProvider><AppContent /></I18nProvider>; }
