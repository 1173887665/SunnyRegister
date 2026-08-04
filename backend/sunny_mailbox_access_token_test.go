package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func putSunnyMailboxAccessToken(t *testing.T, s *Server, mailboxID uint, accessToken string) {
	t.Helper()
	payload, err := json.Marshal(map[string]any{"access_token": accessToken})
	if err != nil {
		t.Fatalf("marshal mailbox update: %v", err)
	}
	req := httptest.NewRequest(http.MethodPut, fmt.Sprintf("/api/sunny/mailboxes/%d", mailboxID), bytes.NewReader(payload))
	req.Header.Set("Content-Type", "application/json")
	res := httptest.NewRecorder()
	s.handleSunny(res, req, fmt.Sprintf("mailboxes/%d", mailboxID))
	if res.Code != http.StatusOK {
		t.Fatalf("update mailbox access token: status=%d body=%s", res.Code, res.Body.String())
	}
}

func TestSunnyMailboxManualAccessTokenUpdatesExistingLinkedRows(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var mailbox SunnyMailbox
	if err := s.db.Where("email = ?", "session@example.com").First(&mailbox).Error; err != nil {
		t.Fatalf("load mailbox: %v", err)
	}
	if err := s.db.Model(&SunnySession{}).Where("email = ?", mailbox.Email).Updates(map[string]any{
		"access_token_status": "invalid", "access_token_error": "stale token",
	}).Error; err != nil {
		t.Fatalf("prepare stale token state: %v", err)
	}

	putSunnyMailboxAccessToken(t, s, mailbox.ID, "manual-access-token")

	var account SunnyAccount
	if err := s.db.Where("email = ?", mailbox.Email).First(&account).Error; err != nil {
		t.Fatalf("load account: %v", err)
	}
	if account.AccessToken != "manual-access-token" {
		t.Fatalf("account access token=%q", account.AccessToken)
	}
	var session SunnySession
	if err := s.db.Where("email = ?", mailbox.Email).First(&session).Error; err != nil {
		t.Fatalf("load session: %v", err)
	}
	if session.AccessToken != "manual-access-token" {
		t.Fatalf("session access token=%q", session.AccessToken)
	}
	if session.AccessTokenStatus != "unknown" || session.AccessTokenError != "" {
		t.Fatalf("manual token status=%q error=%q", session.AccessTokenStatus, session.AccessTokenError)
	}
}

func TestSunnyMailboxManualAccessTokenCreatesLinkedRowsWithoutLengthLimit(t *testing.T) {
	s := newSunnySessionTestServer(t)
	mailbox := SunnyMailbox{
		Email: "manual-token@icloud.com", MailboxType: "apple", MailboxChannel: "url_api",
		AccessKey: "https://mail.example.com/messages/token", Raw: "manual-token@icloud.com----https://mail.example.com/messages/token",
		AccountType: "plus", Status: "已注册", Enabled: true,
	}
	if err := s.db.Create(&mailbox).Error; err != nil {
		t.Fatalf("create mailbox: %v", err)
	}
	longToken := "eyJ" + strings.Repeat("manual-access-token-segment", 2000)

	putSunnyMailboxAccessToken(t, s, mailbox.ID, longToken)

	var account SunnyAccount
	if err := s.db.Where("email = ?", mailbox.Email).First(&account).Error; err != nil {
		t.Fatalf("manual token account was not created: %v", err)
	}
	if account.AccessToken != longToken {
		t.Fatalf("account access token length=%d, want=%d", len(account.AccessToken), len(longToken))
	}
	var session SunnySession
	if err := s.db.Where("email = ?", mailbox.Email).First(&session).Error; err != nil {
		t.Fatalf("manual token session was not created: %v", err)
	}
	if session.AccessToken != longToken || session.AccountID != account.ID {
		t.Fatalf("session token length=%d account_id=%d, want length=%d account_id=%d", len(session.AccessToken), session.AccountID, len(longToken), account.ID)
	}

	listReq := httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes?summary=true&page=1&page_size=100", nil)
	listRes := httptest.NewRecorder()
	s.handleSunny(listRes, listReq, "mailboxes")
	if listRes.Code != http.StatusOK {
		t.Fatalf("list mailboxes: status=%d body=%s", listRes.Code, listRes.Body.String())
	}
	var listPayload struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(listRes.Body.Bytes(), &listPayload); err != nil {
		t.Fatalf("decode mailbox list: %v", err)
	}
	found := false
	for _, item := range listPayload.Items {
		if item["email"] == mailbox.Email {
			found = true
			if item["has_access_token"] != true {
				t.Fatalf("mailbox list has_access_token=%v", item["has_access_token"])
			}
		}
	}
	if !found {
		t.Fatalf("manual token mailbox missing from list")
	}

	fieldReq := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/sunny/mailboxes/%d/field?name=access_token", mailbox.ID), nil)
	fieldRes := httptest.NewRecorder()
	s.handleSunny(fieldRes, fieldReq, fmt.Sprintf("mailboxes/%d/field", mailbox.ID))
	if fieldRes.Code != http.StatusOK {
		t.Fatalf("read mailbox field: status=%d body=%s", fieldRes.Code, fieldRes.Body.String())
	}
	var fieldPayload map[string]any
	if err := json.Unmarshal(fieldRes.Body.Bytes(), &fieldPayload); err != nil {
		t.Fatalf("decode mailbox field: %v", err)
	}
	if fieldPayload["value"] != longToken {
		t.Fatalf("field endpoint token length=%d, want=%d", len(fmt.Sprint(fieldPayload["value"])), len(longToken))
	}
}
