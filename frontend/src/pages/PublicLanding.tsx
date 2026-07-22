import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Database,
  Github,
  KeyRound,
  Languages,
  Link2,
  LogIn,
  Mail,
  Network,
  PhoneCall,
  Route,
  ShieldCheck,
  Terminal,
  Users,
  X,
} from "lucide-react";
import { API } from "@/lib/utils";
import { useI18n } from "@/lib/i18n-context";

gsap.registerPlugin(useGSAP);

const GITHUB_URL = "https://github.com/pxygit/SunnyRegister";

const COPY = {
  "zh-CN": {
    sub: "GPT 账号注册与管理",
    github: "查看 GitHub 开源仓库",
    language: "切换到 English",
    login: "登录",
    eyebrow: "一体化账号注册工作流",
    lead: "把邮箱验证、账号注册、接码绑定、代理出站、反代导入和 Session 管理放进同一套安静、可控的工作台。",
    openConsole: "进入控制台",
    source: "查看源代码",
    live: "运行链路",
    ready: "资源就绪",
    running: "自动注册执行中",
    complete: "Session 已安全存储",
    accounts: "账户",
    mailboxes: "邮箱资源",
    successRate: "任务进度",
    sectionEyebrow: "核心能力",
    sectionTitle: "从资源配置到账号交付，全程可观察",
    sectionDesc: "每个阶段独立配置、按需启用，任务日志与账户状态实时回写。",
    features: [
      ["邮箱池管理", "批量导入 Outlook 邮箱，支持分组、状态、邮件查询与资源启停。"],
      ["自动注册工作台", "按邮箱批量创建任务，识别注册或登录路径并实时展示分账户日志。"],
      ["接码能力编排", "统一管理自建手机号池与外部接码供应商，按可用性选择资源。"],
      ["代理出站控制", "管理代理池、健康状态与国家标签，为注册流量提供独立出口。"],
      ["反代平台联动", "完成账号阶段后按配置导入 sub2api，并保留每一步执行结果。"],
      ["Session 管理", "集中保存和导出 Auth Session、Access Token 及账户有效信息。"],
    ],
    flowEyebrow: "阶段可控",
    flowTitle: "一条清晰的注册链路",
    flowDesc: "按任务选择终止阶段，已完成结果即时保存，未启用的资源不会被误用。",
    flow: ["邮箱验证", "注册 / 登录", "接码绑定", "反代导入"],
    secureTitle: "管理入口受会话保护",
    secureDesc: "未登录用户只能访问项目介绍。登录后才会加载管理界面与业务数据。",
    drawerTitle: "登录 SunnyRegister",
    drawerDesc: "使用管理员凭据进入注册机控制台。",
    username: "用户名",
    password: "密码",
    submit: "安全登录",
    checking: "正在验证...",
    failed: "登录失败，请检查用户名和密码",
    tooMany: "登录尝试过于频繁，请稍后再试",
    close: "关闭登录面板",
    protected: "受保护的管理会话",
    logoutSuccess: "已安全退出登录",
  },
  "en-US": {
    sub: "GPT account registration and management",
    github: "View the GitHub repository",
    language: "切换到中文",
    login: "Sign in",
    eyebrow: "Unified account registration workflow",
    lead: "Bring mailbox verification, account registration, phone binding, proxy routing, reverse-platform import, and Session management into one calm, controlled workspace.",
    openConsole: "Open console",
    source: "View source",
    live: "Live workflow",
    ready: "Resources ready",
    running: "Registration in progress",
    complete: "Session securely stored",
    accounts: "Accounts",
    mailboxes: "Mailboxes",
    successRate: "Task progress",
    sectionEyebrow: "Core capabilities",
    sectionTitle: "Observable from resource setup to account delivery",
    sectionDesc: "Enable each stage independently while task logs and account states update in real time.",
    features: [
      ["Mailbox pools", "Import Outlook mailboxes in bulk with grouping, status, mail query, and enable controls."],
      ["Registration workspace", "Create mailbox batches, detect register or login paths, and follow per-account logs."],
      ["SMS orchestration", "Coordinate self-managed phone pools and external SMS providers by availability."],
      ["Proxy routing", "Manage proxy health and country labels to provide dedicated registration egress."],
      ["Reverse integration", "Import completed accounts into sub2api and retain each execution result."],
      ["Session management", "Store and export Auth Sessions, Access Tokens, and valid account data centrally."],
    ],
    flowEyebrow: "Stage control",
    flowTitle: "One clear registration pipeline",
    flowDesc: "Choose the final stage per task. Completed results persist immediately, and disabled resources stay untouched.",
    flow: ["Mail verification", "Register / login", "Phone binding", "Reverse import"],
    secureTitle: "Protected administration entry",
    secureDesc: "Signed-out visitors only see the product overview. Management UI and business data load after authentication.",
    drawerTitle: "Sign in to SunnyRegister",
    drawerDesc: "Use administrator credentials to open the registration console.",
    username: "Username",
    password: "Password",
    submit: "Secure sign in",
    checking: "Verifying...",
    failed: "Sign-in failed. Check your username and password.",
    tooMany: "Too many sign-in attempts. Please try again later.",
    close: "Close sign-in panel",
    protected: "Protected admin session",
    logoutSuccess: "Signed out securely",
  },
} as const;

const FEATURE_ICONS = [Mail, Users, PhoneCall, Route, Network, Database];

type PublicLandingProps = {
  onLogin: () => void;
  logoutNotice?: boolean;
  onNoticeDone?: () => void;
};

export default function PublicLanding({ onLogin, logoutNotice = false, onNoticeDone }: PublicLandingProps) {
  const { language, toggleLanguage } = useI18n();
  const c = COPY[language];
  const rootRef = useRef<HTMLDivElement | null>(null);
  const usernameRef = useRef<HTMLInputElement | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const closeDrawer = useCallback(() => {
    if (loading) return;
    setDrawerOpen(false);
    setError("");
    setUsername("");
    setPassword("");
  }, [loading]);

  useGSAP(
    () => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      const timeline = gsap.timeline({ defaults: { ease: "power3.out" } });
      timeline
        .from(".public-nav", { autoAlpha: 0, y: -14, duration: 0.42 })
        .from(".public-hero-copy > *", { autoAlpha: 0, y: 22, duration: 0.48, stagger: 0.07 }, "-=0.18")
        .from(".public-console", { autoAlpha: 0, y: 28, scale: 0.985, duration: 0.58 }, "-=0.28")
        .from(".public-console-row", { autoAlpha: 0, x: -12, duration: 0.3, stagger: 0.055 }, "-=0.24")
        .from(".public-feature", { autoAlpha: 0, y: 18, duration: 0.4, stagger: 0.045 }, "-=0.08");
    },
    { scope: rootRef },
  );

  useGSAP(
    () => {
      if (!drawerOpen || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      gsap.fromTo(".public-drawer-mask", { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.18 });
      gsap.fromTo(".public-login-drawer", { x: "100%" }, { x: 0, duration: 0.42, ease: "power3.out" });
    },
    { scope: rootRef, dependencies: [drawerOpen], revertOnUpdate: true },
  );

  useEffect(() => {
    if (!drawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const timer = window.setTimeout(() => usernameRef.current?.focus(), 120);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeDrawer();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [drawerOpen, closeDrawer]);

  useEffect(() => {
    if (!logoutNotice) return;
    const timer = window.setTimeout(() => onNoticeDone?.(), 3200);
    return () => window.clearTimeout(timer);
  }, [logoutNotice, onNoticeDone]);

  function openDrawer() {
    setError("");
    setDrawerOpen(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password || loading) return;
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API}/auth/login`, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(response.status === 429 ? c.tooMany : c.failed);
      setPassword("");
      onLogin();
    } catch (reason) {
      setPassword("");
      setError(reason instanceof Error && reason.message ? reason.message : c.failed);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div ref={rootRef} className="public-landing">
      {logoutNotice && (
        <div className="public-toast" role="status">
          <CheckCircle2 aria-hidden="true" />
          <span>{c.logoutSuccess}</span>
        </div>
      )}

      <header className="public-nav">
        <a className="public-brand" href="#top" aria-label="SunnyRegister">
          <span className="brand-mark"><Link2 aria-hidden="true" /></span>
          <span><strong>SunnyRegister</strong><small>{c.sub}</small></span>
        </a>
        <div className="public-nav-actions">
          <a className="round-tool" href={GITHUB_URL} target="_blank" rel="noreferrer" title={c.github} aria-label={c.github}>
            <Github aria-hidden="true" />
          </a>
          <button type="button" className="round-tool public-language" onClick={toggleLanguage} title={c.language} aria-label={c.language}>
            <Languages aria-hidden="true" /><span>{language === "zh-CN" ? "中" : "EN"}</span>
          </button>
          <button type="button" className="public-login-button" onClick={openDrawer}>
            <LogIn aria-hidden="true" /><span>{c.login}</span>
          </button>
        </div>
      </header>

      <main id="top">
        <section className="public-hero" aria-labelledby="public-title">
          <div className="public-hero-copy">
            <div className="public-eyebrow"><Activity aria-hidden="true" />{c.eyebrow}</div>
            <h1 id="public-title">SunnyRegister</h1>
            <p>{c.lead}</p>
            <div className="public-hero-actions">
              <button type="button" className="public-primary-button" onClick={openDrawer}>{c.openConsole}<ArrowRight aria-hidden="true" /></button>
              <a className="public-secondary-button" href={GITHUB_URL} target="_blank" rel="noreferrer"><Github aria-hidden="true" />{c.source}</a>
            </div>
          </div>

          <div className="public-console" aria-label={c.live}>
            <div className="public-console-head">
              <div><Terminal aria-hidden="true" /><strong>{c.live}</strong></div>
              <span><i />{c.ready}</span>
            </div>
            <div className="public-console-stats">
              <div><small>{c.accounts}</small><strong>128</strong><span>+12</span></div>
              <div><small>{c.mailboxes}</small><strong>240</strong><span>92%</span></div>
              <div><small>{c.successRate}</small><strong>84%</strong><span>21 / 25</span></div>
            </div>
            <div className="public-console-body">
              <div className="public-console-row done"><CheckCircle2 /><span><strong>Outlook-024</strong><small>{c.complete}</small></span><b>Free</b></div>
              <div className="public-console-row running"><Activity /><span><strong>Outlook-025</strong><small>{c.running}</small></span><b>72%</b></div>
              <div className="public-console-row"><Mail /><span><strong>Outlook-026</strong><small>{c.ready}</small></span><b>Ready</b></div>
            </div>
          </div>
        </section>

        <section className="public-section" aria-labelledby="features-title">
          <div className="public-section-heading">
            <span>{c.sectionEyebrow}</span>
            <h2 id="features-title">{c.sectionTitle}</h2>
            <p>{c.sectionDesc}</p>
          </div>
          <div className="public-feature-grid">
            {c.features.map(([title, description], index) => {
              const Icon = FEATURE_ICONS[index];
              return <article className="public-feature" key={title}><div><Icon aria-hidden="true" /></div><h3>{title}</h3><p>{description}</p></article>;
            })}
          </div>
        </section>

        <section className="public-flow-section" aria-labelledby="flow-title">
          <div className="public-flow-copy">
            <span>{c.flowEyebrow}</span>
            <h2 id="flow-title">{c.flowTitle}</h2>
            <p>{c.flowDesc}</p>
          </div>
          <div className="public-flow">
            {c.flow.map((label, index) => <div key={label}><b>{String(index + 1).padStart(2, "0")}</b><span>{label}</span>{index < c.flow.length - 1 && <ArrowRight aria-hidden="true" />}</div>)}
          </div>
          <div className="public-security-note"><ShieldCheck aria-hidden="true" /><div><strong>{c.secureTitle}</strong><p>{c.secureDesc}</p></div></div>
        </section>
      </main>

      <footer className="public-footer"><span>SunnyRegister</span><span>Open source registration workspace</span></footer>

      {drawerOpen && (
        <div className="public-drawer-mask" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDrawer(); }}>
          <aside className="public-login-drawer" role="dialog" aria-modal="true" aria-labelledby="login-title">
            <div className="public-drawer-head">
              <div className="brand-mark"><KeyRound aria-hidden="true" /></div>
              <button type="button" className="round-tool" onClick={closeDrawer} disabled={loading} title={c.close} aria-label={c.close}><X aria-hidden="true" /></button>
            </div>
            <div className="public-login-heading">
              <span><ShieldCheck aria-hidden="true" />{c.protected}</span>
              <h2 id="login-title">{c.drawerTitle}</h2>
              <p>{c.drawerDesc}</p>
            </div>
            <form onSubmit={submit} className="public-login-form">
              <label><span>{c.username}</span><input ref={usernameRef} value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" spellCheck={false} /></label>
              <label><span>{c.password}</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></label>
              {error && <p className="public-login-error" role="alert">{error}</p>}
              <button type="submit" className="public-primary-button public-submit" disabled={loading || !username.trim() || !password}>
                {loading ? c.checking : c.submit}<ArrowRight aria-hidden="true" />
              </button>
            </form>
          </aside>
        </div>
      )}
    </div>
  );
}
