package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const (
	sunnyTrialTaskType   = "sunny_account_trial_check"
	sunnyTrialUnknown    = "unknown"
	sunnyTrialEligible   = "eligible"
	sunnyTrialIneligible = "ineligible"
	sunnyCheckoutUnknown = "unknown"
)

var (
	sunnyTrialCheckEndpoint    = "https://chatgpt.com/backend-api/promo_campaign/check_coupon?coupon=plus-1-month-free&is_coupon_from_query_param=true"
	sunnyCheckoutEndpoint      = "https://chatgpt.com/backend-api/payments/checkout"
	sunnyCheckTrialEligibility = func(ctx context.Context, accessToken string) (bool, string, bool, error) {
		proxyURL, _ := ctx.Value(sunnyTrialProxyContextKey{}).(string)
		return checkSunnyTrialEligibility(ctx, accessToken, proxyURL)
	}
	sunnyCheckCommerce = func(ctx context.Context, accessToken string) sunnyCommerceProbeResult {
		promotionProxyURL, _ := ctx.Value(sunnyTrialProxyContextKey{}).(string)
		checkoutProxyURL, _ := ctx.Value(sunnyCheckoutProxyContextKey{}).(string)
		result := checkSunnyCommerce(ctx, accessToken, promotionProxyURL, checkoutProxyURL)
		if result.Eligibility != sunnyTrialUnknown || result.TrialError != "" {
			return result
		}
		eligible, message, invalid, err := sunnyCheckTrialEligibility(ctx, accessToken)
		if err == nil {
			if eligible {
				result.Eligibility = sunnyTrialEligible
				result.TrialState = sunnyTrialEligible
			} else {
				result.Eligibility = sunnyTrialIneligible
				result.TrialState = sunnyTrialIneligible
			}
			result.TrialMessage = message
		} else {
			result.Eligibility = sunnyTrialUnknown
			result.TrialState = ""
			result.TrialError = err.Error()
		}
		result.InvalidToken = result.InvalidToken || invalid
		return result
	}
)

type sunnyTrialProxyContextKey struct{}
type sunnyCheckoutProxyContextKey struct{}

type sunnyTrialCandidate struct {
	SessionID   uint
	AccountID   uint
	Email       string
	AccessToken string
	SkipReason  string
	Error       string
}

type sunnyTrialResult struct {
	SessionID      uint
	AccountID      uint
	Email          string
	Eligibility    string
	TrialState     string
	Message        string
	TrialError     string
	CheckoutKind   string
	PaymentMethods []string
	CheckoutError  string
	SkipReason     string
	InvalidToken   bool
	Retried        bool
	Error          string
}

type sunnyCommerceProbeResult struct {
	Eligibility    string
	TrialState     string
	TrialMessage   string
	CheckoutKind   string
	PaymentMethods []string
	TrialError     string
	CheckoutError  string
	InvalidToken   bool
}

func normalizeSunnyTrialEligibility(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case sunnyTrialEligible, "true", "yes", "有0元试用", "有试用资格":
		return sunnyTrialEligible
	case sunnyTrialIneligible, "false", "no", "无0元试用", "无试用资格":
		return sunnyTrialIneligible
	default:
		return sunnyTrialUnknown
	}
}

func normalizeSunnyTrialFilter(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "" {
		return ""
	}
	if value == sunnyTrialUnknown {
		return sunnyTrialUnknown
	}
	if value = normalizeSunnyTrialEligibility(value); value == sunnyTrialEligible || value == sunnyTrialIneligible {
		return value
	}
	return ""
}

func sunnyTrialEligibilityFor(accountValue, mailboxValue string) string {
	if value := normalizeSunnyTrialEligibility(accountValue); value != sunnyTrialUnknown {
		return value
	}
	return normalizeSunnyTrialEligibility(mailboxValue)
}

func sunnyManualTrialCheckedAt(eligibility string) *time.Time {
	if normalizeSunnyTrialEligibility(eligibility) == sunnyTrialUnknown {
		return nil
	}
	now := time.Now()
	return &now
}

func sunnyTrialApplies(status, plan string) bool {
	return normalizeSunnyDisplayStatus(status) == "已注册" && normalizeSunnyPlanType(plan) == "free"
}

func sunnyCommerceHTTPClient(proxyURLs ...string) *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if len(proxyURLs) > 0 {
		proxyText := strings.TrimSpace(proxyURLs[0])
		if proxyText != "" {
			if proxy, parseErr := url.Parse(proxyText); parseErr == nil && proxy.Scheme != "" && proxy.Host != "" {
				transport.Proxy = http.ProxyURL(proxy)
			}
		}
	}
	return &http.Client{Timeout: 45 * time.Second, Transport: transport}
}

func sunnyCommerceHeaders(req *http.Request, accessToken string) {
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(accessToken))
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Accept-Language", "en-US,en;q=0.9")
	req.Header.Set("OAI-Language", "en-US")
	req.Header.Set("Referer", "https://chatgpt.com/")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0.0.0 Safari/537.36")
}

func readSunnyCommerceResponse(resp *http.Response) (map[string]any, []byte, error) {
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 256<<10))
	if err != nil {
		return nil, nil, err
	}
	payload := map[string]any{}
	if len(bytes.TrimSpace(raw)) > 0 {
		if err := json.Unmarshal(raw, &payload); err != nil {
			return nil, raw, fmt.Errorf("响应不是有效 JSON")
		}
	}
	return payload, raw, nil
}

func sunnyCommerceErrorMessage(payload map[string]any, raw []byte) string {
	if message := firstText(text(payload["message"]), text(payload["detail"])); message != "" {
		return message
	}
	if value, ok := payload["error"].(map[string]any); ok {
		if message := firstText(text(value["message"]), text(value["detail"])); message != "" {
			return message
		}
	}
	message := strings.TrimSpace(string(raw))
	if len(message) > 240 {
		message = message[:240]
	}
	return message
}

func probeSunnyTrial(ctx context.Context, client *http.Client, accessToken string) (string, string, string, bool, error) {
	method := http.MethodGet
	var body io.Reader
	if strings.HasPrefix(strings.TrimSpace(sunnyTrialCheckEndpoint), "http://127.0.0.1:") || strings.HasPrefix(strings.TrimSpace(sunnyTrialCheckEndpoint), "http://localhost:") {
		method = http.MethodPost
		payload, _ := json.Marshal(map[string]string{"access_token": strings.TrimSpace(accessToken)})
		body = bytes.NewReader(payload)
	}
	req, err := http.NewRequestWithContext(ctx, method, sunnyTrialCheckEndpoint, body)
	if err != nil {
		return sunnyTrialUnknown, "", "", false, err
	}
	sunnyCommerceHeaders(req, accessToken)
	if method == http.MethodPost {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := client.Do(req)
	if err != nil {
		return sunnyTrialUnknown, "", "", false, fmt.Errorf("连接 ChatGPT 试用接口失败: %w", err)
	}
	defer resp.Body.Close()
	payload, raw, err := readSunnyCommerceResponse(resp)
	if err != nil {
		return sunnyTrialUnknown, "", "", false, fmt.Errorf("读取 ChatGPT 试用接口失败: %w", err)
	}
	message := sunnyCommerceErrorMessage(payload, raw)
	if resp.StatusCode == http.StatusUnauthorized {
		return sunnyTrialUnknown, "", message, true, fmt.Errorf("%s", fallback(message, "Access Token 无效或已过期"))
	}
	if resp.StatusCode != http.StatusOK {
		return sunnyTrialUnknown, "", message, false, fmt.Errorf("ChatGPT 试用接口返回 HTTP %d: %s", resp.StatusCode, fallback(message, "无法确认试用资格"))
	}
	state := strings.ToLower(strings.TrimSpace(text(payload["state"])))
	if eligible, ok := payload["eligible"].(bool); ok {
		if eligible {
			state = "eligible"
		} else {
			state = "ineligible"
		}
	}
	switch state {
	case "eligible":
		return sunnyTrialEligible, state, fallback(message, "该账户有 ChatGPT Plus 0 元试用资格"), false, nil
	case "not_eligible", "ineligible":
		return sunnyTrialIneligible, state, fallback(message, "该账户没有 ChatGPT Plus 0 元试用资格"), false, nil
	default:
		return sunnyTrialUnknown, state, message, false, fmt.Errorf("ChatGPT 试用接口返回未确认状态 %q", fallback(state, "empty"))
	}
}

func sunnyCheckoutBilling() (string, string) {
	country := strings.ToUpper(strings.TrimSpace(os.Getenv("SUNNY_CHECKOUT_COUNTRY")))
	if country == "" {
		country = "US"
	}
	currency := strings.ToUpper(strings.TrimSpace(os.Getenv("SUNNY_CHECKOUT_CURRENCY")))
	if currency == "" {
		currency = map[string]string{"DE": "EUR", "JP": "JPY", "GB": "GBP"}[country]
		if currency == "" {
			currency = "USD"
		}
	}
	return country, currency
}

func sunnyFindStringByKeys(value any, keys map[string]bool) string {
	switch node := value.(type) {
	case map[string]any:
		for key, child := range node {
			if keys[strings.ToLower(strings.TrimSpace(key))] {
				if result := strings.TrimSpace(text(child)); result != "" {
					return result
				}
			}
		}
		for _, child := range node {
			if result := sunnyFindStringByKeys(child, keys); result != "" {
				return result
			}
		}
	case []any:
		for _, child := range node {
			if result := sunnyFindStringByKeys(child, keys); result != "" {
				return result
			}
		}
	}
	return ""
}

func sunnyCheckoutSessionID(payload map[string]any) string {
	for _, key := range []string{"checkout_session_id", "session_id", "id"} {
		if value := strings.TrimSpace(text(payload[key])); value != "" {
			return value
		}
	}
	return sunnyFindStringByKeys(payload, map[string]bool{"checkout_session_id": true, "session_id": true})
}

func sunnyAppendPaymentMethods(value any, methods *[]string, seen map[string]bool) {
	appendMethod := func(raw string) {
		method := strings.ToLower(strings.TrimSpace(raw))
		if method == "" || seen[method] || len(method) > 64 {
			return
		}
		seen[method] = true
		*methods = append(*methods, method)
	}
	switch node := value.(type) {
	case string:
		appendMethod(node)
	case []any:
		for _, child := range node {
			sunnyAppendPaymentMethods(child, methods, seen)
		}
	case map[string]any:
		appendMethod(firstText(text(node["type"]), text(node["id"]), text(node["name"])))
	}
}

func sunnyPaymentMethods(value any) []string {
	methods := []string{}
	seen := map[string]bool{}
	var walk func(any)
	walk = func(node any) {
		switch current := node.(type) {
		case map[string]any:
			for key, child := range current {
				normalized := strings.ToLower(strings.TrimSpace(key))
				if normalized == "payment_method_types" || normalized == "custom_payment_methods" || normalized == "payment_methods" || normalized == "available_payment_methods" {
					sunnyAppendPaymentMethods(child, &methods, seen)
				}
				walk(child)
			}
		case []any:
			for _, child := range current {
				walk(child)
			}
		}
	}
	walk(value)
	return methods
}

func probeSunnyCheckout(ctx context.Context, client *http.Client, accessToken string) (string, []string, bool, error) {
	country, currency := sunnyCheckoutBilling()
	body, err := json.Marshal(map[string]any{
		"entry_point":      "all_plans_pricing_modal",
		"plan_name":        "chatgptplusplan",
		"billing_details":  map[string]string{"country": country, "currency": currency},
		"cancel_url":       "https://chatgpt.com/",
		"checkout_ui_mode": "custom",
		"check_card_proxy": true,
	})
	if err != nil {
		return sunnyCheckoutUnknown, nil, false, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, sunnyCheckoutEndpoint, bytes.NewReader(body))
	if err != nil {
		return sunnyCheckoutUnknown, nil, false, err
	}
	sunnyCommerceHeaders(req, accessToken)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-OpenAI-Target-Path", "/backend-api/payments/checkout")
	req.Header.Set("X-OpenAI-Target-Route", "/backend-api/payments/checkout")
	resp, err := client.Do(req)
	if err != nil {
		return sunnyCheckoutUnknown, nil, false, fmt.Errorf("连接 ChatGPT Checkout 接口失败: %w", err)
	}
	defer resp.Body.Close()
	payload, raw, err := readSunnyCommerceResponse(resp)
	if err != nil {
		return sunnyCheckoutUnknown, nil, false, fmt.Errorf("读取 ChatGPT Checkout 接口失败: %w", err)
	}
	message := sunnyCommerceErrorMessage(payload, raw)
	if resp.StatusCode == http.StatusUnauthorized {
		return sunnyCheckoutUnknown, nil, true, fmt.Errorf("%s", fallback(message, "Access Token 无效或已过期"))
	}
	if resp.StatusCode != http.StatusOK {
		return sunnyCheckoutUnknown, nil, false, fmt.Errorf("ChatGPT Checkout 接口返回 HTTP %d: %s", resp.StatusCode, fallback(message, "无法创建 Checkout"))
	}
	sessionID := sunnyCheckoutSessionID(payload)
	kind := sunnyCheckoutUnknown
	switch {
	case strings.HasPrefix(sessionID, "oaics_"):
		kind = "oaics"
	case strings.HasPrefix(sessionID, "cs_live_"):
		kind = "cs_live"
	case strings.HasPrefix(sessionID, "cs_test_"):
		kind = "cs_test"
	}
	if kind == sunnyCheckoutUnknown {
		return kind, sunnyPaymentMethods(payload), false, fmt.Errorf("Checkout 响应未包含可识别的会话类型")
	}
	return kind, sunnyPaymentMethods(payload), false, nil
}

func checkSunnyCommerce(ctx context.Context, accessToken string, proxyURLs ...string) sunnyCommerceProbeResult {
	result := sunnyCommerceProbeResult{Eligibility: sunnyTrialUnknown, CheckoutKind: sunnyCheckoutUnknown, PaymentMethods: []string{}}
	token := strings.TrimSpace(accessToken)
	if token == "" {
		result.TrialError = "账户缺少 Access Token"
		result.CheckoutError = result.TrialError
		return result
	}
	promotionProxyURL := ""
	checkoutProxyURL := ""
	if len(proxyURLs) > 0 {
		promotionProxyURL = strings.TrimSpace(proxyURLs[0])
	}
	if len(proxyURLs) > 1 {
		checkoutProxyURL = strings.TrimSpace(proxyURLs[1])
	}
	if checkoutProxyURL == "" {
		checkoutProxyURL = promotionProxyURL
	}
	if workerResult, ok := probeSunnyCommerceViaWorker(ctx, token, promotionProxyURL, checkoutProxyURL); ok {
		return workerResult
	}
	client := sunnyCommerceHTTPClient(checkoutProxyURL)
	checkoutKind, methods, checkoutInvalid, checkoutErr := probeSunnyCheckout(ctx, client, token)
	result.CheckoutKind, result.PaymentMethods = checkoutKind, methods
	result.InvalidToken = result.InvalidToken || checkoutInvalid
	if checkoutErr != nil {
		result.CheckoutError = checkoutErr.Error()
	}
	return result
}

func probeSunnyCommerceViaWorker(ctx context.Context, accessToken, promotionProxyURL string, checkoutProxyURLs ...string) (sunnyCommerceProbeResult, bool) {
	result := sunnyCommerceProbeResult{Eligibility: sunnyTrialUnknown, CheckoutKind: sunnyCheckoutUnknown, PaymentMethods: []string{}}
	workerURL := strings.TrimRight(strings.TrimSpace(os.Getenv("PYTHON_WORKER_URL")), "/")
	if workerURL == "" {
		workerURL = "http://127.0.0.1:8765"
	}
	country, currency := sunnyCheckoutBilling()
	checkoutProxyURL := promotionProxyURL
	if len(checkoutProxyURLs) > 0 && strings.TrimSpace(checkoutProxyURLs[0]) != "" {
		checkoutProxyURL = strings.TrimSpace(checkoutProxyURLs[0])
	}
	body, _ := json.Marshal(map[string]string{
		"access_token":        accessToken,
		"proxy_url":           promotionProxyURL,
		"promotion_proxy_url": promotionProxyURL,
		"checkout_proxy_url":  checkoutProxyURL,
		"country":             country,
		"currency":            currency,
	})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, workerURL+"/probe-commerce", bytes.NewReader(body))
	if err != nil {
		return result, false
	}
	req.Header.Set("Content-Type", "application/json")
	if token := secretValue("PYTHON_WORKER_TOKEN", "PYTHON_WORKER_TOKEN_FILE"); token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := (&http.Client{Timeout: 90 * time.Second}).Do(req)
	if err != nil {
		return result, false
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 256<<10))
	if err != nil || resp.StatusCode == http.StatusNotFound || resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return result, false
	}
	var payload struct {
		Trial struct {
			State string `json:"state"`
			HTTP  int    `json:"http"`
			Error string `json:"error"`
		} `json:"trial"`
		Checkout struct {
			Kind           string   `json:"kind"`
			PaymentMethods []string `json:"payment_methods"`
			HTTP           int      `json:"http"`
			Error          string   `json:"error"`
		} `json:"checkout"`
	}
	if json.Unmarshal(raw, &payload) != nil {
		return result, false
	}
	result.TrialState = strings.ToLower(strings.TrimSpace(payload.Trial.State))
	switch result.TrialState {
	case "eligible":
		result.Eligibility = sunnyTrialEligible
	case "not_eligible", "ineligible":
		result.Eligibility = sunnyTrialIneligible
	default:
		result.TrialError = fallback(strings.TrimSpace(payload.Trial.Error), fmt.Sprintf("ChatGPT 试用接口返回 HTTP %d，未提供有效状态", payload.Trial.HTTP))
	}
	result.CheckoutKind = normalizeSunnyCheckoutKind(payload.Checkout.Kind)
	result.PaymentMethods = payload.Checkout.PaymentMethods
	if result.CheckoutKind == sunnyCheckoutUnknown {
		result.CheckoutError = fallback(strings.TrimSpace(payload.Checkout.Error), fmt.Sprintf("ChatGPT Checkout 接口返回 HTTP %d，未提供可识别类型", payload.Checkout.HTTP))
	}
	result.InvalidToken = payload.Trial.HTTP == http.StatusUnauthorized || payload.Checkout.HTTP == http.StatusUnauthorized
	return result, true
}

func checkSunnyTrialEligibility(ctx context.Context, accessToken string, proxyURLs ...string) (bool, string, bool, error) {
	client := sunnyCommerceHTTPClient(proxyURLs...)
	eligibility, _, message, invalid, err := probeSunnyTrial(ctx, client, accessToken)
	return eligibility == sunnyTrialEligible, message, invalid, err
}

func (s *Server) sunnyTrialConcurrency() int {
	value := intValue(strings.TrimSpace(os.Getenv("SUNNY_TRIAL_CONCURRENCY")), 8)
	if value < 1 {
		return 1
	}
	if value > 16 {
		return 16
	}
	return value
}

func sunnyCommerceProbeNeedsRetry(result sunnyCommerceProbeResult) bool {
	if result.InvalidToken {
		return false
	}
	return normalizeSunnyTrialEligibility(result.Eligibility) == sunnyTrialUnknown || normalizeSunnyCheckoutKind(result.CheckoutKind) == sunnyCheckoutUnknown
}

func mergeSunnyCommerceProbeResults(initial, retried sunnyCommerceProbeResult) sunnyCommerceProbeResult {
	merged := retried
	if normalizeSunnyTrialEligibility(retried.Eligibility) == sunnyTrialUnknown {
		if normalizeSunnyTrialEligibility(initial.Eligibility) != sunnyTrialUnknown {
			merged.Eligibility = initial.Eligibility
			merged.TrialState = initial.TrialState
			merged.TrialMessage = initial.TrialMessage
			merged.TrialError = initial.TrialError
		} else if strings.TrimSpace(merged.TrialError) == "" {
			merged.TrialError = initial.TrialError
		}
	}
	if normalizeSunnyCheckoutKind(retried.CheckoutKind) == sunnyCheckoutUnknown {
		if normalizeSunnyCheckoutKind(initial.CheckoutKind) != sunnyCheckoutUnknown {
			merged.CheckoutKind = initial.CheckoutKind
			merged.PaymentMethods = initial.PaymentMethods
			merged.CheckoutError = initial.CheckoutError
		} else if strings.TrimSpace(merged.CheckoutError) == "" {
			merged.CheckoutError = initial.CheckoutError
		}
	}
	merged.InvalidToken = initial.InvalidToken || retried.InvalidToken
	return merged
}

func checkSunnyCommerceWithRetry(ctx context.Context, accessToken string) (sunnyCommerceProbeResult, bool) {
	initial := sunnyCheckCommerce(ctx, accessToken)
	if !sunnyCommerceProbeNeedsRetry(initial) {
		return initial, false
	}
	retried := sunnyCheckCommerce(ctx, accessToken)
	return mergeSunnyCommerceProbeResults(initial, retried), true
}

func (s *Server) sunnyTrialBatchSize() int {
	return sunnyDetectionBatchSize("SUNNY_TRIAL_BATCH_SIZE", 12, 100)
}

func (s *Server) sunnyTrialCandidates(ids []uint) ([]sunnyTrialCandidate, error) {
	if len(ids) == 0 {
		return nil, fmt.Errorf("请选择需要检测试用资格的账户")
	}
	var sessions []SunnySession
	if err := s.db.Where("id IN ?", ids).Order("id asc").Find(&sessions).Error; err != nil {
		return nil, err
	}
	accounts, mailboxes := s.sunnySessionSidecars(sessions)
	candidates := make([]sunnyTrialCandidate, 0, len(sessions))
	for _, session := range sessions {
		account := accounts[sunnyEmailKey(session.Email)]
		item := s.serializeSunnySession(session, accounts, mailboxes)
		candidate := sunnyTrialCandidate{
			SessionID:   session.ID,
			AccountID:   firstUint(session.AccountID, account.ID),
			Email:       session.Email,
			AccessToken: firstText(session.AccessToken, sunnyAccessTokenFromSessionJSON(session.SessionJSON), account.AccessToken),
		}
		if !sunnyTrialApplies(text(item["status"]), text(item["plan_type"])) {
			candidate.SkipReason = "仅已注册且套餐为 free 的账户支持试用资格检测"
		} else if strings.TrimSpace(candidate.AccessToken) == "" {
			candidate.Error = "账户缺少 Access Token"
		}
		candidates = append(candidates, candidate)
	}
	return candidates, nil
}

func firstUint(values ...uint) uint {
	for _, value := range values {
		if value != 0 {
			return value
		}
	}
	return 0
}

func (s *Server) createSunnyTrialTask(body map[string]any) (Task, error) {
	ids := uintSlice(body["session_ids"])
	if len(ids) == 0 {
		return Task{}, fmt.Errorf("请选择需要检测试用资格的账户")
	}
	var active int64
	s.db.Model(&Task{}).Where("type = ? AND status NOT IN ?", sunnyTrialTaskType, []string{TaskSucceeded, TaskFailed, TaskInterrupted, TaskCancelled}).Count(&active)
	if active > 0 {
		return Task{}, fmt.Errorf("已有试用资格检测任务正在执行，请稍候")
	}
	candidates, err := s.sunnyTrialCandidates(ids)
	if err != nil {
		return Task{}, err
	}
	if len(candidates) == 0 {
		return Task{}, fmt.Errorf("未找到需要检测试用资格的账户")
	}
	return s.createTask(sunnyTrialTaskType, "sunny", map[string]any{"session_ids": ids}, len(candidates)), nil
}

func (s *Server) executeSunnyTrialTask(task *Task, payload map[string]any) {
	task.Status = TaskRunning
	task.StartedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	candidates, err := s.sunnyTrialCandidates(uintSlice(payload["session_ids"]))
	if err != nil {
		s.failSunnyTrialTask(task, err.Error())
		return
	}
	result := map[string]any{"requested": len(candidates), "eligible": 0, "ineligible": 0, "checkout_detected": 0, "payment_detected": 0, "retried": 0, "partial": 0, "skipped": 0, "failed": 0, "items": []any{}}
	invalidAccounts := []uint{}
	invalidSessions := []uint{}
	seenAccounts := map[uint]bool{}
	items := make([]any, 0, len(candidates))
	batchSize := s.sunnyTrialBatchSize()
	concurrency := s.sunnyTrialConcurrency()
	for start := 0; start < len(candidates); start += batchSize {
		end := start + batchSize
		if end > len(candidates) {
			end = len(candidates)
		}
		results := streamSunnyDetectionBatch(candidates[start:end], concurrency, func(candidate sunnyTrialCandidate) sunnyTrialResult {
			outcome := sunnyTrialResult{SessionID: candidate.SessionID, AccountID: candidate.AccountID, Email: candidate.Email, SkipReason: candidate.SkipReason, Error: candidate.Error}
			if outcome.SkipReason == "" && outcome.Error == "" {
				trialCtx := context.WithValue(context.Background(), sunnyTrialProxyContextKey{}, s.sunnyCommerceProxyURL(candidate.Email))
				commerce, retried := checkSunnyCommerceWithRetry(trialCtx, candidate.AccessToken)
				outcome.Eligibility = commerce.Eligibility
				outcome.TrialState = commerce.TrialState
				outcome.Message = commerce.TrialMessage
				outcome.TrialError = commerce.TrialError
				outcome.CheckoutKind = commerce.CheckoutKind
				outcome.PaymentMethods = commerce.PaymentMethods
				outcome.CheckoutError = commerce.CheckoutError
				outcome.InvalidToken = commerce.InvalidToken
				outcome.Retried = retried
			}
			return outcome
		})
		for outcome := range results {
			item := map[string]any{"session_id": outcome.SessionID, "email": outcome.Email}
			if outcome.Retried {
				result["retried"] = result["retried"].(int) + 1
				item["retried"] = true
			}
			now := time.Now()
			switch {
			case outcome.SkipReason != "":
				result["skipped"] = result["skipped"].(int) + 1
				item["status"], item["message"] = "skipped", outcome.SkipReason
			case outcome.Error != "":
				result["failed"] = result["failed"].(int) + 1
				item["status"], item["error"] = "failed", outcome.Error
				updates := map[string]any{"trial_eligibility": sunnyTrialUnknown, "trial_check_error": outcome.Error, "trial_checked_at": now,
					"checkout_kind": sunnyCheckoutUnknown, "payment_methods_json": "[]", "commerce_check_error": outcome.Error, "commerce_checked_at": now}
				s.db.Model(&SunnyAccount{}).Where("email = ?", outcome.Email).Updates(updates)
				s.db.Model(&SunnyMailbox{}).Where("email = ?", outcome.Email).Updates(map[string]any{"trial_eligibility": sunnyTrialUnknown, "trial_check_error": outcome.Error, "trial_checked_at": now})
			default:
				eligibility := normalizeSunnyTrialEligibility(outcome.Eligibility)
				checkoutKind := normalizeSunnyCheckoutKind(outcome.CheckoutKind)
				paymentJSON := dumpJSON(outcome.PaymentMethods)
				commerceError := strings.Join(compactStrings(outcome.TrialError, outcome.CheckoutError), "; ")
				item["trial_eligibility"] = eligibility
				item["trial_state"] = outcome.TrialState
				item["checkout_kind"] = checkoutKind
				item["payment_methods"] = outcome.PaymentMethods
				if outcome.TrialError != "" {
					item["trial_error"] = outcome.TrialError
				}
				if outcome.CheckoutError != "" {
					item["checkout_error"] = outcome.CheckoutError
				}
				if eligibility == sunnyTrialEligible || eligibility == sunnyTrialIneligible {
					result[eligibility] = result[eligibility].(int) + 1
				}
				if checkoutKind != sunnyCheckoutUnknown {
					result["checkout_detected"] = result["checkout_detected"].(int) + 1
				}
				if len(outcome.PaymentMethods) > 0 {
					result["payment_detected"] = result["payment_detected"].(int) + 1
				}
				if commerceError != "" {
					result["partial"] = result["partial"].(int) + 1
					item["status"], item["error"] = "partial", commerceError
				} else {
					item["status"], item["message"] = eligibility, outcome.Message
				}
				accountUpdates := map[string]any{
					"trial_eligibility": eligibility, "trial_check_error": outcome.TrialError, "trial_checked_at": now,
					"checkout_kind": checkoutKind, "payment_methods_json": paymentJSON, "commerce_check_error": commerceError, "commerce_checked_at": now,
				}
				mailboxUpdates := map[string]any{"trial_eligibility": eligibility, "trial_check_error": outcome.TrialError, "trial_checked_at": now}
				tx := s.db.Begin()
				updateErr := tx.Model(&SunnyAccount{}).Where("email = ?", outcome.Email).Updates(accountUpdates).Error
				if updateErr == nil {
					updateErr = tx.Model(&SunnyMailbox{}).Where("email = ?", outcome.Email).Updates(mailboxUpdates).Error
				}
				if updateErr == nil {
					updateErr = tx.Commit().Error
				} else {
					tx.Rollback()
				}
				if updateErr != nil {
					result["failed"] = result["failed"].(int) + 1
					item["status"], item["error"] = "failed", updateErr.Error()
				} else if commerceError != "" {
					s.appendAccountTaskEvent(task.ID, outcome.Email, "trial", "commerce.check_partial", fmt.Sprintf("账户 %s 商业状态检测部分完成：%s", outcome.Email, commerceError), "warning", map[string]any{"error": commerceError})
				} else {
					s.appendAccountTaskEvent(task.ID, outcome.Email, "trial", "commerce.checked", fmt.Sprintf("账户 %s 商业状态检测完成：试用=%s，Checkout=%s，支付方式=%s", outcome.Email, eligibility, checkoutKind, strings.Join(outcome.PaymentMethods, ",")), "info", map[string]any{"trial_eligibility": eligibility, "checkout_kind": checkoutKind, "payment_methods": outcome.PaymentMethods})
				}
				if outcome.InvalidToken {
					errorMessage := fallback(strings.Join(compactStrings(outcome.TrialError, outcome.CheckoutError), "; "), "Access Token 无效或已过期")
					s.db.Model(&SunnySession{}).Where("id = ?", outcome.SessionID).Updates(map[string]any{"access_token_status": "invalid", "access_token_error": errorMessage, "access_token_checked_at": now})
					invalidSessions = append(invalidSessions, outcome.SessionID)
					if outcome.AccountID != 0 && !seenAccounts[outcome.AccountID] {
						seenAccounts[outcome.AccountID] = true
						invalidAccounts = append(invalidAccounts, outcome.AccountID)
					}
				}
			}
			items = append(items, item)
			task.ProgressCurrent++
			s.db.Model(&Task{}).Where("id = ?", task.ID).Updates(map[string]any{"progress_current": task.ProgressCurrent, "updated_at": now})
		}
	}
	if len(invalidAccounts) > 0 {
		renewalTask := s.createSunnyAccessTokenRenewalTask(task, "trial_check", invalidAccounts)
		result["renewal_task_id"] = renewalTask.ID
		result["renewal_queued"] = len(invalidAccounts)
		result["invalid_session_ids"] = invalidSessions
	}
	result["items"] = items
	s.completeSunnyTrialTask(task, result)
}

// sunnyCommerceProxyURL keeps trial, Checkout and payment-method checks on a
// dedicated healthy proxy when one is explicitly tagged for account checks.
// An empty result intentionally preserves the direct-egress fallback.
func (s *Server) sunnyCommerceProxyURL(accountKey string) string {
	var proxies []SunnyProxy
	query := "(',' || replace(lower(coalesce(purpose_tags, 'register')), ' ', '') || ',') LIKE ?"
	if err := s.db.Where("status = ? AND enabled = ? AND last_check_ok = ?", "enabled", true, true).
		Where(query, "%,"+sunnyProxyPurposeCommerce+",%").
		Order("updated_at desc, id asc").Find(&proxies).Error; err != nil || len(proxies) == 0 {
		return ""
	}
	country, _ := sunnyCheckoutBilling()
	matched := make([]SunnyProxy, 0, len(proxies))
	for _, proxy := range proxies {
		if strings.EqualFold(strings.TrimSpace(proxy.Country), country) {
			matched = append(matched, proxy)
		}
	}
	if len(matched) > 0 {
		proxies = matched
	}
	hash := fnv.New32a()
	_, _ = hash.Write([]byte(strings.ToLower(strings.TrimSpace(accountKey))))
	return normalizeSunnyProxyAddress(proxies[int(hash.Sum32())%len(proxies)].Address)
}

func (s *Server) failSunnyTrialTask(task *Task, message string) {
	task.Status = TaskFailed
	task.Error = message
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	task.ResultJSON = dumpJSON(map[string]any{"requested": task.ProgressTotal, "eligible": 0, "ineligible": 0, "skipped": 0, "failed": task.ProgressTotal})
	s.db.Save(task)
	s.appendTaskEvent(task.ID, message, "log", "error", nil)
}

func (s *Server) completeSunnyTrialTask(task *Task, result map[string]any) {
	task.Status = TaskSucceeded
	task.SuccessCount = intValue(result["eligible"], 0) + intValue(result["ineligible"], 0)
	task.ErrorCount = intValue(result["failed"], 0) + intValue(result["partial"], 0)
	task.ResultJSON = dumpJSON(result)
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	s.appendTaskEvent(task.ID, "账户试用资格、Checkout 与支付方式检测任务完成", "log", "info", result)
}

func compactStrings(values ...string) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		if value = strings.TrimSpace(value); value != "" {
			result = append(result, value)
		}
	}
	return result
}

func normalizeSunnyCheckoutKind(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "oaics":
		return "oaics"
	case "cs_live":
		return "cs_live"
	case "cs_test":
		return "cs_test"
	default:
		return sunnyCheckoutUnknown
	}
}

func normalizeSunnyCheckoutFilter(value string) string {
	raw := strings.ToLower(strings.TrimSpace(value))
	if raw == "" {
		return ""
	}
	normalized := normalizeSunnyCheckoutKind(raw)
	if normalized == sunnyCheckoutUnknown && raw != sunnyCheckoutUnknown {
		return ""
	}
	return normalized
}
