package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestMoeMailClientOpenAPIAdapter(t *testing.T) {
	var seenKey string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seenKey = r.Header.Get("X-API-Key")
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/config":
			_ = json.NewEncoder(w).Encode(map[string]any{"emailDomains": "obo1688.us.ci,obo1688.cc.cd,obo1688.de5.net"})
		case "/api/emails/generate":
			_ = json.NewEncoder(w).Encode(map[string]any{"id": "email-1", "email": "abc@obo1688.us.ci"})
		case "/api/emails/email-1":
			if r.Method == http.MethodGet {
				_ = json.NewEncoder(w).Encode(map[string]any{"messages": []any{map[string]any{"id": "message-1", "from_address": "sender@example.com", "subject": "Code"}}})
				return
			}
			if r.Method == http.MethodDelete {
				_ = json.NewEncoder(w).Encode(map[string]any{"success": true})
				return
			}
			http.NotFound(w, r)
		case "/api/emails/email-1/message-1":
			_ = json.NewEncoder(w).Encode(map[string]any{"message": map[string]any{"id": "message-1", "from_address": "sender@example.com", "subject": "Code", "content": "OTP 123456", "html": "<b>OTP 123456</b>", "received_at": 1704110400000}})
		default:
			if r.Method == http.MethodDelete {
				_ = json.NewEncoder(w).Encode(map[string]any{"success": true})
				return
			}
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	client, err := newMoeMailClient(map[string]any{"moemail_api_url": server.URL, "moemail_api_key": "TOKEN"})
	if err != nil {
		t.Fatal(err)
	}
	if cfg, err := client.config(context.Background()); err != nil || cfg["emailDomains"] == nil {
		t.Fatalf("config: %#v %v", cfg, err)
	}
	id, email, err := client.generate(context.Background(), "abc", "obo1688.us.ci", 0)
	if err != nil || id != "email-1" || email != "abc@obo1688.us.ci" {
		t.Fatalf("generate: %q %q %v", id, email, err)
	}
	messages, err := client.listMessages(context.Background(), email, id)
	if err != nil || len(messages) != 1 || text(messages[0]["content"]) != "OTP 123456" || text(messages[0]["from"]) != "sender@example.com" {
		t.Fatalf("messages: %#v %v", messages, err)
	}
	if err := client.deleteMailbox(context.Background(), email, id); err != nil {
		t.Fatal(err)
	}
	if seenKey != "TOKEN" {
		t.Fatalf("X-API-Key not forwarded: %q", seenKey)
	}
}

func TestMoeMailConfiguredUsesEnvironmentKey(t *testing.T) {
	t.Setenv("MOEMAIL_API_KEY", "env-key")
	if !moeMailConfigured(map[string]any{"provider": "moemail"}) {
		t.Fatal("explicit MoeMail provider should be recognized")
	}
	if !moeMailConfigured(map[string]any{}) {
		t.Fatal("environment API key should select MoeMail")
	}
}
