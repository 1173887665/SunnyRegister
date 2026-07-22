package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

func TestFetchLatestMailsViaGraph(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer graph-access-token" {
			t.Fatalf("unexpected authorization header: %q", got)
		}
		if r.URL.Query().Get("$top") != "10" || !strings.Contains(r.URL.Query().Get("$select"), "body") {
			t.Fatalf("unexpected Graph query: %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"value":[{"id":"message-id","subject":"Your ChatGPT code is 654321","from":{"emailAddress":{"name":"OpenAI","address":"noreply@openai.com"}},"toRecipients":[{"emailAddress":{"address":"user@example.com"}}],"receivedDateTime":"2026-07-22T08:00:00Z","bodyPreview":"Use 654321 to continue","body":{"contentType":"html","content":"<p>Use <b>654321</b> to continue</p>"}}]}`))
	}))
	defer server.Close()

	originalURL := outlookGraphMessagesURL
	outlookGraphMessagesURL = server.URL
	defer func() { outlookGraphMessagesURL = originalURL }()

	items, err := fetchLatestMailsViaGraph("user@example.com", "graph-access-token", 10, "")
	if err != nil {
		t.Fatalf("fetch Graph messages: %v", err)
	}
	if len(items) != 1 {
		t.Fatalf("expected one message, got %d", len(items))
	}
	item := items[0]
	if item["source"] != "graph" || item["otp"] != "654321" {
		t.Fatalf("unexpected Graph item: %#v", item)
	}
	if !strings.Contains(item["from"].(string), "noreply@openai.com") {
		t.Fatalf("unexpected sender: %v", item["from"])
	}
}

func TestFetchLatestMailsViaGraphRejectsUnauthorizedToken(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error":{"code":"InvalidAuthenticationToken","message":"token audience is invalid"}}`))
	}))
	defer server.Close()

	originalURL := outlookGraphMessagesURL
	outlookGraphMessagesURL = server.URL
	defer func() { outlookGraphMessagesURL = originalURL }()

	_, err := fetchLatestMailsViaGraph("user@example.com", "invalid-token", 1, "")
	if err == nil || !strings.Contains(err.Error(), "InvalidAuthenticationToken") {
		t.Fatalf("expected Graph token error, got %v", err)
	}
}

func TestOutlookGraphLiveCredential(t *testing.T) {
	email := os.Getenv("SUNNY_TEST_OUTLOOK_EMAIL")
	clientID := os.Getenv("SUNNY_TEST_OUTLOOK_CLIENT_ID")
	refreshToken := os.Getenv("SUNNY_TEST_OUTLOOK_REFRESH_TOKEN")
	if email == "" || clientID == "" || refreshToken == "" {
		t.Skip("set SUNNY_TEST_OUTLOOK_EMAIL, SUNNY_TEST_OUTLOOK_CLIENT_ID and SUNNY_TEST_OUTLOOK_REFRESH_TOKEN")
	}
	payload, err := fetchOutlookLatestMail(email, clientID, refreshToken, 3, "")
	if err != nil {
		t.Fatalf("live Outlook Graph query failed: %v", err)
	}
	if payload["mail_protocol"] != "graph" {
		t.Fatalf("expected Graph credential, got protocol %v", payload["mail_protocol"])
	}
	if _, ok := payload["items"].([]map[string]any); !ok {
		t.Fatalf("unexpected Graph message payload type: %T", payload["items"])
	}
}
