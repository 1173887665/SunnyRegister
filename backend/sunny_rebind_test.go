package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSunnyRebindTaskRequiresEnabledDomainMailbox(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/sessions/rebind", strings.NewReader(`{"session_ids":[1]}`))
	rec := httptest.NewRecorder()
	s.handleSunny(rec, req, "sessions/rebind")
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "未启用") {
		t.Fatalf("expected disabled domain mailbox rejection, got %d %s", rec.Code, rec.Body.String())
	}
}

func TestSunnyRebindTaskCreatesWorkerTask(t *testing.T) {
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgDomainMailbox, mergeConfig(defaultDomainMailboxConfig(), map[string]any{
		"enabled_for_rebinding": true,
		"base_url":              "https://mail.example",
		"auth_token":            "token-1",
		"pickup_base_url":       "https://sunny.example",
		"domain":                "example.com",
	}))
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/sessions/rebind", strings.NewReader(`{"session_ids":[1]}`))
	rec := httptest.NewRecorder()
	s.handleSunny(rec, req, "sessions/rebind")
	if rec.Code != http.StatusAccepted {
		t.Fatalf("expected accepted task, got %d %s", rec.Code, rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), `"type":"sunny_rebind"`) {
		t.Fatalf("worker task type missing: %s", rec.Body.String())
	}
}
