package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSunnyPhoneRegisterCreatesRegisterOnlyTask(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var mailbox SunnyMailbox
	if err := s.db.Where("email = ?", "session@example.com").First(&mailbox).Error; err != nil {
		t.Fatalf("load mailbox: %v", err)
	}
	s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), map[string]any{
		"luban_enabled": true, "luban_api_key": "test-key", "luban_service_id": "openai",
	}))
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/phone-register", strings.NewReader(`{"mailbox_ids":[1],"count":2,"sms_provider":"luban","sms_country":"","registration_stage":"codex_phone_bind"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnyTasks(rec, req, []string{"phone-register"})
	if rec.Code != http.StatusOK {
		t.Fatalf("phone register status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode task response: %v", err)
	}
	if response["id"] == nil && response["task_id"] == nil {
		t.Fatalf("task response has no id: %#v", response)
	}
	var task Task
	if err := s.db.Order("created_at desc").First(&task).Error; err != nil {
		t.Fatalf("load created task: %v", err)
	}
	if task.Type != "sunny_phone_register" {
		t.Fatalf("task type = %q, want sunny_phone_register", task.Type)
	}
	payload := jsonMap(task.PayloadJSON)
	if got := uintSlice(payload["mailbox_ids"]); len(got) != 0 {
		t.Fatalf("task mailbox_ids = %#v, want empty for phone registration", got)
	}
	if got := text(payload["registration_stage"]); got != "register_only" {
		t.Fatalf("phone registration stage = %q, want register_only", got)
	}
	if got := text(payload["identity"]); got != "phone" {
		t.Fatalf("phone registration identity = %q, want phone", got)
	}
	if got := intValue(payload["count"], 0); got != 2 {
		t.Fatalf("phone registration count = %d, want 2", got)
	}
}

func TestValidateImportedRebindMailboxAcceptsMicrosoftOAuthCredential(t *testing.T) {
	err := validateImportedRebindMailbox(
		"mail-password----11111111-1111-1111-1111-111111111111----refresh-token",
		"target@example.com",
		"microsoft",
		"outlook",
	)
	if err != nil {
		t.Fatalf("expected Microsoft target credential to validate: %v", err)
	}
}

func TestValidateImportedRebindMailboxRejectsIncompleteMicrosoftOAuthCredential(t *testing.T) {
	if err := validateImportedRebindMailbox("mail-password----client-id", "target@example.com", "microsoft", "outlook"); err == nil {
		t.Fatal("expected incomplete Microsoft target credential to be rejected")
	}
}

func TestSunnyPhoneRegisterAllocatesMailboxWhenSelectionIsEmpty(t *testing.T) {
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), map[string]any{
		"luban_enabled": true, "luban_api_key": "test-key", "luban_service_id": "openai",
	}))
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/phone-register", strings.NewReader(`{"count":1,"sms_provider":"luban","sms_country":""}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnyTasks(rec, req, []string{"phone-register"})
	if rec.Code != http.StatusOK {
		t.Fatalf("phone register without explicit selection status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var task Task
	if err := s.db.Order("created_at desc").First(&task).Error; err != nil {
		t.Fatalf("load created task: %v", err)
	}
	payload := jsonMap(task.PayloadJSON)
	if got := intValue(payload["count"], 0); got != 1 {
		t.Fatalf("allocated phone register count = %d, want 1", got)
	}
}

func TestSunnyPhoneRegisterDoesNotRequireMailboxPoolSwitch(t *testing.T) {
	s := newSunnySessionTestServer(t)
	s.sunnySaveConfig(sunnyCfgMailbox, mergeConfig(defaultMailboxConfig(), map[string]any{"pool_enabled": false}))
	s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), map[string]any{
		"luban_enabled": true, "luban_api_key": "test-key", "luban_service_id": "openai",
	}))
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/phone-register", strings.NewReader(`{"count":1,"sms_provider":"luban"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnyTasks(rec, req, []string{"phone-register"})
	if rec.Code != http.StatusOK {
		t.Fatalf("phone register with mailbox pool switch off status = %d, body = %s", rec.Code, rec.Body.String())
	}
}
