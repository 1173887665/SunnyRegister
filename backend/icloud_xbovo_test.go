package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
)

func TestParseSunnyMailboxLineForXbovo(t *testing.T) {
	parsed, err := parseSunnyMailboxLineForProvider("alias@icloud.com----alias_key", "apple", "xbovo")
	if err != nil {
		t.Fatalf("parse xbovo mailbox: %v", err)
	}
	if parsed["email"] != "alias@icloud.com" || parsed["access_key"] != "alias_key" {
		t.Fatalf("unexpected parsed mailbox: %#v", parsed)
	}
	if _, err := parseSunnyMailboxLineForProvider("alias@icloud.com----", "apple", "xbovo"); err == nil {
		t.Fatal("expected missing xbovo key to fail")
	}
}

func TestFetchXbovoLatestMailNormalizesMessages(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-API-Key") != "alias_key" {
			t.Fatal("missing xbovo API key header")
		}
		if r.URL.Path == "/api/v1/messages" && r.URL.Query().Get("email") != "alias@icloud.com" {
			t.Fatalf("missing xbovo query values: %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/messages":
			_, _ = w.Write([]byte(`{"ok":true,"messages":[{"id":12,"alias_email":"alias@icloud.com","from":"OpenAI <noreply@example.com>","to":"alias@icloud.com","subject":"Your code","preview":"Use 123456 to continue","code":"123456","received_at":"2026-07-31T10:00:00+08:00"}]}`))
		case "/api/v1/message/raw":
			_, _ = w.Write([]byte(`{"ok":true,"text":"Complete message with code 123456","html":"<p>Complete message with code 123456</p>"}`))
		default:
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
	}))
	defer server.Close()
	oldBase := xbovoAPIBaseURL
	xbovoAPIBaseURL = server.URL
	defer func() { xbovoAPIBaseURL = oldBase }()

	payload, err := fetchXbovoLatestMail("alias@icloud.com", "alias_key", 5, "")
	if err != nil {
		t.Fatalf("fetch xbovo mail: %v", err)
	}
	items, ok := payload["items"].([]map[string]any)
	if !ok || len(items) != 1 {
		t.Fatalf("unexpected items: %#v", payload["items"])
	}
	if items[0]["otp"] != "123456" || items[0]["source"] != "xbovo" || items[0]["body_preview"] == "" {
		t.Fatalf("unexpected normalized item: %#v", items[0])
	}
	if items[0]["body"] != "Complete message with code 123456" || items[0]["raw_html"] == "" {
		t.Fatalf("raw message was not normalized: %#v", items[0])
	}
}

func TestFetchXbovoLatestMailFallsBackToPreview(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/v1/messages" {
			_, _ = w.Write([]byte(`{"ok":true,"messages":[{"id":12,"subject":"Preview only","preview":"Use 654321 to continue","code":"654321"}]}`))
			return
		}
		w.WriteHeader(http.StatusForbidden)
		_, _ = w.Write([]byte(`{"ok":false,"error":"raw message unavailable"}`))
	}))
	defer server.Close()
	oldBase := xbovoAPIBaseURL
	xbovoAPIBaseURL = server.URL
	defer func() { xbovoAPIBaseURL = oldBase }()

	payload, err := fetchXbovoLatestMail("alias@icloud.com", "alias_key", 5, "")
	if err != nil {
		t.Fatalf("preview fallback failed: %v", err)
	}
	items := payload["items"].([]map[string]any)
	if len(items) != 1 || items[0]["body"] != "Use 654321 to continue" || items[0]["otp"] != "654321" {
		t.Fatalf("unexpected preview fallback: %#v", items)
	}
}

func TestFetchXbovoLatestMailClassifiesInvalidKey(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"ok":false,"error":"API Key 不正确"}`))
	}))
	defer server.Close()
	oldBase := xbovoAPIBaseURL
	xbovoAPIBaseURL = server.URL
	defer func() { xbovoAPIBaseURL = oldBase }()

	_, err := fetchXbovoLatestMail("alias@icloud.com", "bad_key", 5, "")
	if err == nil {
		t.Fatal("expected invalid key error")
	}
	mailErr := classifyOutlookMailError(err)
	if mailErr.Code != "mailbox_credential_invalid" || mailErr.HTTPStatus != http.StatusUnprocessableEntity {
		t.Fatalf("unexpected classification: %#v", mailErr)
	}
}

func TestFetchXbovoLatestMailIntegration(t *testing.T) {
	email := os.Getenv("XBOVO_TEST_EMAIL")
	key := os.Getenv("XBOVO_TEST_KEY")
	if email == "" || key == "" {
		t.Skip("XBOVO_TEST_EMAIL and XBOVO_TEST_KEY are not configured")
	}
	payload, err := fetchXbovoLatestMail(email, key, 3, "")
	if err != nil {
		t.Fatalf("live xbovo query failed: %v", err)
	}
	if payload["mail_protocol"] != "xbovo_api" {
		t.Fatalf("unexpected live payload: %#v", payload)
	}
}
