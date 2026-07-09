package main

import (
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"gorm.io/gorm"
)

func (s *Server) handleStats(w http.ResponseWriter, r *http.Request, rest string) {
	switch {
	case rest == "/overview" && r.Method == http.MethodGet:
		var total, success int64
		s.db.Model(&TaskLog{}).Count(&total)
		s.db.Model(&TaskLog{}).Where("status = ?", "success").Count(&success)
		var accounts int64
		s.db.Model(&Account{}).Count(&accounts)
		_, items := s.listAccountRecords("", "", "", 1, 1000000)
		dist := map[string]int{}
		for _, item := range items {
			dist[fallback(item.LifecycleStatus, "registered")]++
		}
		rate := 0.0
		if total > 0 {
			rate = float64(success) / float64(total) * 100
		}
		writeJSON(w, 200, map[string]any{"total_registrations": total, "success": success, "failed": total - success, "success_rate": rate, "total_accounts": accounts, "account_distribution": dist})
	case rest == "/by-platform" && r.Method == http.MethodGet:
		var logs []TaskLog
		s.db.Find(&logs)
		m := map[string]map[string]any{}
		for _, log := range logs {
			row := m[log.Platform]
			if row == nil {
				row = map[string]any{"platform": log.Platform, "success": 0, "failed": 0, "total": 0}
				m[log.Platform] = row
			}
			if log.Status == "success" {
				row["success"] = intValue(row["success"], 0) + 1
			} else {
				row["failed"] = intValue(row["failed"], 0) + 1
			}
			row["total"] = intValue(row["total"], 0) + 1
		}
		out := []map[string]any{}
		for _, row := range m {
			total := intValue(row["total"], 0)
			if total > 0 {
				row["success_rate"] = float64(intValue(row["success"], 0)) / float64(total) * 100
			} else {
				row["success_rate"] = 0
			}
			out = append(out, row)
		}
		writeJSON(w, 200, out)
	case rest == "/by-day" && r.Method == http.MethodGet:
		days := intValue(r.URL.Query().Get("days"), 30)
		cutoff := time.Now().AddDate(0, 0, -days)
		var logs []TaskLog
		q := s.db.Where("created_at >= ?", cutoff)
		if p := r.URL.Query().Get("platform"); p != "" {
			q = q.Where("platform = ?", p)
		}
		q.Order("created_at ASC").Find(&logs)
		m := map[string]map[string]any{}
		for _, log := range logs {
			day := log.CreatedAt.Format("2006-01-02")
			row := m[day]
			if row == nil {
				row = map[string]any{"date": day, "success": 0, "failed": 0, "total": 0}
				m[day] = row
			}
			if log.Status == "success" {
				row["success"] = intValue(row["success"], 0) + 1
			} else {
				row["failed"] = intValue(row["failed"], 0) + 1
			}
			row["total"] = intValue(row["total"], 0) + 1
		}
		keys := []string{}
		for k := range m {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out := []map[string]any{}
		for _, k := range keys {
			out = append(out, m[k])
		}
		writeJSON(w, 200, out)
	case rest == "/by-proxy" && r.Method == http.MethodGet:
		var items []Proxy
		s.db.Order("success_count DESC").Find(&items)
		out := []map[string]any{}
		for _, p := range items {
			total := p.SuccessCount + p.FailCount
			rate := 0.0
			if total > 0 {
				rate = float64(p.SuccessCount) / float64(total) * 100
			}
			out = append(out, map[string]any{"id": p.ID, "url": p.URL, "region": p.Region, "success": p.SuccessCount, "fail": p.FailCount, "total": total, "success_rate": rate, "is_active": p.IsActive})
		}
		writeJSON(w, 200, out)
	case rest == "/errors" && r.Method == http.MethodGet:
		var logs []TaskLog
		days := intValue(r.URL.Query().Get("days"), 7)
		q := s.db.Where("status = ? AND error <> ? AND created_at >= ?", "failed", "", time.Now().AddDate(0, 0, -days))
		if p := r.URL.Query().Get("platform"); p != "" {
			q = q.Where("platform = ?", p)
		}
		q.Find(&logs)
		m := map[string]int{}
		for _, log := range logs {
			m[log.Error]++
		}
		out := []map[string]any{}
		for err, count := range m {
			out = append(out, map[string]any{"error": err, "count": count})
		}
		writeJSON(w, 200, out)
	default:
		writeError(w, 404, "not found")
	}
}

func (s *Server) handleSmsPool(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "/blacklist" && r.Method == http.MethodGet {
		var items []SmsPoolBlacklist
		s.db.Order("last_attempted_at DESC").Find(&items)
		out := []map[string]any{}
		for _, item := range items {
			out = append(out, smsBLToMap(item))
		}
		writeJSON(w, 200, map[string]any{"items": out, "total": len(out)})
		return
	}
	if rest == "/blacklist" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		phone := normalizePhone(text(body["phone"]))
		if phone == "" {
			writeError(w, 400, "phone 涓嶅彲涓虹┖ / 鏍煎紡鏃犳晥")
			return
		}
		var item SmsPoolBlacklist
		s.db.Where("phone_e164 = ?", phone).First(&item)
		item.PhoneE164 = phone
		item.RelayURL = text(body["relay_url"])
		if u, err := url.Parse(item.RelayURL); err == nil {
			item.RelayHost = u.Host
		}
		item.Reason = fallback(text(body["reason"]), "manual")
		item.ErrorCode = text(body["error_code"])
		item.TaskID = text(body["task_id"])
		item.FailCount++
		if item.FailCount <= 1 {
			item.FailCount = 1
		}
		item.LastErrorMessage = text(body["error_message"])
		item.LastAttemptedAt = time.Now()
		s.db.Save(&item)
		writeJSON(w, 200, smsBLToMap(item))
		return
	}
	if rest == "/blacklist" && r.Method == http.MethodDelete {
		var count int64
		s.db.Model(&SmsPoolBlacklist{}).Count(&count)
		s.db.Session(&gorm.Session{AllowGlobalUpdate: true}).Delete(&SmsPoolBlacklist{})
		writeJSON(w, 200, map[string]any{"ok": true, "removed": count})
		return
	}
	if strings.HasPrefix(rest, "/blacklist/") && r.Method == http.MethodDelete {
		phone := normalizePhone(strings.TrimPrefix(rest, "/blacklist/"))
		res := s.db.Where("phone_e164 = ?", phone).Delete(&SmsPoolBlacklist{})
		if res.RowsAffected == 0 {
			writeError(w, 404, "鍙风爜涓嶅湪榛戝悕鍗曚腑")
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "phone": phone})
		return
	}
	writeError(w, 404, "not found")
}

func normalizePhone(phone string) string {
	phone = strings.TrimSpace(phone)
	phone = strings.ReplaceAll(phone, " ", "")
	if phone == "" {
		return ""
	}
	if !strings.HasPrefix(phone, "+") {
		phone = "+" + phone
	}
	return phone
}

func smsBLToMap(item SmsPoolBlacklist) map[string]any {
	return map[string]any{"id": item.ID, "phone_e164": item.PhoneE164, "phone": item.PhoneE164, "relay_url": item.RelayURL, "relay_host": item.RelayHost, "reason": item.Reason, "error_code": item.ErrorCode, "task_id": item.TaskID, "fail_count": item.FailCount, "last_error_message": item.LastErrorMessage, "created_at": formatTime(item.CreatedAt), "last_attempted_at": formatTime(item.LastAttemptedAt)}
}

func (s *Server) handleSms(w http.ResponseWriter, r *http.Request, rest string) {
	switch {
	case rest == "/herosms/countries" || rest == "/smsbower/countries":
		writeJSON(w, 200, map[string]any{"countries": []map[string]any{{"id": "6", "chn": "Indonesia", "eng": "Indonesia", "country": "6"}, {"id": "187", "chn": "United States", "eng": "United States", "country": "187"}}})
	case rest == "/herosms/services" || rest == "/smsbower/services":
		writeJSON(w, 200, map[string]any{"services": []map[string]any{{"code": "ot", "name": "OpenAI/ChatGPT"}, {"code": "go", "name": "GoPay"}}})
	case strings.HasSuffix(rest, "/balance"):
		writeJSON(w, 200, map[string]any{"balance": "0"})
	case strings.HasSuffix(rest, "/prices"):
		writeJSON(w, 200, map[string]any{"prices": []map[string]any{}})
	case strings.HasSuffix(rest, "/top-countries"):
		writeJSON(w, 200, map[string]any{"countries": []map[string]any{}, "service": "ot"})
	case strings.HasSuffix(rest, "/best-country"):
		writeJSON(w, 200, map[string]any{"country": nil, "detail": nil, "service": "ot"})
	default:
		writeError(w, 404, "not found")
	}
}

func (s *Server) handleBitBrowserProfiles(w http.ResponseWriter, r *http.Request, rest string) {
	const key = "bitbrowser_profile_pool"
	readIDs := func() []string {
		var item ConfigItem
		if s.db.First(&item, "key = ?", key).Error != nil {
			return []string{}
		}
		raw := strings.NewReplacer(",", "\n", ";", "\n").Replace(item.Value)
		ids := []string{}
		seen := map[string]bool{}
		for _, line := range strings.Split(raw, "\n") {
			id := strings.TrimSpace(line)
			if id != "" && !seen[id] {
				ids = append(ids, id)
				seen[id] = true
			}
		}
		return ids
	}
	writeIDs := func(ids []string) []string {
		clean := []string{}
		seen := map[string]bool{}
		for _, id := range ids {
			id = strings.TrimSpace(id)
			if id != "" && !seen[id] {
				clean = append(clean, id)
				seen[id] = true
			}
		}
		s.db.Save(&ConfigItem{Key: key, Value: strings.Join(clean, "\n")})
		return clean
	}
	if rest == "" && r.Method == http.MethodGet {
		out := []map[string]any{}
		for _, id := range readIDs() {
			out = append(out, map[string]any{"profile_id": id, "in_use": 0})
		}
		writeJSON(w, 200, map[string]any{"items": out})
		return
	}
	if rest == "" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		pid := text(body["profile_id"])
		ids := readIDs()
		created := true
		for _, id := range ids {
			if id == pid {
				created = false
			}
		}
		if created {
			ids = append(ids, pid)
			writeIDs(ids)
		}
		writeJSON(w, 200, map[string]any{"created": created, "profile_id": pid})
		return
	}
	if rest == "" && r.Method == http.MethodPut {
		body, _ := parseBody(r)
		ids := writeIDs(stringSlice(body["profile_ids"]))
		out := []map[string]any{}
		for _, id := range ids {
			out = append(out, map[string]any{"profile_id": id, "in_use": 0})
		}
		writeJSON(w, 200, map[string]any{"items": out})
		return
	}
	if strings.HasPrefix(rest, "/") && r.Method == http.MethodDelete {
		pid := strings.TrimPrefix(rest, "/")
		ids := []string{}
		removed := false
		for _, id := range readIDs() {
			if id == pid {
				removed = true
			} else {
				ids = append(ids, id)
			}
		}
		if !removed {
			writeError(w, 404, "profile_id 不存在")
			return
		}
		writeIDs(ids)
		writeJSON(w, 200, map[string]any{"removed": true, "profile_id": pid})
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) handleSystem(w http.ResponseWriter, r *http.Request, rest string) {
	switch {
	case rest == "/version" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"current": "go-port", "latest": nil, "has_update": false})
	case rest == "/solver/status" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"running": false, "status": "disabled"})
	case rest == "/solver/restart" && r.Method == http.MethodPost:
		writeJSON(w, 200, map[string]any{"ok": true, "message": "Go 迁移版未启用独立 solver 进程"})
	default:
		writeError(w, 404, "not found")
	}
}

func (s *Server) handleActions(w http.ResponseWriter, r *http.Request, rest string) {
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) == 1 && r.Method == http.MethodGet {
		writeJSON(w, 200, []map[string]any{
			{"id": "check", "label": "检测账号", "description": "创建账号检测任务", "params_schema": []any{}},
			{"id": "refresh_plan", "label": "刷新套餐", "description": "刷新账号概览状态", "params_schema": []any{}},
		})
		return
	}
	if len(parts) == 2 && parts[1] == "capabilities" && r.Method == http.MethodGet {
		writeJSON(w, 200, map[string]any{"actions": []string{"check", "refresh_plan"}})
		return
	}
	if len(parts) >= 3 && r.Method == http.MethodPost {
		platform := parts[0]
		accountID := intValue(parts[1], 0)
		actionID := parts[2]
		task := s.createTask("platform_action", platform, map[string]any{"account_id": accountID, "action_id": actionID}, 1)
		writeJSON(w, 200, map[string]any{"ok": true, "task_id": task.ID, "task": serializeTask(task)})
		return
	}
	writeError(w, 404, "not found")
}
