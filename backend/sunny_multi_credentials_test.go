package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestSunnyURLAPIReimportUpdatesCredentialsAndPreservesLifecycle(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var mailbox SunnyMailbox
	if err := s.db.Where("email = ?", "session@example.com").First(&mailbox).Error; err != nil {
		t.Fatalf("load mailbox: %v", err)
	}
	registeredAt := time.Now().Add(-48 * time.Hour).Truncate(time.Second)
	trialCheckedAt := time.Now().Add(-24 * time.Hour).Truncate(time.Second)
	if err := s.db.Model(&mailbox).Updates(map[string]any{
		"status": "已注册", "registered_at": registeredAt, "trial_eligibility": "eligible", "trial_checked_at": trialCheckedAt,
	}).Error; err != nil {
		t.Fatalf("prepare mailbox lifecycle: %v", err)
	}
	const secret = "JBSWY3DPEHPK3PXP"
	body := map[string]any{
		"mailbox_type": "apple", "mailbox_channel": "url_api",
		"lines": "session@example.com----new-chat-password----https://mail.example.test/latest----" + secret,
	}
	raw, _ := json.Marshal(body)
	recorder := httptest.NewRecorder()
	s.sunnyImportMailboxes(recorder, httptest.NewRequest(http.MethodPost, "/sunny/mailboxes/import", bytes.NewReader(raw)))
	if recorder.Code != http.StatusOK {
		t.Fatalf("import status=%d body=%s", recorder.Code, recorder.Body.String())
	}

	var updated SunnyMailbox
	if err := s.db.First(&updated, mailbox.ID).Error; err != nil {
		t.Fatalf("reload mailbox: %v", err)
	}
	if updated.MailboxType != "apple" || updated.MailboxChannel != "url_api" || updated.ChatGPTPassword != "new-chat-password" || updated.AccessKey != "https://mail.example.test/latest" || updated.TOTPSecret != secret {
		t.Fatalf("credentials not updated: %#v", updated)
	}
	if updated.Status != "已注册" || updated.TrialEligibility != "eligible" || !updated.RegisteredAt.Valid || updated.RegisteredAt.Time.Unix() != registeredAt.Unix() || updated.TrialCheckedAt == nil || updated.TrialCheckedAt.Unix() != trialCheckedAt.Unix() {
		t.Fatalf("lifecycle fields changed unexpectedly: %#v", updated)
	}
	var session SunnySession
	if err := s.db.Where("email = ?", updated.Email).First(&session).Error; err != nil || session.AccessToken != "session-access-token" || session.RefreshToken != "session-refresh-token" {
		t.Fatalf("session credentials were not preserved: %#v err=%v", session, err)
	}

	summary := serializeSunnyMailboxList(updated, s.sunnyGroupMap(), "plus", "access-token", 1, "eligible", true)
	if summary["has_chatgpt_password"] != true || summary["has_totp_secret"] != true {
		t.Fatalf("credential flags missing: %#v", summary)
	}
	for _, key := range []string{"password", "chatgpt_password", "totp_secret", "client_id", "refresh_token", "access_key", "raw"} {
		if _, exists := summary[key]; exists {
			t.Fatalf("summary leaked sensitive field %q", key)
		}
	}
	if got := sunnyMailboxCredentialLine(updated); got != body["lines"] {
		t.Fatalf("canonical export=%q want=%q", got, body["lines"])
	}

	listRecorder := httptest.NewRecorder()
	s.sunnyMailboxes(listRecorder, httptest.NewRequest(http.MethodGet, "/sunny/mailboxes?summary=true&page=1&page_size=10", nil), nil)
	if listRecorder.Code != http.StatusOK {
		t.Fatalf("summary list status=%d body=%s", listRecorder.Code, listRecorder.Body.String())
	}
	var listPayload struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(listRecorder.Body.Bytes(), &listPayload); err != nil || len(listPayload.Items) != 1 {
		t.Fatalf("decode summary list: items=%d err=%v body=%s", len(listPayload.Items), err, listRecorder.Body.String())
	}
	listed := listPayload.Items[0]
	if listed["has_chatgpt_password"] != true || listed["has_totp_secret"] != true || listed["has_login_secret"] != true {
		t.Fatalf("summary endpoint omitted credential flags: %#v", listed)
	}
	if listed["chatgpt_password_preview"] != "new-••••••" || listed["totp_secret_preview"] != "JBSW••••••" {
		t.Fatalf("summary endpoint returned unexpected previews: %#v", listed)
	}
	for _, key := range []string{"chatgpt_password", "totp_secret"} {
		if _, exists := listed[key]; exists {
			t.Fatalf("summary endpoint leaked sensitive field %q", key)
		}
	}
}

func TestSunnyMailboxCredentialEditRequiresExplicitClear(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var mailbox SunnyMailbox
	if err := s.db.Where("email = ?", "session@example.com").First(&mailbox).Error; err != nil {
		t.Fatalf("load mailbox: %v", err)
	}
	if err := s.db.Model(&mailbox).Updates(map[string]any{
		"mailbox_type": "apple", "mailbox_channel": "url_api", "password": "", "client_id": "", "refresh_token": "",
		"chat_gpt_password": "chat-password", "totp_secret": "JBSWY3DPEHPK3PXP", "raw": "session@example.com----chat-password----JBSWY3DPEHPK3PXP",
	}).Error; err != nil {
		t.Fatalf("prepare credentials: %v", err)
	}
	update := func(payload map[string]any) *httptest.ResponseRecorder {
		raw, _ := json.Marshal(payload)
		recorder := httptest.NewRecorder()
		s.sunnyMailboxes(recorder, httptest.NewRequest(http.MethodPut, "/sunny/mailboxes", bytes.NewReader(raw)), []string{strings.TrimSpace(text(mailbox.ID))})
		return recorder
	}
	if recorder := update(map[string]any{"chatgpt_password": "", "totp_secret": ""}); recorder.Code != http.StatusOK {
		t.Fatalf("empty edit status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if err := s.db.First(&mailbox, mailbox.ID).Error; err != nil || mailbox.ChatGPTPassword != "chat-password" || mailbox.TOTPSecret != "JBSWY3DPEHPK3PXP" {
		t.Fatalf("empty edit changed credentials: %#v err=%v", mailbox, err)
	}
	if recorder := update(map[string]any{"clear_chatgpt_password": true, "clear_totp_secret": true}); recorder.Code != http.StatusOK {
		t.Fatalf("clear edit status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	if err := s.db.First(&mailbox, mailbox.ID).Error; err != nil || mailbox.ChatGPTPassword != "" || mailbox.TOTPSecret != "" {
		t.Fatalf("explicit clear failed: %#v err=%v", mailbox, err)
	}
}

func TestSunnySub2APIOptionsAndBatchImport(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var batchCalls atomic.Int32
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/v1/admin/groups/all":
			writeJSON(w, http.StatusOK, []map[string]any{{"id": 7, "name": "OpenAI"}})
		case "/api/v1/admin/proxies/all":
			writeJSON(w, http.StatusOK, []map[string]any{{"id": 9, "name": "Remote"}})
		case "/api/v1/admin/accounts/batch":
			if r.Header.Get("Idempotency-Key") == "" {
				t.Error("missing idempotency key")
			}
			if batchCalls.Add(1) == 1 {
				writeJSON(w, http.StatusServiceUnavailable, map[string]any{"message": "retry"})
				return
			}
			var payload map[string]any
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Fatalf("decode batch payload: %v", err)
			}
			accounts, _ := payload["accounts"].([]any)
			if len(accounts) != 1 {
				t.Fatalf("batch accounts=%d", len(accounts))
			}
			account, _ := accounts[0].(map[string]any)
			credentials, _ := account["credentials"].(map[string]any)
			mapping, _ := credentials["model_mapping"].(map[string]any)
			if account["notes"] != "邮箱凭证：session@example.com----mailbox-password----client-id----mailbox-refresh-token" || intValue(account["proxy_id"], 0) != 9 || intValue(account["load_factor"], 0) != 80 || mapping["gpt-5.6-sol"] != "gpt-5.6-sol" {
				t.Fatalf("unexpected account payload: %#v", account)
			}
			writeJSON(w, http.StatusOK, map[string]any{
				"success": 0,
				"failed":  0,
				"results": []any{map[string]any{
					"status":  "created",
					"account": map[string]any{"id": "remote-17", "email": "session@example.com"},
				}},
			})
		default:
			http.NotFound(w, r)
		}
	}))
	defer remote.Close()
	s.sunnySaveConfig(sunnyCfgSub2API, mergeConfig(defaultSub2APIConfig(), map[string]any{
		"base_url": remote.URL, "admin_token": "admin-key", "group_ids": []any{7}, "proxy_id": 9, "load_factor": 80,
		"model_whitelist": []any{"gpt-5.6-sol"},
	}))

	optionsRecorder := httptest.NewRecorder()
	s.sunnySub2API(optionsRecorder, httptest.NewRequest(http.MethodGet, "/sunny/sub2api/options", nil), []string{"options"})
	if optionsRecorder.Code != http.StatusOK || !strings.Contains(optionsRecorder.Body.String(), "OpenAI") || !strings.Contains(optionsRecorder.Body.String(), "Remote") {
		t.Fatalf("options status=%d body=%s", optionsRecorder.Code, optionsRecorder.Body.String())
	}

	var session SunnySession
	if err := s.db.Where("email = ?", "session@example.com").First(&session).Error; err != nil {
		t.Fatalf("load session: %v", err)
	}
	raw, _ := json.Marshal(map[string]any{"session_ids": []uint{session.ID}})
	importRecorder := httptest.NewRecorder()
	s.sunnySub2API(importRecorder, httptest.NewRequest(http.MethodPost, "/sunny/sub2api/import", bytes.NewReader(raw)), []string{"import"})
	if importRecorder.Code != http.StatusOK || batchCalls.Load() != 2 {
		t.Fatalf("import status=%d calls=%d body=%s", importRecorder.Code, batchCalls.Load(), importRecorder.Body.String())
	}
	var account SunnyAccount
	if err := s.db.Where("email = ?", session.Email).First(&account).Error; err != nil || account.Sub2APIStatus != "imported" || account.Sub2APIID != "remote-17" {
		t.Fatalf("account import status=%q remote_id=%q err=%v", account.Sub2APIStatus, account.Sub2APIID, err)
	}
}

func TestSub2APIGenericPayloadUsesMatchingMailboxSecretKey(t *testing.T) {
	s := newSunnySessionTestServer(t)
	payload := s.sub2APIAccountPayload(
		AccountRecord{ID: 12, Email: "session@example.com"},
		"openai",
		7,
		map[string]any{"notes": "legacy-shared-note"},
	)
	if payload["notes"] != "邮箱凭证：session@example.com----mailbox-password----client-id----mailbox-refresh-token" {
		t.Fatalf("generic sub2api notes = %q", payload["notes"])
	}
	preview := maskSub2APIPayload(map[string]any{"accounts": []any{payload}})
	previewAccount := preview["accounts"].([]any)[0].(map[string]any)
	if previewAccount["notes"] == payload["notes"] {
		t.Fatal("sub2api preview exposed the complete mailbox secret key")
	}

	legacyPayload := s.sub2APIAccountPayload(
		AccountRecord{
			ID:    13,
			Email: "legacy@example.com",
			ProviderAccounts: []map[string]any{{
				"provider_type":    "mailbox",
				"login_identifier": "legacy@example.com",
				"credentials": map[string]any{
					"password": "mail-password", "client_id": "mail-client", "refresh_token": "mail-refresh",
				},
			}},
		},
		"openai",
		7,
		map[string]any{},
	)
	if legacyPayload["notes"] != "邮箱凭证：legacy@example.com----mail-password----mail-client----mail-refresh" {
		t.Fatalf("legacy sub2api notes = %q", legacyPayload["notes"])
	}
}

func TestSunnySub2NotesIncludesLoginSecretLine(t *testing.T) {
	mailbox := SunnyMailbox{Email: "ls@example.com", ChatGPTPassword: "ChatGPT-pass", TOTPSecret: "JBSWY3DPEHPK3PXP"}
	if got := sunnySub2Notes(mailbox, "sk@example.com----mail----client----refresh"); got != "邮箱凭证：sk@example.com----mail----client----refresh\n密码2FA：ls@example.com----ChatGPT-pass----JBSWY3DPEHPK3PXP" {
		t.Fatalf("unexpected combined sub2api notes: %q", got)
	}
	if got := sunnySub2Notes(mailbox, ""); got != "密码2FA：ls@example.com----ChatGPT-pass----JBSWY3DPEHPK3PXP" {
		t.Fatalf("unexpected LS-only sub2api notes: %q", got)
	}
}

func TestSunnyLubanSMSConfigValidation(t *testing.T) {
	s := newSunnySMSOptionsTestServer(t)
	for _, body := range []string{
		`{"luban_api_key":"","luban_service_id":"openai"}`,
		`{"luban_api_key":"key","luban_service_id":"invalid service"}`,
	} {
		recorder := httptest.NewRecorder()
		s.sunnyCheckLubanSMS(recorder, httptest.NewRequest(http.MethodPost, "/sunny/phones/luban/check", strings.NewReader(body)))
		if recorder.Code != http.StatusBadRequest {
			t.Fatalf("expected invalid config to fail: status=%d body=%s", recorder.Code, recorder.Body.String())
		}
	}
}
