package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
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
	pickup, err := domainMailboxPickupCredential("https://sunny.example/", "user@example.com", "pickup-token")
	if err != nil {
		t.Fatalf("build pickup credential: %v", err)
	}
	pickupEmail, pickupToken, err := parseDomainMailboxPickupCredential(pickup)
	if err != nil || pickupEmail != "user@example.com" || pickupToken != "pickup-token" {
		t.Fatalf("unexpected pickup credential: %q %q %v", pickupEmail, pickupToken, err)
	}
	if _, err := parseSunnyMailboxLineForProvider("user@example.com----"+pickup, "domain", "domain_api"); err != nil {
		t.Fatalf("parse pickup mailbox line: %v", err)
	}
}

func TestRandomDomainPickupTokenUsesDMSKPrefix(t *testing.T) {
	first, err := randomDomainPickupToken()
	if err != nil {
		t.Fatalf("generate first pickup token: %v", err)
	}
	second, err := randomDomainPickupToken()
	if err != nil {
		t.Fatalf("generate second pickup token: %v", err)
	}
	if !strings.HasPrefix(first, "dmsk_") || !strings.HasPrefix(second, "dmsk_") {
		t.Fatalf("pickup tokens must use dmsk_ prefix: %q %q", first, second)
	}
	if first == second {
		t.Fatal("pickup tokens must be unique")
	}
}

func TestDomainMailboxDomainsRotateAndKeepLegacyFallback(t *testing.T) {
	first, err := nextDomainMailboxDomain(map[string]any{"domains": "one.example\ntwo.example"})
	if err != nil {
		t.Fatalf("select first domain: %v", err)
	}
	second, err := nextDomainMailboxDomain(map[string]any{"domains": []any{"one.example", "two.example"}})
	if err != nil {
		t.Fatalf("select second domain: %v", err)
	}
	if first == second {
		t.Fatalf("domains should rotate, got %q twice", first)
	}
	legacy, err := domainMailboxDomains(map[string]any{"domain": "legacy.example"})
	if err != nil || len(legacy) != 1 || legacy[0] != "legacy.example" {
		t.Fatalf("legacy domain fallback failed: %#v %v", legacy, err)
	}
}

func TestDomainMailboxPublicItemsUseRemailShapeAndRecentLimit(t *testing.T) {
	now := time.Date(2026, 8, 24, 12, 0, 0, 0, time.UTC)
	messages := make([]map[string]any, 0, 13)
	for index := 0; index < 12; index++ {
		bodyPreview := "verification email"
		if index == 0 {
			bodyPreview = "<style>.code{content:202123}</style><p>verification email 876769</p>"
		}
		message := map[string]any{
			"id":          100 + index,
			"sender":      "noreply@tm.openai.com",
			"recipient":   "user@example.com",
			"receivedAt":  now.Add(-time.Duration(index) * time.Minute).Format(time.RFC3339),
			"subject":     "ChatGPT code",
			"bodyPreview": bodyPreview,
		}
		if index == 0 {
			message["verificationCode"] = "202123"
		}
		messages = append(messages, message)
	}
	messages = append(messages, map[string]any{
		"id":         999,
		"sender":     "noreply@tm.openai.com",
		"recipient":  "user@example.com",
		"receivedAt": now.Add(-73 * time.Hour).Format(time.RFC3339),
		"subject":    "old message",
	})

	items := domainMailPublicItems(messages, "user@example.com", now)
	if len(items) != 10 {
		t.Fatalf("expected ten recent items, got %d", len(items))
	}
	if items[0]["id"] != 100 || items[9]["id"] != 109 {
		t.Fatalf("items are not sorted newest-first: first=%v last=%v", items[0]["id"], items[9]["id"])
	}
	if items[0]["receivedAt"] != now.Format(time.RFC3339) || items[0]["sender"] != "noreply@tm.openai.com" || items[0]["recipient"] != "user@example.com" {
		t.Fatalf("unexpected Remail-shaped item: %#v", items[0])
	}
	if items[0]["verificationCode"] != "876769" {
		t.Fatalf("verification code missing: %#v", items[0])
	}
	if preview, ok := items[0]["bodyPreview"].(string); !ok || strings.Contains(preview, "<") || !strings.Contains(preview, "876769") {
		t.Fatalf("bodyPreview must be plain text: %#v", items[0]["bodyPreview"])
	}
	if _, exists := items[1]["verificationCode"]; exists {
		t.Fatalf("verificationCode should only be present when detected: %#v", items[1])
	}
}

func TestDomainMailItemsPreferPlainTextAndRetainHTML(t *testing.T) {
	items := domainMailItems([]map[string]any{{
		"id":          1,
		"text":        "Plain verification code 876769",
		"content":     "Plain verification code 876769",
		"body":        "Plain verification code 876769",
		"html":        "<p>HTML verification code <b>876769</b></p>",
		"bodyPreview": "<p>HTML verification code <b>876769</b></p>",
	}}, "user@example.com")
	if len(items) != 1 {
		t.Fatalf("expected one normalized message, got %d", len(items))
	}
	if items[0]["body"] != "Plain verification code 876769" || items[0]["body_preview"] != "Plain verification code 876769" {
		t.Fatalf("plain text fields were not preferred: %#v", items[0])
	}
	if items[0]["raw_html"] != "<p>HTML verification code <b>876769</b></p>" {
		t.Fatalf("HTML field was not retained separately: %#v", items[0]["raw_html"])
	}
}

func TestDomainMailboxLatestMailUsesEmailListAndExtractsCode(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/api/public/emailList" {
			t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "token-1" || r.Header.Get("X-Auth-Token") != "token-1" || r.Header.Get("x-custom-auth") != "site-password" {
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
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgDomainMailbox, map[string]any{"site_password": "site-password"})
	payload, err := s.domainMailLatestMail(domainMailboxCredential(server.URL, "token-1"), "user@example.com", 5)
	if err != nil {
		t.Fatalf("latest domain mail: %v", err)
	}
	items, ok := payload["items"].([]map[string]any)
	if !ok || len(items) != 2 || text(items[1]["otp"]) != "978744" {
		t.Fatalf("unexpected domain mail payload: %#v", payload)
	}
}

func TestDomainMailAddUserAcceptsPlainTextSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("邮箱用户创建成功"))
	}))
	defer server.Close()
	client := &domainMailClient{baseURL: server.URL, token: "token-1", sitePassword: "site-password", client: server.Client()}
	if err := client.addUser(context.Background(), "user@example.com"); err != nil {
		t.Fatalf("plain-text addUser success must be accepted: %v", err)
	}
}

func TestDomainMailResponsesKeepJSONStrictAndReportHTTPStatus(t *testing.T) {
	t.Run("add user rejects plain-text failure with HTTP 200", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("请求参数不完整"))
		}))
		defer server.Close()
		client := &domainMailClient{baseURL: server.URL, token: "token-1", sitePassword: "site-password", client: server.Client()}
		err := client.addUser(context.Background(), "user@example.com")
		if err == nil || !strings.Contains(err.Error(), "请求参数不完整") {
			t.Fatalf("unexpected plain-text failure result: %v", err)
		}
	})

	t.Run("email list rejects plain text", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("邮件服务暂时不可用"))
		}))
		defer server.Close()
		client := &domainMailClient{baseURL: server.URL, token: "token-1", sitePassword: "site-password", client: server.Client()}
		_, err := client.listMessages(context.Background(), "user@example.com")
		if err == nil || !strings.Contains(err.Error(), "不是有效 JSON") || strings.Contains(err.Error(), "invalid character") {
			t.Fatalf("unexpected strict JSON error: %v", err)
		}
	})

	t.Run("http error is reported before decoding", func(t *testing.T) {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(http.StatusBadGateway)
			_, _ = w.Write([]byte("上游服务异常"))
		}))
		defer server.Close()
		client := &domainMailClient{baseURL: server.URL, token: "token-1", sitePassword: "site-password", client: server.Client()}
		err := client.addUser(context.Background(), "user@example.com")
		if err == nil || !strings.Contains(err.Error(), "HTTP 502") || !strings.Contains(err.Error(), "上游服务异常") {
			t.Fatalf("unexpected HTTP error: %v", err)
		}
	})
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
		"base_url": server.URL, "auth_token": "token-1", "site_password": "site-password", "pickup_base_url": "https://sunny.example", "domain": "example.com", "random_local_length": 10, "auto_add_user": true,
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
	if mailbox.PickupTokenHash == "" || strings.Contains(mailbox.AccessKey, "token-1") || !strings.Contains(mailbox.AccessKey, "/api/sunny/domain-mail/pickup?") {
		t.Fatalf("generated mailbox must use an individual pickup credential: %#v", mailbox)
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
		"enabled_for_registration": true, "base_url": server.URL, "auth_token": "token-1", "site_password": "site-password", "pickup_base_url": "https://sunny.example", "domain": "example.com", "auto_add_user": true,
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
	var mailboxes []SunnyMailbox
	if err := s.db.Where("id IN ?", ids).Find(&mailboxes).Error; err != nil || len(mailboxes) != 2 {
		t.Fatalf("load generated mailboxes: %v, count=%d", err, len(mailboxes))
	}
	if mailboxes[0].PickupTokenHash == mailboxes[1].PickupTokenHash || mailboxes[0].AccessKey == mailboxes[1].AccessKey {
		t.Fatal("each generated domain mailbox must have a unique pickup token")
	}
}

func TestDomainMailboxGenerateRejectsWhenPoolDisabled(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatalf("disabled pool should not call upstream: %s", r.URL.Path)
	}))
	defer server.Close()
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgDomainMailbox, map[string]any{
		"enabled": false, "base_url": server.URL, "auth_token": "token-1", "site_password": "site-password", "pickup_base_url": "https://sunny.example", "domain": "example.com",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/domain-mail/generate", strings.NewReader(`{"enabled":true}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.handleSunny(recorder, req, "domain-mail/generate")
	if recorder.Code != http.StatusBadRequest || !strings.Contains(recorder.Body.String(), "已关闭") {
		t.Fatalf("disabled pool status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
}

func TestDomainMailboxPublicPickupBindsTokenToMailbox(t *testing.T) {
	receivedAt := time.Now().UTC().Format(time.RFC3339)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		writeJSON(w, http.StatusOK, map[string]any{"items": []any{map[string]any{
			"id": 7, "toEmail": text(body["toEmail"]), "receivedAt": receivedAt, "verificationCode": "978744",
		}}})
	}))
	defer upstream.Close()
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgDomainMailbox, map[string]any{
		"enabled": true, "base_url": upstream.URL, "auth_token": "manager-token", "site_password": "site-password", "pickup_base_url": "https://sunny.example", "domain": "example.com",
	})
	tokenA, tokenB := "mailbox-token-a", "mailbox-token-b"
	mailboxA := SunnyMailbox{Email: "a@example.com", MailboxType: "domain", MailboxChannel: "domain_api", AccessKey: "unused", PickupTokenHash: domainMailboxPickupTokenHash(tokenA), Status: "未注册", Enabled: true}
	mailboxB := SunnyMailbox{Email: "b@example.com", MailboxType: "domain", MailboxChannel: "domain_api", AccessKey: "unused", PickupTokenHash: domainMailboxPickupTokenHash(tokenB), Status: "未注册", Enabled: true}
	mailboxC := SunnyMailbox{Email: "legacy@example.com", RebindEmail: "new@example.com", RebindMailboxAPI: "https://sunny.example/api/sunny/domain-mail/pickup?email=new%40example.com&token=" + tokenA, MailboxType: "domain", MailboxChannel: "domain_api", AccessKey: "https://sunny.example/api/sunny/domain-mail/pickup?email=new%40example.com&token=" + tokenA, PickupTokenHash: domainMailboxPickupTokenHash(tokenA), Status: "已注册", Enabled: true}
	if err := s.db.Create(&mailboxA).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.db.Create(&mailboxB).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.db.Create(&mailboxC).Error; err != nil {
		t.Fatal(err)
	}

	request := func(email, token string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, "/api/sunny/domain-mail/pickup?"+url.Values{"email": {email}, "token": {token}}.Encode(), nil)
		recorder := httptest.NewRecorder()
		s.serveHTTP(recorder, req)
		return recorder
	}
	if recorder := request(mailboxA.Email, tokenA); recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), "978744") {
		t.Fatalf("valid pickup failed: %d %s", recorder.Code, recorder.Body.String())
	} else {
		var payload map[string]any
		if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
			t.Fatalf("decode public pickup payload: %v", err)
		}
		if _, exists := payload["mailbox_type"]; exists {
			t.Fatalf("public pickup must use the simple items response: %#v", payload)
		}
		items, ok := payload["items"].([]any)
		if !ok || len(items) != 1 {
			t.Fatalf("unexpected public pickup items: %#v", payload["items"])
		}
		item, ok := items[0].(map[string]any)
		if !ok || item["verificationCode"] != "978744" || item["recipient"] != mailboxA.Email || item["receivedAt"] != receivedAt {
			t.Fatalf("unexpected public pickup item: %#v", item)
		}
	}
	if recorder := request(mailboxB.Email, tokenA); recorder.Code != http.StatusForbidden {
		t.Fatalf("cross-mailbox token must be rejected: %d %s", recorder.Code, recorder.Body.String())
	}
	if recorder := request(mailboxA.Email, "wrong-token"); recorder.Code != http.StatusForbidden {
		t.Fatalf("wrong token must be rejected: %d %s", recorder.Code, recorder.Body.String())
	}
	if recorder := request(mailboxC.RebindEmail, tokenA); recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), "978744") {
		t.Fatalf("rebound mailbox pickup failed: %d %s", recorder.Code, recorder.Body.String())
	}
	if err := s.db.Model(&mailboxA).Update("enabled", false).Error; err != nil {
		t.Fatal(err)
	}
	if recorder := request(mailboxA.Email, tokenA); recorder.Code != http.StatusForbidden {
		t.Fatalf("disabled mailbox must be rejected: %d %s", recorder.Code, recorder.Body.String())
	}
	if err := s.db.Model(&mailboxA).Update("enabled", true).Error; err != nil {
		t.Fatal(err)
	}
	s.sunnySaveConfig(sunnyCfgDomainMailbox, map[string]any{
		"enabled": false, "base_url": upstream.URL, "auth_token": "manager-token", "site_password": "site-password", "pickup_base_url": "https://sunny.example", "domain": "example.com",
	})
	if recorder := request(mailboxA.Email, tokenA); recorder.Code != http.StatusForbidden {
		t.Fatalf("disabled pool must reject pickup: %d %s", recorder.Code, recorder.Body.String())
	}
}

func TestSavingDomainMailboxConfigMigratesLegacyGlobalCredential(t *testing.T) {
	s := newSunnySessionTestServer(t)
	legacy := SunnyMailbox{
		Email: "legacy@example.com", MailboxType: "domain", MailboxChannel: "domain_api",
		AccessKey: domainMailboxCredential("https://cloudmail.example", "manager-token"),
		Raw:       sunnyURLAPIRaw("legacy@example.com", domainMailboxCredential("https://cloudmail.example", "manager-token")),
		Status:    "未注册", Enabled: true,
	}
	if err := s.db.Create(&legacy).Error; err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPut, "/api/sunny/domain-mail/config", strings.NewReader(`{
		"enabled":true,"base_url":"https://cloudmail.example","auth_token":"manager-token","site_password":"site-password",
		"pickup_base_url":"https://sunny.example","domain":"example.com"
	}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.handleSunny(recorder, req, "domain-mail/config")
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"migrated_mailboxes":1`) {
		t.Fatalf("config save failed: %d %s", recorder.Code, recorder.Body.String())
	}
	if err := s.db.First(&legacy, legacy.ID).Error; err != nil {
		t.Fatal(err)
	}
	if legacy.PickupTokenHash == "" || !strings.HasPrefix(legacy.AccessKey, "https://sunny.example/api/sunny/domain-mail/pickup?") {
		t.Fatalf("legacy credential was not migrated: %#v", legacy)
	}
	if strings.Contains(legacy.AccessKey, "manager-token") || strings.Contains(legacy.Raw, "manager-token") {
		t.Fatal("migrated mailbox must not retain the CloudMail manager token")
	}
}
