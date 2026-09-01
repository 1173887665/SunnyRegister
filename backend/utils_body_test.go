package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestParseBodyRejectsOversizedJSON(t *testing.T) {
	raw := `{"payload":"` + strings.Repeat("x", maxJSONBodySize) + `"}`
	req := httptest.NewRequest(http.MethodPost, "/api/test", strings.NewReader(raw))
	body, err := parseBody(req)
	if err == nil {
		t.Fatalf("expected oversized body error, got body=%#v", body)
	}
	if !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("unexpected oversized body error: %v", err)
	}
}

func TestParseBodyRejectsTrailingJSON(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/test", strings.NewReader(`{"ok":true}{"ignored":true}`))
	body, err := parseBody(req)
	if err == nil {
		t.Fatalf("expected trailing JSON error, got body=%#v", body)
	}
	if !strings.Contains(err.Error(), "multiple JSON values") {
		t.Fatalf("unexpected trailing JSON error: %v", err)
	}
}

func TestParseBodyAllowsEmptyBody(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/test", nil)
	body, err := parseBody(req)
	if err != nil {
		t.Fatalf("empty body returned error: %v", err)
	}
	if body == nil || len(body) != 0 {
		t.Fatalf("empty body result = %#v", body)
	}
}

func TestGeneratedExportSecretDoesNotUsePlaceholder(t *testing.T) {
	t.Setenv("ANY2API_EXPORT_API_KEY", "")
	value := generatedExportSecret("ANY2API_EXPORT_API_KEY", "test")
	if value == "changeme" || value == "0000" || value == "" {
		t.Fatalf("generated export secret is unsafe: %q", value)
	}
	t.Setenv("ANY2API_EXPORT_API_KEY", "configured-secret")
	if got := generatedExportSecret("ANY2API_EXPORT_API_KEY", "test"); got != "configured-secret" {
		t.Fatalf("configured export secret = %q", got)
	}
}
