package main

import (
	"strings"
	"testing"
)

func TestSanitizePersistedJSON(t *testing.T) {
	raw := `{"access_token":"eyJheader.payload.signature","nested":{"password":"secret-value"},"proxy":"http://user:pass@example.com:8080","ok":"kept"}`
	got := sanitizePersistedJSON(raw)
	for _, forbidden := range []string{"eyJheader", "secret-value", "user:pass"} {
		if strings.Contains(got, forbidden) {
			t.Fatalf("sanitized JSON still contains %q: %s", forbidden, got)
		}
	}
	if !strings.Contains(got, `"ok":"kept"`) {
		t.Fatalf("non-secret field was lost: %s", got)
	}
}

func TestSanitizePersistedMessage(t *testing.T) {
	got := sanitizePersistedString("Received OpenAI OTP 123456 using Bearer abcdefghijklmnopqrstuvwxyz")
	if strings.Contains(got, "123456") || strings.Contains(got, "abcdefghijklmnopqrstuvwxyz") {
		t.Fatalf("message was not redacted: %s", got)
	}
}
