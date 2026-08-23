package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestDomainMailboxCredentialAndTypeHelpers(t *testing.T) {
	credential := domainMailboxCredential("https://mail.example/", "token-1")
	base, token, err := parseDomainMailboxCredential(credential)
	if err != nil {
		t.Fatalf("parse domain credential: %v", err)
	}
	if base != "https://mail.example" || token != "token-1" {
		t.Fatalf("unexpected credential values: %q %q", base, token)
	}
	parsed, err := parseSunnyMailboxLineForProvider("user@example.com----"+credential, "自建域名邮箱", "outlook")
	if err != nil || parsed["access_key"] != credential {
		t.Fatalf("parse domain mailbox line: %#v, %v", parsed, err)
	}
	if normalizeSunnyMailboxType("自建域名邮箱") != "domain" || normalizeSunnyMailboxChannel("domain", "outlook") != "domain_api" {
		t.Fatal("domain mailbox normalization failed")
	}
}

func TestDomainMailboxLatestMailUsesEmailListAndExtractsCode(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/public/emailList" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "token-1" || r.Header.Get("X-Auth-Token") != "token-1" {
			t.Errorf("missing auth headers")
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || text(body["toEmail"]) != "user@example.com" {
			t.Errorf("unexpected request body: %#v, %v", body, err)
		}
		writeJSON(w, http.StatusOK, map[string]any{"items": []any{
			map[string]any{"id": 1, "receivedAt": "2020-01-01T00:00:00Z", "verificationCode": "111111"},
			map[string]any{"id": 2, "receivedAt": "2099-01-01T00:00:00Z", "bodyPreview": "ChatGPT code 978744"},
		}})
	}))
	defer server.Close()
	payload, err := domainMailLatestMail(domainMailboxCredential(server.URL, "token-1"), "user@example.com", 5)
	if err != nil {
		t.Fatalf("latest domain mail: %v", err)
	}
	items, ok := payload["items"].([]map[string]any)
	if !ok || len(items) != 2 || text(items[1]["otp"]) != "978744" {
		t.Fatalf("unexpected domain mail payload: %#v", payload)
	}
}

func TestDomainMailboxGenerateCreatesMailboxRecord(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/public/addUser":
			if r.Method != http.MethodPost {
				t.Errorf("addUser method = %s", r.Method)
			}
			writeJSON(w, http.StatusOK, map[string]any{"code": 0})
		case "/api/public/emailList":
			writeJSON(w, http.StatusOK, map[string]any{"items": []any{}})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgDomainMailbox, map[string]any{
		"base_url": server.URL, "auth_token": "token-1", "domain": "example.com", "random_local_length": 10, "auto_add_user": true,
	})
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/domain-mail/generate", strings.NewReader(`{}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.handleSunny(recorder, req, "domain-mail/generate")
	if recorder.Code != http.StatusOK {
		t.Fatalf("generate status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	var result map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &result); err != nil || !strings.Contains(text(result["email"]), "@example.com") {
		t.Fatalf("unexpected generate response: %s", recorder.Body.String())
	}
	var mailbox SunnyMailbox
	if err := s.db.Where("mailbox_type = ?", "domain").First(&mailbox).Error; err != nil {
		t.Fatalf("generated mailbox not persisted: %v", err)
	}
	if mailbox.MailboxType != "domain" || mailbox.MailboxChannel != "domain_api" || !strings.Contains(mailbox.Raw, "----") {
		t.Fatalf("unexpected generated mailbox: %#v", mailbox)
	}
}

func TestDomainMailboxRegisterTaskPreparesMailboxIds(t *testing.T) {
	var addUserCalls int
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/public/addUser" {
			addUserCalls++
			writeJSON(w, http.StatusOK, map[string]any{"code": 0})
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgDomainMailbox, map[string]any{
		"enabled_for_registration": true, "base_url": server.URL, "auth_token": "token-1", "domain": "example.com", "auto_add_user": true,
	})
	s.sunnySaveConfig(sunnyCfgProxy, mergeConfig(defaultProxyConfig(), map[string]any{"proxy_enabled": false}))
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/register", strings.NewReader(`{"identity":"domain","count":2,"concurrency":1}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.handleSunny(recorder, req, "tasks/register")
	if recorder.Code != http.StatusOK {
		t.Fatalf("domain task status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	var task Task
	if err := s.db.Order("created_at desc").First(&task).Error; err != nil {
		t.Fatal(err)
	}
	payload := jsonMap(task.PayloadJSON)
	ids := uintSlice(payload["mailbox_ids"])
	if task.ProgressTotal != 2 || len(ids) != 2 || addUserCalls != 2 {
		t.Fatalf("unexpected domain task preparation: total=%d ids=%v addUserCalls=%d", task.ProgressTotal, ids, addUserCalls)
	}
}
