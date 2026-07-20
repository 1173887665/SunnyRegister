package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"mime"
	"mime/multipart"
	"mime/quotedprintable"
	"net"
	"net/http"
	"net/mail"
	"net/textproto"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/text/encoding/htmlindex"
)

const (
	sunnyCfgProxy    = "proxy"
	sunnyCfgSub2API  = "sub2api"
	sunnyCfgPhone    = "phone"
	sunnyCfgMailbox  = "mailbox"
	defaultGroupName = "默认分组"
)

var sunnyMailboxStatuses = []string{"未注册", "已注册", "已接码", "已反代", "PLUS试用中", "已封禁", "需二验"}

func (s *Server) handleSunny(w http.ResponseWriter, r *http.Request, rest string) {
	rest = strings.Trim(rest, "/")
	if rest == "" {
		writeJSON(w, 200, map[string]any{"ok": true, "module": "sunny"})
		return
	}
	parts := strings.Split(rest, "/")
	switch parts[0] {
	case "workbench":
		if len(parts) == 2 && parts[1] == "accounts" && r.Method == http.MethodGet {
			s.sunnyListAccounts(w, r)
			return
		}
	case "mailbox-groups":
		s.sunnyMailboxGroups(w, r, parts[1:])
		return
	case "mailboxes":
		s.sunnyMailboxes(w, r, parts[1:])
		return
	case "phones":
		s.sunnyPhones(w, r, parts[1:])
		return
	case "proxy-config":
		s.sunnyProxyConfig(w, r, parts[1:])
		return
	case "sub2api-config":
		s.sunnySub2APIConfig(w, r)
		return
	case "sub2api":
		s.sunnySub2API(w, r, parts[1:])
		return
	case "sessions":
		s.sunnySessions(w, r, parts[1:])
		return
	case "tasks":
		s.sunnyTasks(w, r, parts[1:])
		return
	case "import-state":
		if r.Method == http.MethodPost {
			s.sunnyImportState(w, r)
			return
		}
	}
	writeError(w, 404, "not found")
}

func (s *Server) sunnyEnsureDefaultGroup() uint {
	var g SunnyMailboxGroup
	if err := s.db.First(&g, "name = ?", defaultGroupName).Error; err == nil {
		return g.ID
	}
	g = SunnyMailboxGroup{Name: defaultGroupName}
	s.db.Create(&g)
	return g.ID
}

func (s *Server) sunnyMailboxGroups(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 0 && r.Method == http.MethodGet {
		s.sunnyEnsureDefaultGroup()
		var rows []SunnyMailboxGroup
		s.db.Order("id asc").Find(&rows)
		writeJSON(w, 200, map[string]any{"items": rows})
		return
	}
	if len(parts) == 0 && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		name := fallback(text(body["name"]), defaultGroupName)
		g := SunnyMailboxGroup{Name: name, Description: text(body["description"])}
		if err := s.db.Create(&g).Error; err != nil {
			writeError(w, 400, err.Error())
			return
		}
		writeJSON(w, 200, g)
		return
	}
	if len(parts) == 1 {
		id := uint(intValue(parts[0], 0))
		if id == 0 {
			writeError(w, 400, "invalid group id")
			return
		}
		if r.Method == http.MethodPut {
			body, _ := parseBody(r)
			var g SunnyMailboxGroup
			if s.db.First(&g, id).Error != nil {
				writeError(w, 404, "group not found")
				return
			}
			if text(body["name"]) != "" {
				g.Name = text(body["name"])
			}
			g.Description = text(body["description"])
			s.db.Save(&g)
			writeJSON(w, 200, g)
			return
		}
		if r.Method == http.MethodDelete {
			s.db.Delete(&SunnyMailboxGroup{}, id)
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
	}
	writeError(w, 404, "not found")
}

func serializeSunnyMailbox(m SunnyMailbox, groups map[uint]string, planType ...string) map[string]any {
	status := m.Status
	if status == "" {
		status = "unused"
	}
	plan := "-"
	if len(planType) > 0 && strings.TrimSpace(planType[0]) != "" {
		plan = planType[0]
	}
	accessToken := ""
	if len(planType) > 1 {
		accessToken = planType[1]
	}
	accountID := uint(0)
	if len(planType) > 2 {
		accountID = uint(intValue(planType[2], 0))
	}
	return map[string]any{
		"id": m.ID, "account_id": accountID, "group_id": m.GroupID, "group_name": groups[m.GroupID], "email": m.Email,
		"password": m.Password, "client_id": m.ClientID, "refresh_token": m.RefreshToken, "openai_rt": m.OpenAIRT, "access_token": accessToken,
		"raw": m.Raw, "account_type": fallback(m.AccountType, "free"), "plan_type": plan, "status": status, "enabled": m.Enabled,
		"last_error": m.LastError, "latest_mail": jsonMap(m.LatestMailJSON),
		"last_mail_at":  nullableTime(m.LastMailAt.Valid, m.LastMailAt.Time),
		"registered_at": nullableTime(m.RegisteredAt.Valid, m.RegisteredAt.Time),
		"created_at":    formatTime(m.CreatedAt), "updated_at": formatTime(m.UpdatedAt),
	}
}

func serializeSunnyMailboxList(m SunnyMailbox, groups map[uint]string, plan, accessToken string, accountID uint, summary bool) map[string]any {
	item := serializeSunnyMailbox(m, groups, plan, accessToken, strconv.FormatUint(uint64(accountID), 10))
	if !summary {
		return item
	}
	item["has_openai_rt"] = strings.TrimSpace(m.OpenAIRT) != ""
	for _, key := range []string{"password", "client_id", "refresh_token", "openai_rt", "access_token", "raw", "last_error", "latest_mail", "last_mail_at"} {
		delete(item, key)
	}
	return item
}

func normalizeSunnyPlanType(v string) string {
	v = strings.TrimSpace(strings.ToLower(v))
	v = strings.Trim(v, "\"'")
	switch v {
	case "", "unknown", "null", "none":
		return ""
	case "chatgptplus", "chatgpt_plus", "plus_user", "paid":
		return "plus"
	case "chatgptfree", "free_user":
		return "free"
	default:
		return v
	}
}

func sunnyPlanTypeFromSessionJSON(raw string) string {
	data := jsonMap(raw)
	if len(data) == 0 {
		return ""
	}
	if account, ok := data["account"].(map[string]any); ok {
		if plan := normalizeSunnyPlanType(firstText(account["planType"], account["plan_type"], account["plan"], account["type"])); plan != "" {
			return plan
		}
	}
	if user, ok := data["user"].(map[string]any); ok {
		if account, ok := user["account"].(map[string]any); ok {
			if plan := normalizeSunnyPlanType(firstText(account["planType"], account["plan_type"], account["plan"], account["type"])); plan != "" {
				return plan
			}
		}
	}
	return normalizeSunnyPlanType(firstText(data["planType"], data["plan_type"], data["plan"], data["account_type"]))
}

func sunnyAccessTokenFromSessionJSON(raw string) string {
	data := jsonMap(raw)
	if len(data) == 0 {
		return ""
	}
	if token := firstText(data["accessToken"], data["access_token"], data["token"]); token != "" {
		return token
	}
	if auth, ok := data["auth"].(map[string]any); ok {
		if token := firstText(auth["accessToken"], auth["access_token"]); token != "" {
			return token
		}
	}
	return ""
}

func sunnyEmailKey(email string) string {
	return strings.ToLower(strings.TrimSpace(email))
}

func sunnyMailboxStatusLooksRegistered(status string) bool {
	status = strings.TrimSpace(strings.ToLower(status))
	if status == "" || status == "unused" || status == "unregistered" || status == "未注册" {
		return false
	}
	return true
}
func (s *Server) sunnySessionPlanTypesByEmail(emails []string) map[string]string {
	out := map[string]string{}
	if len(emails) == 0 {
		return out
	}
	var rows []SunnySession
	s.db.Select("email", "session_json").Where("email IN ?", emails).Find(&rows)
	for _, sess := range rows {
		plan := sunnyPlanTypeFromSessionJSON(sess.SessionJSON)
		if plan == "" {
			plan = "free"
		}
		out[sunnyEmailKey(sess.Email)] = plan
	}
	return out
}

func (s *Server) sunnyAccountPresenceByEmail(emails []string) map[string]bool {
	out := map[string]bool{}
	if len(emails) == 0 {
		return out
	}
	var rows []SunnyAccount
	s.db.Select("email").Where("email IN ?", emails).Find(&rows)
	for _, a := range rows {
		out[sunnyEmailKey(a.Email)] = true
	}
	return out
}

func (s *Server) sunnyMailboxAccessTokensByEmail(emails []string) map[string]string {
	out := map[string]string{}
	if len(emails) == 0 {
		return out
	}
	var accounts []SunnyAccount
	s.db.Select("email", "access_token").Where("email IN ?", emails).Find(&accounts)
	for _, a := range accounts {
		if strings.TrimSpace(a.AccessToken) != "" {
			out[sunnyEmailKey(a.Email)] = a.AccessToken
		}
	}
	var sessions []SunnySession
	s.db.Select("email", "access_token", "session_json").Where("email IN ?", emails).Find(&sessions)
	for _, sess := range sessions {
		key := sunnyEmailKey(sess.Email)
		if out[key] == "" {
			out[key] = fallback(sess.AccessToken, sunnyAccessTokenFromSessionJSON(sess.SessionJSON))
		}
	}
	return out
}

func (s *Server) sunnyAccountIDsByEmail(emails []string) map[string]uint {
	out := map[string]uint{}
	if len(emails) == 0 {
		return out
	}
	var accounts []SunnyAccount
	s.db.Select("id", "email").Where("email IN ?", emails).Find(&accounts)
	for _, account := range accounts {
		out[sunnyEmailKey(account.Email)] = account.ID
	}
	return out
}

type sunnyMailboxLinkedData struct {
	sessionPlans  map[string]string
	accountExists map[string]bool
	accessTokens  map[string]string
	accountIDs    map[string]uint
	accountRTs    map[string]string
}

func (s *Server) sunnyMailboxLinkedDataByEmail(emails []string) sunnyMailboxLinkedData {
	linked := sunnyMailboxLinkedData{
		sessionPlans:  map[string]string{},
		accountExists: map[string]bool{},
		accessTokens:  map[string]string{},
		accountIDs:    map[string]uint{},
		accountRTs:    map[string]string{},
	}
	if len(emails) == 0 {
		return linked
	}
	var accounts []SunnyAccount
	s.db.Select("id", "email", "access_token", "openai_rt").Where("email IN ?", emails).Find(&accounts)
	for _, account := range accounts {
		key := sunnyEmailKey(account.Email)
		linked.accountExists[key] = true
		linked.accountIDs[key] = account.ID
		linked.accountRTs[key] = account.OpenAIRT
		if strings.TrimSpace(account.AccessToken) != "" {
			linked.accessTokens[key] = account.AccessToken
		}
	}
	var sessions []SunnySession
	s.db.Select("email", "access_token", "session_json").Where("email IN ?", emails).Find(&sessions)
	for _, session := range sessions {
		key := sunnyEmailKey(session.Email)
		plan := sunnyPlanTypeFromSessionJSON(session.SessionJSON)
		if plan != "" {
			linked.sessionPlans[key] = plan
		} else if linked.sessionPlans[key] == "" {
			linked.sessionPlans[key] = "free"
		}
		if linked.accessTokens[key] == "" {
			linked.accessTokens[key] = fallback(session.AccessToken, sunnyAccessTokenFromSessionJSON(session.SessionJSON))
		}
	}
	return linked
}

func sunnyPlanTypeForMailbox(m SunnyMailbox, sessionPlans map[string]string, accountExists map[string]bool) string {
	key := sunnyEmailKey(m.Email)
	if plan := normalizeSunnyPlanType(m.AccountType); plan != "" && plan != "free" {
		return plan
	}
	if plan := sessionPlans[key]; plan != "" {
		return plan
	}
	if accountExists[key] || strings.TrimSpace(m.OpenAIRT) != "" || sunnyMailboxStatusLooksRegistered(m.Status) {
		return fallback(normalizeSunnyPlanType(m.AccountType), "free")
	}
	return "-"
}

func (s *Server) sunnyGroupMap() map[uint]string {
	var groups []SunnyMailboxGroup
	s.db.Find(&groups)
	out := map[uint]string{}
	for _, g := range groups {
		out[g.ID] = g.Name
	}
	return out
}

func (s *Server) sunnyMailboxes(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 1 && parts[0] == "config" {
		if r.Method == http.MethodGet {
			cfg := s.sunnyGetConfig(sunnyCfgMailbox, defaultMailboxConfig())
			writeJSON(w, 200, cfg)
			return
		}
		if r.Method == http.MethodPut {
			body, _ := parseBody(r)
			s.sunnySaveConfig(sunnyCfgMailbox, mergeConfig(defaultMailboxConfig(), body))
			writeJSON(w, 200, s.sunnyGetConfig(sunnyCfgMailbox, defaultMailboxConfig()))
			return
		}
	}
	if len(parts) == 0 && r.Method == http.MethodGet {
		q := r.URL.Query()
		summary := boolValue(q.Get("summary"), false)
		page := intValue(q.Get("page"), 1)
		if page < 1 {
			page = 1
		}
		size := intValue(q.Get("page_size"), 10)
		if size < 1 {
			size = 10
		}
		if size > 100 {
			size = 100
		}
		query := s.db.Model(&SunnyMailbox{})
		if gid := intValue(q.Get("group_id"), 0); gid > 0 {
			query = query.Where("group_id = ?", gid)
		}
		if status := q.Get("status"); status != "" {
			query = query.Where("status = ?", status)
		}
		if enabled := strings.TrimSpace(q.Get("enabled")); enabled != "" {
			query = query.Where("enabled = ?", boolValue(enabled, true))
		}
		if kw := strings.TrimSpace(q.Get("q")); kw != "" {
			query = query.Where("email LIKE ?", "%"+kw+"%")
		}
		if planFilter := normalizeSunnyPlanType(q.Get("plan_type")); planFilter != "" {
			var allRows []SunnyMailbox
			allQuery := query
			if summary {
				allQuery = allQuery.Select("id", "group_id", "email", "openai_rt", "account_type", "status", "enabled", "registered_at", "created_at", "updated_at")
			}
			allQuery.Order(sunnySortClause(q.Get("sort_by"), q.Get("sort_order"), map[string]string{"updated_at": "updated_at", "created_at": "created_at", "registered_at": "registered_at"}, "id desc")).Find(&allRows)
			gm := s.sunnyGroupMap()
			emails := []string{}
			for _, m := range allRows {
				emails = append(emails, m.Email)
			}
			linked := s.sunnyMailboxLinkedDataByEmail(emails)
			filtered := []map[string]any{}
			for _, m := range allRows {
				key := sunnyEmailKey(m.Email)
				plan := sunnyPlanTypeForMailbox(m, linked.sessionPlans, linked.accountExists)
				if normalizeSunnyPlanType(plan) == planFilter {
					item := serializeSunnyMailboxList(m, gm, plan, linked.accessTokens[key], linked.accountIDs[key], summary)
					if summary && strings.TrimSpace(linked.accountRTs[key]) != "" {
						item["has_openai_rt"] = true
					}
					filtered = append(filtered, item)
				}
			}
			total := int64(len(filtered))
			start := (page - 1) * size
			if start > len(filtered) {
				start = len(filtered)
			}
			end := start + size
			if end > len(filtered) {
				end = len(filtered)
			}
			writeJSON(w, 200, map[string]any{"items": filtered[start:end], "total": total, "page": page, "page_size": size, "statuses": sunnyMailboxStatuses})
			return
		}
		var total int64
		query.Count(&total)
		var rows []SunnyMailbox
		listQuery := query
		if summary {
			listQuery = listQuery.Select("id", "group_id", "email", "openai_rt", "account_type", "status", "enabled", "registered_at", "created_at", "updated_at")
		}
		listQuery.Order(sunnySortClause(q.Get("sort_by"), q.Get("sort_order"), map[string]string{"updated_at": "updated_at", "created_at": "created_at", "registered_at": "registered_at"}, "id desc")).Offset((page - 1) * size).Limit(size).Find(&rows)
		gm := s.sunnyGroupMap()
		emails := []string{}
		for _, m := range rows {
			emails = append(emails, m.Email)
		}
		linked := s.sunnyMailboxLinkedDataByEmail(emails)
		items := []map[string]any{}
		for _, m := range rows {
			key := sunnyEmailKey(m.Email)
			item := serializeSunnyMailboxList(m, gm, sunnyPlanTypeForMailbox(m, linked.sessionPlans, linked.accountExists), linked.accessTokens[key], linked.accountIDs[key], summary)
			if summary && strings.TrimSpace(linked.accountRTs[key]) != "" {
				item["has_openai_rt"] = true
			}
			items = append(items, item)
		}
		writeJSON(w, 200, map[string]any{"items": items, "total": total, "page": page, "page_size": size, "statuses": sunnyMailboxStatuses})
		return
	}
	if len(parts) == 0 && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		m, err := s.sunnyMailboxFromBody(body)
		if err != nil {
			writeError(w, 400, err.Error())
			return
		}
		if err := s.db.Create(&m).Error; err != nil {
			writeError(w, 400, err.Error())
			return
		}
		writeJSON(w, 200, serializeSunnyMailbox(m, s.sunnyGroupMap(), sunnyPlanTypeForMailbox(m, s.sunnySessionPlanTypesByEmail([]string{m.Email}), s.sunnyAccountPresenceByEmail([]string{m.Email})), s.sunnyMailboxAccessTokensByEmail([]string{m.Email})[sunnyEmailKey(m.Email)]))
		return
	}
	if len(parts) == 1 && parts[0] == "import" && r.Method == http.MethodPost {
		s.sunnyImportMailboxes(w, r)
		return
	}
	if len(parts) >= 1 {
		id := uint(intValue(parts[0], 0))
		var m SunnyMailbox
		if id == 0 || s.db.First(&m, id).Error != nil {
			writeError(w, 404, "mailbox not found")
			return
		}
		if len(parts) == 1 && r.Method == http.MethodGet {
			key := sunnyEmailKey(m.Email)
			linked := s.sunnyMailboxLinkedDataByEmail([]string{m.Email})
			writeJSON(w, 200, serializeSunnyMailboxList(m, s.sunnyGroupMap(), sunnyPlanTypeForMailbox(m, linked.sessionPlans, linked.accountExists), linked.accessTokens[key], linked.accountIDs[key], false))
			return
		}
		if len(parts) == 1 && r.Method == http.MethodPut {
			body, _ := parseBody(r)
			if v := text(body["email"]); v != "" {
				m.Email = v
			}
			if v := text(body["password"]); v != "" {
				m.Password = v
			}
			if v := text(body["client_id"]); v != "" {
				m.ClientID = v
			}
			if v := text(body["refresh_token"]); v != "" {
				m.RefreshToken = v
			}
			if v := text(body["openai_rt"]); v != "" {
				m.OpenAIRT = v
			}
			if v := text(body["raw"]); v != "" {
				if p, err := parseSunnyMailboxLine(v); err == nil {
					m.Email = p["email"]
					m.Password = p["password"]
					m.ClientID = p["client_id"]
					m.RefreshToken = p["refresh_token"]
					m.Raw = v
					if p["openai_rt"] != "" {
						m.OpenAIRT = p["openai_rt"]
					}
				}
			}
			if gid := uint(intValue(body["group_id"], 0)); gid > 0 {
				m.GroupID = gid
			}
			if v := text(body["status"]); v != "" {
				m.Status = v
			}
			if v := fallback(text(body["plan_type"]), text(body["account_type"])); v != "" && v != "-" {
				m.AccountType = normalizeSunnyPlanType(v)
			}
			if _, ok := body["enabled"]; ok {
				m.Enabled = boolValue(body["enabled"], m.Enabled)
			}
			if _, ok := body["last_error"]; ok {
				m.LastError = text(body["last_error"])
			}
			s.db.Save(&m)
			if v, ok := body["access_token"]; ok {
				accessToken := text(v)
				s.db.Model(&SunnyAccount{}).Where("email = ?", m.Email).Update("access_token", accessToken)
				s.db.Model(&SunnySession{}).Where("email = ?", m.Email).Update("access_token", accessToken)
			}
			if m.AccountType != "" {
				s.db.Model(&SunnyAccount{}).Where("email = ?", m.Email).Update("account_type", m.AccountType)
			}
			writeJSON(w, 200, serializeSunnyMailbox(m, s.sunnyGroupMap(), sunnyPlanTypeForMailbox(m, s.sunnySessionPlanTypesByEmail([]string{m.Email}), s.sunnyAccountPresenceByEmail([]string{m.Email})), s.sunnyMailboxAccessTokensByEmail([]string{m.Email})[sunnyEmailKey(m.Email)]))
			return
		}
		if len(parts) == 1 && r.Method == http.MethodDelete {
			s.db.Delete(&m)
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
		if len(parts) == 2 && parts[1] == "latest-mail" && r.Method == http.MethodPost {
			s.sunnyLatestMail(w, r, &m)
			return
		}
	}
	writeError(w, 404, "not found")
}

func (s *Server) sunnyMailboxFromBody(body map[string]any) (SunnyMailbox, error) {
	raw := text(body["raw"])
	email, password, clientID, refreshToken, openaiRT := "", "", "", "", ""
	if raw != "" {
		p, err := parseSunnyMailboxLine(raw)
		if err != nil {
			return SunnyMailbox{}, err
		}
		email, password, clientID, refreshToken, openaiRT = p["email"], p["password"], p["client_id"], p["refresh_token"], p["openai_rt"]
	} else {
		email, password, clientID, refreshToken, openaiRT = text(body["email"]), text(body["password"]), text(body["client_id"]), text(body["refresh_token"]), text(body["openai_rt"])
		raw = strings.Join([]string{email, password, clientID, refreshToken}, "----")
	}
	if email == "" || clientID == "" || refreshToken == "" {
		return SunnyMailbox{}, fmt.Errorf("邮箱格式必须为 email----password----client_id----refresh_token")
	}
	gid := uint(intValue(body["group_id"], 0))
	if gid == 0 {
		gid = s.sunnyEnsureDefaultGroup()
	}
	enabled := boolValue(body["enabled"], true)
	status := fallback(text(body["status"]), "未注册")
	if openaiRT != "" && status == "未注册" {
		status = "已注册"
	}
	return SunnyMailbox{GroupID: gid, Email: email, Password: password, ClientID: clientID, RefreshToken: refreshToken, OpenAIRT: openaiRT, Raw: raw, AccountType: fallback(normalizeSunnyPlanType(fallback(text(body["plan_type"]), text(body["account_type"]))), "free"), Status: status, Enabled: enabled, LatestMailJSON: "{}"}, nil
}

func parseSunnyMailboxLine(raw string) (map[string]string, error) {
	parts := strings.Split(strings.TrimSpace(raw), "----")
	if len(parts) < 4 {
		return nil, fmt.Errorf("格式错误，应为 email----password----client_id----refresh_token")
	}
	email, password, clientID, rt := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1]), strings.TrimSpace(parts[2]), strings.TrimSpace(parts[3])
	if email == "" || !strings.Contains(email, "@") || clientID == "" || rt == "" {
		return nil, fmt.Errorf("email / client_id / refresh_token 涓嶈兘涓虹┖")
	}
	out := map[string]string{"email": email, "password": password, "client_id": clientID, "refresh_token": rt, "openai_rt": ""}
	for _, extra := range parts[4:] {
		extra = strings.TrimSpace(extra)
		lower := strings.ToLower(extra)
		if strings.HasPrefix(lower, "rt_token=") || strings.HasPrefix(lower, "openai_rt=") {
			_, v, _ := strings.Cut(extra, "=")
			out["openai_rt"] = strings.TrimSpace(v)
		}
	}
	return out, nil
}

func (s *Server) sunnyImportMailboxes(w http.ResponseWriter, r *http.Request) {
	body := s.sunnyReadImportBody(r)
	gid := uint(intValue(body["group_id"], 0))
	if gid == 0 && text(body["group_name"]) != "" {
		g := SunnyMailboxGroup{Name: text(body["group_name"])}
		s.db.FirstOrCreate(&g, SunnyMailboxGroup{Name: g.Name})
		gid = g.ID
	}
	if gid == 0 {
		gid = s.sunnyEnsureDefaultGroup()
	}
	lines := strings.Split(text(body["lines"]), "\n")
	ok, bad := 0, []string{}
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			continue
		}
		p, err := parseSunnyMailboxLine(line)
		if err != nil {
			bad = append(bad, line+" => "+err.Error())
			continue
		}
		status := "未注册"
		if p["openai_rt"] != "" {
			status = "已注册"
		}
		m := SunnyMailbox{GroupID: gid, Email: p["email"], Password: p["password"], ClientID: p["client_id"], RefreshToken: p["refresh_token"], OpenAIRT: p["openai_rt"], Raw: strings.Join(strings.Split(strings.TrimSpace(line), "----")[:4], "----"), AccountType: "free", Status: status, Enabled: true, LatestMailJSON: "{}"}
		var old SunnyMailbox
		if err := s.db.First(&old, "email = ?", m.Email).Error; err == nil {
			m.ID, m.CreatedAt = old.ID, old.CreatedAt
			s.db.Save(&m)
		} else {
			s.db.Create(&m)
		}
		ok++
	}
	writeJSON(w, 200, map[string]any{"ok": true, "imported": ok, "failed": len(bad), "errors": bad})
}

func (s *Server) sunnyReadImportBody(r *http.Request) map[string]any {
	ct := r.Header.Get("Content-Type")
	if strings.Contains(ct, "multipart/form-data") {
		_ = r.ParseMultipartForm(32 << 20)
		out := map[string]any{"lines": r.FormValue("lines"), "group_id": r.FormValue("group_id"), "group_name": r.FormValue("group_name")}
		if r.MultipartForm != nil {
			for _, files := range r.MultipartForm.File {
				for _, fh := range files {
					if f, err := fh.Open(); err == nil {
						b, _ := io.ReadAll(io.LimitReader(f, 16<<20))
						_ = f.Close()
						out["lines"] = text(out["lines"]) + "\n" + string(b)
						break
					}
				}
			}
		}
		return out
	}
	body, _ := parseBody(r)
	return body
}

func (s *Server) sunnyLatestMail(w http.ResponseWriter, r *http.Request, m *SunnyMailbox) {
	body, _ := parseBody(r)
	limit := toInt(body["limit"])
	if limit <= 0 {
		limit = toInt(r.URL.Query().Get("limit"))
	}
	if limit <= 0 {
		limit = 5
	}
	if limit > 50 {
		limit = 50
	}
	proxyURL := s.sunnyMailboxProxyURL()
	payload, err := fetchOutlookLatestMail(m.Email, m.ClientID, m.RefreshToken, limit, proxyURL)
	if err != nil {
		m.LastError = err.Error()
		s.db.Save(m)
		writeError(w, 502, err.Error())
		return
	}
	m.LatestMailJSON = dumpJSON(payload)
	m.LastMailAt = sql.NullTime{Time: time.Now(), Valid: true}
	m.LastError = ""
	s.db.Save(m)
	writeJSON(w, 200, payload)
}

// sunnyMailboxProxyURL returns the same enabled proxy used by registration tasks.
// Outlook IMAPS is not reliably reachable directly from all server networks, so
// mailbox reads must use that proxy too instead of silently bypassing it.
func (s *Server) sunnyMailboxProxyURL() string {
	cfg := s.sunnyGetConfig(sunnyCfgProxy, defaultProxyConfig())
	if !boolValue(cfg["proxy_enabled"], true) {
		return ""
	}
	if proxy := normalizeSunnyProxyAddress(text(cfg["register_proxy"])); proxy != "" {
		return proxy
	}
	var row SunnyProxy
	if s.db.Where("status = ? AND enabled = ? AND last_check_ok = ?", "enabled", true, true).
		Order("updated_at desc, id asc").First(&row).Error == nil {
		return normalizeSunnyProxyAddress(row.Address)
	}
	return normalizeSunnyProxyAddress(text(cfg["local_proxy"]))
}

func fetchOutlookLatestMail(email, clientID, refreshToken string, limit int, proxyURL string) (map[string]any, error) {
	// Microsoft can issue a token from more than one compatible endpoint, while
	// IMAP accepts only one of them for certain legacy Outlook accounts. Pair
	// each refresh attempt with IMAP authentication instead of treating the
	// first token response as proof that it is usable for IMAP.
	errors := []string{}
	for _, endpoint := range hotmailTokenEndpoints {
		token, err := refreshHotmailAccessTokenFromEndpoint(clientID, refreshToken, endpoint, proxyURL)
		if err != nil {
			errors = append(errors, endpoint.Name+" token: "+err.Error())
			continue
		}
		items, err := fetchLatestMailsViaIMAP(email, token, limit, proxyURL)
		if err == nil {
			return map[string]any{"email": email, "token_endpoint": endpoint.Name, "items": items, "count": len(items), "limit": limit}, nil
		}
		errors = append(errors, endpoint.Name+" IMAP: "+err.Error())
	}
	return nil, fmt.Errorf("Outlook IMAP authentication failed for all compatible endpoints: %s", strings.Join(errors, " | "))
}

type hotmailTokenEndpoint struct {
	Name     string
	URL      string
	Scope    string
	Resource string
}

var hotmailTokenEndpoints = []hotmailTokenEndpoint{
	{Name: "LIVE", URL: "https://login.live.com/oauth20_token.srf"},
	{Name: "LIVE+scope", URL: "https://login.live.com/oauth20_token.srf", Scope: "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"},
	{Name: "V1-COMMON", URL: "https://login.microsoftonline.com/common/oauth2/token", Resource: "https://outlook.office.com/"},
	{Name: "V1-CONSUMERS", URL: "https://login.microsoftonline.com/consumers/oauth2/token", Resource: "https://outlook.office.com/"},
	{Name: "CONSUMERS", URL: "https://login.microsoftonline.com/consumers/oauth2/v2.0/token", Scope: "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"},
	{Name: "CONSUMERS-noscope", URL: "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"},
	{Name: "COMMON", URL: "https://login.microsoftonline.com/common/oauth2/v2.0/token", Scope: "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"},
	{Name: "COMMON-noscope", URL: "https://login.microsoftonline.com/common/oauth2/v2.0/token"},
}

type hotmailAccessTokenCacheEntry struct {
	Token     string
	Endpoint  string
	ExpiresAt time.Time
}

var hotmailAccessTokenCache sync.Map

func hotmailAccessTokenCacheKey(email, clientID, refreshToken, proxyURL string) string {
	return strings.ToLower(strings.TrimSpace(email)) + "\x00" + strings.TrimSpace(clientID) + "\x00" + strings.TrimSpace(refreshToken) + "\x00" + strings.TrimSpace(proxyURL)
}

func refreshHotmailAccessTokenCached(email, clientID, refreshToken, proxyURL string) (string, string, error) {
	key := hotmailAccessTokenCacheKey(email, clientID, refreshToken, proxyURL)
	if value, ok := hotmailAccessTokenCache.Load(key); ok {
		if entry, ok := value.(hotmailAccessTokenCacheEntry); ok && entry.Token != "" && time.Now().Before(entry.ExpiresAt) {
			return entry.Token, entry.Endpoint, nil
		}
	}
	token, endpoint, err := refreshHotmailAccessToken(email, clientID, refreshToken, proxyURL)
	if err != nil {
		hotmailAccessTokenCache.Delete(key)
		return "", "", err
	}
	hotmailAccessTokenCache.Store(key, hotmailAccessTokenCacheEntry{
		Token:     token,
		Endpoint:  endpoint,
		ExpiresAt: time.Now().Add(50 * time.Minute),
	})
	return token, endpoint, nil
}

func refreshHotmailAccessTokenFromEndpoint(clientID, refreshToken string, ep hotmailTokenEndpoint, proxyURL string) (string, error) {
	client := &http.Client{Timeout: 20 * time.Second}
	if proxyURL != "" {
		if u, err := url.Parse(proxyURL); err == nil {
			client.Transport = &http.Transport{Proxy: http.ProxyURL(u)}
		}
	}
	form := url.Values{}
	form.Set("client_id", clientID)
	form.Set("grant_type", "refresh_token")
	form.Set("refresh_token", refreshToken)
	if ep.Scope != "" {
		form.Set("scope", ep.Scope)
	}
	if ep.Resource != "" {
		form.Set("resource", ep.Resource)
	}
	req, _ := http.NewRequest(http.MethodPost, ep.URL, strings.NewReader(form.Encode()))
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	var payload map[string]any
	_ = json.Unmarshal(raw, &payload)
	if resp.StatusCode >= 200 && resp.StatusCode < 300 && text(payload["access_token"]) != "" {
		return text(payload["access_token"]), nil
	}
	return "", fmt.Errorf("%s", firstText(payload["error_description"], payload["error"], fmt.Sprintf("HTTP %d", resp.StatusCode)))
}

func refreshHotmailAccessToken(email, clientID, refreshToken, proxyURL string) (string, string, error) {
	client := &http.Client{Timeout: 20 * time.Second}
	if proxyURL != "" {
		if u, err := url.Parse(proxyURL); err == nil {
			client.Transport = &http.Transport{Proxy: http.ProxyURL(u)}
		}
	}
	errors := []string{}
	for _, ep := range hotmailTokenEndpoints {
		form := url.Values{}
		form.Set("client_id", clientID)
		form.Set("grant_type", "refresh_token")
		form.Set("refresh_token", refreshToken)
		if ep.Scope != "" {
			form.Set("scope", ep.Scope)
		}
		if ep.Resource != "" {
			form.Set("resource", ep.Resource)
		}
		req, _ := http.NewRequest(http.MethodPost, ep.URL, strings.NewReader(form.Encode()))
		req.Header.Set("Accept", "application/json")
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		resp, err := client.Do(req)
		if err != nil {
			errors = append(errors, ep.Name+": "+err.Error())
			continue
		}
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
		_ = resp.Body.Close()
		var payload map[string]any
		_ = json.Unmarshal(raw, &payload)
		if resp.StatusCode >= 200 && resp.StatusCode < 300 && text(payload["access_token"]) != "" {
			return text(payload["access_token"]), ep.Name, nil
		}
		msg := firstText(payload["error_description"], payload["error"], fmt.Sprintf("HTTP %d %s", resp.StatusCode, string(raw[:min(len(raw), 260)])))
		errors = append(errors, ep.Name+": "+msg)
	}
	return "", "", fmt.Errorf("Outlook token 刷新失败，所有 sunny 兼容端点均失败：%s", strings.Join(errors, " | "))
}

func dialOutlookIMAPS(proxyURL string) (*tls.Conn, error) {
	const host = "outlook.office365.com"
	const target = host + ":993"
	const timeout = 30 * time.Second
	if strings.TrimSpace(proxyURL) == "" {
		return tls.DialWithDialer(&net.Dialer{Timeout: timeout}, "tcp", target, &tls.Config{ServerName: host})
	}

	proxy, err := url.Parse(proxyURL)
	if err != nil || proxy.Hostname() == "" {
		return nil, fmt.Errorf("invalid IMAP proxy URL")
	}
	if scheme := strings.ToLower(proxy.Scheme); scheme != "http" && scheme != "https" {
		return nil, fmt.Errorf("IMAP proxy only supports HTTP CONNECT: %s", proxy.Scheme)
	}
	port := proxy.Port()
	if port == "" {
		port = "80"
	}
	raw, err := (&net.Dialer{Timeout: timeout}).Dial("tcp", net.JoinHostPort(proxy.Hostname(), port))
	if err != nil {
		return nil, fmt.Errorf("IMAP proxy dial failed: %w", err)
	}
	closeRaw := true
	defer func() {
		if closeRaw {
			_ = raw.Close()
		}
	}()
	_ = raw.SetDeadline(time.Now().Add(timeout))

	request := []string{
		"CONNECT " + target + " HTTP/1.1",
		"Host: " + target,
		"Proxy-Connection: keep-alive",
		"User-Agent: SunnyRegister/1.0",
	}
	if proxy.User != nil {
		password, _ := proxy.User.Password()
		auth := base64.StdEncoding.EncodeToString([]byte(proxy.User.Username() + ":" + password))
		request = append(request, "Proxy-Authorization: Basic "+auth)
	}
	if _, err := io.WriteString(raw, strings.Join(request, "\r\n")+"\r\n\r\n"); err != nil {
		return nil, fmt.Errorf("IMAP proxy CONNECT write failed: %w", err)
	}
	// Keep one buffered reader for the whole CONNECT response: creating a new
	// reader after the status line could discard headers already buffered by the
	// first reader.
	reader := bufio.NewReader(raw)
	response, err := reader.ReadString('\n')
	if err != nil {
		return nil, fmt.Errorf("IMAP proxy CONNECT response failed: %w", err)
	}
	if !strings.Contains(response, " 200 ") {
		return nil, fmt.Errorf("IMAP proxy CONNECT failed: %s", strings.TrimSpace(response))
	}
	// Consume the remaining HTTP response headers before beginning TLS.
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return nil, fmt.Errorf("IMAP proxy CONNECT headers failed: %w", err)
		}
		if line == "\r\n" || line == "\n" {
			break
		}
	}
	_ = raw.SetDeadline(time.Time{})
	conn := tls.Client(raw, &tls.Config{ServerName: host})
	if err := conn.Handshake(); err != nil {
		return nil, fmt.Errorf("IMAP TLS handshake via proxy failed: %w", err)
	}
	closeRaw = false
	return conn, nil
}

func fetchLatestMailViaIMAP(emailAddr, accessToken string) (map[string]any, error) {
	items, err := fetchLatestMailsViaIMAP(emailAddr, accessToken, 1, "")
	if err != nil {
		return nil, err
	}
	if len(items) == 0 {
		return map[string]any{"empty": true}, nil
	}
	return items[0], nil
}

func fetchLatestMailsViaIMAP(emailAddr, accessToken string, limit int, proxyURL string) ([]map[string]any, error) {
	if limit < 1 {
		limit = 5
	}
	if limit > 50 {
		limit = 50
	}
	conn, err := dialOutlookIMAPS(proxyURL)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	reader := bufio.NewReader(conn)
	if _, err := reader.ReadString('\n'); err != nil {
		return nil, fmt.Errorf("IMAP greeting failed: %w", err)
	}
	write := func(format string, args ...any) error {
		_, err := fmt.Fprintf(conn, format+"\r\n", args...)
		return err
	}
	readUntil := func(tag string) (string, error) {
		var b strings.Builder
		for {
			line, err := reader.ReadString('\n')
			if err != nil {
				return b.String(), err
			}
			b.WriteString(line)
			if strings.HasPrefix(line, tag+" ") {
				return b.String(), nil
			}
		}
	}
	auth := base64.StdEncoding.EncodeToString([]byte(fmt.Sprintf("user=%s\x01auth=Bearer %s\x01\x01", emailAddr, accessToken)))
	if err := write("A1 AUTHENTICATE XOAUTH2 %s", auth); err != nil {
		return nil, err
	}
	// Outlook may issue a SASL error continuation before it sends A1 NO. Send
	// the required empty response so rejected candidates fail immediately rather
	// than leaving the request blocked until the socket timeout.
	var authOut strings.Builder
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return nil, fmt.Errorf("IMAP XOAUTH2 response failed: %w", err)
		}
		authOut.WriteString(line)
		if strings.HasPrefix(line, "+") {
			if err := write(""); err != nil {
				return nil, err
			}
			continue
		}
		if strings.HasPrefix(line, "A1 ") {
			break
		}
	}
	if !strings.Contains(authOut.String(), "A1 OK") {
		return nil, fmt.Errorf("IMAP XOAUTH2 authentication failed: %s", strings.TrimSpace(authOut.String()))
	}
	if err := write("A2 SELECT INBOX"); err != nil {
		return nil, err
	}
	selectOut, err := readUntil("A2")
	if err != nil || !strings.Contains(selectOut, "A2 OK") {
		return nil, fmt.Errorf("IMAP SELECT INBOX 失败: %s", strings.TrimSpace(selectOut))
	}
	totalMessages := 0
	for _, line := range strings.Split(selectOut, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "* ") && strings.HasSuffix(line, " EXISTS") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				totalMessages = toInt(fields[1])
				break
			}
		}
	}
	if totalMessages <= 0 {
		return []map[string]any{}, nil
	}
	startSeq := totalMessages - limit + 1
	if startSeq < 1 {
		startSeq = 1
	}
	const maxIMAPMailBytes = 384 * 1024
	tag := "A3"
	if err := write("%s FETCH %d:%d BODY.PEEK[]<0.%d>", tag, startSeq, totalMessages, maxIMAPMailBytes); err != nil {
		return nil, err
	}
	rawBatch, err := readUntil(tag)
	if err != nil {
		return nil, err
	}
	items := []map[string]any{}
	for seq := totalMessages; seq >= startSeq; seq-- {
		msgID := strconv.Itoa(seq)
		raw := extractIMAPFetchFragment(rawBatch, seq, totalMessages, tag)
		if raw == "" {
			continue
		}
		item := parseIMAPMailItem(msgID, raw, tag)
		if item != nil {
			item["email"] = emailAddr
			items = append(items, item)
		}
	}
	return items, nil
}

func extractIMAPFetchFragment(raw string, seq, endSeq int, tag string) string {
	marker := fmt.Sprintf("* %d FETCH", seq)
	start := strings.Index(raw, marker)
	if start < 0 {
		marker = "\r\n" + marker
		start = strings.Index(raw, marker)
		if start >= 0 {
			start += 2
		}
	}
	if start < 0 {
		return ""
	}
	end := len(raw)
	for next := seq + 1; next <= endSeq; next++ {
		nextMarker := fmt.Sprintf("\r\n* %d FETCH", next)
		if pos := strings.Index(raw[start+1:], nextMarker); pos >= 0 {
			end = start + 1 + pos
			break
		}
	}
	if tagPos := strings.LastIndex(raw, "\r\n"+tag+" "); tagPos >= 0 && tagPos > start && tagPos < end {
		end = tagPos
	}
	if end <= start {
		return ""
	}
	return raw[start:end] + "\r\n" + tag + " OK\r\n"
}

func parseIMAPMailItem(msgID, raw, tag string) map[string]any {
	start := strings.Index(raw, "\r\n")
	end := strings.LastIndex(raw, "\r\n"+tag+" ")
	if start >= 0 && end > start {
		raw = raw[start+2 : end]
	}
	m, err := mail.ReadMessage(strings.NewReader(raw))
	if err != nil {
		return map[string]any{"id": msgID, "parse_error": err.Error(), "raw_preview": strings.TrimSpace(raw[:min(len(raw), 1200)])}
	}
	decoder := &mime.WordDecoder{CharsetReader: mailCharsetReader}
	subject := m.Header.Get("Subject")
	if dec, derr := decoder.DecodeHeader(subject); derr == nil {
		subject = dec
	}
	from := m.Header.Get("From")
	if dec, derr := decoder.DecodeHeader(from); derr == nil {
		from = dec
	}
	bodyText, bodyHTML := extractMailBodies(textproto.MIMEHeader(m.Header), m.Body)
	bodyRaw := bodyHTML
	if bodyRaw == "" {
		bodyRaw = bodyText
	}
	if bodyText == "" && bodyHTML != "" {
		bodyText = html.UnescapeString(regexp.MustCompile(`<[^>]+>`).ReplaceAllString(bodyHTML, " "))
	}
	bodyText = strings.TrimSpace(regexp.MustCompile(`\s+`).ReplaceAllString(bodyText, " "))
	otp := ""
	for _, pat := range []string{`(?i)(?:OpenAI|ChatGPT|verification|verify|code)[^\d]{0,120}(\d{6})`, `\b(\d{6})\b`} {
		if match := regexp.MustCompile(pat).FindStringSubmatch(subject + "\n" + bodyText); len(match) > 1 {
			otp = match[1]
			break
		}
	}
	return map[string]any{
		"id": msgID, "subject": subject, "from": from, "to": m.Header.Get("To"),
		"date": m.Header.Get("Date"), "body": bodyText, "body_preview": strings.TrimSpace(bodyText[:min(len(bodyText), 1200)]),
		"raw_html": bodyRaw, "otp": otp,
	}
}

func mailCharsetReader(charset string, input io.Reader) (io.Reader, error) {
	encoding, err := htmlindex.Get(strings.TrimSpace(charset))
	if err != nil {
		return nil, err
	}
	return encoding.NewDecoder().Reader(input), nil
}

func decodeMailBytes(raw []byte, charset string) string {
	charset = strings.TrimSpace(charset)
	if charset == "" || strings.EqualFold(charset, "utf-8") || strings.EqualFold(charset, "us-ascii") {
		return string(raw)
	}
	encoding, err := htmlindex.Get(charset)
	if err != nil {
		return string(raw)
	}
	decoded, err := encoding.NewDecoder().Bytes(raw)
	if err != nil {
		return string(raw)
	}
	return string(decoded)
}

func htmlDeclaredCharset(raw []byte) string {
	// Some legacy mail omits charset in Content-Type but provides it in an HTML
	// meta tag. Decode that declaration before handing HTML to the browser.
	match := regexp.MustCompile(`(?i)<meta[^>]+charset\s*=\s*["']?\s*([a-z0-9._-]+)`).FindSubmatch(raw)
	if len(match) > 1 {
		return string(match[1])
	}
	match = regexp.MustCompile(`(?i)<meta[^>]+content\s*=\s*["'][^"']*charset\s*=\s*([a-z0-9._-]+)`).FindSubmatch(raw)
	if len(match) > 1 {
		return string(match[1])
	}
	return ""
}

func extractMailBodies(header textproto.MIMEHeader, body io.Reader) (string, string) {
	mediaType, params, _ := mime.ParseMediaType(header.Get("Content-Type"))
	mediaType = strings.ToLower(mediaType)
	if strings.HasPrefix(mediaType, "multipart/") && params["boundary"] != "" {
		mr := multipart.NewReader(body, params["boundary"])
		texts, htmls := []string{}, []string{}
		for {
			part, err := mr.NextPart()
			if err != nil {
				break
			}
			t, h := extractMailBodies(part.Header, part)
			if t != "" {
				texts = append(texts, t)
			}
			if h != "" {
				htmls = append(htmls, h)
			}
		}
		return strings.Join(texts, "\n"), strings.Join(htmls, "\n")
	}
	// Attachments such as PNG/PDF must not be decoded as mail text; doing so
	// produced binary garbage in the preview.
	if mediaType != "text/plain" && mediaType != "text/html" {
		return "", ""
	}
	reader := body
	switch strings.ToLower(strings.TrimSpace(header.Get("Content-Transfer-Encoding"))) {
	case "quoted-printable":
		reader = quotedprintable.NewReader(body)
	case "base64":
		reader = base64.NewDecoder(base64.StdEncoding, body)
	}
	raw, _ := io.ReadAll(io.LimitReader(reader, 4<<20))
	charset := params["charset"]
	if charset == "" && mediaType == "text/html" {
		charset = htmlDeclaredCharset(raw)
	}
	value := decodeMailBytes(raw, charset)
	if mediaType == "text/html" {
		return "", value
	}
	return value, ""
}

func defaultPhoneConfig() map[string]any {
	return map[string]any{
		"pool_enabled":             true,
		"smsbower_enabled":         false,
		"smsbower_base_url":        "https://smsbower.page/stubs/handler_api.php",
		"smsbower_api_key":         "",
		"smsbower_default_country": "187",
		"smsbower_default_service": "dr",
		"smsbower_max_price":       -1,
		"smspool_enabled":          false,
		"smspool_base_url":         "https://api.smspool.net",
		"smspool_api_key":          "",
		"smspool_default_country":  "1",
		"smspool_default_service":  "671",
		"smspool_max_price":        -1,
	}
}
func defaultMailboxConfig() map[string]any { return map[string]any{"pool_enabled": true} }

func (s *Server) sunnyPhones(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 1 && parts[0] == "config" && r.Method == http.MethodGet {
		cfg := s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig())
		cfg["usable_count"] = s.sunnyUsablePhoneCount()
		cfg["total_count"] = s.sunnyPhoneTotalCount()
		writeJSON(w, 200, cfg)
		return
	}
	if len(parts) == 1 && parts[0] == "config" && r.Method == http.MethodPut {
		body, _ := parseBody(r)
		s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), body))
		writeJSON(w, 200, s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig()))
		return
	}
	if len(parts) == 2 && parts[0] == "smsbower" && parts[1] == "check" && r.Method == http.MethodPost {
		s.sunnyCheckSMSBower(w, r)
		return
	}
	if len(parts) == 2 && parts[0] == "smspool" && parts[1] == "check" && r.Method == http.MethodPost {
		s.sunnyCheckSMSPool(w, r)
		return
	}
	if len(parts) == 1 && parts[0] == "provider-options" && (r.Method == http.MethodGet || r.Method == http.MethodPost) {
		s.sunnySMSProviderOptions(w, r)
		return
	}
	if len(parts) == 0 && r.Method == http.MethodGet {
		var rows []SunnyPhone
		page := intValue(r.URL.Query().Get("page"), 1)
		if page < 1 {
			page = 1
		}
		pageSize := intValue(r.URL.Query().Get("page_size"), 10)
		if pageSize < 1 {
			pageSize = 10
		}
		if pageSize > 100 {
			pageSize = 100
		}
		q := strings.TrimSpace(r.URL.Query().Get("q"))
		status := strings.TrimSpace(r.URL.Query().Get("status"))
		countFilter := strings.TrimSpace(r.URL.Query().Get("count"))
		query := s.db.Model(&SunnyPhone{})
		if q != "" {
			query = query.Where("number LIKE ?", "%"+q+"%")
		}
		switch status {
		case "enabled", "available":
			query = query.Where("enabled = ?", true)
		case "disabled":
			query = query.Where("enabled = ? OR status = ?", false, "disabled")
		}
		if countFilter != "" && countFilter != "all" {
			count := intValue(countFilter, -1)
			if count >= 0 && count <= 3 {
				query = query.Where("success_count = ?", count)
			}
		}
		var total int64
		query.Count(&total)
		query.Order(sunnySortClause(r.URL.Query().Get("sort_by"), r.URL.Query().Get("sort_order"), map[string]string{"last_used_at": "last_used_at", "updated_at": "updated_at", "created_at": "created_at", "cooldown_until": "cooldown_until"}, "id desc")).Limit(pageSize).Offset((page - 1) * pageSize).Find(&rows)
		items := make([]map[string]any, 0, len(rows))
		for _, row := range rows {
			items = append(items, serializeSunnyPhone(row))
		}
		writeJSON(w, 200, map[string]any{"items": items, "total": total, "page": page, "page_size": pageSize, "now": time.Now().Format(time.RFC3339)})
		return
	}
	if len(parts) == 0 && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		p, err := sunnyPhoneFromBody(body)
		if err != nil {
			writeError(w, 400, err.Error())
			return
		}
		if err := s.db.Create(&p).Error; err != nil {
			writeError(w, 400, err.Error())
			return
		}
		writeJSON(w, 200, serializeSunnyPhone(p))
		return
	}
	if len(parts) == 1 && parts[0] == "import" && r.Method == http.MethodPost {
		body := s.sunnyReadImportBody(r)
		ok, bad := 0, []string{}
		for _, line := range strings.Split(text(body["lines"]), "\n") {
			if strings.TrimSpace(line) == "" {
				continue
			}
			p, err := parseSunnyPhoneLine(line)
			if err != nil {
				bad = append(bad, line+" => "+err.Error())
				continue
			}
			var old SunnyPhone
			if err := s.db.First(&old, "number = ?", p.Number).Error; err == nil {
				p.ID, p.CreatedAt, p.SuccessCount, p.CooldownUntil, p.LastUsedAt = old.ID, old.CreatedAt, old.SuccessCount, old.CooldownUntil, old.LastUsedAt
				s.db.Save(&p)
			} else {
				s.db.Create(&p)
			}
			ok++
		}
		writeJSON(w, 200, map[string]any{"ok": true, "imported": ok, "failed": len(bad), "errors": bad})
		return
	}
	if len(parts) == 1 {
		id := uint(intValue(parts[0], 0))
		var p SunnyPhone
		if id == 0 || s.db.First(&p, id).Error != nil {
			writeError(w, 404, "phone not found")
			return
		}
		if r.Method == http.MethodPut {
			body, _ := parseBody(r)
			next, err := sunnyPhoneFromBody(body)
			if err != nil {
				writeError(w, 400, err.Error())
				return
			}
			next.ID, next.CreatedAt, next.CooldownUntil, next.LastUsedAt, next.LastCode, next.LastError = p.ID, p.CreatedAt, p.CooldownUntil, p.LastUsedAt, p.LastCode, p.LastError
			s.db.Save(&next)
			writeJSON(w, 200, serializeSunnyPhone(next))
			return
		}
		if r.Method == http.MethodDelete {
			s.db.Delete(&p)
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
	}
	writeError(w, 404, "not found")
}

func (s *Server) sunnyCheckSMSBower(w http.ResponseWriter, r *http.Request) {
	body, _ := parseBody(r)
	cfg := mergeConfig(s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig()), body)
	apiKey := strings.TrimSpace(text(cfg["smsbower_api_key"]))
	if apiKey == "" {
		writeError(w, 400, "SMSBower API Key is required")
		return
	}
	baseURL := strings.TrimSpace(text(cfg["smsbower_base_url"]))
	if baseURL == "" {
		baseURL = "https://smsbower.page/stubs/handler_api.php"
	}
	params := url.Values{}
	params.Set("api_key", apiKey)
	params.Set("action", "getBalance")
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, baseURL+"?"+params.Encode(), nil)
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	client := &http.Client{Timeout: 20 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	raw := strings.TrimSpace(string(b))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		writeError(w, 400, fmt.Sprintf("SMSBower HTTP %d: %s", resp.StatusCode, raw))
		return
	}
	if !strings.HasPrefix(raw, "ACCESS_BALANCE:") {
		writeError(w, 400, raw)
		return
	}
	balance := strings.TrimPrefix(raw, "ACCESS_BALANCE:")
	writeJSON(w, 200, map[string]any{"ok": true, "balance": balance, "raw": raw})
}

func (s *Server) sunnyCheckSMSPool(w http.ResponseWriter, r *http.Request) {
	body, _ := parseBody(r)
	cfg := mergeConfig(s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig()), body)
	apiKey := strings.TrimSpace(text(cfg["smspool_api_key"]))
	if apiKey == "" {
		writeError(w, 400, "SMSPool API Key is required")
		return
	}
	baseURL := strings.TrimRight(strings.TrimSpace(text(cfg["smspool_base_url"])), "/")
	if baseURL == "" {
		baseURL = "https://api.smspool.net"
	}
	raw, status, err := postSunnyMultipart(r.Context(), baseURL+"/request/balance", apiKey, map[string]string{"key": apiKey})
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	if status < 200 || status >= 300 {
		writeError(w, 400, fmt.Sprintf("SMSPool HTTP %d: %s", status, raw))
		return
	}
	data := jsonMap(raw)
	balance := strings.TrimSpace(text(data["balance"]))
	if balance == "" {
		if msg := firstText(data["message"], data["type"]); msg != "" {
			writeError(w, 400, msg)
			return
		}
		balance = raw
	}
	writeJSON(w, 200, map[string]any{"ok": true, "balance": balance, "raw": raw})
}

func postSunnyMultipart(ctx context.Context, targetURL string, apiKey string, fields map[string]string) (string, int, error) {
	var requestBody bytes.Buffer
	writer := multipart.NewWriter(&requestBody)
	for k, v := range fields {
		if err := writer.WriteField(k, v); err != nil {
			return "", 0, err
		}
	}
	if err := writer.Close(); err != nil {
		return "", 0, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, targetURL, &requestBody)
	if err != nil {
		return "", 0, err
	}
	req.Header.Set("Content-Type", writer.FormDataContentType())
	req.Header.Set("Accept", "application/json")
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	resp, err := (&http.Client{Timeout: 20 * time.Second}).Do(req)
	if err != nil {
		return "", 0, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return strings.TrimSpace(string(b)), resp.StatusCode, nil
}

func (s *Server) sunnySMSProviderOptions(w http.ResponseWriter, r *http.Request) {
	body := map[string]any{}
	if r.Method == http.MethodPost {
		body, _ = parseBody(r)
	}
	q := r.URL.Query()
	provider := strings.ToLower(strings.TrimSpace(fallback(text(body["provider"]), q.Get("provider"))))
	kind := strings.ToLower(strings.TrimSpace(fallback(text(body["kind"]), q.Get("kind"))))
	parent := strings.TrimSpace(fallback(text(body["country"]), q.Get("country")))
	refresh := boolValue(firstText(body["refresh"], q.Get("refresh")), false)
	if provider != "smsbower" && provider != "smspool" {
		writeError(w, 400, "invalid sms provider")
		return
	}
	if kind != "countries" && kind != "services" {
		writeError(w, 400, "invalid option kind")
		return
	}
	cacheKind := strings.TrimSuffix(kind, "s")
	if !refresh {
		items := s.sunnyCachedSMSProviderOptions(provider, cacheKind, parent)
		if len(items) > 0 {
			writeJSON(w, 200, map[string]any{"items": items, "cached": true})
			return
		}
	}
	cfg := mergeConfig(s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig()), body)
	items, err := s.sunnyFetchSMSProviderOptions(r.Context(), provider, cacheKind, parent, cfg)
	if err != nil {
		cached := s.sunnyCachedSMSProviderOptions(provider, cacheKind, parent)
		if len(cached) > 0 {
			writeJSON(w, 200, map[string]any{"items": cached, "cached": true, "warning": err.Error()})
			return
		}
		writeError(w, 400, err.Error())
		return
	}
	s.sunnySaveSMSProviderOptions(provider, cacheKind, parent, items)
	writeJSON(w, 200, map[string]any{"items": items, "cached": false})
}

func (s *Server) sunnyCachedSMSProviderOptions(provider, kind, parent string) []map[string]any {
	var rows []SunnySMSProviderOption
	q := s.db.Where("provider = ? AND kind = ?", provider, kind)
	if kind == "service" && parent != "" {
		q = q.Where("parent_value = ?", parent)
	}
	q.Order("label asc").Find(&rows)
	items := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		items = append(items, map[string]any{"value": row.Value, "label": row.Label, "provider": row.Provider, "kind": row.Kind, "parent_value": row.ParentValue, "extra": jsonMap(row.ExtraJSON)})
	}
	return items
}

func (s *Server) sunnySaveSMSProviderOptions(provider, kind, parent string, items []map[string]any) {
	for _, item := range items {
		value := strings.TrimSpace(text(item["value"]))
		if value == "" {
			continue
		}
		label := strings.TrimSpace(fallback(text(item["label"]), value))
		var row SunnySMSProviderOption
		err := s.db.First(&row, "provider = ? AND kind = ? AND parent_value = ? AND value = ?", provider, kind, parent, value).Error
		if err != nil {
			row = SunnySMSProviderOption{Provider: provider, Kind: kind, ParentValue: parent, Value: value}
		}
		row.Label = label
		row.ExtraJSON = dumpJSON(item["extra"])
		s.db.Save(&row)
	}
}

func (s *Server) sunnyFetchSMSProviderOptions(ctx context.Context, provider, kind, parent string, cfg map[string]any) ([]map[string]any, error) {
	switch provider {
	case "smsbower":
		return fetchSMSBowerOptions(ctx, kind, parent, cfg)
	case "smspool":
		return fetchSMSPoolOptions(ctx, kind, parent, cfg)
	default:
		return nil, fmt.Errorf("invalid sms provider")
	}
}

func (s *Server) sunnyWarmSMSProviderOptions() {
	time.Sleep(800 * time.Millisecond)
	cfg := s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig())
	providers := []struct {
		name       string
		enabledKey string
		apiKey     string
		countryKey string
	}{
		{name: "smsbower", enabledKey: "smsbower_enabled", apiKey: "smsbower_api_key", countryKey: "smsbower_default_country"},
		{name: "smspool", enabledKey: "smspool_enabled", apiKey: "smspool_api_key", countryKey: "smspool_default_country"},
	}
	for _, p := range providers {
		if !boolValue(cfg[p.enabledKey], false) || strings.TrimSpace(text(cfg[p.apiKey])) == "" {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
		if len(s.sunnyCachedSMSProviderOptions(p.name, "country", "")) == 0 {
			if items, err := s.sunnyFetchSMSProviderOptions(ctx, p.name, "country", "", cfg); err == nil {
				s.sunnySaveSMSProviderOptions(p.name, "country", "", items)
			}
		}
		parent := strings.TrimSpace(text(cfg[p.countryKey]))
		if parent != "" && len(s.sunnyCachedSMSProviderOptions(p.name, "service", parent)) == 0 {
			if items, err := s.sunnyFetchSMSProviderOptions(ctx, p.name, "service", parent, cfg); err == nil {
				s.sunnySaveSMSProviderOptions(p.name, "service", parent, items)
			}
		}
		cancel()
	}
}

func fetchSMSBowerOptions(ctx context.Context, kind, parent string, cfg map[string]any) ([]map[string]any, error) {
	apiKey := strings.TrimSpace(text(cfg["smsbower_api_key"]))
	if apiKey == "" {
		return nil, fmt.Errorf("SMSBower API Key is required")
	}
	baseURL := strings.TrimSpace(text(cfg["smsbower_base_url"]))
	if baseURL == "" {
		baseURL = "https://smsbower.page/stubs/handler_api.php"
	}
	params := url.Values{}
	params.Set("api_key", apiKey)
	if kind == "country" {
		params.Set("action", "getCountries")
	} else {
		params.Set("action", "getServicesList")
		params.Set("lang", "en")
		if parent != "" {
			params.Set("country", parent)
		}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+"?"+params.Encode(), nil)
	if err != nil {
		return nil, err
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("SMSBower HTTP %d: %s", resp.StatusCode, string(b)[:min(len(b), 500)])
	}
	var data any
	if err := json.Unmarshal(b, &data); err != nil {
		return nil, err
	}
	if kind == "country" {
		return normalizeSMSProviderOptions(data, []string{"id", "country", "ID", "code"}, []string{"chn", "eng", "name", "label"}, "country"), nil
	}
	return normalizeSMSProviderOptions(data, []string{"code", "id", "service", "ID"}, []string{"name", "label", "title"}, "service"), nil
}

func fetchSMSPoolOptions(ctx context.Context, kind, parent string, cfg map[string]any) ([]map[string]any, error) {
	apiKey := strings.TrimSpace(text(cfg["smspool_api_key"]))
	baseURL := strings.TrimRight(strings.TrimSpace(text(cfg["smspool_base_url"])), "/")
	if baseURL == "" {
		baseURL = "https://api.smspool.net"
	}
	path := "/country/retrieve_all"
	if kind == "service" {
		path = "/service/retrieve_all"
		if parent != "" {
			path += "?country=" + url.QueryEscape(parent)
		}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, baseURL+path, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	resp, err := (&http.Client{Timeout: 30 * time.Second}).Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("SMSPool HTTP %d: %s", resp.StatusCode, string(b)[:min(len(b), 500)])
	}
	var data any
	if err := json.Unmarshal(b, &data); err != nil {
		return nil, err
	}
	if kind == "country" {
		return normalizeSMSProviderOptions(data, []string{"ID", "id", "country_id", "short_name"}, []string{"name", "short_name", "region"}, "country"), nil
	}
	return normalizeSMSProviderOptions(data, []string{"ID", "id", "code", "name"}, []string{"name", "code"}, "service"), nil
}

func normalizeSMSProviderOptions(data any, valueKeys []string, labelKeys []string, kind string) []map[string]any {
	var arr []any
	switch v := data.(type) {
	case []any:
		arr = v
	case map[string]any:
		for _, key := range []string{"countries", "services", "data", "items", "result"} {
			if x, ok := v[key].([]any); ok {
				arr = x
				break
			}
		}
		if len(arr) == 0 {
			for key, value := range v {
				if child, ok := value.(map[string]any); ok {
					child["value"] = key
					arr = append(arr, child)
				}
			}
		}
	}
	seen := map[string]bool{}
	items := []map[string]any{}
	for _, raw := range arr {
		m, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		value := firstByKeys(m, valueKeys...)
		label := firstByKeys(m, labelKeys...)
		if value == "" {
			continue
		}
		if label == "" {
			label = value
		}
		if seen[value] {
			continue
		}
		seen[value] = true
		items = append(items, map[string]any{"value": value, "label": label, "kind": kind, "extra": m})
	}
	sort.SliceStable(items, func(i, j int) bool {
		return strings.ToLower(text(items[i]["label"])) < strings.ToLower(text(items[j]["label"]))
	})
	return items
}

func firstByKeys(m map[string]any, keys ...string) string {
	for _, key := range keys {
		if v := strings.TrimSpace(text(m[key])); v != "" {
			return v
		}
	}
	return ""
}

func sunnyPhoneFromBody(body map[string]any) (SunnyPhone, error) {
	if raw := text(body["raw"]); raw != "" {
		return parseSunnyPhoneLine(raw)
	}
	number, smsURL := text(body["number"]), text(body["sms_url"])
	if number == "" || smsURL == "" || !strings.HasPrefix(number, "+") || !strings.HasPrefix(strings.ToLower(smsURL), "http") {
		return SunnyPhone{}, fmt.Errorf("invalid phone format: +phone----https://sms-url")
	}
	statusRaw := strings.ToLower(strings.TrimSpace(text(body["status"])))
	enabled := boolValue(body["enabled"], statusRaw != "disabled")
	status := "available"
	if statusRaw == "disabled" || !enabled {
		status = "disabled"
		enabled = false
	} else {
		enabled = true
	}
	maxSuccess := intValue(body["max_success"], 3)
	if maxSuccess < 1 {
		maxSuccess = 3
	}
	successCount := intValue(body["success_count"], 0)
	if successCount < 0 {
		successCount = 0
	}
	if successCount > maxSuccess {
		successCount = maxSuccess
	}
	return SunnyPhone{Number: number, SmsURL: smsURL, Status: status, Enabled: enabled, SuccessCount: successCount, MaxSuccess: maxSuccess}, nil
}

func parseSunnyPhoneLine(line string) (SunnyPhone, error) {
	raw := strings.TrimSpace(line)
	parts := strings.Split(raw, "----")
	if len(parts) != 2 {
		return SunnyPhone{}, fmt.Errorf("invalid phone format: +phone----https://sms-url")
	}
	number, smsURL := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
	if number == "" || !strings.HasPrefix(number, "+") || smsURL == "" || !strings.HasPrefix(strings.ToLower(smsURL), "http") {
		return SunnyPhone{}, fmt.Errorf("invalid phone format: +phone----https://sms-url")
	}
	return SunnyPhone{Number: number, SmsURL: smsURL, Status: "available", Enabled: true, MaxSuccess: 3}, nil
}

func serializeSunnyPhone(p SunnyPhone) map[string]any {
	displayStatus := "enabled"
	if !p.Enabled || p.Status == "disabled" {
		displayStatus = "disabled"
	}
	return map[string]any{
		"id":             p.ID,
		"number":         p.Number,
		"sms_url":        p.SmsURL,
		"status":         p.Status,
		"display_status": displayStatus,
		"enabled":        p.Enabled,
		"success_count":  p.SuccessCount,
		"max_success":    p.MaxSuccess,
		"cooldown_until": nullableTime(p.CooldownUntil.Valid, p.CooldownUntil.Time),
		"last_used_at":   nullableTime(p.LastUsedAt.Valid, p.LastUsedAt.Time),
		"last_error":     p.LastError,
		"created_at":     p.CreatedAt,
		"updated_at":     p.UpdatedAt,
	}
}

func (s *Server) sunnyListAccounts(w http.ResponseWriter, r *http.Request) {
	var accounts []SunnyAccount
	s.db.Order("updated_at desc").Find(&accounts)
	emails := []string{}
	for _, a := range accounts {
		emails = append(emails, a.Email)
	}
	sessionPlans := s.sunnySessionPlanTypesByEmail(emails)
	items := []map[string]any{}
	for _, a := range accounts {
		manualPlan := normalizeSunnyPlanType(a.AccountType)
		plan := manualPlan
		if plan == "" || plan == "free" {
			if sessionPlan := sessionPlans[sunnyEmailKey(a.Email)]; sessionPlan != "" {
				plan = sessionPlan
			}
		}
		if plan == "" {
			plan = "free"
		}
		items = append(items, map[string]any{
			"id": a.ID, "mailbox_id": a.MailboxID, "email": a.Email, "group_name": a.GroupName,
			"status": a.Status, "account_type": fallback(a.AccountType, "free"), "plan_type": plan,
			"openai_rt": a.OpenAIRT, "access_token": a.AccessToken, "phone_number": a.PhoneNumber,
			"sub2api_status": a.Sub2APIStatus, "sub2api_id": a.Sub2APIID, "last_error": a.LastError,
			"metadata": jsonMap(a.MetadataJSON), "created_at": formatTime(a.CreatedAt), "updated_at": formatTime(a.UpdatedAt),
		})
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func (s *Server) sunnyProxyConfig(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 0 && r.Method == http.MethodGet {
		writeJSON(w, 200, s.sunnyGetConfig(sunnyCfgProxy, defaultProxyConfig()))
		return
	}
	if len(parts) == 0 && r.Method == http.MethodPut {
		body, _ := parseBody(r)
		s.sunnySaveConfig(sunnyCfgProxy, mergeConfig(defaultProxyConfig(), body))
		writeJSON(w, 200, s.sunnyGetConfig(sunnyCfgProxy, defaultProxyConfig()))
		return
	}
	if len(parts) == 1 && parts[0] == "check" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		proxy := fallback(text(body["proxy"]), text(s.sunnyGetConfig(sunnyCfgProxy, defaultProxyConfig())["local_proxy"]))
		result := map[string]any{"proxy": proxy, "ok": false}
		client := &http.Client{Timeout: 15 * time.Second}
		if proxy != "" {
			u, err := url.Parse(proxy)
			if err == nil {
				client.Transport = &http.Transport{Proxy: http.ProxyURL(u)}
			}
		}
		resp, err := client.Get("https://chatgpt.com/")
		if err != nil {
			result["error"] = err.Error()
		} else {
			result["ok"] = resp.StatusCode < 500
			result["status"] = resp.StatusCode
			_ = resp.Body.Close()
		}
		writeJSON(w, 200, result)
		return
	}
	if len(parts) >= 1 && parts[0] == "pool" {
		s.sunnyProxyPool(w, r, parts[1:])
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) sunnyProxyPool(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 0 && r.Method == http.MethodGet {
		page, _ := strconv.Atoi(r.URL.Query().Get("page"))
		pageSize, _ := strconv.Atoi(r.URL.Query().Get("page_size"))
		if page <= 0 {
			page = 1
		}
		if pageSize <= 0 {
			pageSize = 10
		}
		if pageSize > 100 {
			pageSize = 100
		}
		query := strings.TrimSpace(r.URL.Query().Get("q"))
		status := normalizeSunnyProxyStatus(r.URL.Query().Get("status"))
		country := strings.TrimSpace(r.URL.Query().Get("country"))
		db := s.db.Model(&SunnyProxy{})
		if query != "" {
			like := "%" + query + "%"
			db = db.Where("address LIKE ? OR country LIKE ?", like, like)
		}
		if country != "" {
			db = db.Where("country = ?", country)
		}
		switch status {
		case "enabled":
			db = db.Where("status = ? AND enabled = ?", "enabled", true)
		case "disabled":
			db = db.Where("status = ?", "disabled")
		case "invalid":
			db = db.Where("status = ? OR (last_check_ok = ? AND last_checked_at IS NOT NULL)", "invalid", false)
		}
		var total int64
		db.Count(&total)
		var proxies []SunnyProxy
		db.Order(sunnySortClause(r.URL.Query().Get("sort_by"), r.URL.Query().Get("sort_order"), map[string]string{"last_checked_at": "last_checked_at", "updated_at": "updated_at", "created_at": "created_at"}, "updated_at desc")).Limit(pageSize).Offset((page - 1) * pageSize).Find(&proxies)
		var proxyStats struct {
			Total    int64 `gorm:"column:total"`
			Enabled  int64 `gorm:"column:enabled"`
			Disabled int64 `gorm:"column:disabled"`
			Invalid  int64 `gorm:"column:invalid"`
		}
		s.db.Model(&SunnyProxy{}).Select(`
			COUNT(*) AS total,
			COALESCE(SUM(CASE WHEN status = 'enabled' AND enabled = 1 THEN 1 ELSE 0 END), 0) AS enabled,
			COALESCE(SUM(CASE WHEN status = 'disabled' THEN 1 ELSE 0 END), 0) AS disabled,
			COALESCE(SUM(CASE WHEN status = 'invalid' OR (last_check_ok = 0 AND last_checked_at IS NOT NULL) THEN 1 ELSE 0 END), 0) AS invalid`).Scan(&proxyStats)
		var countries []string
		s.db.Model(&SunnyProxy{}).Where("country <> ''").Distinct().Order("country asc").Pluck("country", &countries)
		items := make([]map[string]any, 0, len(proxies))
		for _, p := range proxies {
			items = append(items, sunnyProxyJSON(p))
		}
		writeJSON(w, 200, map[string]any{
			"items": items, "total": total, "page": page, "page_size": pageSize, "countries": countries,
			"stats": map[string]any{"total": proxyStats.Total, "enabled": proxyStats.Enabled, "disabled": proxyStats.Disabled, "invalid": proxyStats.Invalid},
		})
		return
	}
	if len(parts) == 0 && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		addresses := []string{}
		if arr, ok := body["addresses"].([]any); ok {
			for _, raw := range arr {
				if v := normalizeSunnyProxyAddress(text(raw)); v != "" {
					addresses = append(addresses, v)
				}
			}
		}
		if lines := strings.TrimSpace(text(body["lines"])); lines != "" {
			for _, line := range strings.Split(lines, "\n") {
				if v := normalizeSunnyProxyAddress(line); v != "" {
					addresses = append(addresses, v)
				}
			}
		}
		if address := normalizeSunnyProxyAddress(text(body["address"])); address != "" {
			addresses = append(addresses, address)
		}
		if len(addresses) == 0 {
			writeError(w, 400, "proxy address is required")
			return
		}
		enabled := true
		if v, ok := body["enabled"]; ok {
			enabled = asBool(v)
		}
		created := []map[string]any{}
		for _, address := range addresses {
			p := SunnyProxy{
				Address: address,
				Country: strings.TrimSpace(text(body["country"])),
				Status:  fallback(normalizeSunnyProxyStatus(text(body["status"])), "enabled"),
				Enabled: enabled,
			}
			if !p.Enabled {
				p.Status = "disabled"
			}
			if p.Status == "invalid" {
				p.Enabled = false
				p.LastCheckOK = false
			}
			if err := s.db.Create(&p).Error; err != nil {
				writeError(w, 400, err.Error())
				return
			}
			created = append(created, sunnyProxyJSON(p))
		}
		if len(created) == 1 {
			writeJSON(w, 200, created[0])
		} else {
			writeJSON(w, 200, map[string]any{"items": created, "created": len(created)})
		}
		return
	}
	if len(parts) == 1 && parts[0] == "check" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		var proxies []SunnyProxy
		if rawIDs, ok := body["ids"].([]any); ok && len(rawIDs) > 0 {
			ids := make([]uint, 0, len(rawIDs))
			for _, v := range rawIDs {
				if id := uint(toInt(v)); id > 0 {
					ids = append(ids, id)
				}
			}
			s.db.Where("id IN ?", ids).Find(&proxies)
		} else {
			s.db.Where("enabled = ?", true).Order("updated_at desc").Limit(200).Find(&proxies)
		}
		okCount := 0
		for i := range proxies {
			result := checkSunnyProxy(proxies[i].Address)
			applySunnyProxyCheck(&proxies[i], result)
			if proxies[i].LastCheckOK {
				okCount++
			}
			s.db.Save(&proxies[i])
		}
		writeJSON(w, 200, map[string]any{"checked": len(proxies), "available": okCount})
		return
	}
	if len(parts) >= 1 {
		id64, err := strconv.ParseUint(parts[0], 10, 64)
		if err != nil || id64 == 0 {
			writeError(w, 400, "invalid proxy id")
			return
		}
		var p SunnyProxy
		if err := s.db.First(&p, uint(id64)).Error; err != nil {
			writeError(w, 404, "proxy not found")
			return
		}
		if len(parts) == 1 && r.Method == http.MethodPut {
			body, _ := parseBody(r)
			if v := normalizeSunnyProxyAddress(text(body["address"])); v != "" {
				p.Address = v
			}
			if _, ok := body["country"]; ok {
				p.Country = strings.TrimSpace(text(body["country"]))
			}
			if _, ok := body["enabled"]; ok {
				p.Enabled = asBool(body["enabled"])
			}
			if v := normalizeSunnyProxyStatus(text(body["status"])); v != "" {
				p.Status = v
				if v == "disabled" {
					p.Enabled = false
				}
				if v == "enabled" {
					p.Enabled = true
				}
				if v == "invalid" {
					p.Enabled = false
					p.LastCheckOK = false
				}
			}
			if !p.Enabled {
				p.Status = "disabled"
			} else if p.Status == "disabled" {
				p.Status = "enabled"
			}
			if err := s.db.Save(&p).Error; err != nil {
				writeError(w, 400, err.Error())
				return
			}
			writeJSON(w, 200, sunnyProxyJSON(p))
			return
		}
		if len(parts) == 1 && r.Method == http.MethodDelete {
			s.db.Delete(&p)
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
		if len(parts) == 2 && parts[1] == "check" && r.Method == http.MethodPost {
			result := checkSunnyProxy(p.Address)
			applySunnyProxyCheck(&p, result)
			s.db.Save(&p)
			writeJSON(w, 200, sunnyProxyJSON(p))
			return
		}
	}
	writeError(w, 404, "not found")
}

func normalizeSunnyProxyStatus(status string) string {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "启用", "可用", "enabled", "available", "enable", "on", "ok", "valid":
		return "enabled"
	case "停用", "disabled", "disable", "off":
		return "disabled"
	case "失效", "invalid", "failed", "fail":
		return "invalid"
	default:
		return ""
	}
}

func normalizeSunnyProxyAddress(raw string) string {
	value := strings.TrimSpace(raw)
	if value == "" {
		return ""
	}
	if strings.Contains(value, "://") {
		if u, err := url.Parse(value); err == nil && u.Scheme != "" && u.Host != "" {
			return u.String()
		}
		return value
	}
	if strings.Contains(value, "@") {
		chunks := strings.SplitN(value, "@", 2)
		leftParts := strings.Split(chunks[0], ":")
		rightParts := strings.Split(chunks[1], ":")
		if len(leftParts) >= 2 && len(rightParts) >= 2 && looksLikeProxyHost(leftParts[0]) {
			if _, err := strconv.Atoi(leftParts[1]); err == nil {
				host := leftParts[0]
				port := leftParts[1]
				user := rightParts[0]
				pass := strings.Join(rightParts[1:], ":")
				return (&url.URL{Scheme: "http", User: url.UserPassword(user, pass), Host: net.JoinHostPort(host, port)}).String()
			}
		}
		return "http://" + value
	}
	parts := strings.Split(value, ":")
	if len(parts) >= 4 {
		last := parts[len(parts)-1]
		if _, err := strconv.Atoi(last); err == nil {
			host := parts[len(parts)-2]
			if looksLikeProxyHost(host) {
				user := parts[0]
				pass := strings.Join(parts[1:len(parts)-2], ":")
				return (&url.URL{Scheme: "http", User: url.UserPassword(user, pass), Host: net.JoinHostPort(host, last)}).String()
			}
		}
		if _, err := strconv.Atoi(parts[1]); err == nil && looksLikeProxyHost(parts[0]) {
			host := parts[0]
			port := parts[1]
			user := parts[2]
			pass := strings.Join(parts[3:], ":")
			return (&url.URL{Scheme: "http", User: url.UserPassword(user, pass), Host: net.JoinHostPort(host, port)}).String()
		}
	}
	return "http://" + value
}

func looksLikeProxyHost(value string) bool {
	host := strings.Trim(value, "[]")
	if host == "" {
		return false
	}
	if net.ParseIP(host) != nil {
		return true
	}
	lower := strings.ToLower(host)
	return lower == "localhost" || strings.Contains(host, ".")
}

func sunnyProxyDisplayStatus(p SunnyProxy) string {
	switch normalizeSunnyProxyStatus(p.Status) {
	case "invalid":
		return "失效"
	case "disabled":
		return "停用"
	case "enabled":
		return "启用"
	}
	if p.LastCheckedAt != nil && !p.LastCheckOK {
		return "失效"
	}
	if !p.Enabled {
		return "停用"
	}
	return "启用"
}

func sunnyProxyJSON(p SunnyProxy) map[string]any {
	return map[string]any{
		"id": p.ID, "address": p.Address, "country": p.Country, "status": sunnyProxyDisplayStatus(p), "status_key": normalizeSunnyProxyStatus(p.Status),
		"enabled": p.Enabled, "last_check_ok": p.LastCheckOK, "latency_ms": p.LatencyMS, "last_error": p.LastError,
		"last_checked_at": p.LastCheckedAt, "created_at": p.CreatedAt, "updated_at": p.UpdatedAt,
	}
}

func checkSunnyProxy(proxyAddr string) map[string]any {
	proxyAddr = normalizeSunnyProxyAddress(proxyAddr)
	result := map[string]any{"proxy": proxyAddr, "ok": false, "latency_ms": int64(0), "check_mode": "tcp_connect"}
	if proxyAddr == "" {
		result["error"] = "proxy is empty"
		return result
	}
	u, err := url.Parse(proxyAddr)
	if err != nil || u.Scheme == "" || u.Host == "" {
		result["error"] = "proxy format is invalid"
		return result
	}
	host := u.Hostname()
	port := u.Port()
	if port == "" {
		switch strings.ToLower(u.Scheme) {
		case "socks5", "socks5h":
			port = "1080"
		case "https":
			port = "443"
		default:
			port = "80"
		}
	}
	if host == "" || port == "" {
		result["error"] = "proxy host or port is empty"
		return result
	}
	start := time.Now()
	conn, err := net.DialTimeout("tcp", net.JoinHostPort(host, port), 8*time.Second)
	result["latency_ms"] = time.Since(start).Milliseconds()
	if err != nil {
		result["error"] = err.Error()
		return result
	}
	_ = conn.Close()
	result["ok"] = true
	return result
}

func applySunnyProxyCheck(p *SunnyProxy, result map[string]any) {
	now := time.Now()
	p.LastCheckedAt = &now
	if normalized := normalizeSunnyProxyAddress(text(result["proxy"])); normalized != "" {
		p.Address = normalized
	}
	p.LastCheckOK = asBool(result["ok"])
	p.LatencyMS = int64(toInt(result["latency_ms"]))
	p.LastError = text(result["error"])
	if p.LastCheckOK {
		if normalizeSunnyProxyStatus(p.Status) == "disabled" || !p.Enabled {
			p.Status = "disabled"
			p.Enabled = false
		} else {
			p.Status = "enabled"
			p.Enabled = true
		}
	} else {
		p.Status = "invalid"
		p.Enabled = false
	}
}

func asBool(v any) bool {
	switch x := v.(type) {
	case bool:
		return x
	case string:
		s := strings.ToLower(strings.TrimSpace(x))
		return s == "true" || s == "1" || s == "yes" || s == "on" || s == "启用" || s == "可用"
	case float64:
		return x != 0
	case int:
		return x != 0
	case int64:
		return x != 0
	default:
		return false
	}
}

func toInt(v any) int {
	switch x := v.(type) {
	case int:
		return x
	case int64:
		return int(x)
	case float64:
		return int(x)
	case json.Number:
		i, _ := x.Int64()
		return int(i)
	case string:
		i, _ := strconv.Atoi(strings.TrimSpace(x))
		return i
	default:
		return 0
	}
}

func defaultProxyConfig() map[string]any {
	return map[string]any{"proxy_enabled": true, "local_proxy": "http://127.0.0.1:7897", "register_proxy": "", "provider_configs": []any{}, "precheck": true, "sid_mode": "random"}
}

func (s *Server) sunnySub2APIConfig(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		writeJSON(w, 200, s.sunnyGetConfig(sunnyCfgSub2API, defaultSub2APIConfig()))
		return
	}
	if r.Method == http.MethodPut {
		body, _ := parseBody(r)
		s.sunnySaveConfig(sunnyCfgSub2API, mergeConfig(defaultSub2APIConfig(), body))
		writeJSON(w, 200, s.sunnyGetConfig(sunnyCfgSub2API, defaultSub2APIConfig()))
		return
	}
	writeError(w, 404, "not found")
}

func defaultSub2APIConfig() map[string]any {
	return map[string]any{"enabled": true, "base_url": "", "admin_token": "", "name_prefix": "", "codex_image_bridge": false, "group_ids": []any{}, "concurrency": 3, "priority": 50}
}

func (s *Server) sunnySub2API(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 1 && parts[0] == "groups" && (r.Method == http.MethodGet || r.Method == http.MethodPost) {
		cfg := s.sunnyGetConfig(sunnyCfgSub2API, defaultSub2APIConfig())
		baseURL, token := strings.TrimRight(text(cfg["base_url"]), "/"), text(cfg["admin_token"])
		if r.Method == http.MethodPost {
			body, err := parseBody(r)
			if err != nil {
				writeError(w, http.StatusBadRequest, "Invalid sub2api request")
				return
			}
			if value := strings.TrimRight(text(body["base_url"]), "/"); value != "" {
				baseURL = value
			}
			if value := text(body["admin_token"]); value != "" {
				token = value
			}
		}
		if baseURL == "" || token == "" {
			writeError(w, 400, "Please fill sub2api Base URL and Admin Token")
			return
		}
		resp, err := callSub2API(r.Context(), baseURL, "/api/v1/admin/groups/all?platform=openai", token, "x-api-key", nil)
		if err != nil {
			writeError(w, 502, err.Error())
			return
		}
		writeJSON(w, 200, resp)
		return
	}
	if len(parts) == 1 && parts[0] == "import" && r.Method == http.MethodPost {
		s.sunnySub2APIImport(w, r)
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) sunnySub2APIImport(w http.ResponseWriter, r *http.Request) {
	body, _ := parseBody(r)
	cfg := mergeConfig(s.sunnyGetConfig(sunnyCfgSub2API, defaultSub2APIConfig()), body)
	baseURL, token := strings.TrimRight(text(cfg["base_url"]), "/"), text(cfg["admin_token"])
	if baseURL == "" || token == "" {
		writeError(w, 400, "閻犲洤鍢查崢娑㈡煀瀹ュ洨鏋?sub2api Base URL 濞?Admin Token")
		return
	}
	ids := uintSlice(body["account_ids"])
	var sessions []SunnySession
	q := s.db.Model(&SunnySession{})
	if len(ids) > 0 {
		q = q.Where("account_id IN ?", ids)
	}
	q.Find(&sessions)
	if len(sessions) == 0 {
		writeError(w, 400, "濞屸剝婀侀崣顖氼嚤閸忋儳娈?Session")
		return
	}
	ok, errs := 0, []string{}
	for _, sess := range sessions {
		payload := buildSunnySub2AccountPayload(sess, cfg)
		resp, err := callSub2API(r.Context(), baseURL, "/api/v1/admin/accounts", token, "x-api-key", payload)
		if err != nil {
			errs = append(errs, sess.Email+": "+err.Error())
			continue
		}
		ok++
		s.db.Model(&SunnyAccount{}).Where("email = ?", sess.Email).Updates(map[string]any{"status": "reverse_proxied", "sub2api_status": "imported", "sub2api_id": text(resp["id"]), "last_error": ""})
		s.db.Model(&SunnyMailbox{}).Where("email = ?", sess.Email).Updates(map[string]any{"status": "已反代", "last_error": ""})
	}
	writeJSON(w, 200, map[string]any{"ok": ok, "failed": len(errs), "errors": errs})
}

func buildSunnySub2AccountPayload(sess SunnySession, cfg map[string]any) map[string]any {
	claims := decodeJWTPayload(sess.AccessToken)
	auth, _ := claims["https://api.openai.com/auth"].(map[string]any)
	groupIDs := []int64{}
	for _, raw := range stringSlice(cfg["group_ids"]) {
		if n, err := strconv.ParseInt(raw, 10, 64); err == nil && n > 0 {
			groupIDs = append(groupIDs, n)
		}
	}
	if len(groupIDs) == 0 {
		for _, raw := range uintSlice(cfg["group_ids"]) {
			groupIDs = append(groupIDs, int64(raw))
		}
	}
	extra := map[string]any{"import_source": "sunnyregister", "email": sess.Email}
	if boolValue(cfg["codex_image_bridge"], false) {
		extra["codex_image_generation_bridge"] = true
	}
	return map[string]any{
		"name": fallback(text(cfg["name_prefix"])+sess.Email, sess.Email), "platform": "openai", "type": "oauth",
		"credentials": map[string]any{"access_token": sess.AccessToken, "refresh_token": fallback(sess.RefreshToken, text(auth["refresh_token"])), "id_token": sess.IDToken, "email": sess.Email, "client_id": text(auth["client_id"]), "chatgpt_account_id": firstText(auth["chatgpt_account_id"], auth["account_id"]), "chatgpt_user_id": firstText(auth["user_id"], claims["sub"]), "organization_id": firstText(auth["organization_id"], auth["poid"]), "plan_type": firstText(auth["plan_type"], auth["plan"]), "expires_at": intValue(claims["exp"], 0)},
		"extra":       extra, "group_ids": groupIDs, "concurrency": intValue(cfg["concurrency"], 3), "priority": intValue(cfg["priority"], 50),
	}
}

func normalizeSunnyDisplayStatus(status string) string {
	raw := strings.TrimSpace(status)
	switch strings.ToLower(raw) {
	case "registered", "success", "succeeded":
		return "已注册"
	case "phone_bound", "phone-bound", "bound":
		return "已接码"
	case "reverse_proxied", "reverse-proxied", "proxied", "imported":
		return "已反代"
	case "failed", "error":
		return "失败"
	case "pending", "":
		return "已注册"
	default:
		return raw
	}
}

func (s *Server) serializeSunnySession(sess SunnySession, accounts map[string]SunnyAccount, mailboxes map[string]SunnyMailbox) map[string]any {
	key := sunnyEmailKey(sess.Email)
	acc := accounts[key]
	mb := mailboxes[key]
	statusSource := acc.Status
	if mb.ID != 0 && strings.TrimSpace(mb.Status) != "" {
		statusSource = mb.Status
	}
	status := normalizeSunnyDisplayStatus(statusSource)
	sessionPlan := sunnyPlanTypeFromSessionJSON(sess.SessionJSON)
	plan := ""
	if mb.ID != 0 {
		if mailboxPlan := normalizeSunnyPlanType(mb.AccountType); mailboxPlan != "" && mailboxPlan != "free" {
			plan = mailboxPlan
		} else if sessionPlan != "" {
			plan = sessionPlan
		} else if acc.ID != 0 || strings.TrimSpace(mb.OpenAIRT) != "" || sunnyMailboxStatusLooksRegistered(mb.Status) {
			plan = fallback(normalizeSunnyPlanType(mb.AccountType), "free")
		}
	} else {
		plan = normalizeSunnyPlanType(acc.AccountType)
		if plan == "" {
			plan = sessionPlan
		}
		if plan == "" && (sess.AccessToken != "" || sunnyAccessTokenFromSessionJSON(sess.SessionJSON) != "" || acc.ID != 0 || sunnyMailboxStatusLooksRegistered(status)) {
			plan = "free"
		}
	}
	raw := sess.RawMailboxLine
	if raw == "" && mb.Raw != "" {
		raw = mb.Raw
	}
	refreshToken := sess.RefreshToken
	if refreshToken == "" {
		refreshToken = acc.OpenAIRT
	}
	return map[string]any{
		"id": sess.ID, "account_id": sess.AccountID, "email": sess.Email,
		"status": status, "plan_type": plan,
		"access_token": fallback(sess.AccessToken, sunnyAccessTokenFromSessionJSON(sess.SessionJSON)), "refresh_token": refreshToken, "id_token": sess.IDToken,
		"session_json": sess.SessionJSON, "storage_state_json": sess.StorageStateJSON,
		"raw_mailbox_line": raw,
		"mailbox_password": mb.Password, "mailbox_client_id": mb.ClientID, "mailbox_refresh_token": mb.RefreshToken,
		"expires_at":      nullableTime(sess.ExpiresAt.Valid, sess.ExpiresAt.Time),
		"last_refresh_at": nullableTime(sess.LastRefreshAt.Valid, sess.LastRefreshAt.Time),
		"created_at":      formatTime(sess.CreatedAt), "updated_at": formatTime(sess.UpdatedAt),
	}
}

type sunnySessionListRow struct {
	ID              uint      `gorm:"column:id"`
	AccountID       uint      `gorm:"column:account_id"`
	Email           string    `gorm:"column:email"`
	HasAccessToken  int       `gorm:"column:has_access_token"`
	HasRefreshToken int       `gorm:"column:has_refresh_token"`
	HasSecretKey    int       `gorm:"column:has_secret_key"`
	UpdatedAt       time.Time `gorm:"column:updated_at"`
}

type sunnySessionAccountSummary struct {
	ID              uint   `gorm:"column:id"`
	Email           string `gorm:"column:email"`
	Status          string `gorm:"column:status"`
	AccountType     string `gorm:"column:account_type"`
	HasAccessToken  int    `gorm:"column:has_access_token"`
	HasRefreshToken int    `gorm:"column:has_refresh_token"`
}

type sunnySessionMailboxSummary struct {
	ID           uint   `gorm:"column:id"`
	Email        string `gorm:"column:email"`
	Status       string `gorm:"column:status"`
	AccountType  string `gorm:"column:account_type"`
	HasSecretKey int    `gorm:"column:has_secret_key"`
}

const sunnySessionListColumns = `id, account_id, email, updated_at,
	CASE WHEN access_token IS NOT NULL AND access_token <> '' THEN 1 ELSE 0 END AS has_access_token,
	CASE WHEN refresh_token IS NOT NULL AND refresh_token <> '' THEN 1 ELSE 0 END AS has_refresh_token,
	CASE WHEN raw_mailbox_line IS NOT NULL AND raw_mailbox_line <> '' THEN 1 ELSE 0 END AS has_secret_key`

func serializeSunnySessionList(row sunnySessionListRow, accounts map[string]sunnySessionAccountSummary, mailboxes map[string]sunnySessionMailboxSummary) map[string]any {
	key := sunnyEmailKey(row.Email)
	account := accounts[key]
	mailbox := mailboxes[key]
	statusSource := account.Status
	if strings.TrimSpace(mailbox.Status) != "" {
		statusSource = mailbox.Status
	}
	status := normalizeSunnyDisplayStatus(statusSource)
	plan := normalizeSunnyPlanType(account.AccountType)
	if mailboxPlan := normalizeSunnyPlanType(mailbox.AccountType); mailboxPlan != "" && mailboxPlan != "free" {
		plan = mailboxPlan
	} else if plan == "" {
		plan = mailboxPlan
	}
	if plan == "" && row.ID != 0 {
		plan = "free"
	}
	return map[string]any{
		"id": row.ID, "account_id": row.AccountID, "email": row.Email,
		"status": status, "plan_type": plan,
		"has_access_token":  row.HasAccessToken != 0 || account.HasAccessToken != 0,
		"has_refresh_token": row.HasRefreshToken != 0 || account.HasRefreshToken != 0,
		"has_secret_key":    row.HasSecretKey != 0 || mailbox.HasSecretKey != 0,
		"updated_at":        formatTime(row.UpdatedAt),
	}
}
func (s *Server) sunnySessionSidecars(rows []SunnySession) (map[string]SunnyAccount, map[string]SunnyMailbox) {
	emails := []string{}
	for _, row := range rows {
		emails = append(emails, row.Email)
	}
	accounts := map[string]SunnyAccount{}
	mailboxes := map[string]SunnyMailbox{}
	if len(emails) == 0 {
		return accounts, mailboxes
	}
	var accRows []SunnyAccount
	s.db.Where("email IN ?", emails).Find(&accRows)
	for _, a := range accRows {
		accounts[sunnyEmailKey(a.Email)] = a
	}
	var mbRows []SunnyMailbox
	s.db.Where("email IN ?", emails).Find(&mbRows)
	for _, m := range mbRows {
		mailboxes[sunnyEmailKey(m.Email)] = m
	}
	return accounts, mailboxes
}

func (s *Server) sunnySessionListSidecars(rows []sunnySessionListRow) (map[string]sunnySessionAccountSummary, map[string]sunnySessionMailboxSummary) {
	emails := make([]string, 0, len(rows))
	for _, row := range rows {
		emails = append(emails, row.Email)
	}
	accounts := map[string]sunnySessionAccountSummary{}
	mailboxes := map[string]sunnySessionMailboxSummary{}
	if len(emails) == 0 {
		return accounts, mailboxes
	}
	var accRows []sunnySessionAccountSummary
	s.db.Model(&SunnyAccount{}).Select(`id, email, status, account_type,
		CASE WHEN access_token IS NOT NULL AND access_token <> '' THEN 1 ELSE 0 END AS has_access_token,
		CASE WHEN openai_rt IS NOT NULL AND openai_rt <> '' THEN 1 ELSE 0 END AS has_refresh_token`).Where("email IN ?", emails).Find(&accRows)
	for _, account := range accRows {
		accounts[sunnyEmailKey(account.Email)] = account
	}
	var mailboxRows []sunnySessionMailboxSummary
	s.db.Model(&SunnyMailbox{}).Select(`id, email, status, account_type,
		CASE WHEN email IS NOT NULL AND email <> '' AND password IS NOT NULL AND password <> '' AND client_id IS NOT NULL AND client_id <> '' AND refresh_token IS NOT NULL AND refresh_token <> '' THEN 1 ELSE 0 END AS has_secret_key`).Where("email IN ?", emails).Find(&mailboxRows)
	for _, mailbox := range mailboxRows {
		mailboxes[sunnyEmailKey(mailbox.Email)] = mailbox
	}
	return accounts, mailboxes
}

func (s *Server) sunnySessionFieldValue(id uint, field string) (string, error) {
	var sess SunnySession
	query := s.db.Model(&SunnySession{}).Where("id = ?", id)
	switch field {
	case "access_token":
		if err := query.Select("id", "email", "access_token", "session_json").First(&sess).Error; err != nil {
			return "", fmt.Errorf("session not found")
		}
		value := fallback(sess.AccessToken, sunnyAccessTokenFromSessionJSON(sess.SessionJSON))
		if value == "" {
			var account SunnyAccount
			s.db.Select("access_token").Where("email = ?", sess.Email).First(&account)
			value = account.AccessToken
		}
		return value, nil
	case "refresh_token":
		if err := query.Select("id", "email", "refresh_token").First(&sess).Error; err != nil {
			return "", fmt.Errorf("session not found")
		}
		value := sess.RefreshToken
		if value == "" {
			var account SunnyAccount
			s.db.Select("openai_rt").Where("email = ?", sess.Email).First(&account)
			value = account.OpenAIRT
		}
		return value, nil
	case "secret_key":
		if err := query.Select("id", "email", "raw_mailbox_line").First(&sess).Error; err != nil {
			return "", fmt.Errorf("session not found")
		}
		var mailbox SunnyMailbox
		if err := s.db.Select("email", "password", "client_id", "refresh_token", "raw").Where("email = ?", sess.Email).First(&mailbox).Error; err == nil {
			if mailbox.Email != "" && mailbox.Password != "" && mailbox.ClientID != "" && mailbox.RefreshToken != "" {
				return strings.Join([]string{mailbox.Email, mailbox.Password, mailbox.ClientID, mailbox.RefreshToken}, "----"), nil
			}
			if strings.TrimSpace(mailbox.Raw) != "" {
				return mailbox.Raw, nil
			}
		}
		return sess.RawMailboxLine, nil
	default:
		return "", fmt.Errorf("unsupported session field")
	}
}

func (s *Server) sunnySessions(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 0 && r.Method == http.MethodGet {
		q := r.URL.Query()
		page := intValue(q.Get("page"), 1)
		if page < 1 {
			page = 1
		}
		pageSize := intValue(q.Get("page_size"), 10)
		if pageSize < 1 {
			pageSize = 10
		}
		if pageSize > 100 {
			pageSize = 100
		}
		kw := strings.ToLower(strings.TrimSpace(q.Get("q")))
		statusFilter := strings.TrimSpace(q.Get("status"))
		planFilter := strings.ToLower(strings.TrimSpace(q.Get("plan_type")))
		if statusFilter == "" && planFilter == "" {
			query := s.db.Model(&SunnySession{})
			if kw != "" {
				query = query.Where("LOWER(email) LIKE ?", "%"+kw+"%")
			}
			var total int64
			query.Count(&total)
			var rows []sunnySessionListRow
			query.Select(sunnySessionListColumns).Order(sunnySortClause(q.Get("sort_by"), q.Get("sort_order"), map[string]string{"updated_at": "updated_at", "created_at": "created_at", "last_refresh_at": "last_refresh_at"}, "updated_at desc")).Offset((page - 1) * pageSize).Limit(pageSize).Scan(&rows)
			accounts, mailboxes := s.sunnySessionListSidecars(rows)
			items := make([]map[string]any, 0, len(rows))
			for _, row := range rows {
				items = append(items, serializeSunnySessionList(row, accounts, mailboxes))
			}
			writeJSON(w, 200, map[string]any{"items": items, "total": total, "page": page, "page_size": pageSize})
			return
		}
		var rows []sunnySessionListRow
		s.db.Model(&SunnySession{}).Select(sunnySessionListColumns).Scan(&rows)
		accounts, mailboxes := s.sunnySessionListSidecars(rows)
		itemsAll := []map[string]any{}
		for _, row := range rows {
			item := serializeSunnySessionList(row, accounts, mailboxes)
			if kw != "" && !strings.Contains(strings.ToLower(text(item["email"])), kw) {
				continue
			}
			if statusFilter != "" && text(item["status"]) != statusFilter {
				continue
			}
			if planFilter != "" && strings.ToLower(text(item["plan_type"])) != planFilter {
				continue
			}
			itemsAll = append(itemsAll, item)
		}
		sortBy := q.Get("sort_by")
		if sortBy == "" {
			sortBy = "updated_at"
		}
		desc := strings.ToLower(q.Get("sort_order")) != "asc"
		sort.SliceStable(itemsAll, func(i, j int) bool {
			a, b := text(itemsAll[i][sortBy]), text(itemsAll[j][sortBy])
			if desc {
				return a > b
			}
			return a < b
		})
		total := len(itemsAll)
		start := (page - 1) * pageSize
		if start > total {
			start = total
		}
		end := start + pageSize
		if end > total {
			end = total
		}
		writeJSON(w, 200, map[string]any{"items": itemsAll[start:end], "total": total, "page": page, "page_size": pageSize})
		return
	}
	if len(parts) == 1 && parts[0] == "export" && r.Method == http.MethodPost {
		body, _ := parseBody(r)
		format := fallback(text(body["format"]), "json")
		var rows []SunnySession
		sessionIDs := uintSlice(body["session_ids"])
		accountIDs := uintSlice(body["account_ids"])
		emails := stringSlice(body["emails"])
		q := s.db.Model(&SunnySession{})
		if len(sessionIDs) > 0 {
			q = q.Where("id IN ?", sessionIDs)
		} else if len(accountIDs) > 0 {
			q = q.Where("account_id IN ?", accountIDs)
		} else if len(emails) > 0 {
			q = q.Where("email IN ?", emails)
		}
		q.Find(&rows)
		s.sunnyExportSessions(w, rows, format)
		return
	}
	if len(parts) == 2 && parts[1] == "field" && r.Method == http.MethodGet {
		id := uint(intValue(parts[0], 0))
		field := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("name")))
		if id == 0 {
			writeError(w, 400, "invalid session id")
			return
		}
		value, err := s.sunnySessionFieldValue(id, field)
		if err != nil {
			status := http.StatusBadRequest
			if err.Error() == "session not found" {
				status = http.StatusNotFound
			}
			writeError(w, status, err.Error())
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Pragma", "no-cache")
		writeJSON(w, 200, map[string]any{"field": field, "value": value})
		return
	}
	if len(parts) == 1 {
		id := uint(intValue(parts[0], 0))
		var sess SunnySession
		if id == 0 || s.db.First(&sess, id).Error != nil {
			writeError(w, 404, "session not found")
			return
		}
		if r.Method == http.MethodGet {
			w.Header().Set("Cache-Control", "no-store")
			w.Header().Set("Pragma", "no-cache")
			accounts, mailboxes := s.sunnySessionSidecars([]SunnySession{sess})
			writeJSON(w, 200, s.serializeSunnySession(sess, accounts, mailboxes))
			return
		}
		if r.Method == http.MethodPut {
			body, _ := parseBody(r)
			if v := text(body["access_token"]); v != "" {
				sess.AccessToken = v
			}
			if _, ok := body["refresh_token"]; ok {
				sess.RefreshToken = text(body["refresh_token"])
			}
			if v := text(body["session_json"]); v != "" {
				sess.SessionJSON = v
			}
			s.db.Save(&sess)
			if status := text(body["status"]); status != "" {
				s.db.Model(&SunnyAccount{}).Where("email = ?", sess.Email).Updates(map[string]any{"status": status})
				s.db.Model(&SunnyMailbox{}).Where("email = ?", sess.Email).Updates(map[string]any{"status": status})
			}
			accounts, mailboxes := s.sunnySessionSidecars([]SunnySession{sess})
			writeJSON(w, 200, s.serializeSunnySession(sess, accounts, mailboxes))
			return
		}
		if r.Method == http.MethodDelete {
			s.db.Delete(&sess)
			writeJSON(w, 200, map[string]any{"ok": true})
			return
		}
	}
	writeError(w, 404, "not found")
}

func (s *Server) sunnyExportSessions(w http.ResponseWriter, rows []SunnySession, format string) {
	switch format {
	case "session_json", "json":
		arr := []any{}
		for _, r := range rows {
			if strings.TrimSpace(r.SessionJSON) != "" {
				arr = append(arr, jsonMap(r.SessionJSON))
			}
		}
		writeTextFile(w, timestampName("auth_sessions", "json"), "application/json", []byte(dumpJSONPretty(map[string]any{"items": arr})+"\n"))
	case "access_token":
		lines := []string{}
		for _, r := range rows {
			lines = append(lines, fallback(r.AccessToken, sunnyAccessTokenFromSessionJSON(r.SessionJSON)))
		}
		writeTextFile(w, timestampName("access_tokens", "txt"), "text/plain; charset=utf-8", []byte(strings.Join(lines, "\n")+"\n"))
	case "secret_key", "mailbox_account", "raw":
		lines := []string{}
		_, mailboxes := s.sunnySessionSidecars(rows)
		for _, r := range rows {
			line := r.RawMailboxLine
			if line == "" {
				if mb := mailboxes[sunnyEmailKey(r.Email)]; mb.Email != "" {
					line = strings.Join([]string{mb.Email, mb.Password, mb.ClientID, mb.RefreshToken}, "----")
				}
			}
			if line != "" {
				lines = append(lines, line)
			}
		}
		writeTextFile(w, timestampName("mailbox_accounts", "txt"), "text/plain; charset=utf-8", []byte(strings.Join(lines, "\n")+"\n"))
	case "all":
		accounts, mailboxes := s.sunnySessionSidecars(rows)
		arr := []any{}
		for _, r := range rows {
			arr = append(arr, s.serializeSunnySession(r, accounts, mailboxes))
		}
		writeTextFile(w, timestampName("session_accounts", "json"), "application/json", []byte(dumpJSONPretty(map[string]any{"items": arr})+"\n"))
	case "sub2api_json":
		arr := []any{}
		cfg := defaultSub2APIConfig()
		for _, r := range rows {
			arr = append(arr, buildSunnySub2AccountPayload(r, cfg))
		}
		writeTextFile(w, timestampName("sub2api", "json"), "application/json", []byte(dumpJSONPretty(map[string]any{"accounts": arr})+"\n"))
	default:
		writeTextFile(w, timestampName("sessions", "json"), "application/json", []byte(dumpJSONPretty(rows)+"\n"))
	}
}

func (s *Server) sunnyTasks(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) != 1 || r.Method != http.MethodPost {
		writeError(w, 404, "not found")
		return
	}
	body, _ := parseBody(r)
	typemap := map[string]string{"register": "sunny_register", "login": "sunny_login", "refresh-session": "sunny_refresh_session"}
	typ := typemap[parts[0]]
	if typ == "" {
		writeError(w, 404, "not found")
		return
	}
	if typ == "sunny_register" {
		if err := s.sunnyValidateRegisterStageResources(body); err != nil {
			writeError(w, 400, err.Error())
			return
		}
	}
	total := len(uintSlice(body["mailbox_ids"])) + len(uintSlice(body["account_ids"]))
	if total == 0 {
		total = intValue(body["count"], 1)
	}
	body = s.sunnyTaskProxySnapshot(body)
	task := s.createTask(typ, "sunny", body, total)
	writeJSON(w, 200, serializeTask(task))
}

func sunnyRegistrationStage(body map[string]any) string {
	stage := strings.ToLower(strings.TrimSpace(firstText(body["registration_stage"], body["stage"])))
	switch stage {
	case "codex_phone_bind", "import_reverse_proxy":
		return stage
	default:
		return "register_only"
	}
}

func sunnySortClause(sortBy string, sortOrder string, allowed map[string]string, fallback string) string {
	col := allowed[strings.ToLower(strings.TrimSpace(sortBy))]
	if col == "" {
		return fallback
	}
	order := strings.ToLower(strings.TrimSpace(sortOrder))
	if order != "asc" {
		order = "desc"
	}
	return col + " " + order
}

func (s *Server) sunnyValidateRegisterStageResources(body map[string]any) error {
	mailboxCfg := s.sunnyGetConfig(sunnyCfgMailbox, defaultMailboxConfig())
	if !boolValue(mailboxCfg["pool_enabled"], true) {
		return fmt.Errorf("mailbox config is unavailable: enable the self-managed mailbox pool first")
	}
	mailboxes, err := s.sunnyMailboxesForRegisterTask(body)
	if err != nil {
		return err
	}
	if len(mailboxes) == 0 {
		return fmt.Errorf("mailbox config is unavailable: import and enable at least one mailbox first")
	}
	if err := s.sunnyValidateProxyForRegisterTask(); err != nil {
		return err
	}
	// SMS and sub2api are post-registration stages. Missing resources must not
	// block the base ChatGPT registration/login; the Worker records the last
	// completed stage and stops at 已注册 or 已接码 with a detailed log.
	return nil
}

func (s *Server) sunnyValidateProxyForRegisterTask() error {
	cfg := s.sunnyGetConfig(sunnyCfgProxy, defaultProxyConfig())
	if !boolValue(cfg["proxy_enabled"], true) {
		return nil
	}
	if normalizeSunnyProxyAddress(text(cfg["register_proxy"])) != "" {
		return nil
	}
	var n int64
	s.db.Model(&SunnyProxy{}).Where("status = ? AND enabled = ? AND last_check_ok = ?", "enabled", true, true).Count(&n)
	if n <= 0 {
		stats := s.sunnyProxyStats()
		return fmt.Errorf("proxy config is enabled but no checked usable proxy is available: total=%d enabled=%d disabled=%d invalid=%d", stats["total"], stats["enabled"], stats["disabled"], stats["invalid"])
	}
	return nil
}

func (s *Server) sunnyMailboxesForRegisterTask(body map[string]any) ([]SunnyMailbox, error) {
	ids := uintSlice(body["mailbox_ids"])
	var rows []SunnyMailbox
	if len(ids) > 0 {
		s.db.Where("id IN ?", ids).Order("id asc").Find(&rows)
		if len(rows) != len(ids) {
			return nil, fmt.Errorf("mailbox config is unavailable: selected mailbox does not exist")
		}
		seen := map[uint]bool{}
		for _, m := range rows {
			seen[m.ID] = true
			if !m.Enabled {
				return nil, fmt.Errorf("mailbox config is unavailable: selected mailbox is disabled: %s", m.Email)
			}
		}
		for _, id := range ids {
			if !seen[id] {
				return nil, fmt.Errorf("mailbox config is unavailable: selected mailbox does not exist")
			}
		}
		return rows, nil
	}
	query := s.db.Where("enabled = ? AND status NOT IN ?", true, []string{"disabled", "禁用"})
	if count := intValue(body["count"], 0); count > 0 {
		query = query.Limit(count)
	}
	query.Order("id asc").Find(&rows)
	return rows, nil
}

func (s *Server) sunnyMailboxesNeedPhone(rows []SunnyMailbox) bool {
	for _, m := range rows {
		if strings.TrimSpace(m.OpenAIRT) != "" {
			continue
		}
		var account SunnyAccount
		if err := s.db.Select("id", "openai_rt").First(&account, "email = ?", m.Email).Error; err == nil && strings.TrimSpace(account.OpenAIRT) != "" {
			continue
		}
		var session SunnySession
		if err := s.db.Select("id", "refresh_token").First(&session, "email = ?", m.Email).Error; err == nil && strings.TrimSpace(session.RefreshToken) != "" {
			continue
		}
		return true
	}
	return false
}

func (s *Server) sunnyPhoneTotalCount() int64 {
	var n int64
	s.db.Model(&SunnyPhone{}).Count(&n)
	return n
}

func (s *Server) sunnyUsablePhoneCount() int64 {
	cfg := s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig())
	if !boolValue(cfg["pool_enabled"], true) {
		return 0
	}
	var n int64
	s.db.Model(&SunnyPhone{}).
		Where("enabled = ?", true).
		Where("coalesce(status,'available') NOT IN ?", []string{"disabled", "full", "in_use"}).
		Where("coalesce(success_count,0) < coalesce(max_success,3)").
		Where("(cooldown_until IS NULL OR cooldown_until = '' OR datetime(cooldown_until) <= datetime('now'))").
		Count(&n)
	return n
}

func (s *Server) sunnyHasUsableSMSConfig() bool {
	if s.sunnyUsablePhoneCount() > 0 {
		return true
	}
	cfg := s.sunnyGetConfig(sunnyCfgPhone, defaultPhoneConfig())
	if boolValue(cfg["smsbower_enabled"], false) && strings.TrimSpace(text(cfg["smsbower_api_key"])) != "" {
		return true
	}
	return boolValue(cfg["smspool_enabled"], false) && strings.TrimSpace(text(cfg["smspool_api_key"])) != ""
}

func (s *Server) sunnyProxyStats() map[string]int64 {
	var allTotal, enabledTotal, disabledTotal, invalidTotal int64
	s.db.Model(&SunnyProxy{}).Count(&allTotal)
	s.db.Model(&SunnyProxy{}).Where("status = ? AND enabled = ?", "enabled", true).Count(&enabledTotal)
	s.db.Model(&SunnyProxy{}).Where("status = ?", "disabled").Count(&disabledTotal)
	s.db.Model(&SunnyProxy{}).Where("status = ? OR (last_check_ok = ? AND last_checked_at IS NOT NULL)", "invalid", false).Count(&invalidTotal)
	return map[string]int64{"total": allTotal, "enabled": enabledTotal, "disabled": disabledTotal, "invalid": invalidTotal}
}

func (s *Server) sunnyTaskProxySnapshot(payload map[string]any) map[string]any {
	next := map[string]any{}
	for k, v := range payload {
		next[k] = v
	}
	cfg := s.sunnyGetConfig(sunnyCfgProxy, defaultProxyConfig())
	stats := s.sunnyProxyStats()
	proxyEnabled := boolValue(cfg["proxy_enabled"], true)
	localProxy := normalizeSunnyProxyAddress(fallback(text(cfg["local_proxy"]), "http://127.0.0.1:7897"))
	next["proxy_enabled"] = proxyEnabled
	next["proxy_stats"] = stats
	next["local_proxy"] = localProxy
	if !proxyEnabled {
		next["register_proxy"] = ""
		next["proxy"] = ""
		next["system_proxy"] = normalizeSunnyProxyAddress(text(cfg["system_proxy"]))
		return next
	}
	next["system_proxy"] = localProxy
	registerProxy := normalizeSunnyProxyAddress(text(cfg["register_proxy"]))
	var proxies []SunnyProxy
	s.db.Where("status = ? AND enabled = ? AND last_check_ok = ?", "enabled", true, true).
		Order("updated_at desc, id asc").Find(&proxies)
	proxyPool := make([]string, 0, len(proxies))
	proxyIDs := make([]uint, 0, len(proxies))
	for _, p := range proxies {
		address := normalizeSunnyProxyAddress(p.Address)
		if address == "" {
			continue
		}
		proxyPool = append(proxyPool, address)
		proxyIDs = append(proxyIDs, p.ID)
	}
	if len(proxyPool) > 0 {
		registerProxy = proxyPool[0]
		next["proxy_pool"] = proxyPool
		next["proxy_ids"] = proxyIDs
		next["proxy_pool_size"] = len(proxyPool)
	}
	next["local_proxy"] = localProxy
	next["register_proxy"] = registerProxy
	next["proxy"] = registerProxy
	return next
}

func (s *Server) sunnyHasUsableMailbox(body map[string]any) bool {
	rows, err := s.sunnyMailboxesForRegisterTask(body)
	return err == nil && len(rows) > 0
}

func (s *Server) sunnyImportState(w http.ResponseWriter, r *http.Request) {
	body, _ := parseBody(r)
	path := strings.TrimSpace(text(body["path"]))
	if path == "" {
		writeError(w, 400, "璇锋彁渚?state.json 璺緞")
		return
	}
	b, err := os.ReadFile(filepath.Clean(path))
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	var state map[string]any
	if json.Unmarshal(b, &state) != nil {
		writeError(w, 400, "state.json 鏍煎紡閿欒")
		return
	}
	imported := 0
	if arr, ok := state["accounts"].([]any); ok {
		gid := s.sunnyEnsureDefaultGroup()
		for _, raw := range arr {
			if m, ok := raw.(map[string]any); ok {
				line := text(m["raw"])
				if line == "" {
					line = strings.Join([]string{text(m["email"]), text(m["password"]), text(m["client_id"]), text(m["refresh_token"])}, "----")
				}
				if p, err := parseSunnyMailboxLine(line); err == nil {
					mb := SunnyMailbox{GroupID: gid, Email: p["email"], Password: p["password"], ClientID: p["client_id"], RefreshToken: p["refresh_token"], OpenAIRT: fallback(text(m["openai_rt"]), p["openai_rt"]), Raw: line, AccountType: fallback(text(m["account_type"]), "free"), Status: fallback(text(m["status"]), "unused"), Enabled: true, LatestMailJSON: "{}"}
					s.db.FirstOrCreate(&mb, SunnyMailbox{Email: mb.Email})
					imported++
				}
			}
		}
	}
	if arr, ok := state["phones"].([]any); ok {
		for _, raw := range arr {
			if m, ok := raw.(map[string]any); ok {
				p := SunnyPhone{Number: text(m["number"]), SmsURL: text(m["sms_url"]), Status: "available", Enabled: true, SuccessCount: intValue(m["receive_count"], 0), MaxSuccess: 3}
				if p.Number != "" {
					s.db.FirstOrCreate(&p, SunnyPhone{Number: p.Number})
				}
			}
		}
	}
	writeJSON(w, 200, map[string]any{"ok": true, "imported_mailboxes": imported})
}

func (s *Server) sunnyGetConfig(key string, def map[string]any) map[string]any {
	var row SunnyKVConfig
	if s.db.First(&row, "key = ?", key).Error != nil {
		return def
	}
	return mergeConfig(def, jsonMap(row.ValueJSON))
}
func (s *Server) sunnySaveConfig(key string, value map[string]any) {
	row := SunnyKVConfig{Key: key, ValueJSON: dumpJSON(value)}
	s.db.Save(&row)
}
func mergeConfig(base map[string]any, over map[string]any) map[string]any {
	out := map[string]any{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range over {
		out[k] = v
	}
	return out
}
func firstText(values ...any) string {
	for _, v := range values {
		if t := text(v); t != "" {
			return t
		}
	}
	return ""
}
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
