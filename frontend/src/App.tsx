import { BrowserRouter, Route, Routes, NavLink, useLocation } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { Languages, Link2, Moon, Sun } from "lucide-react";
import { API, cn, getAuthToken, setAuthToken } from "@/lib/utils";
import { I18nProvider, useI18n } from "@/lib/i18n-context";
import { useTopBarGsap } from "@/lib/useSunnyGsap";
import SunnyRegister from "@/pages/SunnyRegister";
import { Button } from "@/components/ui/button";
import SettingsPage from "@/pages/SettingsPage";

function words(language: string) {
  return language === "en-US"
    ? { app: "SunnyRegister", sub: "GPT account registration manager", home: "Studio", settings: "Settings", loginTitle: "Welcome back", loginDesc: "Enter the admin password to continue.", user: "Username", pass: "Password", submit: "Sign in", checking: "Checking...", failed: "Login failed", loading: "Loading..." }
    : { app: "SunnyRegister", sub: "GPT 账号注册与管理", home: "工作台", settings: "设置", loginTitle: "欢迎回来", loginDesc: "请输入管理员密码继续。", user: "用户名", pass: "密码", submit: "登录", checking: "验证中...", failed: "登录失败", loading: "加载中..." };
}

function TopBar({ theme, setTheme }: { theme: string; setTheme: (v: string) => void }) {
  const { language, toggleLanguage } = useI18n();
  const c = words(language);
  const location = useLocation();
  const headerRef = useRef<HTMLElement | null>(null);
  useTopBarGsap(headerRef, `${location.pathname}:${language}`);
  const menus = language === "en-US"
    ? [["/", "Workbench"], ["/mailbox", "Mailbox"], ["/phone", "SMS"], ["/sub2api", "Reverse"], ["/proxy", "Proxy"], ["/session", "Session"]]
    : [["/", "工作台"], ["/mailbox", "邮箱配置"], ["/phone", "接码配置"], ["/sub2api", "反代配置"], ["/proxy", "代理配置"], ["/session", "Session管理"]];
  const navClass = (active: boolean) => cn("inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-semibold transition-all", active ? "bg-[var(--accent)] text-white shadow-[var(--shadow-glow)]" : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]");
  return (
    <header ref={headerRef} className="sticky top-0 z-[300] border-b border-[var(--border)] bg-[var(--bg-shell)]/80 backdrop-blur-2xl">
      <div className="mx-auto grid max-w-7xl grid-cols-[1fr_auto] items-center gap-4 px-4 py-3 md:px-6 lg:grid-cols-[280px_minmax(0,1fr)_160px]">
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
        </div>
      </div>
    </header>
  );
}

function Shell({ theme, setTheme }: { theme: string; setTheme: (v: string) => void }) {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[var(--bg-base)]">
        <TopBar theme={theme} setTheme={setTheme} />
        <main className="mx-auto max-w-7xl px-4 py-6 md:px-6 md:py-8">
          <Routes>
            <Route path="/" element={<SunnyRegister />} />
            <Route path="/mailbox" element={<SunnyRegister />} />
            <Route path="/phone" element={<SunnyRegister />} />
            <Route path="/sub2api" element={<SunnyRegister />} />
            <Route path="/proxy" element={<SunnyRegister />} />
            <Route path="/session" element={<SunnyRegister />} />
            <Route path="/settings" element={<SettingsPage theme={theme} setTheme={setTheme} />} />
            <Route path="*" element={<SunnyRegister />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

function LoginScreen({ onLogin }: { onLogin: () => void }) {
  const { language, toggleLanguage } = useI18n();
  const c = words(language);
  const [username, setUsername] = useState("admin");
  const [pw, setPw] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setLoading(true); setError("");
    try {
      const res = await fetch(API + "/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password: pw }) });
      const data = await res.json();
      if (data.ok) { setAuthToken(data.token || ""); onLogin(); } else setError(data.error || c.failed);
    } catch { setError(c.failed); } finally { setLoading(false); }
  }
  return <div className="flex min-h-screen items-center justify-center bg-[var(--bg-base)] p-4"><form onSubmit={submit} className="login-card w-full max-w-sm space-y-4 rounded-[32px] border border-[var(--border)] bg-[var(--bg-card)] p-6"><div className="flex items-center justify-between"><div className="brand-mark"><Link2 className="h-5 w-5" /></div><button type="button" className="round-tool" onClick={toggleLanguage}><Languages className="h-4 w-4" /></button></div><div><h1 className="text-2xl font-black tracking-tight">{c.loginTitle}</h1><p className="mt-2 text-sm text-[var(--text-muted)]">{c.loginDesc}</p></div><input className="control-surface h-12" value={username} onChange={(e) => setUsername(e.target.value)} placeholder={c.user} /><input className="control-surface h-12" type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder={c.pass} autoFocus />{error && <p className="text-sm text-red-500">{error}</p>}<Button className="h-12 w-full rounded-full" disabled={loading || !pw}>{loading ? c.checking : c.submit}</Button></form></div>;
}

function AppContent() {
  const { language } = useI18n();
  const c = words(language);
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") === "dark" ? "dark" : "light");
  const [authState, setAuthState] = useState<"loading" | "open" | "locked" | "authed">("loading");
  useEffect(() => { document.documentElement.classList.toggle("light", theme === "light"); localStorage.setItem("theme", theme); }, [theme]);
  useEffect(() => { fetch(API + "/auth/check").then((r) => r.json()).then((data) => { if (!data.required) setAuthState("open"); else if (getAuthToken()) setAuthState("authed"); else setAuthState("locked"); }).catch(() => setAuthState("open")); }, []);
  if (authState === "loading") return <div className="flex h-screen items-center justify-center bg-[var(--bg-base)] text-sm text-[var(--text-muted)]">{c.loading}</div>;
  if (authState === "locked") return <LoginScreen onLogin={() => setAuthState("authed")} />;
  return <Shell theme={theme} setTheme={setTheme} />;
}

export default function App() { return <I18nProvider><AppContent /></I18nProvider>; }





