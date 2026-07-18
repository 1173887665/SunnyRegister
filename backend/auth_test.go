package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func newAuthTestServer() *Server {
	return &Server{
		adminUser: "sunnyadmin", adminPass: "correct-horse-battery-staple",
		sessions: map[string]time.Time{}, loginFailures: map[string]*loginFailure{},
		sessionTTL: time.Hour,
	}
}

func TestLoginCreatesHttpOnlySessionCookie(t *testing.T) {
	s := newAuthTestServer()
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(`{"username":"sunnyadmin","password":"correct-horse-battery-staple"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.serveHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("login status = %d, body = %s", rec.Code, rec.Body.String())
	}
	cookies := rec.Result().Cookies()
	if len(cookies) != 1 || cookies[0].Name != "sunny_session" || !cookies[0].HttpOnly || cookies[0].SameSite != http.SameSiteStrictMode {
		t.Fatalf("unexpected session cookie: %#v", cookies)
	}
	check := httptest.NewRequest(http.MethodGet, "/api/auth/check", nil)
	check.AddCookie(cookies[0])
	checkRec := httptest.NewRecorder()
	s.serveHTTP(checkRec, check)
	if !strings.Contains(checkRec.Body.String(), `"authenticated":true`) {
		t.Fatalf("authenticated check failed: %s", checkRec.Body.String())
	}
	var checkBody map[string]any
	if err := json.Unmarshal(checkRec.Body.Bytes(), &checkBody); err != nil {
		t.Fatalf("decode auth check: %v", err)
	}
	if _, disclosed := checkBody["username"]; disclosed || strings.Contains(checkRec.Body.String(), "sunnyadmin") {
		t.Fatalf("auth check disclosed the administrator username: %s", checkRec.Body.String())
	}
}

func TestLoginRequiresExplicitUsername(t *testing.T) {
	s := newAuthTestServer()
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(`{"password":"correct-horse-battery-staple"}`))
	rec := httptest.NewRecorder()
	s.serveHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("password-only login status = %d", rec.Code)
	}
}

func TestLoginRateLimit(t *testing.T) {
	s := newAuthTestServer()
	for attempt := 0; attempt < 5; attempt++ {
		req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(`{"username":"sunnyadmin","password":"wrong-password"}`))
		req.RemoteAddr = "203.0.113.10:12345"
		rec := httptest.NewRecorder()
		s.serveHTTP(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("attempt %d status = %d", attempt+1, rec.Code)
		}
	}
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(`{"username":"sunnyadmin","password":"wrong-password"}`))
	req.RemoteAddr = "203.0.113.10:12345"
	rec := httptest.NewRecorder()
	s.serveHTTP(rec, req)
	if rec.Code != http.StatusTooManyRequests || rec.Header().Get("Retry-After") == "" {
		t.Fatalf("rate limit status = %d, retry-after = %q", rec.Code, rec.Header().Get("Retry-After"))
	}
}

func TestCrossOriginMutationRejected(t *testing.T) {
	s := newAuthTestServer()
	req := httptest.NewRequest(http.MethodPost, "/api/auth/login", strings.NewReader(`{}`))
	req.Host = "register.example.com"
	req.Header.Set("Origin", "https://evil.example")
	rec := httptest.NewRecorder()
	s.serveHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("cross-origin status = %d", rec.Code)
	}
}
