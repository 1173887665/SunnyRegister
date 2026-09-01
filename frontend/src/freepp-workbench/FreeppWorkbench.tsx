import { useCallback, useEffect } from "react";
import { useWebSocket } from "./hooks/useWebSocket";
import { useStore } from "./store/useStore";
import { apiFetch } from "@/lib/utils";
import { setRuntimeError, setRuntimeProxies } from "./integration/runtime";
import type { ViewName } from "./types";
import { ChainsView } from "./views/ChainsView";
import { LogsView } from "./views/LogsView";
import { TokensView } from "./views/TokensView";
import { InventoryView } from "./views/InventoryView";
import { PipelineView } from "./views/PipelineView";
import { FlowWorkspaceView } from "./views/FlowWorkspaceView";
import { PayPalExtractView } from "./views/PayPalExtractView";
import { MomoView } from "./views/MomoView";
import { GrokView } from "./views/GrokView";
import { PixView } from "./views/PixView";
import { BranchConfigView } from "./views/BranchConfigView";
import { DirectView } from "./views/DirectView";
import { PayPalView } from "./views/PayPalView";
import { DirectPayView } from "./views/DirectPayView";
import FreeppAccountPicker from "./FreeppAccountPicker";

function ProjectView({ view }: { view: ViewName }) {
  switch (view) {
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

function ViewBody({ view }: { view: ViewName }) {
  switch (view) {
    case "workspace":
      return <FlowWorkspaceView />;
    case "chains":
      return <ChainsView />;
    case "logs":
      return <LogsView />;
    case "tokens":
      return <TokensView />;
    case "inventory":
      return <InventoryView />;
    case "pipeline":
      return <PipelineView />;
    default:
      return <ProjectView view={view} />;
  }
}

function FreeppRuntimeBridge() {
  const load = useCallback(async () => {
    try {
      const result = await apiFetch("/sunny/proxy-config/pool?page=1&page_size=100&status=enabled");
      const items = Array.isArray(result?.items) ? result.items : [];
      const proxies = items.map((item: Record<string, any>) => ({
        id: String(item.id ?? item.proxy_id ?? ""),
        address: String(item.address || item.proxy || item.url || "").trim(),
        country: String(item.country || item.country_code || "").trim().toUpperCase(),
        purposeTags: Array.isArray(item.purpose_tags) ? item.purpose_tags.map(String).map((value) => value.toLowerCase()) :
          String(item.purpose || "payment_probe").split(/[,\s]+/).filter(Boolean).map((value) => value.toLowerCase()),
        status: String(item.status || item.status_key || "enabled"),
        enabled: item.enabled !== false && String(item.status || item.status_key || "enabled") !== "disabled",
        latencyMs: Number(item.latency_ms || 0) || undefined,
      })).filter((item: { address: string }) => item.address);
      setRuntimeProxies(proxies);
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => { void load(); }, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);
  return null;
}

/**
 * The local chain workspace is embedded as a feature inside SunnyRegister.
 * It uses the SunnyRegister API and account/proxy stores directly.
 */
export default function FreeppWorkbench() {
  useWebSocket();
  const view = useStore((state) => state.currentView);
  const setView = useStore((state) => state.setView);
  const setWorkspaceProject = useStore((state) => state.setWorkspaceProject);

  const isWorkspace = view === "workspace";
  const goBack = () => {
    setWorkspaceProject(null);
    setView("workspace");
  };

  return (
    <div className="freepp-integrated-shell">
      <FreeppRuntimeBridge />
      <div className="freepp-integrated-content">
        {!isWorkspace && (
          <div className="freepp-integrated-nav">
            <button type="button" className="btn btn-ghost" onClick={goBack}>
              <span aria-hidden="true">←</span>
              返回链路工作台
            </button>
            <span className="freepp-integrated-view-label">链路工作台 / {view}</span>
          </div>
        )}
        <ViewBody view={view} />
        <FreeppAccountPicker />
      </div>
    </div>
  );
}
