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
	if !isRemailInsufficientBalance(&remailAPIError{StatusCode: 422, Message: "Consumer balance is insufficient"}) {
		t.Fatal("expected insufficient balance error to be detected")
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

func TestPrepareRemailRegistrationKeepsPurchasedMailboxesWhenBalanceRunsOut(t *testing.T) {
	var orderCalls atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/open/orders" {
			writeError(w, http.StatusNotFound, "not found")
			return
		}
		if orderCalls.Add(1) == 1 {
			writeJSON(w, http.StatusOK, map[string]any{"orderNo": "R-1", "deliveryEmail": "purchased@example.com", "serviceToken": "st-1"})
			return
		}
		writeJSON(w, http.StatusUnprocessableEntity, map[string]any{"message": "Consumer balance is insufficient"})
	}))
	defer provider.Close()

	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgRemail, map[string]any{"enabled": true, "base_url": provider.URL, "api_key": "secret", "project_id": 2, "service_mode": "purchase", "service_mode_explicit": true, "supply": "private_first"})
	body := map[string]any{"count": 2}
	if err := s.prepareRemailRegistration(body); err != nil {
		t.Fatalf("prepare registration: %v", err)
	}
	ids := uintSlice(body["mailbox_ids"])
	if len(ids) != 1 || !strings.Contains(text(body["provider_stop_reason"]), "余额不足") {
		t.Fatalf("unexpected partial preparation: %#v", body)
	}
	var mailbox SunnyMailbox
	if err := s.db.First(&mailbox, ids[0]).Error; err != nil {
		t.Fatal(err)
	}
	if mailbox.MailboxType != "remail" || mailbox.MailboxChannel != "remail_api" || mailbox.Email != "purchased@example.com" {
		t.Fatalf("unexpected persisted mailbox: %#v", mailbox)
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
