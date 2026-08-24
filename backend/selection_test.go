package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

type filteredSelectionResponse struct {
	IDs   []uint           `json:"ids"`
	Items []map[string]any `json:"items"`
	Total int              `json:"total"`
}

func decodeFilteredSelection(t *testing.T, recorder *httptest.ResponseRecorder) filteredSelectionResponse {
	t.Helper()
	if recorder.Code != http.StatusOK {
		t.Fatalf("selection status = %d, body = %s", recorder.Code, recorder.Body.String())
	}
	var payload filteredSelectionResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode selection response: %v", err)
	}
	return payload
}

func requireSelectionCount(t *testing.T, payload filteredSelectionResponse, want int) {
	t.Helper()
	if payload.Total != want || len(payload.IDs) != want {
		t.Fatalf("selection total = %d, ids = %d, want %d", payload.Total, len(payload.IDs), want)
	}
}

func TestSunnyFilteredSelectionsIgnorePagination(t *testing.T) {
	s := newSunnySessionTestServer(t)
	if err := s.db.AutoMigrate(&SunnyPhone{}); err != nil {
		t.Fatalf("migrate phones: %v", err)
	}

	for index := 0; index < 12; index++ {
		if err := s.db.Create(&SunnyMailbox{Email: fmt.Sprintf("select-mail-%02d@example.com", index), Status: "未注册", Enabled: true}).Error; err != nil {
			t.Fatalf("create mailbox: %v", err)
		}
		if err := s.db.Create(&SunnyPhone{Number: fmt.Sprintf("+1202555%04d", index), Status: "available", Enabled: true, SuccessCount: 1}).Error; err != nil {
			t.Fatalf("create phone: %v", err)
		}
		if err := s.db.Create(&SunnyProxy{Address: fmt.Sprintf("http://select-proxy-%02d.example:8080", index), Country: "JP", Status: "enabled", Enabled: true}).Error; err != nil {
			t.Fatalf("create proxy: %v", err)
		}
		if err := s.db.Create(&SunnySession{Email: fmt.Sprintf("select-session-%02d@example.com", index), CreatedAt: time.Now(), UpdatedAt: time.Now()}).Error; err != nil {
			t.Fatalf("create session: %v", err)
		}
	}
	disabledMailbox := SunnyMailbox{Email: "select-mail-disabled@example.com", Status: "未注册", Enabled: true}
	if err := s.db.Create(&disabledMailbox).Error; err != nil {
		t.Fatalf("create disabled mailbox: %v", err)
	}
	if err := s.db.Model(&disabledMailbox).Update("enabled", false).Error; err != nil {
		t.Fatalf("disable mailbox: %v", err)
	}

	mailboxes := httptest.NewRecorder()
	s.sunnyMailboxes(mailboxes, httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes?selection=all&page=1&page_size=1&q=select-mail-&enabled=true", nil), nil)
	mailboxPayload := decodeFilteredSelection(t, mailboxes)
	requireSelectionCount(t, mailboxPayload, 12)
	if len(mailboxPayload.Items) != 12 || mailboxPayload.Items[0]["email"] == nil {
		t.Fatalf("mailbox selection items must include lightweight email data: %#v", mailboxPayload.Items)
	}
	mailboxPlan := httptest.NewRecorder()
	s.sunnyMailboxes(mailboxPlan, httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes?selection=all&q=session%40example.com&plan_type=plus", nil), nil)
	requireSelectionCount(t, decodeFilteredSelection(t, mailboxPlan), 1)

	phones := httptest.NewRecorder()
	s.sunnyPhones(phones, httptest.NewRequest(http.MethodGet, "/api/sunny/phones?selection=all&page=1&page_size=1&q=%2B1202555&status=enabled&count=1", nil), nil)
	requireSelectionCount(t, decodeFilteredSelection(t, phones), 12)

	proxies := httptest.NewRecorder()
	s.sunnyProxyPool(proxies, httptest.NewRequest(http.MethodGet, "/api/sunny/proxy-config/pool?selection=all&page=1&page_size=1&q=select-proxy-&status=enabled&country=JP", nil), nil)
	requireSelectionCount(t, decodeFilteredSelection(t, proxies), 12)

	sessions := httptest.NewRecorder()
	s.sunnySessions(sessions, httptest.NewRequest(http.MethodGet, "/api/sunny/sessions?selection=all&page=1&page_size=1&q=select-session-", nil), nil)
	requireSelectionCount(t, decodeFilteredSelection(t, sessions), 12)
	sessionPlan := httptest.NewRecorder()
	s.sunnySessions(sessionPlan, httptest.NewRequest(http.MethodGet, "/api/sunny/sessions?selection=all&q=session%40example.com&plan_type=plus", nil), nil)
	requireSelectionCount(t, decodeFilteredSelection(t, sessionPlan), 1)
}

func TestSunnyMailboxSearchMatchesRebindEmailCaseInsensitively(t *testing.T) {
	s := newSunnySessionTestServer(t)
	mailbox := SunnyMailbox{Email: "original-search@example.com", RebindEmail: "Rebound-Search@Example.com", Status: "已注册", Enabled: true}
	if err := s.db.Create(&mailbox).Error; err != nil {
		t.Fatalf("create rebound mailbox: %v", err)
	}
	recorder := httptest.NewRecorder()
	s.sunnyMailboxes(recorder, httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes?q=reBound-search@example.com&page=1&page_size=10", nil), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("rebind search status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Total int `json:"total"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode rebound search response: %v", err)
	}
	if payload.Total != 1 {
		t.Fatalf("rebind search total=%d, want 1", payload.Total)
	}
}

func TestAuditFilteredSelectionIgnoresPagination(t *testing.T) {
	s := newAuditTestServer(t)
	for index := 0; index < 12; index++ {
		if err := s.db.Create(&AuditLog{OccurredAt: time.Now(), Actor: "selection-user", LogType: "operation", Category: "mailbox", Action: "update", Status: "success", Summary: fmt.Sprintf("selection log %d", index)}).Error; err != nil {
			t.Fatalf("create audit log: %v", err)
		}
	}
	if err := s.db.Create(&AuditLog{OccurredAt: time.Now(), Actor: "another-user", Summary: "not selected"}).Error; err != nil {
		t.Fatalf("create unrelated audit log: %v", err)
	}

	recorder := httptest.NewRecorder()
	s.handleAuditList(recorder, httptest.NewRequest(http.MethodGet, "/api/audit/logs?selection=all&page=1&page_size=1&actor=selection-user", nil))
	requireSelectionCount(t, decodeFilteredSelection(t, recorder), 12)
}
