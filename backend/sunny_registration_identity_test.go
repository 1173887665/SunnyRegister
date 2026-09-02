package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestNormalizeSunnyRegistrationIdentity(t *testing.T) {
	cases := []struct {
		input string
		want  string
		err   string
	}{
		{input: "", want: "system"},
		{input: "outlook_mailbox", want: "system"},
		{input: "自建域名邮箱", want: "domain"},
		{input: "Remail邮箱", want: "remail"},
		{input: "phone", err: "phone-register"},
		{input: "google", err: "Google"},
		{input: "microsoft", err: "Microsoft"},
		{input: "unknown", err: "无效"},
	}
	for _, tc := range cases {
		got, err := normalizeSunnyRegistrationIdentity(tc.input)
		if tc.err == "" {
			if err != nil || got != tc.want {
				t.Errorf("normalize(%q) = %q, %v; want %q", tc.input, got, err, tc.want)
			}
			continue
		}
		if err == nil || !strings.Contains(err.Error(), tc.err) {
			t.Errorf("normalize(%q) error = %v; want substring %q", tc.input, err, tc.err)
		}
	}
}

func TestSunnyRegisterRejectsUnsupportedIdentities(t *testing.T) {
	s := newSunnySessionTestServer(t)
	for _, identity := range []string{"google", "microsoft", "phone", "unknown"} {
		req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/register", strings.NewReader(`{"identity":"`+identity+`","count":1}`))
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()
		s.sunnyTasks(rec, req, []string{"register"})
		if rec.Code != http.StatusBadRequest {
			t.Errorf("identity %q status = %d, body = %s; want 400", identity, rec.Code, rec.Body.String())
		}
		var taskCount int64
		if err := s.db.Model(&Task{}).Count(&taskCount).Error; err != nil {
			t.Fatalf("count tasks for %q: %v", identity, err)
		}
		if taskCount != 0 {
			t.Errorf("identity %q created %d task(s)", identity, taskCount)
		}
	}
}

func TestSunnyPhoneRegisterRejectsIncompleteFirefoxConfig(t *testing.T) {
	s := newSunnySessionTestServer(t)
	if err := s.db.Model(&SunnyAccount{}).Where("email = ?", "session@example.com").Updates(map[string]any{"openai_rt": ""}).Error; err != nil {
		t.Fatalf("clear account RT: %v", err)
	}
	if err := s.db.Model(&SunnySession{}).Where("email = ?", "session@example.com").Updates(map[string]any{"refresh_token": ""}).Error; err != nil {
		t.Fatalf("clear session RT: %v", err)
	}
	s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), map[string]any{
		"firefox_enabled": true, "firefox_api_token": "token-only", "firefox_default_country": "", "firefox_default_service": "", "firefox_max_price": 0,
	}))
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/phone-register", strings.NewReader(`{"mailbox_ids":[1],"sms_provider":"firefox","sms_country":"US"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnyTasks(rec, req, []string{"phone-register"})
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "未启用或配置不完整") {
		t.Fatalf("incomplete Firefox config status = %d, body = %s", rec.Code, rec.Body.String())
	}
}

func TestSunnyPhoneRegisterRequiresAnSMSProviderEvenWhenMailboxRowsAreSelected(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/phone-register", strings.NewReader(`{"mailbox_ids":[1]}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnyTasks(rec, req, []string{"phone-register"})
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "接码平台") {
		t.Fatalf("phone task without SMS provider status = %d, body = %s", rec.Code, rec.Body.String())
	}
}
