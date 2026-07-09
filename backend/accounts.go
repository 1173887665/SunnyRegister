package main

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"gorm.io/gorm"
)

type AccountRecord struct {
	ID                uint             `json:"id"`
	Platform          string           `json:"platform"`
	Email             string           `json:"email"`
	Password          string           `json:"password"`
	UserID            string           `json:"user_id"`
	PrimaryToken      string           `json:"primary_token"`
	TrialEndTime      int              `json:"trial_end_time"`
	CashierURL        string           `json:"cashier_url"`
	LifecycleStatus   string           `json:"lifecycle_status"`
	ValidityStatus    string           `json:"validity_status"`
	PlanState         string           `json:"plan_state"`
	PlanName          string           `json:"plan_name"`
	DisplayStatus     string           `json:"display_status"`
	Overview          map[string]any   `json:"overview"`
	DisplaySummary    map[string]any   `json:"display_summary"`
	Credentials       []map[string]any `json:"credentials"`
	ProviderAccounts  []map[string]any `json:"provider_accounts"`
	ProviderResources []map[string]any `json:"provider_resources"`
	CreatedAt         string           `json:"created_at"`
	UpdatedAt         string           `json:"updated_at"`
}

var tokenKeyPriority = map[string][]string{
	"cursor":   {"session_token", "sessionToken", "legacy_token"},
	"chatgpt":  {"access_token", "accessToken", "legacy_token", "session_token", "sessionToken"},
	"kiro":     {"accessToken", "access_token", "legacy_token", "sessionToken", "session_token"},
	"trae":     {"legacy_token", "access_token", "accessToken"},
	"blink":    {"firebase_refresh_token", "legacy_token", "refresh_token", "access_token", "session_token"},
	"windsurf": {"session_token", "sessionToken", "legacy_token", "auth_token", "authToken"},
}

var primaryWriteKey = map[string]string{
	"cursor":        "session_token",
	"chatgpt":       "access_token",
	"kiro":          "accessToken",
	"trae":          "legacy_token",
	"blink":         "firebase_refresh_token",
	"openblocklabs": "wos_session",
	"gopay":         "legacy_token",
	"grok":          "sso",
	"windsurf":      "session_token",
}

func inferCredentialType(key string) string {
	if strings.Contains(strings.ToLower(key), "cookie") || key == "sso" || key == "sso_rw" {
		return "cookie"
	}
	lower := strings.ToLower(key)
	if strings.Contains(lower, "token") || key == "legacy_token" || key == "wos_session" {
		return "token"
	}
	if strings.Contains(lower, "secret") || strings.Contains(lower, "key") || strings.Contains(lower, "password") {
		return "secret"
	}
	if strings.Contains(lower, "client") || strings.Contains(lower, "workspace") || strings.HasSuffix(lower, "_id") {
		return "identifier"
	}
	return "credential"
}

func previewSecret(value string) string {
	value = strings.TrimSpace(value)
	if len(value) <= 10 {
		return value
	}
	return value[:6] + "..." + value[len(value)-4:]
}

func normalizePlanState(v string) string {
	raw := strings.ToLower(strings.TrimSpace(v))
	if raw == "" {
		return ""
	}
	switch raw {
	case "trial", "trialing", "free_trial", "trial-active", "trial_active":
		return "trial"
	case "expired", "cancelled", "canceled", "inactive", "ended":
		return "expired"
	case "free", "basic", "starter", "hobby":
		return "free"
	case "eligible", "trial_eligible":
		return "eligible"
	}
	for _, hint := range []string{"pro", "plus", "premium", "paid", "student", "team", "business", "enterprise", "member"} {
		if strings.Contains(raw, hint) {
			return "subscribed"
		}
	}
	return raw
}

func deriveStatuses(platform, lifecycle string, overview map[string]any) map[string]string {
	if lifecycle == "" {
		lifecycle = text(overview["lifecycle_status"])
	}
	if lifecycle == "" {
		lifecycle = "registered"
	}
	validity := "unknown"
	if lifecycle == "invalid" {
		validity = "invalid"
	} else if _, ok := overview["valid"]; ok {
		if boolValue(overview["valid"], false) {
			validity = "valid"
		} else {
			validity = "invalid"
		}
	}
	planName := text(overview["plan_name"])
	if planName == "" {
		planName = text(overview["plan"])
	}
	if planName == "" {
		planName = text(overview["membership_type"])
	}
	planState := normalizePlanState(text(overview["plan_state"]))
	if planState == "" {
		planState = normalizePlanState(text(overview["membership_type"]))
	}
	if planState == "" {
		planState = normalizePlanState(text(overview["plan"]))
	}
	if planState == "" && (lifecycle == "trial" || lifecycle == "subscribed" || lifecycle == "expired") {
		planState = lifecycle
	}
	if planState == "" {
		planState = "unknown"
	}
	display := lifecycle
	if validity == "invalid" {
		display = "invalid"
	} else if planState == "expired" || lifecycle == "expired" {
		display = "expired"
	} else if planState == "subscribed" {
		display = "subscribed"
	} else if planState == "trial" {
		display = "trial"
	}
	if display == "" {
		display = "registered"
	}
	return map[string]string{
		"lifecycle_status": lifecycle,
		"validity_status":  validity,
		"plan_state":       planState,
		"plan_name":        planName,
		"display_status":   display,
		"platform":         platform,
	}
}

func loadAccountGraphs(db *gorm.DB, ids []uint) map[uint]map[string]any {
	graphs := map[uint]map[string]any{}
	for _, id := range ids {
		graphs[id] = map[string]any{
			"overview":           map[string]any{},
			"credentials":        []map[string]any{},
			"provider_accounts":  []map[string]any{},
			"provider_resources": []map[string]any{},
		}
	}
	if len(ids) == 0 {
		return graphs
	}
	var ovs []AccountOverview
	db.Where("account_id IN ?", ids).Find(&ovs)
	for _, ov := range ovs {
		summary := jsonMap(ov.SummaryJSON)
		summary["lifecycle_status"] = ov.LifecycleStatus
		summary["validity_status"] = ov.ValidityStatus
		summary["plan_state"] = ov.PlanState
		summary["plan_name"] = ov.PlanName
		summary["display_status"] = ov.DisplayStatus
		summary["remote_email"] = ov.RemoteEmail
		summary["checked_at"] = nullableTime(ov.CheckedAt.Valid, ov.CheckedAt.Time)
		graphs[ov.AccountID]["overview"] = summary
		graphs[ov.AccountID]["lifecycle_status"] = ov.LifecycleStatus
		graphs[ov.AccountID]["validity_status"] = ov.ValidityStatus
		graphs[ov.AccountID]["plan_state"] = ov.PlanState
		graphs[ov.AccountID]["plan_name"] = ov.PlanName
		graphs[ov.AccountID]["display_status"] = ov.DisplayStatus
	}
	var creds []AccountCredential
	db.Where("account_id IN ?", ids).Find(&creds)
	for _, c := range creds {
		arr := graphs[c.AccountID]["credentials"].([]map[string]any)
		arr = append(arr, map[string]any{
			"id":              c.ID,
			"scope":           c.Scope,
			"provider_name":   c.ProviderName,
			"credential_type": c.CredentialType,
			"key":             c.Key,
			"value":           c.Value,
			"preview":         previewSecret(c.Value),
			"is_primary":      c.IsPrimary,
			"source":          c.Source,
			"metadata":        jsonMap(c.MetadataJSON),
		})
		graphs[c.AccountID]["credentials"] = arr
	}
	var pas []ProviderAccount
	db.Where("account_id IN ?", ids).Find(&pas)
	for _, p := range pas {
		credsMap := jsonMap(p.CredentialsJSON)
		previews := map[string]string{}
		for k, v := range credsMap {
			previews[k] = previewSecret(text(v))
		}
		arr := graphs[p.AccountID]["provider_accounts"].([]map[string]any)
		arr = append(arr, map[string]any{
			"id":                  p.ID,
			"provider_type":       p.ProviderType,
			"provider_name":       p.ProviderName,
			"login_identifier":    p.LoginIdentifier,
			"display_name":        p.DisplayName,
			"credentials":         credsMap,
			"credential_previews": previews,
			"metadata":            jsonMap(p.MetadataJSON),
		})
		graphs[p.AccountID]["provider_accounts"] = arr
	}
	var prs []ProviderResource
	db.Where("account_id IN ?", ids).Find(&prs)
	for _, p := range prs {
		arr := graphs[p.AccountID]["provider_resources"].([]map[string]any)
		item := map[string]any{
			"id":                  p.ID,
			"provider_type":       p.ProviderType,
			"provider_name":       p.ProviderName,
			"resource_type":       p.ResourceType,
			"resource_identifier": p.ResourceIdentifier,
			"handle":              p.Handle,
			"display_name":        p.DisplayName,
			"metadata":            jsonMap(p.MetadataJSON),
		}
		arr = append(arr, item)
		graphs[p.AccountID]["provider_resources"] = arr
		if p.ResourceType == "mailbox" && graphs[p.AccountID]["verification_mailbox"] == nil {
			graphs[p.AccountID]["verification_mailbox"] = item
		}
	}
	for id, g := range graphs {
		ov, _ := g["overview"].(map[string]any)
		if text(g["lifecycle_status"]) == "" {
			status := deriveStatuses("", "registered", ov)
			for k, v := range status {
				g[k] = v
			}
			graphs[id] = g
		}
	}
	return graphs
}

func resolvePrimaryToken(platform string, graph map[string]any) string {
	credentials, _ := graph["credentials"].([]map[string]any)
	keys := tokenKeyPriority[platform]
	if len(keys) == 0 {
		keys = []string{"access_token", "accessToken", "session_token", "sessionToken", "legacy_token"}
	}
	for _, key := range keys {
		for _, item := range credentials {
			if text(item["key"]) == key && text(item["value"]) != "" {
				return text(item["value"])
			}
		}
	}
	for _, item := range credentials {
		if text(item["credential_type"]) == "token" && text(item["value"]) != "" {
			return text(item["value"])
		}
	}
	return ""
}

func displaySummary(account Account, graph map[string]any) map[string]any {
	overview, _ := graph["overview"].(map[string]any)
	lifecycle := text(graph["lifecycle_status"])
	validity := text(graph["validity_status"])
	planState := text(graph["plan_state"])
	planName := text(graph["plan_name"])
	display := text(graph["display_status"])
	checked := text(overview["checked_at"])
	primary := []map[string]any{}
	secondary := []map[string]any{}
	if planName != "" {
		secondary = append(secondary, map[string]any{"key": "plan_name", "label": "套餐", "value": planName, "tone": "muted"})
	}
	if planState != "" && planState != "unknown" {
		secondary = append(secondary, map[string]any{"key": "plan_state", "label": "套餐状态", "value": planState, "tone": "muted"})
	}
	if checked != "" {
		secondary = append(secondary, map[string]any{"key": "checked_at", "label": "最近检测", "value": checked, "tone": "muted"})
	}
	for _, key := range []string{"remaining_credits", "usage_total"} {
		if text(overview[key]) != "" {
			primary = append(primary, map[string]any{"key": key, "label": key, "value": text(overview[key]), "tone": "good"})
		}
	}
	warnings := []map[string]any{}
	if validity == "invalid" || lifecycle == "invalid" {
		warnings = append(warnings, map[string]any{"key": "invalid", "tone": "danger", "message": "账号当前检测为失效"})
	}
	if validity == "unknown" {
		warnings = append(warnings, map[string]any{"key": "unknown_validity", "tone": "warning", "message": "尚未完成有效性检测"})
	}
	badges := []map[string]any{}
	if chips, ok := overview["chips"].([]any); ok {
		for _, chip := range chips {
			if text(chip) != "" {
				badges = append(badges, map[string]any{"label": text(chip), "tone": "muted"})
			}
		}
	}
	if resources, ok := graph["provider_resources"].([]map[string]any); ok {
		for _, resource := range resources {
			if text(resource["resource_type"]) == "mailbox" && (text(resource["handle"]) != "" || text(resource["display_name"]) != "") {
				badges = append(badges, map[string]any{"label": "閭楠岃瘉", "tone": "muted"})
				break
			}
		}
	}
	return map[string]any{
		"identity":          map[string]any{"email": account.Email, "remote_email": text(overview["remote_email"]), "platform": account.Platform},
		"status":            map[string]any{"display": display, "lifecycle": lifecycle, "validity": validity, "plan_state": planState, "plan_name": planName, "checked_at": checked},
		"primary_metrics":   primary,
		"secondary_metrics": secondary,
		"badges":            badges,
		"warnings":          warnings,
		"sections":          []map[string]any{},
	}
}

func toAccountRecord(a Account, graph map[string]any) AccountRecord {
	overview, _ := graph["overview"].(map[string]any)
	if overview == nil {
		overview = map[string]any{}
	}
	creds, _ := graph["credentials"].([]map[string]any)
	pas, _ := graph["provider_accounts"].([]map[string]any)
	prs, _ := graph["provider_resources"].([]map[string]any)
	return AccountRecord{
		ID:                a.ID,
		Platform:          a.Platform,
		Email:             a.Email,
		Password:          a.Password,
		UserID:            a.UserID,
		PrimaryToken:      resolvePrimaryToken(a.Platform, graph),
		TrialEndTime:      intValue(overview["trial_end_time"], 0),
		CashierURL:        text(overview["cashier_url"]),
		LifecycleStatus:   text(graph["lifecycle_status"]),
		ValidityStatus:    text(graph["validity_status"]),
		PlanState:         text(graph["plan_state"]),
		PlanName:          text(graph["plan_name"]),
		DisplayStatus:     text(graph["display_status"]),
		Overview:          overview,
		DisplaySummary:    displaySummary(a, graph),
		Credentials:       creds,
		ProviderAccounts:  pas,
		ProviderResources: prs,
		CreatedAt:         formatTime(a.CreatedAt),
		UpdatedAt:         formatTime(a.UpdatedAt),
	}
}

func persistGraph(db *gorm.DB, a *Account, lifecycle string, summary map[string]any, credentialUpdates map[string]any, primaryToken string, providerAccounts []map[string]any, providerResources []map[string]any, replacePA, replacePR bool) {
	var current AccountOverview
	db.Where("account_id = ?", a.ID).First(&current)
	base := jsonMap(current.SummaryJSON)
	for k, v := range summary {
		base[k] = v
	}
	status := deriveStatuses(a.Platform, lifecycle, base)
	for k, v := range status {
		base[k] = v
	}
	ov := AccountOverview{
		AccountID:       a.ID,
		LifecycleStatus: status["lifecycle_status"],
		ValidityStatus:  status["validity_status"],
		PlanState:       status["plan_state"],
		PlanName:        status["plan_name"],
		DisplayStatus:   status["display_status"],
		RemoteEmail:     text(base["remote_email"]),
		SummaryJSON:     dumpJSON(base),
	}
	if current.AccountID != 0 {
		ov.CreatedAt = current.CreatedAt
		ov.CheckedAt = current.CheckedAt
	}
	db.Save(&ov)

	existing := []AccountCredential{}
	db.Where("account_id = ? AND scope = ?", a.ID, "platform").Find(&existing)
	merged := map[string]AccountCredential{}
	for _, c := range existing {
		merged[c.Key] = c
	}
	for k, v := range credentialUpdates {
		if text(v) == "" {
			continue
		}
		merged[k] = AccountCredential{AccountID: a.ID, Scope: "platform", ProviderName: a.Platform, CredentialType: inferCredentialType(k), Key: k, Value: text(v), Source: "runtime.patch", MetadataJSON: "{}"}
	}
	if primaryToken != "" {
		key := primaryWriteKey[a.Platform]
		if key == "" {
			key = "legacy_token"
		}
		merged[key] = AccountCredential{AccountID: a.ID, Scope: "platform", ProviderName: a.Platform, CredentialType: "token", Key: key, Value: primaryToken, IsPrimary: true, Source: "accounts.api", MetadataJSON: "{}"}
	}
	if len(merged) > 0 {
		db.Where("account_id = ? AND scope = ?", a.ID, "platform").Delete(&AccountCredential{})
		keys := make([]string, 0, len(merged))
		for key := range merged {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		primary := ""
		for _, key := range keys {
			if merged[key].IsPrimary {
				primary = key
				break
			}
		}
		if primary == "" {
			preferred := primaryWriteKey[a.Platform]
			if _, ok := merged[preferred]; ok {
				primary = preferred
			} else {
				for _, key := range keys {
					if merged[key].CredentialType == "token" {
						primary = key
						break
					}
				}
			}
		}
		for _, key := range keys {
			c := merged[key]
			c.ID = 0
			c.IsPrimary = key == primary
			if c.MetadataJSON == "" {
				c.MetadataJSON = "{}"
			}
			db.Create(&c)
		}
	}
	if providerAccounts != nil {
		if replacePA {
			db.Where("account_id = ?", a.ID).Delete(&ProviderAccount{})
		}
		for _, item := range providerAccounts {
			db.Create(&ProviderAccount{
				AccountID: a.ID, ProviderType: fallback(text(item["provider_type"]), "mailbox"), ProviderName: text(item["provider_name"]),
				LoginIdentifier: text(item["login_identifier"]), DisplayName: text(item["display_name"]),
				CredentialsJSON: dumpJSON(item["credentials"]), MetadataJSON: dumpJSON(item["metadata"]),
			})
		}
	}
	if providerResources != nil {
		if replacePR {
			db.Where("account_id = ?", a.ID).Delete(&ProviderResource{})
		}
		for _, item := range providerResources {
			db.Create(&ProviderResource{
				AccountID: a.ID, ProviderType: fallback(text(item["provider_type"]), "mailbox"), ProviderName: text(item["provider_name"]),
				ResourceType: fallback(text(item["resource_type"]), "resource"), ResourceIdentifier: text(item["resource_identifier"]),
				Handle: text(item["handle"]), DisplayName: text(item["display_name"]), MetadataJSON: dumpJSON(item["metadata"]),
			})
		}
	}
}

func fallback(v, d string) string {
	if v == "" {
		return d
	}
	return v
}

func matchStatus(rec AccountRecord, status string) bool {
	status = strings.TrimSpace(status)
	if status == "" {
		return true
	}
	return status == rec.DisplayStatus || status == rec.LifecycleStatus || status == rec.PlanState || status == rec.ValidityStatus
}

func (s *Server) listAccountRecords(platform, status, email string, page, pageSize int) (int, []AccountRecord) {
	if page < 1 {
		page = 1
	}
	if pageSize < 1 {
		pageSize = 20
	}
	var accounts []Account
	q := s.db.Order("created_at DESC, id DESC")
	if platform != "" {
		q = q.Where("platform = ?", platform)
	}
	if email != "" {
		q = q.Where("email LIKE ?", "%"+email+"%")
	}
	q.Find(&accounts)
	ids := make([]uint, 0, len(accounts))
	for _, a := range accounts {
		ids = append(ids, a.ID)
	}
	graphs := loadAccountGraphs(s.db, ids)
	all := []AccountRecord{}
	for _, a := range accounts {
		rec := toAccountRecord(a, graphs[a.ID])
		if matchStatus(rec, status) {
			all = append(all, rec)
		}
	}
	total := len(all)
	start := (page - 1) * pageSize
	if start >= total {
		return total, []AccountRecord{}
	}
	end := start + pageSize
	if end > total {
		end = total
	}
	return total, all[start:end]
}

func (s *Server) handleAccounts(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "" {
		switch r.Method {
		case http.MethodGet:
			q := r.URL.Query()
			page := intValue(q.Get("page"), 1)
			size := intValue(q.Get("page_size"), 20)
			total, items := s.listAccountRecords(q.Get("platform"), q.Get("status"), q.Get("email"), page, size)
			writeJSON(w, 200, map[string]any{"total": total, "page": page, "items": items})
		case http.MethodPost:
			body, err := parseBody(r)
			if err != nil {
				writeError(w, 400, err.Error())
				return
			}
			a := Account{Platform: text(body["platform"]), Email: text(body["email"]), Password: text(body["password"]), UserID: text(body["user_id"])}
			if a.Platform == "" || a.Email == "" {
				writeError(w, 400, "platform/email 涓嶈兘涓虹┖")
				return
			}
			s.db.Create(&a)
			summary := map[string]any{}
			for k, v := range mapFromAny(body["overview"]) {
				summary[k] = v
			}
			if text(body["cashier_url"]) != "" {
				summary["cashier_url"] = text(body["cashier_url"])
			}
			if text(body["region"]) != "" {
				summary["region"] = text(body["region"])
			}
			if intValue(body["trial_end_time"], 0) > 0 {
				summary["trial_end_time"] = intValue(body["trial_end_time"], 0)
			}
			persistGraph(s.db, &a, fallback(text(body["lifecycle_status"]), "registered"), summary, mapFromAny(body["credentials"]), text(body["primary_token"]), listMapFromAny(body["provider_accounts"]), listMapFromAny(body["provider_resources"]), true, true)
			graph := loadAccountGraphs(s.db, []uint{a.ID})[a.ID]
			writeJSON(w, 200, toAccountRecord(a, graph))
		default:
			writeError(w, 405, "method not allowed")
		}
		return
	}
	if rest == "/check-all" && r.Method == http.MethodPost {
		platform := r.URL.Query().Get("platform")
		_, items := s.listAccountRecords(platform, "", "", 1, 1000000)
		task := s.createTask("account_check_all", platform, map[string]any{"platform": platform}, len(items))
		writeJSON(w, 200, map[string]any{"ok": true, "task_id": task.ID, "task": serializeTask(task)})
		return
	}
	if rest == "/refresh-plan" && r.Method == http.MethodPost {
		platform := r.URL.Query().Get("platform")
		_, items := s.listAccountRecords(platform, "", "", 1, 1000000)
		updated := 0
		for _, rec := range items {
			var a Account
			if s.db.First(&a, rec.ID).Error == nil {
				persistGraph(s.db, &a, "", map[string]any{"checked_at": time.Now().Format(time.RFC3339), "plan_refresh_note": "Go 迁移版已刷新本地概览"}, nil, "", nil, nil, false, false)
				updated++
			}
		}
		writeJSON(w, 200, map[string]any{"ok": true, "updated": updated})
		return
	}
	if rest == "/stats" && r.Method == http.MethodGet {
		s.handleAccountStats(w, r)
		return
	}
	if rest == "/export" && r.Method == http.MethodGet {
		q := r.URL.Query()
		_, items := s.listAccountRecords(q.Get("platform"), q.Get("status"), "", 1, 100000)
		writeTextFile(w, "accounts.csv", "text/csv; charset=utf-8", []byte(recordsCSV(items)))
		return
	}
	if strings.HasPrefix(rest, "/export/") && r.Method == http.MethodPost {
		format := strings.TrimPrefix(rest, "/export/")
		s.handleBatchExport(w, r, format)
		return
	}
	if rest == "/import" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		platform := text(body["platform"])
		lines := stringSlice(body["lines"])
		created := 0
		var header []string
		for _, line := range lines {
			raw := strings.TrimSpace(line)
			if raw == "" {
				continue
			}
			if header == nil && strings.Contains(raw, ",") {
				row, err := csvLine(raw)
				if err == nil {
					lower := []string{}
					for _, h := range row {
						lower = append(lower, strings.ToLower(strings.TrimSpace(h)))
					}
					if contains(lower, "email") && contains(lower, "password") {
						header = lower
						continue
					}
				}
			}
			email, password, extraRaw, ok := splitImportLine(raw)
			if header != nil {
				row, err := csvLine(raw)
				if err == nil {
					m := map[string]string{}
					for i := range row {
						if i < len(header) {
							m[header[i]] = row[i]
						}
					}
					if m["email"] != "" && m["password"] != "" {
						email, password, extraRaw, ok = strings.TrimSpace(m["email"]), m["password"], "", true
						if m["cashier_url"] != "" {
							extraRaw = dumpJSON(map[string]any{"cashier_url": m["cashier_url"]})
						}
					}
				}
			}
			if !ok || email == "" || password == "" {
				continue
			}
			a := Account{Platform: platform, Email: email, Password: password}
			s.db.Create(&a)
			extra := map[string]any{}
			if strings.TrimSpace(extraRaw) != "" {
				if json.Unmarshal([]byte(extraRaw), &extra) != nil {
					extra = map[string]any{"cashier_url": decodeImportToken(extraRaw)}
				}
			}
			creds := map[string]any{}
			for _, key := range []string{"access_token", "refresh_token", "session_token", "id_token", "accessToken", "refreshToken", "sessionToken", "idToken", "cookies", "cookie", "api_key", "wos_session", "sso", "sso_rw"} {
				if extra[key] != nil {
					creds[key] = extra[key]
				}
			}
			persistGraph(s.db, &a, fallback(text(extra["lifecycle_status"]), fallback(text(extra["status"]), "registered")), extra, creds, fallback(text(extra["primary_token"]), text(extra["token"])), listMapFromAny(extra["provider_accounts"]), listMapFromAny(extra["provider_resources"]), true, true)
			created++
		}
		writeJSON(w, 200, map[string]any{"created": created})
		return
	}
	if rest == "/phone-bind" && r.Method == http.MethodPost {
		s.createSimpleTask(w, r, "phone_bind", "chatgpt")
		return
	}
	if rest == "/ctf-gpt-plus/export-status" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		ids := uintSlice(body["ids"])
		exported := boolValue(body["exported"], true)
		for _, id := range ids {
			var a Account
			if s.db.First(&a, id).Error == nil {
				persistGraph(s.db, &a, "", map[string]any{"ctf_gpt_plus_exported": exported, "exported": exported}, nil, "", nil, nil, false, false)
			}
		}
		writeJSON(w, 200, map[string]any{"ok": true, "updated": len(ids)})
		return
	}
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) >= 1 {
		id64 := intValue(parts[0], 0)
		if id64 <= 0 {
			writeError(w, 404, "账号不存在")
			return
		}
		var a Account
		if s.db.First(&a, id64).Error != nil {
			writeError(w, 404, "账号不存在")
			return
		}
		if len(parts) == 3 && parts[1] == "codex-oauth" {
			if parts[2] == "start" && r.Method == http.MethodPost {
				t := s.createTask("codex_oauth", a.Platform, map[string]any{"account_id": a.ID}, 1)
				writeJSON(w, 200, map[string]any{"ok": true, "task_id": t.ID, "auth_url": "", "message": "已创建 OAuth 任务"})
				return
			}
			if parts[2] == "complete" && r.Method == http.MethodPost {
				body, _ := parseBody(r)
				persistGraph(s.db, &a, "", map[string]any{"codex_oauth_callback_url": text(body["callback_url"]), "codex_oauth_completed": true}, nil, "", nil, nil, false, false)
				writeJSON(w, 200, map[string]any{"ok": true})
				return
			}
		}
		switch r.Method {
		case http.MethodGet:
			graph := loadAccountGraphs(s.db, []uint{a.ID})[a.ID]
			writeJSON(w, 200, toAccountRecord(a, graph))
		case http.MethodPatch:
			body, _ := parseBody(r)
			if _, ok := body["password"]; ok {
				a.Password = text(body["password"])
			}
			if _, ok := body["user_id"]; ok {
				a.UserID = text(body["user_id"])
			}
			s.db.Save(&a)
			summary := mapFromAny(body["overview"])
			if _, ok := body["cashier_url"]; ok {
				summary["cashier_url"] = text(body["cashier_url"])
			}
			if _, ok := body["region"]; ok {
				summary["region"] = text(body["region"])
			}
			if _, ok := body["trial_end_time"]; ok {
				summary["trial_end_time"] = intValue(body["trial_end_time"], 0)
			}
			persistGraph(s.db, &a, text(body["lifecycle_status"]), summary, mapFromAny(body["credentials"]), text(body["primary_token"]), listMapPtr(body["provider_accounts"]), listMapPtr(body["provider_resources"]), boolValue(body["replace_provider_accounts"], false), boolValue(body["replace_provider_resources"], false))
			graph := loadAccountGraphs(s.db, []uint{a.ID})[a.ID]
			writeJSON(w, 200, toAccountRecord(a, graph))
		case http.MethodDelete:
			s.db.Where("account_id = ?", a.ID).Delete(&AccountCredential{})
			s.db.Where("account_id = ?", a.ID).Delete(&ProviderResource{})
			s.db.Where("account_id = ?", a.ID).Delete(&ProviderAccount{})
			s.db.Where("account_id = ?", a.ID).Delete(&AccountOverview{})
			s.db.Delete(&a)
			writeJSON(w, 200, map[string]any{"ok": true})
		default:
			writeError(w, 405, "method not allowed")
		}
		return
	}
	writeError(w, 404, "not found")
}

func mapFromAny(v any) map[string]any {
	if v == nil {
		return map[string]any{}
	}
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func listMapFromAny(v any) []map[string]any {
	if v == nil {
		return nil
	}
	if arr, ok := v.([]map[string]any); ok {
		return arr
	}
	out := []map[string]any{}
	if arr, ok := v.([]any); ok {
		for _, item := range arr {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func listMapPtr(v any) []map[string]any {
	if v == nil {
		return nil
	}
	return listMapFromAny(v)
}

func contains(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}

func (s *Server) handleAccountStats(w http.ResponseWriter, r *http.Request) {
	total, items := s.listAccountRecords("", "", "", 1, 1000000)
	byPlatform := map[string]int{}
	byDisplay := map[string]int{}
	byLifecycle := map[string]int{}
	byPlan := map[string]int{}
	byValidity := map[string]int{}
	for _, item := range items {
		byPlatform[item.Platform]++
		byDisplay[fallback(item.DisplayStatus, "registered")]++
		byLifecycle[fallback(item.LifecycleStatus, "registered")]++
		byPlan[fallback(item.PlanState, "unknown")]++
		byValidity[fallback(item.ValidityStatus, "unknown")]++
	}
	writeJSON(w, 200, map[string]any{
		"total": total, "by_platform": byPlatform, "by_status": byDisplay,
		"by_lifecycle_status": byLifecycle, "by_plan_state": byPlan, "by_validity_status": byValidity, "by_display_status": byDisplay,
	})
}

func recordsCSV(items []AccountRecord) string {
	var buf bytes.Buffer
	w := csv.NewWriter(&buf)
	_ = w.Write([]string{"platform", "email", "password", "user_id", "display_status", "lifecycle_status", "plan_state", "validity_status", "cashier_url", "created_at"})
	for _, item := range items {
		_ = w.Write([]string{item.Platform, item.Email, item.Password, item.UserID, item.DisplayStatus, item.LifecycleStatus, item.PlanState, item.ValidityStatus, item.CashierURL, item.CreatedAt})
	}
	w.Flush()
	return buf.String()
}

func (s *Server) selectedAccounts(body map[string]any, defaultPlatform string) []AccountRecord {
	platform := fallback(text(body["platform"]), defaultPlatform)
	ids := uintSlice(body["ids"])
	selectAll := boolValue(body["select_all"], false)
	status := text(body["status_filter"])
	search := text(body["search_filter"])
	if selectAll || len(ids) == 0 {
		_, items := s.listAccountRecords(platform, status, search, 1, 1000000)
		return items
	}
	var accounts []Account
	q := s.db.Where("id IN ?", ids)
	if platform != "" {
		q = q.Where("platform = ?", platform)
	}
	q.Order("created_at DESC, id DESC").Find(&accounts)
	graphIDs := []uint{}
	for _, a := range accounts {
		graphIDs = append(graphIDs, a.ID)
	}
	graphs := loadAccountGraphs(s.db, graphIDs)
	items := []AccountRecord{}
	for _, a := range accounts {
		rec := toAccountRecord(a, graphs[a.ID])
		if matchStatus(rec, status) && (search == "" || strings.Contains(rec.Email, search)) {
			items = append(items, rec)
		}
	}
	return items
}

func credentialValue(item AccountRecord, keys ...string) string {
	for _, key := range keys {
		for _, c := range item.Credentials {
			if text(c["scope"]) == "platform" && text(c["key"]) == key && text(c["value"]) != "" {
				return text(c["value"])
			}
		}
	}
	return ""
}

func chatGPTPayload(item AccountRecord) map[string]any {
	access := credentialValue(item, "access_token", "accessToken", "legacy_token")
	refresh := credentialValue(item, "refresh_token", "refreshToken")
	idToken := credentialValue(item, "id_token", "idToken")
	session := credentialValue(item, "session_token", "sessionToken")
	workspace := credentialValue(item, "workspace_id", "workspaceId")
	payload := decodeJWTPayload(access)
	client := credentialValue(item, "client_id", "clientId")
	if client == "" {
		client = fallback(text(payload["client_id"]), "app_EMoamEEZ73f0CkXaXp7hrann")
	}
	accountID := fallback(item.UserID, credentialValue(item, "account_id", "chatgpt_account_id"))
	cookies := credentialValue(item, "cookies", "cookie")
	emailService := ""
	for _, r := range item.ProviderResources {
		if text(r["resource_type"]) == "mailbox" && text(r["provider_name"]) != "" {
			emailService = text(r["provider_name"])
			break
		}
	}
	expUnix := intValue(payload["exp"], 0)
	expires := ""
	if expUnix > 0 {
		expires = time.Unix(int64(expUnix), 0).UTC().Format(time.RFC3339)
	}
	return map[string]any{
		"id": item.ID, "email": item.Email, "password": item.Password, "client_id": client, "account_id": accountID, "workspace_id": workspace,
		"access_token": access, "refresh_token": refresh, "id_token": idToken, "session_token": session, "cookies": cookies,
		"email_service": emailService, "registered_at": item.CreatedAt, "last_refresh": item.UpdatedAt, "expires_at": expires, "status": item.DisplayStatus, "expires_at_unix": expUnix,
	}
}

func (s *Server) handleBatchExport(w http.ResponseWriter, r *http.Request, format string) {
	body, _ := parseBody(r)
	items := s.selectedAccounts(body, "chatgpt")
	switch format {
	case "csv":
		var buf bytes.Buffer
		cw := csv.NewWriter(&buf)
		_ = cw.Write([]string{"ID", "Email", "Password", "Client ID", "Account ID", "Workspace ID", "Access Token", "Refresh Token", "ID Token", "Session Token", "Email Service", "Status", "Registered At", "Last Refresh", "Expires At"})
		for _, item := range items {
			p := chatGPTPayload(item)
			_ = cw.Write([]string{fmt.Sprint(p["id"]), text(p["email"]), text(p["password"]), text(p["client_id"]), text(p["account_id"]), text(p["workspace_id"]), text(p["access_token"]), text(p["refresh_token"]), text(p["id_token"]), text(p["session_token"]), text(p["email_service"]), text(p["status"]), text(p["registered_at"]), text(p["last_refresh"]), text(p["expires_at"])})
		}
		cw.Flush()
		writeTextFile(w, timestampName("accounts", "csv"), "text/csv; charset=utf-8", buf.Bytes())
	case "json":
		arr := []map[string]any{}
		for _, item := range items {
			arr = append(arr, chatGPTPayload(item))
		}
		writeTextFile(w, timestampName("accounts", "json"), "application/json", []byte(dumpJSONPretty(arr)))
	case "email-api":
		lines := []string{}
		for _, item := range items {
			lines = append(lines, fmt.Sprintf("%s https://hsxhome.com/api/find/openai?email=%s&t=fzKIywnF4KEGGB_i", item.Email, item.Email))
		}
		writeTextFile(w, timestampName("chatgpt_email_api", "txt"), "text/plain; charset=utf-8", []byte(strings.Join(lines, "\n")))
	case "sub2api":
		files := map[string][]byte{}
		for _, item := range items {
			p := chatGPTPayload(item)
			data := map[string]any{"proxies": []any{}, "accounts": []any{map[string]any{"name": p["email"], "platform": "openai", "type": "oauth", "credentials": map[string]any{"access_token": p["access_token"], "chatgpt_account_id": p["account_id"], "chatgpt_user_id": "", "client_id": p["client_id"], "expires_at": p["expires_at_unix"], "expires_in": 863999, "organization_id": p["workspace_id"], "refresh_token": p["refresh_token"]}, "extra": map[string]any{}, "concurrency": 10, "priority": 1, "rate_multiplier": 1, "auto_pause_on_expired": true}}}
			files[item.Email+"_sub2api.json"] = []byte(dumpJSONPretty(data))
		}
		if len(files) == 1 {
			for name, content := range files {
				writeTextFile(w, name, "application/json", content)
				return
			}
		}
		writeTextFile(w, timestampName("sub2api_tokens", "zip"), "application/zip", zipFiles(files))
	case "cockpit":
		arr := []map[string]any{}
		for _, item := range items {
			p := chatGPTPayload(item)
			arr = append(arr, map[string]any{"type": "codex", "id_token": p["id_token"], "access_token": p["access_token"], "refresh_token": p["refresh_token"], "account_id": p["account_id"], "last_refresh": p["last_refresh"], "email": p["email"], "expired": p["expires_at"], "account_note": ""})
		}
		if len(arr) == 1 {
			writeTextFile(w, timestampName("cockpit_tokens", "json"), "application/json", []byte(dumpJSONPretty(arr[0])))
			return
		}
		writeTextFile(w, timestampName("cockpit_tokens", "json"), "application/json", []byte(dumpJSONPretty(arr)))
	case "kiro-go":
		kiroItems := s.selectedAccounts(body, "kiro")
		accounts := []map[string]any{}
		for _, item := range kiroItems {
			accounts = append(accounts, map[string]any{"id": randomID("kiro"), "email": item.Email, "nickname": strings.Split(item.Email, "@")[0], "accessToken": credentialValue(item, "accessToken", "access_token", "legacy_token"), "refreshToken": credentialValue(item, "refreshToken", "refresh_token"), "clientId": credentialValue(item, "clientId", "client_id"), "clientSecret": credentialValue(item, "clientSecret", "client_secret"), "authMethod": "idc", "provider": "BuilderId", "region": "us-east-1", "startUrl": "https://view.awsapps.com/start", "expiresAt": time.Now().Unix() + 3600, "machineId": randomID("machine"), "weight": 0, "enabled": true})
		}
		writeTextFile(w, timestampName("kiro_go_config", "json"), "application/json", []byte(dumpJSONPretty(map[string]any{"password": "changeme", "port": 8080, "host": "0.0.0.0", "requireApiKey": false, "accounts": accounts})))
	case "any2api":
		writeTextFile(w, timestampName("any2api_admin", "json"), "application/json", []byte(dumpJSONPretty(s.any2apiConfig(items))))
	case "cpa":
		files := map[string][]byte{}
		for _, item := range items {
			files[item.Email+".json"] = []byte(dumpJSONPretty(chatGPTPayload(item)))
		}
		if len(files) == 1 {
			for name, content := range files {
				writeTextFile(w, name, "application/json", content)
				return
			}
		}
		writeTextFile(w, timestampName("cpa_tokens", "zip"), "application/zip", zipFiles(files))
	default:
		writeError(w, 404, "鏈煡瀵煎嚭鏍煎紡")
	}
}

func (s *Server) any2apiConfig(items []AccountRecord) map[string]any {
	providers := map[string]any{}
	kiro := []map[string]any{}
	grok := []map[string]any{}
	for _, item := range items {
		switch item.Platform {
		case "kiro":
			kiro = append(kiro, map[string]any{"id": randomID("kiro"), "name": item.Email, "accessToken": credentialValue(item, "accessToken", "access_token", "legacy_token"), "machineId": randomID("machine"), "preferredEndpoint": "", "active": true, "updatedAt": item.UpdatedAt})
		case "grok":
			grok = append(grok, map[string]any{"id": randomID("grok"), "name": item.Email, "cookieToken": fallback(credentialValue(item, "sso"), credentialValue(item, "sso_rw")), "active": true, "updatedAt": item.UpdatedAt})
		case "cursor":
			if token := credentialValue(item, "session_token", "sessionToken", "wos_session", "legacy_token"); token != "" {
				providers["cursorConfig"] = map[string]any{"cookie": "WorkosCursorSessionToken=" + token}
			}
		case "chatgpt":
			if token := credentialValue(item, "access_token", "accessToken", "legacy_token"); token != "" {
				providers["chatgptConfig"] = map[string]any{"token": token}
			}
		}
	}
	if len(kiro) > 0 {
		providers["kiroAccounts"] = kiro
	}
	if len(grok) > 0 {
		providers["grokTokens"] = grok
	}
	return map[string]any{"settings": map[string]any{"adminPassword": "changeme", "apiKey": "0000", "defaultProvider": "kiro"}, "providers": providers}
}
