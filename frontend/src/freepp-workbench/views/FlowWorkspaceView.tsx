import { useMemo, useState } from "react";
import { useStore } from "../store/useStore";
import type { WorkspaceProject, ViewName } from "../types";
import { PayPalExtractView } from "./PayPalExtractView";
import { MomoView } from "./MomoView";
import { GrokView } from "./GrokView";
import { PixView } from "./PixView";
import { BranchConfigView } from "./BranchConfigView";
import { DirectView } from "./DirectView";
import { PayPalView } from "./PayPalView";
import { DirectPayView } from "./DirectPayView";

type Detail = { label: string; desc: string; view: ViewName };
type Project = {
  id: WorkspaceProject;
  label: string;
  group: "提链配置" | "支付授权";
  channel: string;
  country: string;
  desc: string;
  details: Detail[];
};

const chainDetails = (view: ViewName, label: string, extra: Detail[] = []): Detail[] => [
  { label: "链路参数", desc: `${label} 的分段出口、国家与重试配置`, view },
  { label: "Token 入口", desc: "选择 Token、批量启动与导入", view: "tokens" },
  { label: "实时监控", desc: "查看阶段进度、成功与失败原因", view: "chains" },
  { label: "成功库存", desc: "查看该链路产出的结果与导出项", view: "inventory" },
  ...extra,
];

const PROJECTS: Project[] = [
  { id: "paypal_extract", label: "PayPal 提炼", group: "提链配置", channel: "paypal", country: "US / GB / AU", desc: "PayPal 双链路提炼与 BA URL 产出", details: chainDetails("paypal_extract", "PayPal 提炼", [{ label: "BA 授权", desc: "进入 PayPal 授权队列并处理授权状态", view: "paypal" }]) },
  { id: "momo", label: "MoMo 提链", group: "提链配置", channel: "momo", country: "VN", desc: "MoMo 七段链路与支付 URL 解析", details: chainDetails("momo", "MoMo 提链") },
  { id: "grok", label: "Grok 链路", group: "提链配置", channel: "card", country: "US", desc: "Grok 卡渠道链路配置与提链", details: chainDetails("grok", "Grok 链路") },
  { id: "pix", label: "PIX 二维码", group: "提链配置", channel: "pix", country: "BR", desc: "PIX BR Code 生成与二维码预览", details: chainDetails("pix", "PIX 二维码") },
  { id: "ideal", label: "iDEAL 提链", group: "提链配置", channel: "ideal", country: "NL", desc: "iDEAL EUR 账单链路", details: chainDetails("ideal", "iDEAL 提链") },
  { id: "upi", label: "UPI 提链", group: "提链配置", channel: "upi", country: "IN", desc: "UPI INR 账单链路", details: chainDetails("upi", "UPI 提链") },
  { id: "kakao", label: "Kakao Pay", group: "提链配置", channel: "kakao", country: "KR", desc: "Kakao Pay KRW 链路", details: chainDetails("kakao", "Kakao Pay") },
  { id: "blik", label: "BLIK 提链", group: "提链配置", channel: "blik", country: "PL", desc: "BLIK PLN 链路", details: chainDetails("blik", "BLIK 提链") },
  { id: "twint", label: "TWINT 提链", group: "提链配置", channel: "twint", country: "CH", desc: "TWINT CHF 链路", details: chainDetails("twint", "TWINT 提链") },
  { id: "bizum", label: "Bizum 提链", group: "提链配置", channel: "bizum", country: "ES", desc: "Bizum 手机授权链路", details: chainDetails("bizum", "Bizum 提链") },
  { id: "gopay", label: "GoPay 提链", group: "提链配置", channel: "gopay", country: "ID", desc: "GoPay Midtrans 落地链路", details: chainDetails("gopay", "GoPay 提链") },
  { id: "naver_pay", label: "Naver Pay", group: "提链配置", channel: "naver_pay", country: "KR", desc: "Naver Pay NicePay 链路", details: chainDetails("naver_pay", "Naver Pay") },
  { id: "gcash", label: "GCash 提链", group: "提链配置", channel: "gcash", country: "PH", desc: "GCash Adyen 链路", details: chainDetails("gcash", "GCash 提链") },
  { id: "grabpay", label: "GrabPay 提链", group: "提链配置", channel: "grabpay", country: "PH", desc: "GrabPay 链路", details: chainDetails("grabpay", "GrabPay 提链") },
  { id: "qris", label: "QRIS 提链", group: "提链配置", channel: "qris", country: "ID", desc: "QRIS Midtrans Charge 链路", details: chainDetails("qris", "QRIS 提链") },
  { id: "direct", label: "直卡提链", group: "提链配置", channel: "card", country: "PH", desc: "直卡绑卡与订阅前置链路", details: chainDetails("direct", "直卡提链", [{ label: "直卡支付", desc: "进入绑卡、免税地址与订阅流程", view: "direct_pay" }]) },
  { id: "paypal", label: "PayPal 授权", group: "支付授权", channel: "paypal", country: "跟随提链", desc: "BA 授权队列、接码与授权重试", details: [{ label: "授权队列", desc: "查看待授权、成功与失败记录", view: "paypal" }, { label: "授权配置", desc: "国家、代理、接码与并发参数", view: "paypal" }, { label: "授权监控", desc: "实时查看授权阶段与错误", view: "chains" }] },
  { id: "direct_pay", label: "直卡支付", group: "支付授权", channel: "card", country: "跟随提链", desc: "绑卡、免税地址与订阅执行", details: [{ label: "支付任务", desc: "启动与查看直卡支付任务", view: "direct_pay" }, { label: "任务监控", desc: "查看支付流程进度与结果", view: "chains" }] },
];

/** Projects that can be submitted to the local SunnyRegister checkout runner. */
export const CHAIN_PROJECT_BRANCH: Partial<Record<WorkspaceProject, string>> = Object.fromEntries(
  PROJECTS.filter((project) => project.group === "提链配置").map((project) => [project.id, project.id === "paypal_extract" ? "paypal" : project.id]),
) as Partial<Record<WorkspaceProject, string>>;

const ICONS: Record<string, string> = {
  paypal: "P", momo: "M", grok: "G", pix: "Q", ideal: "i", upi: "U", kakao: "K", blik: "B", twint: "T", bizum: "Bz", gopay: "Go", naver_pay: "N", gcash: "G", grabpay: "Gr", qris: "Qr", direct: "D", direct_pay: "D",
};

/* 工作台弹窗内直接复用各项目的真实配置页，保证所有输入和保存动作仍走原 API。 */
function renderEmbeddedProject(project: WorkspaceProject) {
  switch (project) {
    case "paypal_extract":
      return <PayPalExtractView />;
    case "momo":
      return <MomoView />;
    case "grok":
      return <GrokView />;
    case "pix":
      return <PixView />;
    case "ideal":
      return <BranchConfigView branchName="ideal" title="iDEAL 提链" sub="七段出口配置 (iDEAL 渠道) · NL 账单 EUR" defaultCountry="NL" updateCountry="VN" />;
    case "upi":
      return <BranchConfigView branchName="upi" title="UPI 提链" sub="七段出口配置 (UPI 渠道) · IN 账单 INR" defaultCountry="IN" updateCountry="VN" />;
    case "kakao":
      return <BranchConfigView branchName="kakao" title="Kakao Pay 提链" sub="七段出口配置 (Kakao 渠道) · KR 账单 KRW" defaultCountry="KR" updateCountry="VN" />;
    case "blik":
      return <BranchConfigView branchName="blik" title="BLIK 提链" sub="七段出口配置 (BLIK 渠道) · PL 账单 PLN" defaultCountry="PL" updateCountry="PL" />;
    case "twint":
      return <BranchConfigView branchName="twint" title="TWINT 提链" sub="七段出口配置 (TWINT 渠道) · CH 账单 CHF" defaultCountry="CH" updateCountry="VN" />;
    case "bizum":
      return <BranchConfigView branchName="bizum" title="Bizum 提链" sub="七段出口配置 (Bizum 渠道) · ES 账单 EUR · 手机授权提链" defaultCountry="ES" updateCountry="VN" />;
    case "gopay":
      return <BranchConfigView branchName="gopay" title="GoPay 提链" sub="七段出口配置 (GoPay 渠道) · ID 账单 IDR · Midtrans 落地" defaultCountry="ID" updateCountry="VN" />;
    case "naver_pay":
      return <BranchConfigView branchName="naver_pay" title="Naver Pay 提链" sub="七段出口配置 (Naver Pay 渠道) · KR 账单 KRW · NicePay 落地" defaultCountry="KR" updateCountry="VN" />;
    case "gcash":
      return <BranchConfigView branchName="gcash" title="GCash 提链" sub="七段出口配置 (GCash 渠道) · PH 账单 PHP · Adyen 落地" defaultCountry="PH" updateCountry="VN" />;
    case "grabpay":
      return <BranchConfigView branchName="grabpay" title="GrabPay 提链" sub="七段出口配置 (GrabPay 渠道) · PH 账单 PHP · Grab 落地" defaultCountry="PH" updateCountry="VN" />;
    case "qris":
      return <BranchConfigView branchName="qris" title="QRIS 提链" sub="七段出口配置 (QRIS 渠道) · ID 账单 IDR · Midtrans Charge" defaultCountry="ID" updateCountry="VN" />;
    case "direct":
      return <DirectView />;
    case "paypal":
      return <PayPalView />;
    case "direct_pay":
      return <DirectPayView />;
    default:
      return null;
  }
}

export function FlowWorkspaceView() {
  const selected = useStore((s) => s.workspaceProject);
  const setSelected = useStore((s) => s.setWorkspaceProject);
  const setView = useStore((s) => s.setView);
  const selectedProjects = useStore((s) => s.selectedWorkspaceProjects);
  const toggleProject = useStore((s) => s.toggleWorkspaceProject);
  const [group, setGroup] = useState<"all" | "提链配置" | "支付授权">("all");
  const projects = useMemo(() => group === "all" ? PROJECTS : PROJECTS.filter((p) => p.group === group), [group]);
  const active = PROJECTS.find((p) => p.id === selected) || null;

  function openView(view: ViewName) {
    setSelected(null);
    setView(view);
  }

  return (
    <div className="page page-wide flow-workspace">
      <div className="page-head">
        <div>
          <h2 className="page-title">链路工作台</h2>
          <p className="page-sub">提链配置与支付授权 · 点击项目查看细分入口</p>
        </div>
        <div className="page-actions">
          <button className={`btn ${group === "all" ? "btn-primary" : ""}`} onClick={() => setGroup("all")}>全部 {PROJECTS.length}</button>
          <button className={`btn ${group === "提链配置" ? "btn-primary" : ""}`} onClick={() => setGroup("提链配置")}>提链配置 16</button>
          <button className={`btn ${group === "支付授权" ? "btn-primary" : ""}`} onClick={() => setGroup("支付授权")}>支付授权 2</button>
        </div>
      </div>

      <section className="workspace-band">
        <div className="workspace-band-head">
          <div><span className="section-title">项目总览</span><span className="workspace-count">{projects.length} 个项目</span></div>
          <span className="workspace-hint">配置、启动、监控和产出均从细分入口进入</span>
        </div>
        <div className="workspace-grid">
          {projects.map((project) => (
            <button key={project.id} className={`workspace-card ${selectedProjects.has(project.id) ? "workspace-card-selected" : ""}`} onClick={() => setSelected(project.id)}>
              <span className="workspace-card-top">
                <input type="checkbox" checked={selectedProjects.has(project.id)} onChange={() => toggleProject(project.id)} onClick={(event) => event.stopPropagation()} aria-label={`选择 ${project.label}`} />
                <span className={`workspace-icon workspace-icon-${project.group === "支付授权" ? "pay" : "chain"}`}>{ICONS[project.id]}</span>
                <span className="workspace-card-title">{project.label}</span>
                <span className="workspace-chevron">›</span>
              </span>
              <span className="workspace-card-desc">{project.desc}</span>
              <span className="workspace-card-meta"><span>{project.channel}</span><span>{project.country}</span><span>{project.details.length} 个入口</span></span>
            </button>
          ))}
        </div>
      </section>

      {active && (
        <div className="workspace-overlay" onClick={() => setSelected(null)}>
          <div className="workspace-dialog workspace-dialog-config" onClick={(event) => event.stopPropagation()}>
            <div className="workspace-dialog-head">
              <div><span className="workspace-dialog-kicker">{active.group}</span><h3>{active.label}</h3><p>{active.desc}</p></div>
              <button className="icon-btn" onClick={() => setSelected(null)} aria-label="关闭">✕</button>
            </div>
            <div className="workspace-dialog-body">
              <div className="workspace-subproject-head">
                <span className="section-title">细分项目</span>
                <span className="muted">参数修改会立即保存到当前链路</span>
              </div>
              <div className="workspace-detail-list workspace-detail-list-inline">
                {active.details.map((detail) => (
                  <button key={`${active.id}-${detail.label}`} className="workspace-detail" onClick={() => openView(detail.view)}>
                    <span className="workspace-detail-mark">↗</span><span><strong>{detail.label}</strong><small>{detail.desc}</small></span><b>打开</b>
                  </button>
                ))}
              </div>
              <div className="workspace-embedded-config">
                {renderEmbeddedProject(active.id)}
              </div>
            </div>
            <div className="workspace-dialog-foot"><span>项目：{active.channel} · {active.country}</span><button className="btn btn-primary" onClick={() => openView(active.id as ViewName)}>打开主配置</button></div>
          </div>
        </div>
      )}
    </div>
  );
}
