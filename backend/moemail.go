package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// moeMailClient is the server-side adapter for MoeMail OpenAPI. The API key
// never leaves this process; the frontend only sees the normalized mailbox
// fields already exposed by SunnyRegister.
type moeMailClient struct {
	baseURL string
	apiKey  string
	client  *http.Client
}

func moeMailConfigured(cfg map[string]any) bool {
	provider := strings.ToLower(strings.TrimSpace(text(cfg["provider"])))
	if provider != "" {
		return provider == "moemail" || provider == "moe_mail"
	}
	return strings.TrimSpace(firstText(cfg["moemail_api_key"], cfg["moemail_key"], osEnv("MOEMAIL_API_KEY"))) != ""
}

func splitDomainValues(value any) []string {
	values := make([]string, 0)
	for _, item := range strings.FieldsFunc(text(value), func(r rune) bool { return r == ',' || r == ';' || r == '\n' || r == '\r' }) {
		item = strings.ToLower(strings.TrimSpace(strings.TrimPrefix(item, "@")))
		if item != "" {
			values = append(values, item)
		}
	}
	return values
}

func newMoeMailClient(cfg map[string]any) (*moeMailClient, error) {
	base := strings.TrimRight(strings.TrimSpace(firstText(cfg["moemail_api_url"], osEnv("MOEMAIL_API_URL"))), "/")
	key := strings.TrimSpace(firstText(cfg["moemail_api_key"], cfg["moemail_key"], osEnv("MOEMAIL_API_KEY")))
	if base == "" || key == "" {
		return nil, fmt.Errorf("MoeMail 配置不完整：请填写 API 地址和 MOEMAIL_API_KEY")
	}
	parsed, err := url.Parse(base)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return nil, fmt.Errorf("MoeMail API 地址无效")
	}
	return &moeMailClient{baseURL: base, apiKey: key, client: &http.Client{Timeout: 30 * time.Second}}, nil
}

func osEnv(key string) string {
	// Kept as a tiny indirection so config resolution remains easy to test.
	return strings.TrimSpace(os.Getenv(key))
}

func (c *moeMailClient) request(ctx context.Context, method, path string, query url.Values, body any) (any, error) {
	endpoint, err := url.Parse(c.baseURL + path)
	if err != nil {
		return nil, fmt.Errorf("MoeMail 请求地址无效：%w", err)
	}
	if query != nil {
		endpoint.RawQuery = query.Encode()
	}
	var reader io.Reader
	if body != nil {
		encoded, marshalErr := json.Marshal(body)
		if marshalErr != nil {
			return nil, fmt.Errorf("MoeMail 请求参数编码失败：%w", marshalErr)
		}
		reader = strings.NewReader(string(encoded))
	}
	req, err := http.NewRequestWithContext(ctx, method, endpoint.String(), reader)
	if err != nil {
		return nil, fmt.Errorf("MoeMail 请求创建失败：%w", err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)
	req.Header.Set("User-Agent", "SunnyRegister/1.0")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("MoeMail 请求失败：%w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, fmt.Errorf("MoeMail 响应读取失败：%w", err)
	}
	summary := domainMailResponseSummary(raw)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("MoeMail 请求失败：HTTP %d：%s", resp.StatusCode, summary)
	}
	if len(strings.TrimSpace(string(raw))) == 0 {
		return map[string]any{}, nil
	}
	var payload any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, fmt.Errorf("MoeMail 返回内容不是有效 JSON：%s", summary)
	}
	if obj, ok := payload.(map[string]any); ok {
		if success, exists := obj["success"]; exists && !boolValue(success, true) {
			return nil, fmt.Errorf("MoeMail 请求失败：%s", firstText(obj["message"], obj["error"], obj["detail"], "success=false"))
		}
		if code := text(obj["code"]); code != "" && code != "0" && code != "200" {
			return nil, fmt.Errorf("MoeMail 请求失败：%s", firstText(obj["message"], obj["error"], obj["detail"], code))
		}
	}
	return payload, nil
}

func moeMailObject(payload any) map[string]any {
	if obj, ok := payload.(map[string]any); ok {
		for _, key := range []string{"data", "result", "email", "message"} {
			if nested, ok := obj[key].(map[string]any); ok {
				if key == "data" || key == "result" || key == "email" || key == "message" {
					return nested
				}
			}
		}
		return obj
	}
	return map[string]any{}
}

func moeMailRows(payload any, keys ...string) []map[string]any {
	if list, ok := payload.([]any); ok {
		return domainMailMapList(list)
	}
	if obj, ok := payload.(map[string]any); ok {
		for _, key := range append(keys, "data", "result", "items", "rows", "records") {
			if rows, ok := obj[key].([]any); ok {
				return domainMailMapList(rows)
			}
			if nested, ok := obj[key].(map[string]any); ok {
				if rows := moeMailRows(nested, keys...); len(rows) > 0 {
					return rows
				}
			}
		}
	}
	return nil
}

func (c *moeMailClient) config(ctx context.Context) (map[string]any, error) {
	payload, err := c.request(ctx, http.MethodGet, "/api/config", nil, nil)
	if err != nil {
		return nil, err
	}
	return moeMailObject(payload), nil
}

func (c *moeMailClient) generate(ctx context.Context, name, domain string, expiryTime int64) (string, string, error) {
	payload, err := c.request(ctx, http.MethodPost, "/api/emails/generate", nil, map[string]any{
		"name": name, "expiryTime": expiryTime, "domain": domain,
	})
	if err != nil {
		return "", "", err
	}
	obj := moeMailObject(payload)
	id := firstText(obj["id"], obj["emailId"])
	email := firstText(obj["email"], obj["address"])
	if id == "" || email == "" {
		return "", "", fmt.Errorf("MoeMail 创建邮箱响应缺少 id 或 email")
	}
	return id, strings.ToLower(strings.TrimSpace(email)), nil
}

func (c *moeMailClient) listMailboxes(ctx context.Context) ([]map[string]any, error) {
	var all []map[string]any
	cursor := ""
	for page := 0; page < 100; page++ {
		query := url.Values{}
		if cursor != "" {
			query.Set("cursor", cursor)
		}
		payload, err := c.request(ctx, http.MethodGet, "/api/emails", query, nil)
		if err != nil {
			return nil, err
		}
		rows := moeMailRows(payload, "emails")
		all = append(all, rows...)
		obj := moeMailObject(payload)
		cursor = strings.TrimSpace(firstText(obj["nextCursor"], obj["next_cursor"]))
		if cursor == "" || len(rows) == 0 {
			break
		}
	}
	return all, nil
}

func (c *moeMailClient) resolveMailboxID(ctx context.Context, email string) (string, error) {
	rows, err := c.listMailboxes(ctx)
	if err != nil {
		return "", err
	}
	for _, row := range rows {
		if strings.EqualFold(strings.TrimSpace(firstText(row["address"], row["email"])), strings.TrimSpace(email)) {
			if id := firstText(row["id"], row["emailId"]); id != "" {
				return id, nil
			}
		}
	}
	return "", fmt.Errorf("MoeMail 未找到邮箱：%s", email)
}

func (c *moeMailClient) listMessages(ctx context.Context, email, mailboxID string) ([]map[string]any, error) {
	if strings.TrimSpace(mailboxID) == "" {
		var err error
		mailboxID, err = c.resolveMailboxID(ctx, email)
		if err != nil {
			return nil, err
		}
	}
	payload, err := c.request(ctx, http.MethodGet, "/api/emails/"+url.PathEscape(mailboxID), nil, nil)
	if err != nil {
		return nil, err
	}
	rows := moeMailRows(payload, "messages")
	if len(rows) > 20 {
		rows = rows[:20]
	}
	for index, row := range rows {
		messageID := firstText(row["id"], row["messageId"])
		if messageID == "" {
			continue
		}
		detailPayload, detailErr := c.request(ctx, http.MethodGet, "/api/emails/"+url.PathEscape(mailboxID)+"/"+url.PathEscape(messageID), nil, nil)
		if detailErr == nil {
			if detail := moeMailObject(detailPayload); len(detail) > 0 {
				for key, value := range detail {
					row[key] = value
				}
			}
		} else if index == 0 && len(rows) == 1 {
			return nil, detailErr
		}
		rows[index] = normalizeMoeMailMessage(row, email)
	}
	return rows, nil
}

func normalizeMoeMailMessage(row map[string]any, email string) map[string]any {
	message := map[string]any{}
	for key, value := range row {
		message[key] = value
	}
	message["id"] = firstText(row["id"], row["messageId"])
	message["from"] = firstText(row["from_address"], row["fromAddress"], row["from"])
	message["sender"] = message["from"]
	message["to"] = firstText(row["to_address"], row["toAddress"], row["to"], email)
	message["recipient"] = message["to"]
	message["subject"] = text(row["subject"])
	message["text"] = firstText(row["content"], row["text"], row["body"])
	message["body"] = message["text"]
	message["html"] = firstText(row["html"], row["html_content"])
	message["received_at"] = firstText(row["received_at"], row["receivedAt"], row["createdAt"], row["created_at"])
	message["receivedAt"] = message["received_at"]
	return message
}

func (c *moeMailClient) deleteMailbox(ctx context.Context, email, mailboxID string) error {
	if strings.TrimSpace(mailboxID) == "" {
		var err error
		mailboxID, err = c.resolveMailboxID(ctx, email)
		if err != nil {
			return err
		}
	}
	_, err := c.request(ctx, http.MethodDelete, "/api/emails/"+url.PathEscape(mailboxID), nil, nil)
	return err
}

func (s *Server) moeMailWebhookHandler(w http.ResponseWriter, r *http.Request) {
	cfg := mergeConfig(defaultDomainMailboxConfig(), s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig()))
	if domainMailboxProvider(cfg) != "moemail" {
		writeError(w, http.StatusNotFound, "MoeMail 未启用")
		return
	}
	secret := strings.TrimSpace(firstText(cfg["moemail_webhook_secret"], osEnv("MOEMAIL_WEBHOOK_SECRET")))
	if secret != "" {
		provided := strings.TrimSpace(firstText(r.Header.Get("X-MoeMail-Webhook-Secret"), r.Header.Get("X-Webhook-Secret")))
		if provided == "" && strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			provided = strings.TrimSpace(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
		}
		if subtle.ConstantTimeCompare([]byte(secret), []byte(provided)) != 1 {
			writeError(w, http.StatusUnauthorized, "MoeMail Webhook 密钥无效")
			return
		}
	}
	body, err := parseBody(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "MoeMail Webhook 请求体无效")
		return
	}
	message := normalizeMoeMailMessage(body, firstText(body["toAddress"], body["to_address"], body["email"]))
	email := strings.TrimSpace(text(message["to"]))
	if email == "" {
		writeError(w, http.StatusBadRequest, "MoeMail Webhook 缺少收件邮箱")
		return
	}
	var mailbox SunnyMailbox
	if err := s.db.Where("LOWER(email) = ? OR LOWER(rebind_email) = ?", sunnyEmailKey(email), sunnyEmailKey(email)).First(&mailbox).Error; err != nil {
		// Acknowledge unknown addresses so an unrelated stale mailbox cannot
		// cause MoeMail to retry the delivery forever.
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "matched": false})
		return
	}
	item := domainMailItems([]map[string]any{message}, email)
	payload := map[string]any{"email": email, "mailbox_type": "domain", "mailbox_channel": "domain_api", "mail_protocol": "domain_api", "items": item, "count": len(item), "limit": 1}
	if err := s.db.Model(&mailbox).UpdateColumns(map[string]any{"latest_mail_json": dumpJSON(payload), "last_mail_at": time.Now(), "last_error": ""}).Error; err != nil {
		writeError(w, http.StatusInternalServerError, "保存 MoeMail Webhook 邮件失败："+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "matched": true})
}
