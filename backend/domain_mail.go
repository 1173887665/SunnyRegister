package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const sunnyCfgDomainMailbox = "domain_mailbox"

var domainMailOTPPattern = regexp.MustCompile(`(?:^|\D)(\d{6})(?:\D|$)`)

func defaultDomainMailboxConfig() map[string]any {
	return map[string]any{
		"enabled":                  true,
		"enabled_for_registration": false,
		"enabled_for_rebinding":    false,
		"base_url":                 "",
		"auth_token":               "",
		"domain":                   "",
		"random_local_length":      12,
		"auto_add_user":            true,
	}
}

type domainMailClient struct {
	baseURL string
	token   string
	client  *http.Client
}

func newDomainMailClient(cfg map[string]any) (*domainMailClient, error) {
	base := strings.TrimRight(strings.TrimSpace(text(cfg["base_url"])), "/")
	token := strings.TrimSpace(text(cfg["auth_token"]))
	domain := strings.TrimSpace(text(cfg["domain"]))
	if base == "" || token == "" || domain == "" {
		return nil, fmt.Errorf("自建域名邮箱配置不完整：请填写 API 地址、Authorization Token 和邮箱域名")
	}
	parsed, err := url.Parse(base)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return nil, fmt.Errorf("自建域名邮箱 API 地址无效")
	}
	if strings.ContainsAny(domain, " @\t\r\n") || !strings.Contains(domain, ".") {
		return nil, fmt.Errorf("自建域名邮箱域名无效")
	}
	return &domainMailClient{baseURL: base, token: token, client: &http.Client{Timeout: 30 * time.Second}}, nil
}

func (c *domainMailClient) request(ctx context.Context, method, path string, body any) (any, error) {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = strings.NewReader(string(encoded))
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", c.token)
	req.Header.Set("X-Auth-Token", c.token)
	req.Header.Set("User-Agent", "SunnyRegister/1.0")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("自建域名邮箱请求失败：%w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if err != nil {
		return nil, err
	}
	var payload any
	if strings.TrimSpace(string(raw)) != "" {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return nil, fmt.Errorf("自建域名邮箱返回格式错误：%w", err)
		}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("自建域名邮箱请求失败：HTTP %d：%s", resp.StatusCode, strings.TrimSpace(string(raw)))
	}
	if obj, ok := payload.(map[string]any); ok {
		if code := text(obj["code"]); code != "" && code != "200" && code != "0" {
			return nil, fmt.Errorf("自建域名邮箱请求失败：%s", fallback(firstText(obj["message"], obj["error"], obj["detail"]), code))
		}
	}
	return payload, nil
}

func (c *domainMailClient) addUser(ctx context.Context, email string) error {
	password := randomDomainSecret(18)
	_, err := c.request(ctx, http.MethodPost, "/api/public/addUser", map[string]any{
		"list": []map[string]string{{"email": email, "password": password}},
	})
	return err
}

func (c *domainMailClient) listMessages(ctx context.Context, email string) ([]map[string]any, error) {
	payload, err := c.request(ctx, http.MethodPost, "/api/public/emailList", map[string]any{
		"toEmail": email, "timeSort": "desc", "type": 0, "isDel": 0, "num": 1, "size": 20,
	})
	if err != nil {
		return nil, err
	}
	return domainMailMessageList(payload), nil
}

func domainMailMessageList(payload any) []map[string]any {
	if list, ok := payload.([]any); ok {
		return domainMailMapList(list)
	}
	if obj, ok := payload.(map[string]any); ok {
		for _, key := range []string{"data", "items", "messages", "result", "list", "rows", "records"} {
			if found := domainMailMessageList(obj[key]); len(found) > 0 {
				return found
			}
		}
	}
	return nil
}

func domainMailMapList(raw []any) []map[string]any {
	items := make([]map[string]any, 0, len(raw))
	for _, value := range raw {
		if item, ok := value.(map[string]any); ok {
			items = append(items, item)
		}
	}
	return items
}

func domainMailMessageCode(message map[string]any) string {
	for _, key := range []string{"verificationCode", "verification_code", "otp", "code"} {
		value := strings.TrimSpace(text(message[key]))
		if len(value) == 6 && domainMailOTPPattern.MatchString(value) {
			return value
		}
	}
	for _, key := range []string{"subject", "text", "content", "html", "body", "bodyPreview"} {
		if match := domainMailOTPPattern.FindStringSubmatch(text(message[key])); len(match) > 1 {
			return match[1]
		}
	}
	return ""
}

func domainMailItems(messages []map[string]any, email string) []map[string]any {
	items := make([]map[string]any, 0, len(messages))
	for _, message := range messages {
		body := firstText(message["text"], message["body"], message["content"], message["html"])
		items = append(items, map[string]any{
			"id":           firstText(message["emailId"], message["id"], message["messageId"]),
			"email":        firstText(message["toEmail"], message["recipient"], message["to"], email),
			"folder":       "自建域名邮箱",
			"subject":      text(message["subject"]),
			"from":         firstText(message["sendEmail"], message["sender"], message["from"]),
			"to":           firstText(message["toEmail"], message["recipient"], message["to"], email),
			"date":         firstText(message["createTime"], message["receivedAt"], message["received_at"], message["date"]),
			"body":         body,
			"body_preview": firstText(message["bodyPreview"], body),
			"raw_html":     firstText(message["content"], message["html"], body),
			"otp":          domainMailMessageCode(message),
			"source":       "domain_api",
		})
	}
	return items
}

func randomDomainSecret(length int) string {
	const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	if length < 12 {
		length = 12
	}
	buf := make([]byte, length)
	if _, err := rand.Read(buf); err != nil {
		return randomID("domain")
	}
	for index := range buf {
		buf[index] = alphabet[int(buf[index])%len(alphabet)]
	}
	return string(buf)
}

func randomDomainEmail(domain string, length int) string {
	if length < 6 {
		length = 6
	}
	if length > 32 {
		length = 32
	}
	return strings.ToLower(randomDomainSecret(length)) + "@" + strings.ToLower(strings.TrimSpace(domain))
}

func domainMailboxCredential(baseURL, token string) string {
	return dumpJSON(map[string]string{"base_url": strings.TrimRight(strings.TrimSpace(baseURL), "/"), "auth_token": strings.TrimSpace(token)})
}

func parseDomainMailboxCredential(value string) (string, string, error) {
	var payload map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(value)), &payload); err != nil {
		return "", "", fmt.Errorf("自建域名邮箱凭证格式无效")
	}
	base := strings.TrimRight(strings.TrimSpace(text(payload["base_url"])), "/")
	token := strings.TrimSpace(text(payload["auth_token"]))
	if base == "" || token == "" {
		return "", "", fmt.Errorf("自建域名邮箱凭证缺少 API 地址或 Authorization Token")
	}
	return base, token, nil
}

func domainMailLatestMail(accessKey, email string, limit int) (map[string]any, error) {
	base, token, err := parseDomainMailboxCredential(accessKey)
	if err != nil {
		return nil, err
	}
	client := &domainMailClient{baseURL: base, token: token, client: &http.Client{Timeout: 30 * time.Second}}
	messages, err := client.listMessages(context.Background(), email)
	if err != nil {
		return nil, err
	}
	items := domainMailItems(messages, email)
	if limit < 1 || limit > 50 {
		limit = 5
	}
	if len(items) > limit {
		items = items[:limit]
	}
	return map[string]any{
		"email": email, "mailbox_type": "domain", "mailbox_channel": "domain_api", "mail_protocol": "domain_api",
		"items": items, "count": len(items), "limit": limit,
	}, nil
}

func (s *Server) domainMailboxConfigHandler(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 1 && parts[0] == "config" && r.Method == http.MethodGet {
		cfg := mergeConfig(defaultDomainMailboxConfig(), s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig()))
		cfg["auth_token_configured"] = strings.TrimSpace(text(cfg["auth_token"])) != ""
		cfg["auth_token"] = ""
		writeJSON(w, http.StatusOK, cfg)
		return
	}
	if len(parts) == 1 && parts[0] == "config" && r.Method == http.MethodPut {
		body, _ := parseBody(r)
		if strings.TrimSpace(text(body["auth_token"])) == "" {
			current := mergeConfig(defaultDomainMailboxConfig(), s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig()))
			body["auth_token"] = text(current["auth_token"])
		}
		cfg := mergeConfig(defaultDomainMailboxConfig(), body)
		s.sunnySaveConfig(sunnyCfgDomainMailbox, cfg)
		cfg["auth_token_configured"] = strings.TrimSpace(text(cfg["auth_token"])) != ""
		cfg["auth_token"] = ""
		writeJSON(w, http.StatusOK, cfg)
		return
	}
	if len(parts) != 1 || r.Method != http.MethodPost {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	cfg := mergeConfig(defaultDomainMailboxConfig(), s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig()))
	if body, _ := parseBody(r); body != nil {
		enabled := cfg["enabled"]
		if strings.TrimSpace(text(body["auth_token"])) == "" {
			body["auth_token"] = text(cfg["auth_token"])
		}
		cfg = mergeConfig(cfg, body)
		// Operational requests may test unsaved connection fields, but the
		// persisted master switch cannot be bypassed through request payloads.
		cfg["enabled"] = enabled
	}
	client, err := newDomainMailClient(cfg)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	switch parts[0] {
	case "check":
		_, err = client.listMessages(ctx, "healthcheck@"+strings.TrimSpace(text(cfg["domain"])))
		if err == nil {
			writeJSON(w, http.StatusOK, map[string]any{"ok": true, "domain": text(cfg["domain"])})
			return
		}
	case "generate":
		if !boolValue(cfg["enabled"], true) {
			writeError(w, http.StatusBadRequest, "自建域名邮箱池已关闭，请先在邮箱配置中启用")
			return
		}
		length := intValue(cfg["random_local_length"], 12)
		var mailbox SunnyMailbox
		for attempt := 0; attempt < 3; attempt++ {
			email := randomDomainEmail(text(cfg["domain"]), length)
			if boolValue(cfg["auto_add_user"], true) {
				if err = client.addUser(ctx, email); err != nil {
					continue
				}
			}
			mailbox = SunnyMailbox{GroupID: s.sunnyEnsureDefaultGroup(), Email: email, MailboxType: "domain", MailboxChannel: "domain_api", AccessKey: domainMailboxCredential(client.baseURL, client.token), Raw: sunnyURLAPIRaw(email, domainMailboxCredential(client.baseURL, client.token)), AccountType: "free", Status: "未注册", Enabled: true, LatestMailJSON: "{}"}
			if err = s.db.Create(&mailbox).Error; err == nil {
				writeJSON(w, http.StatusOK, map[string]any{"id": mailbox.ID, "email": mailbox.Email, "mailbox_type": mailbox.MailboxType, "mailbox_channel": mailbox.MailboxChannel})
				return
			}
		}
	default:
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	writeError(w, http.StatusBadRequest, "自建域名邮箱操作失败")
}

func (s *Server) validateDomainMailboxRegistration(body map[string]any) error {
	cfg := mergeConfig(defaultDomainMailboxConfig(), s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig()))
	if !boolValue(cfg["enabled"], true) {
		return fmt.Errorf("自建域名邮箱池已关闭，请先在邮箱配置中启用")
	}
	if !boolValue(cfg["enabled_for_registration"], false) {
		return fmt.Errorf("自建域名邮箱未启用账户注册，请先在邮箱配置中启用")
	}
	if _, err := newDomainMailClient(cfg); err != nil {
		return err
	}
	count := intValue(body["count"], 1)
	if count < 1 || count > 200 {
		return fmt.Errorf("自建域名邮箱本次生成数量必须在 1 到 200 之间")
	}
	return nil
}

func (s *Server) prepareDomainMailboxRegistration(body map[string]any) error {
	if err := s.validateDomainMailboxRegistration(body); err != nil {
		return err
	}
	cfg := s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig())
	client, err := newDomainMailClient(cfg)
	if err != nil {
		return err
	}
	count := intValue(body["count"], 1)
	length := intValue(cfg["random_local_length"], 12)
	groupName := "domain-api-" + time.Now().Format("01-02")
	var group SunnyMailboxGroup
	if err := s.db.Where("name = ?", groupName).First(&group).Error; err != nil {
		group = SunnyMailboxGroup{Name: groupName, Description: "自建域名邮箱 API 自动生成"}
		if err := s.db.Create(&group).Error; err != nil {
			return fmt.Errorf("创建自建域名邮箱分组失败：%w", err)
		}
	}
	credential := domainMailboxCredential(client.baseURL, client.token)
	ids := make([]uint, 0, count)
	created := make([]uint, 0, count)
	for index := 0; index < count; index++ {
		var mailbox SunnyMailbox
		var createErr error
		for attempt := 0; attempt < 5; attempt++ {
			email := randomDomainEmail(text(cfg["domain"]), length)
			var existing SunnyMailbox
			if s.db.Where("lower(email) = ?", sunnyEmailKey(email)).First(&existing).Error == nil {
				continue
			}
			if boolValue(cfg["auto_add_user"], true) {
				if createErr = client.addUser(context.Background(), email); createErr != nil {
					break
				}
			}
			mailbox = SunnyMailbox{
				GroupID: group.ID, Email: email, MailboxType: "domain", MailboxChannel: "domain_api",
				AccessKey: credential, Raw: strings.Join([]string{email, credential}, "----"), AccountType: "free",
				Status: "未注册", Enabled: true, LatestMailJSON: "{}",
			}
			createErr = s.db.Create(&mailbox).Error
			if createErr == nil {
				break
			}
		}
		if createErr != nil || mailbox.ID == 0 {
			for _, id := range created {
				s.db.Delete(&SunnyMailbox{}, id)
			}
			if createErr == nil {
				createErr = fmt.Errorf("生成邮箱失败")
			}
			return fmt.Errorf("自建域名邮箱第 %d 个生成失败：%w", index+1, createErr)
		}
		created = append(created, mailbox.ID)
		ids = append(ids, mailbox.ID)
	}
	body["mailbox_ids"] = ids
	return nil
}
