package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRemailMailboxCredentialHelpers(t *testing.T) {
	parsed, err := parseSunnyMailboxLineForProvider("user@example.com----service-token", "remail", "remail_api")
	if err != nil {
		t.Fatalf("parse remail credential: %v", err)
	}
	if parsed["email"] != "user@example.com" || parsed["access_key"] != "service-token" {
		t.Fatalf("unexpected parsed remail credential: %#v", parsed)
	}
	if normalizeSunnyMailboxType("Remail邮箱") != "remail" || normalizeSunnyMailboxChannel("remail", "outlook") != "remail_api" {
		t.Fatalf("remail normalization failed")
	}
}

func TestRemailDefaultsUsePurchaseMode(t *testing.T) {
	if got := text(defaultRemailConfig()["service_mode"]); got != "purchase" {
		t.Fatalf("default service mode = %q, want purchase", got)
	}
}

func TestRemailConfigRouteAcceptsConfigSegment(t *testing.T) {
	s := newSunnySessionTestServer(t)
	body := strings.NewReader(`{"enabled":true,"base_url":"https://remail.example","api_key":"secret","project_id":2}`)
	req := httptest.NewRequest(http.MethodPut, "/api/sunny/remail/config", body)
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.handleSunny(recorder, req, "remail/config")
	if recorder.Code != http.StatusOK {
		t.Fatalf("save config status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	stored := s.sunnyGetConfig(sunnyCfgRemail, defaultRemailConfig())
	if !boolValue(stored["enabled"], false) || text(stored["api_key"]) != "secret" {
		t.Fatalf("unexpected stored config: %#v", stored)
	}
}

func TestRemailWalletUsesDocumentedEndpoint(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/open/wallet" {
			t.Fatalf("wallet path = %q", r.URL.Path)
		}
		if r.Header.Get("X-API-Key") != "secret" {
			t.Fatalf("missing API key header")
		}
		writeJSON(w, http.StatusOK, map[string]any{"consumerBalance": "168.50", "historicalSpend": "391.20", "orderCount": 486})
	}))
	defer server.Close()
	client, err := newRemailClient(map[string]any{"base_url": server.URL, "api_key": "secret"})
	if err != nil {
		t.Fatal(err)
	}
	wallet, err := client.wallet(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	if text(wallet["consumerBalance"]) != "168.50" || intValue(wallet["orderCount"], 0) != 486 {
		t.Fatalf("unexpected wallet: %#v", wallet)
	}
}

func TestRemailTokenPayloadRoundTrip(t *testing.T) {
	order := remailOrder{OrderNo: "R-1", ServiceToken: "st-1", ReceiveUntil: "2026-08-23T08:00:00Z"}
	raw := remailTokenPayload("https://remail.example", "secret", order)
	var payload map[string]any
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		t.Fatalf("token payload is not JSON: %v", err)
	}
	if remailOrderNoFromAccessKey(raw) != "R-1" || remailServiceTokenFromAccessKey(raw) != "st-1" || remailBaseURLFromAccessKey(raw) != "https://remail.example" {
		t.Fatalf("token payload helpers returned unexpected values: %#v", payload)
	}
}

func TestRemailPickupURLAndMailItems(t *testing.T) {
	pickup := remailPickupURL("https://remail.example/", "user@example.com", "st-1")
	if pickup != "https://remail.example/v1/pickup?email=user@example.com&token=st-1" {
		t.Fatalf("unexpected pickup URL: %s", pickup)
	}
	items := remailMailItems(map[string]any{"items": []any{map[string]any{
		"id": 7, "sender": "noreply@tm.openai.com", "recipient": "user@example.com", "receivedAt": "2099-01-01T00:00:00Z", "subject": "Code", "bodyPreview": "code 323090", "verificationCode": "323090",
	}}}, "user@example.com")
	if len(items) != 1 || text(items[0]["otp"]) != "323090" || text(items[0]["from"]) != "noreply@tm.openai.com" || text(items[0]["date"]) == "" {
		t.Fatalf("unexpected pickup items: %#v", items)
	}
}

func TestRemailLatestMailPickupURL(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/pickup" || r.URL.Query().Get("token") != "st-1" {
			t.Fatalf("unexpected pickup request: %s", r.URL.String())
		}
		writeJSON(w, http.StatusOK, map[string]any{"items": []any{map[string]any{
			"id": 6667637, "sender": "noreply@tm.openai.com", "recipient": "user@example.com", "receivedAt": "2099-01-01T00:00:00Z", "subject": "ChatGPT code", "bodyPreview": "<html>code</html>", "verificationCode": "323090",
		}}})
	}))
	defer server.Close()
	payload, err := remailLatestMail(server.URL+"/v1/pickup?email=user@example.com&token=st-1", "user@example.com", 5)
	if err != nil {
		t.Fatalf("pickup query failed: %v", err)
	}
	items, ok := payload["items"].([]map[string]any)
	if !ok || len(items) != 1 || text(items[0]["otp"]) != "323090" {
		t.Fatalf("unexpected pickup response: %#v", payload)
	}
}

func TestRemailRegisterTaskDefersOrdersToWorker(t *testing.T) {
	var orderCalls atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		orderCalls.Add(1)
		writeJSON(w, http.StatusOK, map[string]any{})
	}))
	defer provider.Close()
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgRemail, map[string]any{"enabled": true, "base_url": provider.URL, "api_key": "secret", "project_id": 2})
	s.sunnySaveConfig(sunnyCfgProxy, mergeConfig(defaultProxyConfig(), map[string]any{"proxy_enabled": false}))
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/register", strings.NewReader(`{"identity":"remail","count":10,"concurrency":3}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.handleSunny(recorder, req, "tasks/register")
	if recorder.Code != http.StatusOK {
		t.Fatalf("create task status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	if orderCalls.Load() != 0 {
		t.Fatalf("task creation placed %d eager orders", orderCalls.Load())
	}
	var task Task
	if err := s.db.Order("created_at desc").First(&task).Error; err != nil {
		t.Fatal(err)
	}
	payload := jsonMap(task.PayloadJSON)
	if task.ProgressTotal != 10 || intValue(payload["count"], 0) != 10 || len(uintSlice(payload["mailbox_ids"])) != 0 {
		t.Fatalf("unexpected deferred task: %#v, payload=%#v", task, payload)
	}
}
