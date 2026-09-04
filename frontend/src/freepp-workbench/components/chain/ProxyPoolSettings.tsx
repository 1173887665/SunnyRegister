import { useEffect, useState } from "react";
import type { BranchCfg, BranchName } from "../../types";
import { CountrySelect } from "./StageSettings";

type CountryOption = { code: string; capital?: string };

/**
 * Project-owned proxy pools for the checkout and promotion routes.
 * Countries are explicit labels only; the worker never probes an exit IP to
 * replace them. Repeated proxy URLs are preserved for rotating gateways.
 */
export function ProxyPoolSettings({
  branchName,
  branch,
  countries,
  onSave,
  saving,
}: {
  branchName: BranchName;
  branch: BranchCfg;
  countries: CountryOption[];
  onSave: (patch: Partial<BranchCfg>) => void | Promise<void>;
  saving?: boolean;
}) {
  const fallbackCheckoutCountry = String(branch.stages?.checkout?.countries?.[0] || "US").toUpperCase();
  const fallbackPromotionCountry = String(
    branch.stages?.update?.countries?.[0] || branch.stages?.init?.countries?.[0] || fallbackCheckoutCountry,
  ).toUpperCase();
  const [checkoutPool, setCheckoutPool] = useState(branch.checkout_proxies || "");
  const [promotionPool, setPromotionPool] = useState(branch.promotion_proxies || "");
  const [checkoutCountry, setCheckoutCountry] = useState(branch.checkout_proxy_country || fallbackCheckoutCountry);
  const [promotionCountry, setPromotionCountry] = useState(branch.promotion_proxy_country || fallbackPromotionCountry);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setCheckoutPool(branch.checkout_proxies || "");
    setPromotionPool(branch.promotion_proxies || "");
    setCheckoutCountry(branch.checkout_proxy_country || fallbackCheckoutCountry);
    setPromotionCountry(branch.promotion_proxy_country || fallbackPromotionCountry);
  }, [branch.checkout_proxies, branch.promotion_proxies, branch.checkout_proxy_country, branch.promotion_proxy_country, fallbackCheckoutCountry, fallbackPromotionCountry]);

  const save = async () => {
    setSaved(false);
    await onSave({
      checkout_proxies: checkoutPool,
      promotion_proxies: promotionPool,
      checkout_proxy_country: checkoutCountry.toUpperCase(),
      promotion_proxy_country: promotionCountry.toUpperCase(),
    });
    setSaved(true);
  };

  return (
    <div className="card proxy-pool-settings">
      <div className="card-head">
        <span className="card-title">{branch.label || branchName} · 项目代理池</span>
        <span className="card-hint">按项目固定使用；国家为手动标注，不探测或改写出口 IP</span>
      </div>
      <div className="card-body proxy-pool-grid">
        <ProxyPoolField
          title="Checkout 代理池"
          hint="结账 / 主链路出口 · 每行一个地址"
          value={checkoutPool}
          country={checkoutCountry}
          countries={countries}
          onValueChange={setCheckoutPool}
          onCountryChange={setCheckoutCountry}
        />
        <ProxyPoolField
          title="Promotion 代理池"
          hint="优惠 / 更新链路出口 · 每行一个地址"
          value={promotionPool}
          country={promotionCountry}
          countries={countries}
          onValueChange={setPromotionPool}
          onCountryChange={setPromotionCountry}
        />
      </div>
      <div className="card-body" style={{ borderTop: "1px solid var(--border-faint)", display: "flex", alignItems: "center", gap: 10 }}>
        <button className="btn btn-primary" type="button" onClick={() => void save()} disabled={Boolean(saving)}>
          {saving ? "保存中…" : "保存代理池"}
        </button>
        {saved && <span className="muted" style={{ fontSize: 12 }}>已保存</span>}
      </div>
    </div>
  );
}

function ProxyPoolField({
  title,
  hint,
  value,
  country,
  countries,
  onValueChange,
  onCountryChange,
}: {
  title: string;
  hint: string;
  value: string;
  country: string;
  countries: CountryOption[];
  onValueChange: (value: string) => void;
  onCountryChange: (value: string) => void;
}) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8, marginBottom: 7 }}>
        <strong style={{ fontSize: 13 }}>{title}</strong>
        <span className="muted" style={{ fontSize: 11 }}>{hint}</span>
      </div>
      <textarea
        className="textarea"
        rows={5}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        placeholder="http://user:pass@host:port"
        spellCheck={false}
        style={{ width: "100%", resize: "vertical", minHeight: 112 }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
        <span className="muted" style={{ fontSize: 11, whiteSpace: "nowrap" }}>代理国家</span>
        <CountrySelect
          value={[country || "US"]}
          options={countries}
          onChange={(value) => onCountryChange(value[0] || "US")}
          autoLabel="请选择国家"
          includeAuto={false}
        />
      </div>
    </div>
  );
}
