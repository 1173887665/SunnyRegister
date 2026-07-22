package main

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"
)

func (s *Server) handleAuth(w http.ResponseWriter, r *http.Request, rest string) {
	w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate")
	w.Header().Set("Pragma", "no-cache")
	if rest == "/check" && r.Method == http.MethodGet {
		writeJSON(w, 200, map[string]any{"required": true, "authenticated": s.hasValidSession(r), "username_required": true})
		return
	}
	if rest == "/login" && r.Method == http.MethodPost {
		key := s.loginClientKey(r)
		if blocked, retryAfter := s.loginBlocked(key); blocked {
			w.Header().Set("Retry-After", fmt.Sprintf("%d", max(1, int(retryAfter.Seconds()))))
			writeError(w, http.StatusTooManyRequests, "Too many login attempts; try again later")
			return
		}
		body, err := parseBody(r)
		if err != nil {
			writeError(w, http.StatusBadRequest, "Invalid login request")
			return
		}
		username := fallback(text(body["username"]), text(body["user"]))
		if constantTimeEqual(username, s.adminUser) && constantTimeEqual(text(body["password"]), s.adminPass) {
			s.clearLoginFailures(key)
			token := s.newSession()
			s.setSessionCookie(w, token, int(s.sessionTTL.Seconds()))
			writeJSON(w, 200, map[string]any{"ok": true, "user": map[string]any{"username": s.adminUser, "role": "admin"}})
			return
		}
		s.recordLoginFailure(key)
		writeJSON(w, http.StatusUnauthorized, map[string]any{"ok": false, "error": "Invalid username or password"})
		return
	}
	if rest == "/logout" && r.Method == http.MethodPost {
		if c, err := r.Cookie(s.sessionCookieName()); err == nil {
			s.deleteSession(c.Value)
		}
		s.setSessionCookie(w, "", -1)
		writeJSON(w, 200, map[string]any{"ok": true})
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) handleConfig(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "" && r.Method == http.MethodGet {
		var items []ConfigItem
		s.db.Find(&items)
		out := map[string]string{}
		allowed := s.allowedConfigKeys()
		for _, item := range items {
			if allowed[item.Key] {
				out[item.Key] = item.Value
			}
		}
		writeJSON(w, 200, out)
		return
	}
	if rest == "/options" && r.Method == http.MethodGet {
		writeJSON(w, 200, s.configOptions())
		return
	}
	if rest == "" && r.Method == http.MethodPut {
		body, _ := parseBody(r)
		data := mapFromAny(body["data"])
		allowed := s.allowedConfigKeys()
		updated := []string{}
		for k, v := range data {
			if !allowed[k] {
				continue
			}
			item := ConfigItem{Key: k, Value: text(v)}
			s.db.Save(&item)
			updated = append(updated, k)
		}
		sort.Strings(updated)
		writeJSON(w, 200, map[string]any{"ok": true, "updated": updated})
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) allowedConfigKeys() map[string]bool {
	keys := map[string]bool{}
	for _, key := range []string{"default_executor", "default_identity_provider", "default_oauth_provider", "oauth_email_hint", "chrome_user_data_dir", "chrome_cdp_url", "cpa_api_url", "cpa_api_key", "team_manager_url", "team_manager_key", "any2api_url", "any2api_password", "bitbrowser_profile_pool", "sub2api_base_url", "sub2api_admin_token", "sub2api_auth_header", "sub2api_group_id", "sub2api_group_name", "sub2api_batch_endpoint"} {
		keys[key] = true
	}
	var defs []ProviderDefinition
	s.db.Find(&defs)
	for _, d := range defs {
		for _, field := range jsonList(d.FieldsJSON) {
			if key := text(field["key"]); key != "" {
				keys[key] = true
			}
		}
	}
	return keys
}

func (s *Server) configOptions() map[string]any {
	return map[string]any{
		"mailbox_providers":      s.providerDefinitions("mailbox", true),
		"captcha_providers":      s.providerDefinitions("captcha", true),
		"sms_providers":          s.providerDefinitions("sms", true),
		"mailbox_drivers":        s.providerDrivers("mailbox"),
		"captcha_drivers":        s.providerDrivers("captcha"),
		"sms_drivers":            s.providerDrivers("sms"),
		"captcha_policy":         s.captchaPolicy(),
		"mailbox_settings":       s.providerSettings("mailbox"),
		"captcha_settings":       s.providerSettings("captcha"),
		"sms_settings":           s.providerSettings("sms"),
		"executor_options":       []map[string]string{{"value": "protocol", "label": "鍗忚妯″紡"}, {"value": "headless", "label": "鍚庡彴娴忚鍣ㄨ嚜鍔ㄥ寲"}, {"value": "headed", "label": "鍙娴忚鍣ㄨ嚜鍔ㄥ寲"}},
		"identity_mode_options":  []map[string]string{{"value": "mailbox", "label": "系统邮箱"}, {"value": "oauth_browser", "label": "第三方账号"}, {"value": "phone", "label": "手机号"}},
		"oauth_provider_options": []map[string]string{{"value": "google", "label": "Google"}, {"value": "github", "label": "GitHub"}, {"value": "microsoft", "label": "Microsoft"}, {"value": "apple", "label": "Apple"}, {"value": "x", "label": "X"}},
	}
}

func (s *Server) handleProviderDefinitions(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "" && r.Method == http.MethodGet {
		writeJSON(w, 200, s.providerDefinitions(r.URL.Query().Get("provider_type"), r.URL.Query().Get("enabled_only") == "true"))
		return
	}
	if rest == "/drivers" && r.Method == http.MethodGet {
		writeJSON(w, 200, s.providerDrivers(r.URL.Query().Get("provider_type")))
		return
	}
	if rest == "" && (r.Method == http.MethodPost || r.Method == http.MethodPut) {
		body, _ := parseBody(r)
		var item ProviderDefinition
		id := intValue(body["id"], 0)
		if id > 0 {
			s.db.First(&item, id)
		} else {
			s.db.Where("provider_type = ? AND provider_key = ?", text(body["provider_type"]), text(body["provider_key"])).First(&item)
		}
		item.ProviderType = text(body["provider_type"])
		item.ProviderKey = text(body["provider_key"])
		item.Label = fallback(text(body["label"]), item.ProviderKey)
		item.Description = text(body["description"])
		item.DriverType = text(body["driver_type"])
		item.DefaultAuthMode = text(body["default_auth_mode"])
		item.Enabled = boolValue(body["enabled"], true)
		item.MetadataJSON = dumpJSON(mapFromAny(body["metadata"]))
		if item.AuthModesJSON == "" {
			item.AuthModesJSON = "[]"
		}
		if item.FieldsJSON == "" {
			item.FieldsJSON = "[]"
		}
		s.db.Save(&item)
		writeJSON(w, 200, map[string]any{"ok": true, "item": serializeProviderDefinition(item)})
		return
	}
	if strings.HasPrefix(rest, "/") && r.Method == http.MethodDelete {
		id := intValue(strings.Trim(rest, "/"), 0)
		var def ProviderDefinition
		if s.db.First(&def, id).Error != nil {
			writeError(w, 404, "provider definition 不存在")
			return
		}
		var count int64
		s.db.Model(&ProviderSetting{}).Where("provider_type = ? AND provider_key = ?", def.ProviderType, def.ProviderKey).Count(&count)
		if count > 0 {
			writeError(w, 400, "璇峰厛鍒犻櫎瀵瑰簲 provider 閰嶇疆锛屽啀鍒犻櫎 definition")
			return
		}
		s.db.Delete(&def)
		writeJSON(w, 200, map[string]any{"ok": true})
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) providerDefinitions(providerType string, enabledOnly bool) []map[string]any {
	var defs []ProviderDefinition
	q := s.db.Order("id ASC")
	if providerType != "" {
		q = q.Where("provider_type = ?", providerType)
	}
	if enabledOnly {
		q = q.Where("enabled = ?", true)
	}
	q.Find(&defs)
	out := []map[string]any{}
	for _, d := range defs {
		out = append(out, serializeProviderDefinition(d))
	}
	return out
}

func serializeProviderDefinition(d ProviderDefinition) map[string]any {
	return map[string]any{"id": d.ID, "provider_type": d.ProviderType, "provider_key": d.ProviderKey, "value": d.ProviderKey, "label": d.Label, "description": d.Description, "driver_type": d.DriverType, "default_auth_mode": d.DefaultAuthMode, "auth_modes": jsonList(d.AuthModesJSON), "fields": jsonList(d.FieldsJSON), "enabled": d.Enabled, "is_builtin": d.IsBuiltin, "category": d.Category, "metadata": jsonMap(d.MetadataJSON)}
}

func (s *Server) providerDrivers(providerType string) []map[string]any {
	defs := s.providerDefinitions(providerType, false)
	seen := map[string]bool{}
	out := []map[string]any{}
	for _, d := range defs {
		dt := text(d["driver_type"])
		if dt == "" || seen[dt] {
			continue
		}
		seen[dt] = true
		out = append(out, map[string]any{"provider_type": d["provider_type"], "provider_key": d["provider_key"], "driver_type": dt, "label": d["label"], "description": d["description"], "default_auth_mode": d["default_auth_mode"], "auth_modes": d["auth_modes"], "fields": d["fields"]})
	}
	return out
}

func (s *Server) handleProviderSettings(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "" && r.Method == http.MethodGet {
		writeJSON(w, 200, s.providerSettings(r.URL.Query().Get("provider_type")))
		return
	}
	if rest == "" && (r.Method == http.MethodPost || r.Method == http.MethodPut) {
		body, _ := parseBody(r)
		pt, pk := text(body["provider_type"]), text(body["provider_key"])
		var def ProviderDefinition
		if s.db.Where("provider_type = ? AND provider_key = ?", pt, pk).First(&def).Error != nil {
			writeError(w, 400, "鏈煡 provider")
			return
		}
		var item ProviderSetting
		id := intValue(body["id"], 0)
		if id > 0 {
			s.db.First(&item, id)
		} else {
			s.db.Where("provider_type = ? AND provider_key = ?", pt, pk).First(&item)
		}
		if boolValue(body["is_default"], false) {
			s.db.Model(&ProviderSetting{}).Where("provider_type = ? AND id <> ?", pt, item.ID).Update("is_default", false)
		}
		item.ProviderType = pt
		item.ProviderKey = pk
		item.DisplayName = fallback(text(body["display_name"]), fallback(def.Label, pk))
		item.AuthMode = fallback(text(body["auth_mode"]), def.DefaultAuthMode)
		item.Enabled = boolValue(body["enabled"], true)
		item.IsDefault = boolValue(body["is_default"], false)
		item.ConfigJSON = dumpJSON(mapFromAny(body["config"]))
		item.AuthJSON = dumpJSON(mapFromAny(body["auth"]))
		item.MetadataJSON = dumpJSON(mapFromAny(body["metadata"]))
		s.db.Save(&item)
		writeJSON(w, 200, map[string]any{"ok": true, "item": s.serializeProviderSetting(item)})
		return
	}
	if rest == "/test" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		pt := text(body["provider_type"])
		if pt == "mailbox" {
			writeJSON(w, 200, map[string]any{"ok": true, "message": "閰嶇疆鏍煎紡妫€鏌ラ€氳繃", "email": "preview@example.local"})
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "message": "閰嶇疆鏍煎紡妫€鏌ラ€氳繃"})
		return
	}
	if strings.HasPrefix(rest, "/") && r.Method == http.MethodDelete {
		id := intValue(strings.Trim(rest, "/"), 0)
		var item ProviderSetting
		if s.db.First(&item, id).Error != nil {
			writeError(w, 404, "provider setting 不存在")
			return
		}
		pt := item.ProviderType
		wasDefault := item.IsDefault
		s.db.Delete(&item)
		if wasDefault {
			var fallback ProviderSetting
			if s.db.Where("provider_type = ?", pt).Order("id ASC").First(&fallback).Error == nil {
				fallback.IsDefault = true
				s.db.Save(&fallback)
			}
		}
		writeJSON(w, 200, map[string]any{"ok": true})
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) providerSettings(providerType string) []map[string]any {
	var items []ProviderSetting
	q := s.db.Order("id ASC")
	if providerType != "" {
		q = q.Where("provider_type = ?", providerType)
	}
	q.Find(&items)
	out := []map[string]any{}
	for _, item := range items {
		out = append(out, s.serializeProviderSetting(item))
	}
	return out
}

func (s *Server) serializeProviderSetting(item ProviderSetting) map[string]any {
	var def ProviderDefinition
	s.db.Where("provider_type = ? AND provider_key = ?", item.ProviderType, item.ProviderKey).First(&def)
	auth := jsonMap(item.AuthJSON)
	preview := map[string]string{}
	for k, v := range auth {
		preview[k] = previewSecret(text(v))
	}
	return map[string]any{"id": item.ID, "provider_type": item.ProviderType, "provider_key": item.ProviderKey, "display_name": item.DisplayName, "catalog_label": fallback(def.Label, item.ProviderKey), "description": def.Description, "driver_type": def.DriverType, "auth_mode": item.AuthMode, "auth_modes": jsonList(def.AuthModesJSON), "enabled": item.Enabled, "is_default": item.IsDefault, "is_builtin": def.IsBuiltin, "category": def.Category, "fields": jsonList(def.FieldsJSON), "config": jsonMap(item.ConfigJSON), "auth": auth, "auth_preview": preview, "metadata": jsonMap(item.MetadataJSON)}
}

func (s *Server) captchaPolicy() map[string]any {
	settings := s.providerSettings("captcha")
	order := []string{}
	defaultKey := ""
	for _, item := range settings {
		if boolValue(item["enabled"], false) {
			key := text(item["provider_key"])
			if key != "" && key != "manual" && key != "local_solver" {
				order = append(order, key)
			}
			if defaultKey == "" || boolValue(item["is_default"], false) {
				defaultKey = key
			}
		}
	}
	return map[string]any{"protocol_mode": "auto_first_enabled_remote", "protocol_order": order, "browser_mode": defaultKey}
}

func (s *Server) handlePlatforms(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "" && r.Method == http.MethodGet {
		writeJSON(w, 200, []map[string]any{
			{"name": "chatgpt", "display_name": "ChatGPT", "version": "go", "supported_executors": []string{"protocol", "headless", "headed"}, "supported_identity_modes": []string{"mailbox", "oauth_browser", "phone"}, "supported_oauth_providers": []string{"google", "microsoft", "apple"}},
			{"name": "kiro", "display_name": "Kiro", "version": "go", "supported_executors": []string{"protocol"}, "supported_identity_modes": []string{"mailbox", "oauth_browser"}, "supported_oauth_providers": []string{"google", "github", "builderid"}},
			{"name": "cursor", "display_name": "Cursor", "version": "go", "supported_executors": []string{"protocol"}, "supported_identity_modes": []string{"oauth_browser"}, "supported_oauth_providers": []string{"google", "github"}},
			{"name": "gopay", "display_name": "GoPay", "version": "go", "supported_executors": []string{"protocol"}, "supported_identity_modes": []string{"phone"}, "supported_oauth_providers": []string{}},
		})
		return
	}
	if strings.HasSuffix(rest, "/desktop-state") && r.Method == http.MethodGet {
		writeJSON(w, 200, map[string]any{"available": false, "message": "desktop probe is disabled in Docker/Go service"})
		return
	}
	if strings.HasSuffix(rest, "/capabilities") {
		name := strings.TrimSuffix(strings.Trim(rest, "/"), "/capabilities")
		if r.Method == http.MethodPut {
			body, _ := parseBody(r)
			item := PlatformCapabilityOverride{PlatformName: name, CapabilitiesJSON: dumpJSON(body)}
			var existing PlatformCapabilityOverride
			if s.db.Where("platform_name = ?", name).First(&existing).Error == nil {
				item.ID = existing.ID
			}
			s.db.Save(&item)
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
		if r.Method == http.MethodDelete {
			s.db.Where("platform_name = ?", name).Delete(&PlatformCapabilityOverride{})
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
	}
	writeError(w, 404, "not found")
}

func (s *Server) handleProxies(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "" && r.Method == http.MethodGet {
		var items []Proxy
		s.db.Find(&items)
		out := []map[string]any{}
		for _, p := range items {
			out = append(out, map[string]any{"id": p.ID, "url": p.URL, "region": p.Region, "success_count": p.SuccessCount, "fail_count": p.FailCount, "is_active": p.IsActive, "last_checked": nullableTime(p.LastChecked.Valid, p.LastChecked.Time)})
		}
		writeJSON(w, 200, out)
		return
	}
	if rest == "" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		p := Proxy{URL: text(body["url"]), Region: text(body["region"]), IsActive: true}
		if p.URL == "" {
			writeError(w, 400, "url 涓嶈兘涓虹┖")
			return
		}
		if s.db.Where("url = ?", p.URL).First(&Proxy{}).Error == nil {
			writeError(w, 400, "代理已存在")
			return
		}
		s.db.Create(&p)
		writeJSON(w, 200, p)
		return
	}
	if rest == "/bulk" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		added := 0
		for _, raw := range stringSlice(body["proxies"]) {
			url := strings.TrimSpace(raw)
			if url == "" {
				continue
			}
			if s.db.Where("url = ?", url).First(&Proxy{}).Error == nil {
				continue
			}
			s.db.Create(&Proxy{URL: url, Region: text(body["region"]), IsActive: true})
			added++
		}
		writeJSON(w, 200, map[string]any{"added": added})
		return
	}
	if rest == "/check" && r.Method == http.MethodPost {
		s.db.Model(&Proxy{}).Updates(map[string]any{"last_checked": time.Now()})
		writeJSON(w, 200, map[string]any{"message": "妫€娴嬩换鍔″凡瀹屾垚"})
		return
	}
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) >= 1 {
		id := intValue(parts[0], 0)
		var p Proxy
		if s.db.First(&p, id).Error != nil {
			writeError(w, 404, "代理不存在")
			return
		}
		if len(parts) == 1 && r.Method == http.MethodDelete {
			s.db.Delete(&p)
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
		if len(parts) == 2 && parts[1] == "toggle" && r.Method == http.MethodPatch {
			p.IsActive = !p.IsActive
			s.db.Save(&p)
			writeJSON(w, 200, map[string]any{"is_active": p.IsActive})
			return
		}
	}
	writeError(w, 404, "not found")
}
