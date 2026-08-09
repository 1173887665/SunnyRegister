package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const sub2APIDefaultBatchEndpoint = "/api/v1/admin/accounts/batch"
const sub2APIGroupLookupEndpoint = "/api/v1/admin/groups/all"

func (s *Server) handleIntegrations(w http.ResponseWriter, r *http.Request, rest string) {
	if rest == "/sub2api/import" && r.Method == http.MethodPost {
		s.handleSub2APIImport(w, r)
		return
	}
	writeError(w, http.StatusNotFound, "not found")
}

func (s *Server) configValue(key string) string {
	var item ConfigItem
	if err := s.db.First(&item, "key = ?", key).Error; err != nil {
		return ""
	}
	return strings.TrimSpace(item.Value)
}

func (s *Server) handleSub2APIImport(w http.ResponseWriter, r *http.Request) {
	body, err := parseBody(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}

	baseURL := strings.TrimRight(fallback(text(body["base_url"]), s.configValue("sub2api_base_url")), "/")
	adminToken := fallback(text(body["admin_token"]), s.configValue("sub2api_admin_token"))
	authHeader := strings.ToLower(fallback(text(body["auth_header"]), s.configValue("sub2api_auth_header")))
	endpoint := fallback(text(body["endpoint"]), s.configValue("sub2api_batch_endpoint"))
	if endpoint == "" {
		endpoint = sub2APIDefaultBatchEndpoint
	}
	platform := fallback(text(body["platform"]), "chatgpt")
	targetPlatform := fallback(text(body["target_platform"]), "openai")
	groupIDRaw := fallback(text(body["group_id"]), s.configValue("sub2api_group_id"))
	groupName := fallback(text(body["group_name"]), s.configValue("sub2api_group_name"))
	dryRun := boolValue(body["dry_run"], false)
	includePayload := boolValue(body["include_payload"], false)

	if baseURL == "" {
		writeError(w, http.StatusBadRequest, "sub2api base_url 不能为空")
		return
	}
	if adminToken == "" && !dryRun {
		writeError(w, http.StatusBadRequest, "sub2api admin_token 不能为空")
		return
	}

	groupID, resolvedGroup, resolveErr := s.resolveSub2APIGroupID(r.Context(), baseURL, adminToken, authHeader, groupIDRaw, groupName, targetPlatform, dryRun)
	if resolveErr != nil {
		writeError(w, http.StatusBadRequest, resolveErr.Error())
		return
	}
	if groupID <= 0 {
		writeError(w, http.StatusBadRequest, "group_id/group_name 必须指向一个有效的 Sub2API 分组")
		return
	}

	items := s.selectedAccounts(body, platform)
	if len(items) == 0 {
		writeError(w, http.StatusBadRequest, "没有匹配到可导入的账号")
		return
	}

	accounts := make([]map[string]any, 0, len(items))
	for _, item := range items {
		accounts = append(accounts, s.sub2APIAccountPayload(item, targetPlatform, groupID, body))
	}
	payload := map[string]any{"accounts": accounts}

	result := map[string]any{
		"ok":             true,
		"dry_run":        dryRun,
		"selected_count": len(items),
		"target": map[string]any{
			"base_url": baseURL,
			"endpoint": endpoint,
			"group_id": groupID,
			"group":    resolvedGroup,
			"platform": targetPlatform,
		},
	}

	if dryRun {
		result["message"] = "dry_run 已启用：只生成 Sub2API 批量导入请求，不发起远程调用"
		result["payload_preview"] = maskSub2APIPayload(payload)
		if includePayload {
			result["payload"] = payload
		}
		writeJSON(w, http.StatusOK, result)
		return
	}

	resp, err := callSub2API(r.Context(), baseURL, endpoint, adminToken, authHeader, payload)
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	result["response"] = resp
	if data, ok := resp["data"].(map[string]any); ok {
		result["remote_success"] = intValue(data["success"], 0)
		result["remote_failed"] = intValue(data["failed"], 0)
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) sub2APIAccountPayload(item AccountRecord, targetPlatform string, groupID int64, body map[string]any) map[string]any {
	p := chatGPTPayload(item)
	creds := map[string]any{
		"access_token":       p["access_token"],
		"refresh_token":      p["refresh_token"],
		"id_token":           p["id_token"],
		"session_token":      p["session_token"],
		"chatgpt_account_id": p["account_id"],
		"chatgpt_user_id":    fallback(text(p["account_id"]), item.UserID),
		"client_id":          p["client_id"],
		"organization_id":    p["workspace_id"],
		"expires_at":         p["expires_at_unix"],
		"expires_in":         863999,
	}
	for k, v := range creds {
		if text(v) == "" || text(v) == "0" {
			delete(creds, k)
		}
	}
	extra := map[string]any{
		"import_source":     "SunnyRegister",
		"source_account_id": item.ID,
		"source_email":      item.Email,
		"source_status":     item.DisplayStatus,
		"imported_at":       formatTime(time.Now()),
	}
	if text(p["email_service"]) != "" {
		extra["email_service"] = p["email_service"]
	}
	if cookies := text(p["cookies"]); cookies != "" {
		extra["cookies"] = cookies
	}
	concurrency := intValue(body["concurrency"], 10)
	priority := intValue(body["priority"], 1)
	rateMultiplier := 1.0
	if raw := text(body["rate_multiplier"]); raw != "" {
		if v, err := strconv.ParseFloat(raw, 64); err == nil && v >= 0 {
			rateMultiplier = v
		}
	} else if n := intValue(body["rate_multiplier"], 0); n > 0 {
		rateMultiplier = float64(n)
	}
	autoPause := boolValue(body["auto_pause_on_expired"], true)
	confirmMixed := boolValue(body["confirm_mixed_channel_risk"], true)
	exp := int64(intValue(p["expires_at_unix"], 0))

	account := map[string]any{
		"name":                       fallback(text(p["email"]), fmt.Sprintf("account-%d", item.ID)),
		"platform":                   targetPlatform,
		"type":                       fallback(text(body["account_type"]), "oauth"),
		"credentials":                creds,
		"extra":                      extra,
		"concurrency":                concurrency,
		"priority":                   priority,
		"rate_multiplier":            rateMultiplier,
		"group_ids":                  []int64{groupID},
		"auto_pause_on_expired":      autoPause,
		"confirm_mixed_channel_risk": confirmMixed,
	}
	if exp > 0 {
		account["expires_at"] = exp
	}
	if notes := text(body["notes"]); notes != "" {
		account["notes"] = notes
	}
	return account
}

func (s *Server) resolveSub2APIGroupID(ctx context.Context, baseURL, adminToken, authHeader, groupIDRaw, groupName, targetPlatform string, dryRun bool) (int64, map[string]any, error) {
	if groupIDRaw != "" {
		id, err := strconv.ParseInt(strings.TrimSpace(groupIDRaw), 10, 64)
		if err != nil || id <= 0 {
			return 0, nil, fmt.Errorf("group_id 必须是正整数")
		}
		return id, map[string]any{"id": id, "name": groupName}, nil
	}
	if groupName == "" {
		return 0, nil, nil
	}
	if dryRun && adminToken == "" {
		return 0, nil, fmt.Errorf("dry_run 使用 group_name 时也需要 admin_token 来查询分组；也可以直接填写 group_id")
	}

	q := url.Values{}
	if targetPlatform != "" {
		q.Set("platform", targetPlatform)
	}
	q.Set("include_inactive", "true")
	resp, err := callSub2API(ctx, baseURL, sub2APIGroupLookupEndpoint+"?"+q.Encode(), adminToken, authHeader, nil)
	if err != nil {
		return 0, nil, fmt.Errorf("查询 Sub2API 分组失败: %w", err)
	}
	var groups []any
	if data, ok := resp["data"].([]any); ok {
		groups = data
	} else if arr, ok := resp["items"].([]any); ok {
		groups = arr
	}
	for _, raw := range groups {
		g, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if strings.EqualFold(text(g["name"]), groupName) {
			id := int64(intValue(g["id"], 0))
			if id > 0 {
				return id, g, nil
			}
		}
	}
	return 0, nil, fmt.Errorf("未在 Sub2API 中找到分组 %q", groupName)
}

func callSub2API(ctx context.Context, baseURL, endpoint, token, authHeader string, payload any) (map[string]any, error) {
	return callSub2APIWithHeaders(ctx, baseURL, endpoint, token, authHeader, payload, nil, false)
}

func callSub2APIWithHeaders(ctx context.Context, baseURL, endpoint, token, authHeader string, payload any, extraHeaders map[string]string, retryTransient bool) (map[string]any, error) {
	method := http.MethodGet
	var bodyBytes []byte
	if payload != nil {
		method = http.MethodPost
		b, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		bodyBytes = b
	}
	fullURL := strings.TrimRight(baseURL, "/") + "/" + strings.TrimLeft(endpoint, "/")
	client := &http.Client{Timeout: 90 * time.Second}
	var raw []byte
	var status int
	for attempt := 0; attempt < 2; attempt++ {
		var body io.Reader
		if bodyBytes != nil {
			body = bytes.NewReader(bodyBytes)
		}
		req, err := http.NewRequestWithContext(ctx, method, fullURL, body)
		if err != nil {
			return nil, err
		}
		if payload != nil {
			req.Header.Set("Content-Type", "application/json")
		}
		for key, value := range extraHeaders {
			req.Header.Set(key, value)
		}
		applySub2APIAuth(req, token, authHeader)
		res, err := client.Do(req)
		if err != nil {
			if retryTransient && attempt == 0 {
				continue
			}
			return nil, err
		}
		raw, _ = io.ReadAll(io.LimitReader(res.Body, 4<<20))
		status = res.StatusCode
		_ = res.Body.Close()
		if retryTransient && attempt == 0 && (status == http.StatusTooManyRequests || status >= 500) {
			continue
		}
		break
	}
	var decoded any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	_ = dec.Decode(&decoded)
	var out map[string]any
	switch value := decoded.(type) {
	case map[string]any:
		out = value
	case []any:
		out = map[string]any{"data": value}
	}
	if status < 200 || status >= 300 {
		if msg := text(out["message"]); msg != "" {
			return nil, fmt.Errorf("Sub2API %s %s 返回 %d: %s", method, endpoint, status, msg)
		}
		return nil, fmt.Errorf("Sub2API %s %s 返回 %d: %s", method, endpoint, status, strings.TrimSpace(string(raw)))
	}
	if out == nil {
		out = map[string]any{"raw": string(raw)}
	}
	if code := intValue(out["code"], 0); code != 0 {
		return out, fmt.Errorf("Sub2API 返回业务错误 code=%d message=%s", code, text(out["message"]))
	}
	return out, nil
}

func applySub2APIAuth(req *http.Request, token, authHeader string) {
	token = strings.TrimSpace(token)
	if token == "" {
		return
	}
	switch strings.ToLower(strings.TrimSpace(authHeader)) {
	case "authorization", "bearer", "jwt":
		req.Header.Set("Authorization", "Bearer "+token)
	case "x-admin-token":
		req.Header.Set("x-admin-token", token)
	case "x-api-key", "", "admin-api-key":
		req.Header.Set("x-api-key", token)
	default:
		req.Header.Set(authHeader, token)
	}
}

func maskSub2APIPayload(payload map[string]any) map[string]any {
	b, _ := json.Marshal(payload)
	var copy map[string]any
	_ = json.Unmarshal(b, &copy)
	accounts, _ := copy["accounts"].([]any)
	for _, raw := range accounts {
		acc, _ := raw.(map[string]any)
		creds, _ := acc["credentials"].(map[string]any)
		for k, v := range creds {
			if strings.Contains(strings.ToLower(k), "token") || strings.Contains(strings.ToLower(k), "secret") || strings.Contains(strings.ToLower(k), "cookie") {
				creds[k] = previewSecret(text(v))
			}
		}
	}
	return copy
}
