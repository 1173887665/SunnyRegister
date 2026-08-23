package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const sunnyCfgRemail = "remail"

func defaultRemailConfig() map[string]any {
	return map[string]any{
		"enabled":               false,
		"base_url":              "https://remail.aishop6.com",
		"api_key":               "",
		"project_id":            0,
		"email_suffix":          "",
		"service_mode":          "purchase",
		"service_mode_explicit": false,
		"supply":                "private_first",
	}
}

type remailAPIError struct {
	StatusCode int
	Message    string
}

func (e *remailAPIError) Error() string {
	return fmt.Sprintf("Remail HTTP %d：%s", e.StatusCode, e.Message)
}

type remailClient struct {
	baseURL string
	apiKey  string
	client  *http.Client
}

type remailOrder struct {
	ID               any    `json:"id"`
	OrderNo          string `json:"orderNo"`
	ProjectID        any    `json:"projectId"`
	ProductType      string `json:"productType"`
	ServiceMode      string `json:"serviceMode"`
	Status           string `json:"status"`
	DeliveryEmail    string `json:"deliveryEmail"`
	ReceiveStartedAt string `json:"receiveStartedAt"`
	ReceiveUntil     string `json:"receiveUntil"`
	ServiceToken     string `json:"serviceToken"`
	VerificationCode string `json:"verificationCode"`
	LastMailReceived string `json:"lastMailReceivedAt"`
	CreatedAt        string `json:"createdAt"`
	UpdatedAt        string `json:"updatedAt"`
}

func newRemailClient(cfg map[string]any) (*remailClient, error) {
	base := strings.TrimRight(strings.TrimSpace(text(cfg["base_url"])), "/")
	key := strings.TrimSpace(text(cfg["api_key"]))
	if base == "" || key == "" {
		return nil, fmt.Errorf("Remail 配置不完整：请填写接口地址和 API Key")
	}
	if parsed, err := url.Parse(base); err != nil || parsed.Scheme != "http" && parsed.Scheme != "https" || parsed.Host == "" {
		return nil, fmt.Errorf("Remail 接口地址无效")
	}
	return &remailClient{baseURL: base, apiKey: key, client: &http.Client{Timeout: 30 * time.Second}}, nil
}

func (c *remailClient) request(ctx context.Context, method, path string, query url.Values, body any) (map[string]any, error) {
	endpoint := c.baseURL + path
	idempotencyKey := ""
	if query != nil {
		idempotencyKey = query.Get("Idempotency-Key")
		query.Del("Idempotency-Key")
	}
	if len(query) > 0 {
		endpoint += "?" + query.Encode()
	}
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = strings.NewReader(string(encoded))
	}
	req, err := http.NewRequestWithContext(ctx, method, endpoint, reader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "SunnyRegister/1.0")
	req.Header.Set("X-API-Key", c.apiKey)
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	if idempotencyKey != "" {
		req.Header.Set("Idempotency-Key", idempotencyKey)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Remail 请求失败：%w", err)
	}
	defer resp.Body.Close()
	raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if readErr != nil {
		return nil, readErr
	}
	var payload map[string]any
	if len(strings.TrimSpace(string(raw))) > 0 {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return nil, fmt.Errorf("Remail 返回格式错误：%w", err)
		}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		detail := strings.TrimSpace(string(raw))
		if payload != nil {
			detail = fallback(firstText(payload["message"], payload["error"], payload["detail"]), detail)
			if detail != strings.TrimSpace(string(raw)) && strings.Contains(strings.ToLower(string(raw)), "insufficient") {
				detail += "；" + strings.TrimSpace(string(raw))
			}
		}
		return nil, &remailAPIError{StatusCode: resp.StatusCode, Message: detail}
	}
	if payload == nil {
		payload = map[string]any{}
	}
	return payload, nil
}

func (c *remailClient) profile(ctx context.Context) (map[string]any, error) {
	return c.request(ctx, http.MethodGet, "/v1/open/apikey/profile", nil, nil)
}

func (c *remailClient) projects(ctx context.Context) (map[string]any, error) {
	return c.request(ctx, http.MethodGet, "/v1/open/projects", nil, nil)
}

func (c *remailClient) wallet(ctx context.Context) (map[string]any, error) {
	return c.request(ctx, http.MethodGet, "/v1/open/wallet", nil, nil)
}

func (c *remailClient) createOrder(ctx context.Context, cfg map[string]any) (remailOrder, map[string]any, error) {
	projectID := intValue(cfg["project_id"], 0)
	if projectID <= 0 {
		return remailOrder{}, nil, fmt.Errorf("Remail 未配置项目 ID")
	}
	query := url.Values{}
	serviceMode := fallback(text(cfg["service_mode"]), "purchase")
	if serviceMode == "code" && !boolValue(cfg["service_mode_explicit"], false) {
		serviceMode = "purchase"
	}
	supply := fallback(text(cfg["supply"]), "private_first")
	query.Set("serviceMode", serviceMode)
	query.Set("supply", supply)
	body := map[string]any{"projectId": projectID}
	if suffix := strings.TrimSpace(text(cfg["email_suffix"])); suffix != "" {
		body["emailSuffix"] = suffix
	}
	query.Set("Idempotency-Key", randomID("remail"))
	payload, err := c.request(ctx, http.MethodPost, "/v1/open/orders", query, body)
	if err != nil {
		return remailOrder{}, nil, err
	}
	orderPayload := payload
	for _, key := range []string{"data", "order", "result"} {
		if nested, ok := payload[key].(map[string]any); ok {
			orderPayload = nested
			break
		}
	}
	encoded, _ := json.Marshal(orderPayload)
	var order remailOrder
	if err := json.Unmarshal(encoded, &order); err != nil {
		return remailOrder{}, nil, err
	}
	if (order.DeliveryEmail == "" || order.ServiceToken == "") && order.OrderNo != "" {
		for attempt := 0; attempt < 20; attempt++ {
			if ctx.Err() != nil {
				return order, payload, ctx.Err()
			}
			time.Sleep(2 * time.Second)
			latest, latestPayload, latestErr := c.order(ctx, order.OrderNo)
			if latestErr == nil {
				order = latest
				payload = latestPayload
				if order.DeliveryEmail != "" && order.ServiceToken != "" {
					break
				}
			}
		}
	}
	if order.DeliveryEmail == "" || order.ServiceToken == "" {
		return order, payload, fmt.Errorf("Remail 下单成功但未返回邮箱或 serviceToken")
	}
	return order, payload, nil
}

func (c *remailClient) order(ctx context.Context, orderNo string) (remailOrder, map[string]any, error) {
	payload, err := c.request(ctx, http.MethodGet, "/v1/open/orders/"+url.PathEscape(orderNo), nil, nil)
	if err != nil {
		return remailOrder{}, nil, err
	}
	orderPayload := payload
	for _, key := range []string{"data", "order", "result"} {
		if nested, ok := payload[key].(map[string]any); ok {
			orderPayload = nested
			break
		}
	}
	encoded, _ := json.Marshal(orderPayload)
	var order remailOrder
	if err := json.Unmarshal(encoded, &order); err != nil {
		return remailOrder{}, payload, err
	}
	return order, payload, nil
}

func (s *Server) remailConfigHandler(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 1 && parts[0] == "config" && r.Method == http.MethodGet {
		cfg := s.sunnyGetConfig(sunnyCfgRemail, defaultRemailConfig())
		if text(cfg["service_mode"]) == "code" && !boolValue(cfg["service_mode_explicit"], false) {
			cfg["service_mode"] = "purchase"
			s.sunnySaveConfig(sunnyCfgRemail, cfg)
		}
		cfg["api_key_configured"] = strings.TrimSpace(text(cfg["api_key"])) != ""
		cfg["api_key"] = ""
		writeJSON(w, http.StatusOK, cfg)
		return
	}
	if len(parts) == 1 && parts[0] == "config" && r.Method == http.MethodPut {
		body, _ := parseBody(r)
		if strings.TrimSpace(text(body["api_key"])) == "" {
			current := s.sunnyGetConfig(sunnyCfgRemail, defaultRemailConfig())
			if key := strings.TrimSpace(text(current["api_key"])); key != "" {
				body["api_key"] = key
			}
		}
		cfg := mergeConfig(defaultRemailConfig(), body)
		cfg["service_mode_explicit"] = true
		s.sunnySaveConfig(sunnyCfgRemail, cfg)
		cfg["api_key_configured"] = strings.TrimSpace(text(cfg["api_key"])) != ""
		cfg["api_key"] = ""
		writeJSON(w, http.StatusOK, cfg)
		return
	}
	if len(parts) != 1 || r.Method != http.MethodPost {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	cfg := s.sunnyGetConfig(sunnyCfgRemail, defaultRemailConfig())
	if body, _ := parseBody(r); body != nil {
		if strings.TrimSpace(text(body["api_key"])) == "" {
			body["api_key"] = text(cfg["api_key"])
		}
		cfg = mergeConfig(cfg, body)
	}
	client, err := newRemailClient(cfg)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	var payload map[string]any
	switch parts[0] {
	case "profile":
		payload, err = client.profile(ctx)
	case "projects":
		payload, err = client.projects(ctx)
	case "check":
		payload, err = client.wallet(ctx)
		if err == nil {
			payload["ok"] = true
		}
	case "wallet":
		payload, err = client.wallet(ctx)
	default:
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, payload)
}

func remailTokenPayload(baseURL, apiKey string, order remailOrder) string {
	value := map[string]any{"base_url": baseURL, "api_key": apiKey, "order_no": order.OrderNo, "service_token": order.ServiceToken, "receive_until": order.ReceiveUntil}
	b, _ := json.Marshal(value)
	return string(b)
}

func remailOrderNoFromAccessKey(accessKey string) string {
	var payload map[string]any
	if json.Unmarshal([]byte(accessKey), &payload) == nil {
		return strings.TrimSpace(text(payload["order_no"]))
	}
	return ""
}

func remailServiceTokenFromAccessKey(accessKey string) string {
	var payload map[string]any
	if json.Unmarshal([]byte(accessKey), &payload) == nil {
		return strings.TrimSpace(text(payload["service_token"]))
	}
	return strings.TrimSpace(accessKey)
}

func remailBaseURLFromAccessKey(accessKey string) string {
	var payload map[string]any
	if json.Unmarshal([]byte(accessKey), &payload) == nil {
		return strings.TrimRight(strings.TrimSpace(text(payload["base_url"])), "/")
	}
	return ""
}

func remailClientFromAccessKey(accessKey string) (*remailClient, string, error) {
	var payload map[string]any
	if err := json.Unmarshal([]byte(accessKey), &payload); err != nil {
		return nil, "", fmt.Errorf("Remail 凭证格式无效")
	}
	baseURL := strings.TrimRight(strings.TrimSpace(text(payload["base_url"])), "/")
	apiKey := strings.TrimSpace(text(payload["api_key"]))
	orderNo := strings.TrimSpace(text(payload["order_no"]))
	if baseURL == "" || apiKey == "" || orderNo == "" {
		return nil, "", fmt.Errorf("Remail 凭证缺少 base_url、api_key 或 order_no")
	}
	return &remailClient{baseURL: baseURL, apiKey: apiKey, client: &http.Client{Timeout: 30 * time.Second}}, orderNo, nil
}

func remailLatestMail(accessKey, email string) (map[string]any, error) {
	client, orderNo, err := remailClientFromAccessKey(accessKey)
	if err != nil {
		return nil, err
	}
	order, orderPayload, orderErr := client.order(context.Background(), orderNo)
	if orderErr != nil {
		return nil, orderErr
	}
	payload := orderPayload
	if order.VerificationCode == "" {
		query := url.Values{}
		query.Set("orderNo", orderNo)
		query.Set("serviceToken", order.ServiceToken)
		if pickup, err := client.request(context.Background(), http.MethodGet, "/v1/pickup", query, nil); err == nil {
			payload = pickup
			order.VerificationCode = remailCodeFromPayload(pickup)
		}
	}
	return map[string]any{"id": orderNo, "email": email, "folder": "Remail", "subject": "Remail verification", "body": dumpJSON(payload), "body_preview": dumpJSON(payload), "raw_html": "", "otp": order.VerificationCode, "source": "remail_api", "date": order.LastMailReceived}, nil
}

func remailCodeFromPayload(value any) string {
	if obj, ok := value.(map[string]any); ok {
		for _, key := range []string{"verificationCode", "verification_code", "otp", "code"} {
			candidate := strings.TrimSpace(text(obj[key]))
			if len(candidate) == 6 {
				valid := true
				for _, runeValue := range candidate {
					if runeValue < '0' || runeValue > '9' {
						valid = false
						break
					}
				}
				if valid {
					return candidate
				}
			}
		}
		for _, nested := range obj {
			if code := remailCodeFromPayload(nested); code != "" {
				return code
			}
		}
	} else if list, ok := value.([]any); ok {
		for _, nested := range list {
			if code := remailCodeFromPayload(nested); code != "" {
				return code
			}
		}
	}
	return ""
}

func remailDateGroupName(now time.Time) string {
	return "rm-api-" + now.In(applicationLocation()).Format("01-02")
}

func isRemailInsufficientBalance(err error) bool {
	if err == nil {
		return false
	}
	lower := strings.ToLower(err.Error())
	return strings.Contains(lower, "余额不足") || strings.Contains(lower, "insufficient funds") || (strings.Contains(lower, "insufficient") && strings.Contains(lower, "balance"))
}

func (s *Server) prepareRemailRegistration(body map[string]any) error {
	cfg := s.sunnyGetConfig(sunnyCfgRemail, defaultRemailConfig())
	if !boolValue(cfg["enabled"], false) {
		return fmt.Errorf("Remail 未启用，请先在邮箱配置中启用并保存")
	}
	client, err := newRemailClient(cfg)
	if err != nil {
		return err
	}
	count := intValue(body["count"], 1)
	if count < 1 || count > 200 {
		return fmt.Errorf("Remail 注册数量必须在 1 到 200 之间")
	}
	gid := uint(0)
	group := SunnyMailboxGroup{Name: remailDateGroupName(time.Now())}
	if err := s.db.FirstOrCreate(&group, SunnyMailboxGroup{Name: group.Name}).Error; err != nil {
		return err
	}
	gid = group.ID
	ids := make([]uint, 0, count)
	requestedCount := count
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(45+count*5)*time.Second)
	defer cancel()
	for i := 0; i < count; i++ {
		order, _, orderErr := client.createOrder(ctx, cfg)
		if orderErr != nil {
			if isRemailInsufficientBalance(orderErr) && len(ids) > 0 {
				reason := fmt.Sprintf("Remail 余额不足：已成功下单 %d/%d 个邮箱；已下单账户将继续处理，未下单账户已停止", len(ids), requestedCount)
				body["provider_stop_reason"] = reason
				body["provider_requested_count"] = requestedCount
				break
			}
			return fmt.Errorf("Remail 第 %d 个邮箱下单失败：%w", i+1, orderErr)
		}
		mailbox := SunnyMailbox{GroupID: gid, Email: order.DeliveryEmail, MailboxType: "remail", MailboxChannel: "remail_api", AccessKey: remailTokenPayload(client.baseURL, client.apiKey, order), Raw: order.DeliveryEmail + "----" + order.ServiceToken, AccountType: "free", Status: "未注册", Enabled: true, LatestMailJSON: "{}"}
		var existing SunnyMailbox
		if err := s.db.Where("lower(email) = ?", sunnyEmailKey(mailbox.Email)).First(&existing).Error; err != nil {
			if err := s.db.Create(&mailbox).Error; err != nil {
				return err
			}
		} else {
			mailbox.ID = existing.ID
			if err := s.db.Model(&existing).Updates(map[string]any{"group_id": gid, "mailbox_type": "remail", "mailbox_channel": "remail_api", "access_key": mailbox.AccessKey, "raw": mailbox.Raw, "status": "未注册", "enabled": true, "last_error": ""}).Error; err != nil {
				return err
			}
		}
		ids = append(ids, mailbox.ID)
	}
	body["mailbox_ids"] = ids
	body["count"] = len(ids)
	return nil
}
