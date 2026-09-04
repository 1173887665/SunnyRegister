package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

func TestSunnyCheckoutTimeoutConfigIsBounded(t *testing.T) {
	original, hadOriginal := os.LookupEnv("SUNNY_CHECKOUT_TIMEOUT_SECONDS")
	t.Cleanup(func() {
		if hadOriginal {
			_ = os.Setenv("SUNNY_CHECKOUT_TIMEOUT_SECONDS", original)
		} else {
			_ = os.Unsetenv("SUNNY_CHECKOUT_TIMEOUT_SECONDS")
		}
	})

	_ = os.Setenv("SUNNY_CHECKOUT_TIMEOUT_SECONDS", "10")
	if got := sunnyCheckoutTimeout(); got != 5*time.Minute {
		t.Fatalf("minimum timeout = %s, want 5m", got)
	}
	_ = os.Setenv("SUNNY_CHECKOUT_TIMEOUT_SECONDS", "999999")
	if got := sunnyCheckoutTimeout(); got != 2*time.Hour {
		t.Fatalf("maximum timeout = %s, want 2h", got)
	}
	_ = os.Setenv("SUNNY_CHECKOUT_TIMEOUT_SECONDS", "invalid")
	if got := sunnyCheckoutTimeout(); got != 30*time.Minute {
		t.Fatalf("invalid timeout = %s, want 30m default", got)
	}
}

func TestCheckoutWorkerURLSelectsIndependentRuntimes(t *testing.T) {
	t.Setenv("PYTHON_WORKER_URL", "http://manager.example:8765/")
	t.Setenv("PYTHON_WORKBENCH_WORKER_URL", "http://workbench.example:8766/")
	if got := checkoutWorkerURL(checkoutRuntimeManager); got != "http://manager.example:8765" {
		t.Fatalf("manager worker URL = %q", got)
	}
	if got := checkoutWorkerURL(checkoutRuntimeWorkbench); got != "http://workbench.example:8766" {
		t.Fatalf("workbench worker URL = %q", got)
	}
	// Unknown/legacy runtime names must continue using the manager worker.
	if got := checkoutWorkerURL("legacy"); got != "http://manager.example:8765" {
		t.Fatalf("legacy worker URL = %q", got)
	}
}

func TestCheckoutWorkerURLDefaultsRemainSeparate(t *testing.T) {
	t.Setenv("PYTHON_WORKER_URL", "")
	t.Setenv("PYTHON_WORKBENCH_WORKER_URL", "")
	if got := checkoutWorkerURL(checkoutRuntimeManager); got != "http://127.0.0.1:8765" {
		t.Fatalf("manager default URL = %q", got)
	}
	if got := checkoutWorkerURL(checkoutRuntimeWorkbench); got != "http://127.0.0.1:8766" {
		t.Fatalf("workbench default URL = %q", got)
	}
}

func TestCheckoutCredentialsAreIsolatedByRuntime(t *testing.T) {
	s := &Server{
		checkoutCreds:          map[string]checkoutSecret{},
		workbenchCheckoutCreds: map[string]checkoutSecret{},
	}
	managerSecret := checkoutSecret{Tokens: map[string]string{"0": "manager-token"}}
	workbenchSecret := checkoutSecret{Tokens: map[string]string{"0": "workbench-token"}}
	s.checkoutCreds["manager-id"] = managerSecret
	s.workbenchCheckoutCreds["workbench-id"] = workbenchSecret
	if got := s.checkoutCredentialForRuntime(checkoutRuntimeManager, "manager-id", 0); got != "manager-token" {
		t.Fatalf("manager credential = %q", got)
	}
	if got := s.checkoutCredentialForRuntime(checkoutRuntimeWorkbench, "workbench-id", 0); got != "workbench-token" {
		t.Fatalf("workbench credential = %q", got)
	}
	if got := s.checkoutCredentialForRuntime(checkoutRuntimeWorkbench, "manager-id", 0); got != "" {
		t.Fatalf("manager credential leaked into workbench runtime: %q", got)
	}
	if got := s.checkoutCredentialForRuntime(checkoutRuntimeManager, "workbench-id", 0); got != "" {
		t.Fatalf("workbench credential leaked into manager runtime: %q", got)
	}
}

func TestSunnyCheckoutWorkerRequestCarriesAccountProxySlot(t *testing.T) {
	body := sunnyCheckoutWorkerRequestBody("token", "unknown", map[string]any{
		"plan": "plus", "link_type": "hosted", "country": "US", "currency": "USD",
	}, []string{"http://checkout.example:8080"}, []string{"http://promotion.example:8080"}, 7)
	if got := intValue(body["proxy_slot"], -1); got != 7 {
		t.Fatalf("proxy_slot=%d, want 7", got)
	}
	if _, ok := body["chain_config"].(map[string]any); !ok {
		t.Fatalf("chain_config=%#v, want object", body["chain_config"])
	}
}

func TestSunnyCheckoutRuntimeCreatesIndependentMultiAccountTasks(t *testing.T) {
	s := newSunnySessionTestServer(t)
	s.checkoutCreds = map[string]checkoutSecret{}
	s.workbenchCheckoutCreds = map[string]checkoutSecret{}
	now := time.Now()
	var sessionIDs []uint
	var first SunnySession
	if err := s.db.Where("email = ?", "session@example.com").First(&first).Error; err != nil {
		t.Fatal(err)
	}
	sessionIDs = append(sessionIDs, first.ID)
	for i := 2; i <= 3; i++ {
		email := fmt.Sprintf("session-%d@example.com", i)
		account := SunnyAccount{Email: email, Status: "registered", AccountType: "plus", AccessToken: fmt.Sprintf("account-token-%d", i), CreatedAt: now, UpdatedAt: now}
		if err := s.db.Create(&account).Error; err != nil {
			t.Fatal(err)
		}
		session := SunnySession{AccountID: account.ID, Email: email, AccessToken: fmt.Sprintf("session-token-%d", i), CreatedAt: now, UpdatedAt: now}
		if err := s.db.Create(&session).Error; err != nil {
			t.Fatal(err)
		}
		sessionIDs = append(sessionIDs, session.ID)
	}

	requestBody := func() (*httptest.ResponseRecorder, *http.Request) {
		payload := map[string]any{
			"system_at": true, "session_ids": sessionIDs,
			"checkout_proxies":  "http://checkout.example:8080",
			"promotion_proxies": "http://promotion.example:8080",
			"plan":              "plus", "link_type": "hosted", "country": "US", "currency": "USD",
			"retry_count": 1, "concurrency": 2,
		}
		raw, err := json.Marshal(payload)
		if err != nil {
			t.Fatal(err)
		}
		req := httptest.NewRequest(http.MethodPost, "/api/sunny/checkout", bytes.NewReader(raw))
		req.Header.Set("Content-Type", "application/json")
		return httptest.NewRecorder(), req
	}

	managerRec, managerReq := requestBody()
	s.sunnyCheckoutRuntime(managerRec, managerReq, nil, checkoutRuntimeManager)
	if managerRec.Code != http.StatusAccepted {
		t.Fatalf("manager status=%d body=%s", managerRec.Code, managerRec.Body.String())
	}
	var managerTask Task
	if err := s.db.Where("type = ?", sunnyCheckoutTaskType).Order("created_at desc").First(&managerTask).Error; err != nil {
		t.Fatal(err)
	}
	if managerTask.Type != sunnyCheckoutTaskType || !strings.Contains(managerTask.PayloadJSON, `"checkout_runtime":"manager"`) {
		t.Fatalf("manager task type/runtime mismatch: type=%q payload=%s", managerTask.Type, managerTask.PayloadJSON)
	}

	workbenchRec, workbenchReq := requestBody()
	s.sunnyCheckoutRuntime(workbenchRec, workbenchReq, nil, checkoutRuntimeWorkbench)
	if workbenchRec.Code != http.StatusAccepted {
		t.Fatalf("workbench status=%d body=%s", workbenchRec.Code, workbenchRec.Body.String())
	}
	var workbenchTask Task
	if err := s.db.Where("type = ?", sunnyWorkbenchCheckoutTaskType).Order("created_at desc").First(&workbenchTask).Error; err != nil {
		t.Fatal(err)
	}
	if workbenchTask.Type != sunnyWorkbenchCheckoutTaskType || !strings.Contains(workbenchTask.PayloadJSON, `"checkout_runtime":"workbench"`) {
		t.Fatalf("workbench task type/runtime mismatch: type=%q payload=%s", workbenchTask.Type, workbenchTask.PayloadJSON)
	}
	if managerTask.ID == workbenchTask.ID {
		t.Fatal("manager and workbench requests reused the same task")
	}
}

func TestSunnyCheckoutTaskTypesRemainDistinctAndScheduled(t *testing.T) {
	if sunnyCheckoutTaskType == sunnyWorkbenchCheckoutTaskType {
		t.Fatal("manager and workbench checkout task types must be distinct")
	}
	for _, taskType := range []string{sunnyCheckoutTaskType, sunnyWorkbenchCheckoutTaskType} {
		if !sunnyGoTaskType(taskType) {
			t.Fatalf("checkout task type %q is not recognized by Go scheduler", taskType)
		}
	}
}

func TestSplitCheckoutPoolNormalizesAndLimits(t *testing.T) {
	items, err := splitCheckoutPool("http://127.0.0.1:8000\nhttp://127.0.0.1:8000\n")
	if err != nil || len(items) != 2 || items[0] != "http://127.0.0.1:8000" || items[1] != items[0] {
		t.Fatalf("pool=%#v err=%v", items, err)
	}
	if _, err := splitCheckoutPool(""); err == nil {
		t.Fatal("empty pool should fail")
	}
	items, err = splitCheckoutPool("host:8080:user:pass\nuser:pass@host:8080\nsocks5://host:1080")
	if err != nil || len(items) != 3 || !strings.Contains(items[0], "http://user:pass@host:8080") || items[1] != items[0] {
		t.Fatalf("credential proxy normalization=%#v err=%v", items, err)
	}
}

func TestCheckSunnyProxyAddressesPreservesSlotsAndRedactsCredentials(t *testing.T) {
	addresses := []string{
		"http://user:secret@127.0.0.1:1",
		"http://user:secret@127.0.0.1:1",
	}
	results := checkSunnyProxyAddresses(addresses, 2)
	if len(results) != len(addresses) {
		t.Fatalf("results=%d, want %d", len(results), len(addresses))
	}
	for index, result := range results {
		if got := intValue(result["index"], 0); got != index+1 {
			t.Fatalf("result %d index=%d", index, got)
		}
		if got := text(result["address"]); strings.Contains(got, "secret") || !strings.Contains(got, "127.0.0.1:1") {
			t.Fatalf("result %d address was not safely redacted: %q", index, got)
		}
		if _, ok := result["proxy"]; ok {
			t.Fatalf("result %d leaked raw proxy field: %#v", index, result)
		}
	}
}

func TestSunnyCheckoutProxyCheckEndpointDoesNotPersistProjectPool(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/workbench/checkout/proxy-check", strings.NewReader(`{
		"role":"promotion",
		"pool":"http://127.0.0.1:1\nhttp://127.0.0.1:1",
		"limit":20
	}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyCheckoutRuntime(recorder, req, []string{"proxy-check"}, checkoutRuntimeWorkbench)
	if recorder.Code != http.StatusOK {
		t.Fatalf("proxy check status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode proxy check response: %v", err)
	}
	if text(response["role"]) != "promotion" || toInt(response["checked"]) != 2 || toInt(response["available"]) != 0 {
		t.Fatalf("unexpected proxy check response: %#v", response)
	}
	var count int64
	if err := s.db.Model(&SunnyProxy{}).Count(&count).Error; err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("project proxy check persisted %d global proxy rows", count)
	}
}

func TestSunnyCheckoutProxyCheckEndpointRejectsUnknownRole(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/checkout/proxy-check", strings.NewReader(`{"role":"other","pool":"http://127.0.0.1:1"}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyCheckoutRuntime(recorder, req, []string{"proxy-check"}, checkoutRuntimeManager)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("unknown role status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestSunnyCheckoutPrecheckGCashUsesCheckoutPoolWhenPromotionPoolOmitted(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/checkout/precheck", strings.NewReader(`{
		"link_type":"gcash",
		"checkout_proxies":"http://127.0.0.1:1"
	}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyCheckoutRuntime(recorder, req, []string{"precheck"}, checkoutRuntimeManager)
	if recorder.Code != http.StatusOK {
		t.Fatalf("GCash precheck status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestCheckoutProviderDefaultsIncludeAllCurrentPaths(t *testing.T) {
	if len(checkoutProviders) != 12 {
		t.Fatalf("providers=%d", len(checkoutProviders))
	}
	for _, value := range []string{"hosted", "ph_short", "paypal", "ideal", "twint", "upi", "pix", "momo", "gcash", "gopay", "blik", "kakao"} {
		country, currency := checkoutProviderDefaults(value)
		if country == "" || currency == "" {
			t.Fatalf("missing defaults for %s", value)
		}
	}
}

func TestBlikProviderDefaultsToPoland(t *testing.T) {
	country, currency := checkoutProviderDefaults("blik")
	if country != "PL" || currency != "PLN" {
		t.Fatalf("BLIK defaults=%s/%s", country, currency)
	}
}

func TestGoPayProviderDefaultsToIndonesia(t *testing.T) {
	country, currency := checkoutProviderDefaults("gopay")
	if country != "ID" || currency != "IDR" {
		t.Fatalf("GoPay defaults=%s/%s", country, currency)
	}
}

func TestNormalizeGoPayRequestUsesIndonesiaCheckout(t *testing.T) {
	normalized, checkout, promotion, err := normalizeCheckoutRequest(sunnyCheckoutRequest{
		Plan:             "plus",
		LinkType:         "gopay",
		Country:          "US",
		Currency:         "USD",
		CheckoutProxies:  "http://id-proxy.example:8080",
		PromotionProxies: "http://promo-proxy.example:8080",
	})
	if err != nil {
		t.Fatalf("normalize GoPay request: %v", err)
	}
	if normalized.Country != "ID" || normalized.Currency != "IDR" {
		t.Fatalf("unexpected GoPay region: %#v", normalized)
	}
	if len(checkout) != 1 || len(promotion) != 1 || checkout[0] == promotion[0] {
		t.Fatalf("checkout=%#v promotion=%#v", checkout, promotion)
	}
}

func TestNormalizeGCashRequestUsesSinglePHCheckoutPool(t *testing.T) {
	in := sunnyCheckoutRequest{
		Plan:             "plus",
		LinkType:         "gcash",
		Country:          "US",
		Currency:         "USD",
		CheckoutProxies:  "http://ph-proxy.example:8080",
		PromotionProxies: "http://vn-proxy.example:8080",
	}

	normalized, checkout, promotion, err := normalizeCheckoutRequest(in)
	if err != nil {
		t.Fatalf("normalize GCash request: %v", err)
	}
	if normalized.Country != "PH" || normalized.Currency != "PHP" || normalized.PromoCountry != "PH" {
		t.Fatalf("unexpected GCash region: %#v", normalized)
	}
	if len(checkout) != 1 || len(promotion) != 1 || checkout[0] != promotion[0] {
		t.Fatalf("checkout=%#v promotion=%#v", checkout, promotion)
	}
}

func TestNormalizeGCashRequestDoesNotRequirePromotionPool(t *testing.T) {
	_, checkout, promotion, err := normalizeCheckoutRequest(sunnyCheckoutRequest{
		Plan:            "plus",
		LinkType:        "gcash",
		CheckoutProxies: "http://ph-proxy.example:8080",
	})
	if err != nil {
		t.Fatalf("normalize GCash request without promotion pool: %v", err)
	}
	if len(checkout) != 1 || len(promotion) != 1 || checkout[0] != promotion[0] {
		t.Fatalf("checkout=%#v promotion=%#v", checkout, promotion)
	}
}

func TestParseCheckoutExternalAT(t *testing.T) {
	token, email := parseCheckoutExternalAT("eyJhbGciOiJub25lIn0.payload.signature user@example.com")
	if token == "" || email != "user@example.com" {
		t.Fatalf("token=%q email=%q", token, email)
	}
	token, email = parseCheckoutExternalAT(`{"access_token":"eyJabc.def.ghi","email":"json@example.com"}`)
	if token == "" || email != "json@example.com" {
		t.Fatalf("json token=%q email=%q", token, email)
	}
	if token, _ = parseCheckoutExternalAT("not-an-at"); token != "" {
		t.Fatalf("invalid token=%q", token)
	}
	expired := "eyJhbGciOiJub25lIn0.eyJleHAiOjF9.signature user@example.com"
	if token, _ = parseCheckoutExternalAT(expired); token != "" {
		t.Fatal("expired JWT should be rejected")
	}
}

func TestExtractSunnyCheckoutResult(t *testing.T) {
	result := extractSunnyCheckoutResult(map[string]any{"checkout_session_id": "cs_live_123", "redirect_url": "https://pay.example/approve", "qr_data": "upi://pay/x"}, "upi")
	if result["checkout_session_id"] != "cs_live_123" || result["payment_link"] != "https://pay.example/approve" || result["qr_data"] != "upi://pay/x" {
		t.Fatalf("result=%#v", result)
	}
}

func TestExtractSunnyCheckoutResultPrefersGoPayMidtransURL(t *testing.T) {
	midtrans := "https://app.midtrans.com/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174000"
	result := extractSunnyCheckoutResult(map[string]any{
		"provider_redirect_url": midtrans,
		"verification_url":      "https://chatgpt.com/checkout/verify",
	}, "gopay")
	if result["payment_link"] != midtrans {
		t.Fatalf("result=%#v", result)
	}
	if isSunnyGopayMidtransURL("https://app.midtrans.com.evil.example/snap/v4/redirection/123e4567-e89b-12d3-a456-426614174000") {
		t.Fatal("lookalike Midtrans host must be rejected")
	}
}

func TestExtractSunnyCheckoutResultPrefersValidatedBlikURL(t *testing.T) {
	blikURL := "https://checkout.stripe.com/c/pay/cs_live_123#fidnandhYHdWcXxpYCc%2FJ2FgY2RwaXEn"
	result := extractSunnyCheckoutResult(map[string]any{
		"blik_payment_url": blikURL,
		"checkout_url":     "https://chatgpt.com/checkout/openai_ie/cs_live_123",
	}, "blik")
	if result["payment_link"] != blikURL {
		t.Fatalf("result=%#v", result)
	}
	if isSunnyBlikPaymentURL("https://chatgpt.com/checkout/openai_ie/cs_live_123") {
		t.Fatal("ordinary ChatGPT Checkout URL must not pass BLIK validation")
	}
	if isSunnyBlikPaymentURL("https://checkout.stripe.com/c/pay/cs_live_123?redirect_pm_type=blik&lid=generated&ui_mode=custom") {
		t.Fatal("legacy synthetic BLIK query parameters must be rejected")
	}
}

func TestRecordSunnyCheckoutResultPersistsPartialTaskItems(t *testing.T) {
	task := Task{Status: TaskRunning, ProgressTotal: 2, ResultJSON: "{}"}
	result := map[string]any{"requested": 2, "success": 0, "failed": 0, "items": []any{}}
	item := map[string]any{
		"email": "success@example.com", "status": "succeeded", "link_type": "paypal",
		"payment_link": "https://www.paypal.com/agreements/approve?ba_token=BA-test",
	}

	recordSunnyCheckoutResult(&task, result, item)

	serialized := serializeTask(task)
	partial := serialized["result"].(map[string]any)
	items := partial["items"].([]any)
	if len(items) != 1 || text(items[0].(map[string]any)["email"]) != "success@example.com" {
		t.Fatalf("partial task items=%#v", items)
	}
	if task.ProgressCurrent != 1 || task.SuccessCount != 1 || task.ErrorCount != 0 {
		t.Fatalf("task progress=%d success=%d failed=%d", task.ProgressCurrent, task.SuccessCount, task.ErrorCount)
	}
}
