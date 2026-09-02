package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestWebhookSignatureIsStableAndBoundToTimestamp(t *testing.T) {
	payload := []byte(`{"event":"account.updated"}`)
	first := webhookSignature("secret", "1700000000", payload)
	if !strings.HasPrefix(first, "sha256=") {
		t.Fatalf("unexpected signature prefix: %s", first)
	}
	if first != webhookSignature("secret", "1700000000", payload) {
		t.Fatal("signature must be deterministic")
	}
	if first == webhookSignature("secret", "1700000001", payload) {
		t.Fatal("timestamp must be part of the signature")
	}
}

func TestWebhookMatchesScopeAndSelectedEvent(t *testing.T) {
	hook := AccountWebhook{Scope: "group", ScopeValue: "A", EventsJSON: `["account.status_changed"]`}
	if !webhookMatches(hook, "account.status_changed", SunnyAccount{GroupName: "A"}) {
		t.Fatal("expected matching group event")
	}
	if webhookMatches(hook, "account.updated", SunnyAccount{GroupName: "A"}) {
		t.Fatal("unexpected event matched")
	}
	if webhookMatches(hook, "account.status_changed", SunnyAccount{GroupName: "B"}) {
		t.Fatal("unexpected group matched")
	}
}

func TestWebhookRetryDelay(t *testing.T) {
	if webhookRetryDelay(1).Seconds() != 5 || webhookRetryDelay(5).Hours() != 1 {
		t.Fatal("retry schedule changed")
	}
}

func TestWebhookPublicDoesNotExposeSecret(t *testing.T) {
	value := webhookPublic(AccountWebhook{ID: 1, Name: "hook", URL: "https://example.test/callback", Secret: "private", EventsJSON: `[]`})
	if _, ok := value["secret"]; ok {
		t.Fatal("webhook listing must not expose the signing secret")
	}
	if value["secret_configured"] != true {
		t.Fatal("secret_configured should indicate that a secret exists")
	}
}

func TestValidateWebhookInputRequiresScopeValue(t *testing.T) {
	if err := validateWebhookInput(map[string]any{
		"name": "hook", "url": "https://example.test/callback", "scope": "group",
	}, nil); err == nil {
		t.Fatal("group webhook without scope_value should be rejected")
	}
	current := &AccountWebhook{Name: "hook", URL: "https://example.test/callback", Scope: "account", ScopeValue: "42"}
	if err := validateWebhookInput(map[string]any{"url": "https://example.test/next"}, current); err != nil {
		t.Fatalf("partial update lost existing scope: %v", err)
	}
}

func TestValidateWebhookInputBoundsName(t *testing.T) {
	if err := validateWebhookInput(map[string]any{
		"name": strings.Repeat("x", 121), "url": "https://example.test/callback",
	}, nil); err == nil {
		t.Fatal("overlong webhook name should be rejected")
	}
}

func TestDeliverAccountWebhookSendsSignedRequest(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		timestamp := r.Header.Get("X-Sunny-Timestamp")
		mac := hmac.New(sha256.New, []byte("secret"))
		_, _ = mac.Write([]byte(timestamp + "."))
		_, _ = mac.Write(body)
		expected := "sha256=" + hex.EncodeToString(mac.Sum(nil))
		if r.Header.Get("X-Sunny-Signature") != expected || r.Header.Get("X-Sunny-Event") != "account.updated" {
			http.Error(w, "invalid signature", http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	status, _, err := deliverAccountWebhook(AccountWebhook{URL: server.URL, Secret: "secret", TimeoutSec: 2}, "delivery-1", "account.updated", []byte(`{"ok":true}`))
	if err != nil || status != http.StatusNoContent {
		t.Fatalf("delivery failed: status=%d err=%v", status, err)
	}
}
