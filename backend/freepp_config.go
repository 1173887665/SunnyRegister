package main

import (
	"net/http"
	"sort"
	"strings"
)

const freePPChainConfigKey = "freepp_chain_config_json"

var freePPBranchDefaults = map[string]struct {
	Label    string
	Channel  string
	Country  string
	Currency string
}{
	"paypal":    {"PayPal 提炼", "paypal", "US", "USD"},
	"momo":      {"MoMo 提链", "momo", "VN", "VND"},
	"grok":      {"Grok 链路", "card", "US", "USD"},
	"pix":       {"PIX 二维码", "pix", "BR", "BRL"},
	"ideal":     {"iDEAL 提链", "ideal", "NL", "EUR"},
	"upi":       {"UPI 提链", "upi", "IN", "INR"},
	"kakao":     {"Kakao Pay 提链", "kakao", "KR", "KRW"},
	"blik":      {"BLIK 提链", "blik", "PL", "PLN"},
	"twint":     {"TWINT 提链", "twint", "CH", "CHF"},
	"direct":    {"直卡提链", "card", "PH", "PHP"},
	"bizum":     {"Bizum 提链", "bizum", "ES", "EUR"},
	"gopay":     {"GoPay 提链", "gopay", "ID", "IDR"},
	"naver_pay": {"Naver Pay 提链", "naver_pay", "KR", "KRW"},
	"gcash":     {"GCash 提链", "gcash", "PH", "PHP"},
	"grabpay":   {"GrabPay 提链", "grabpay", "PH", "PHP"},
	"qris":      {"QRIS 提链", "qris", "ID", "IDR"},
}

var freePPStageDefaults = map[string]map[string]any{
	"checkout": {"timeout": 15, "retry": 3},
	"init":     {"timeout": 10, "retry": 3},
	"update":   {"timeout": 10, "retry": 3},
	"provider": {"timeout": 8, "retry": 3},
	"approve":  {"timeout": 6, "retry": 3},
	"poll":     {"timeout": 25, "retry": 1, "poll_interval": 0.75, "max_polls": 40},
	"resolve":  {"timeout": 20, "retry": 2},
}

func cloneAnyMap(value map[string]any) map[string]any {
	out := map[string]any{}
	for key, item := range value {
		if nested, ok := item.(map[string]any); ok {
			out[key] = cloneAnyMap(nested)
			continue
		}
		if list, ok := item.([]any); ok {
			out[key] = append([]any{}, list...)
			continue
		}
		out[key] = item
	}
	return out
}

func defaultFreePPBranches() map[string]any {
	branches := map[string]any{}
	for name, spec := range freePPBranchDefaults {
		stages := map[string]any{}
		for stage, defaults := range freePPStageDefaults {
			row := cloneAnyMap(defaults)
			row["countries"] = []any{spec.Country}
			stages[stage] = row
		}
		branches[name] = map[string]any{
			"name": name, "label": spec.Label, "channel": spec.Channel, "token_source": name,
			"checkout_proxies": "", "promotion_proxies": "",
			"checkout_proxy_country": spec.Country, "promotion_proxy_country": spec.Country,
			"require_zero": true, "channel_check": true, "dual_init": true,
			"init0_ccs": []any{spec.Country}, "init1_ccs": []any{spec.Country}, "init_t_ccs": []any{spec.Country},
			"follow_checkout": true, "billing_country": spec.Country, "billing_currency": spec.Currency,
			"attempts": 3, "stages": stages,
		}
	}
	return branches
}

func mergeAnyMap(target, patch map[string]any) map[string]any {
	for key, value := range patch {
		if nestedPatch, ok := value.(map[string]any); ok {
			nestedTarget, _ := target[key].(map[string]any)
			if nestedTarget == nil {
				nestedTarget = map[string]any{}
			}
			target[key] = mergeAnyMap(nestedTarget, nestedPatch)
			continue
		}
		target[key] = value
	}
	return target
}

func (s *Server) freePPBranches() map[string]any {
	branches := defaultFreePPBranches()
	var item ConfigItem
	if s.db.First(&item, "key = ?", freePPChainConfigKey).Error == nil {
		for name, saved := range jsonMap(item.Value) {
			if _, known := freePPBranchDefaults[name]; !known {
				continue
			}
			base, _ := branches[name].(map[string]any)
			patch, _ := saved.(map[string]any)
			branches[name] = mergeAnyMap(base, patch)
		}
	}
	return branches
}

func (s *Server) saveFreePPBranches(branches map[string]any) {
	s.db.Save(&ConfigItem{Key: freePPChainConfigKey, Value: dumpJSON(branches)})
}

func freePPBillingTemplates() []map[string]any {
	byCountry := map[string]string{}
	for _, spec := range freePPBranchDefaults {
		byCountry[spec.Country] = spec.Currency
	}
	for country, currency := range map[string]string{"AU": "AUD", "CA": "CAD", "GB": "GBP", "JP": "JPY", "MX": "MXN", "TH": "THB", "TW": "TWD", "AE": "AED"} {
		byCountry[country] = currency
	}
	countries := make([]string, 0, len(byCountry))
	for country := range byCountry {
		countries = append(countries, country)
	}
	sort.Strings(countries)
	rows := make([]map[string]any, 0, len(countries))
	for _, country := range countries {
		rows = append(rows, map[string]any{"country": country, "currency": byCountry[country], "city": country})
	}
	return rows
}

func (s *Server) handleFreePPConfig(w http.ResponseWriter, r *http.Request, rest string) {
	rest = "/" + strings.Trim(strings.TrimSpace(rest), "/")
	if rest == "/config" && r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "chain": map[string]any{"branches": s.freePPBranches()}})
		return
	}
	if rest == "/config/branch" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		name := strings.ToLower(strings.TrimSpace(text(body["branch"])))
		if _, known := freePPBranchDefaults[name]; !known {
			writeError(w, http.StatusBadRequest, "未知提链项目")
			return
		}
		delete(body, "branch")
		branches := s.freePPBranches()
		current, _ := branches[name].(map[string]any)
		branches[name] = mergeAnyMap(current, body)
		s.saveFreePPBranches(branches)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "branch": branches[name]})
		return
	}
	if rest == "/billing/templates" && r.Method == http.MethodGet {
		rows := freePPBillingTemplates()
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "templates": rows, "total": len(rows)})
		return
	}
	writeError(w, http.StatusNotFound, "not found")
}
