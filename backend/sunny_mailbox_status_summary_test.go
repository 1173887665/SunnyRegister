package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSunnyMailboxSummaryReturnsGlobalStatusCounts(t *testing.T) {
	s := newSunnySessionTestServer(t)
	for index, status := range []string{"未注册", "已接码", "已反代", "已封禁", "需二验", "failed"} {
		mailbox := SunnyMailbox{Email: string(rune('a'+index)) + "@summary.example", Status: status, Enabled: true}
		if err := s.db.Create(&mailbox).Error; err != nil {
			t.Fatalf("create %s mailbox: %v", status, err)
		}
	}
	refreshingMailbox := SunnyMailbox{Email: "refreshing@summary.example", Status: "\u767b\u5f55\u5237\u65b0", Enabled: true}
	if err := s.db.Create(&refreshingMailbox).Error; err != nil {
		t.Fatalf("create login refreshing mailbox: %v", err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes?summary=true&status=已注册&page=1&page_size=10", nil)
	rec := httptest.NewRecorder()
	s.sunnyMailboxes(rec, req, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("list status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var payload struct {
		Total        int64            `json:"total"`
		MailboxTotal int64            `json:"mailbox_total"`
		StatusCounts map[string]int64 `json:"status_counts"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if payload.Total != 1 || payload.MailboxTotal != 8 {
		t.Fatalf("filtered total = %d, mailbox total = %d", payload.Total, payload.MailboxTotal)
	}
	for _, status := range sunnyMailboxStatuses {
		if payload.StatusCounts[status] != 1 {
			t.Fatalf("status %s count = %d, want 1", status, payload.StatusCounts[status])
		}
	}

	failedReq := httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes?summary=true&status=失败&page=1&page_size=10", nil)
	failedRec := httptest.NewRecorder()
	s.sunnyMailboxes(failedRec, failedReq, nil)
	var failedPayload struct {
		Total int64 `json:"total"`
	}
	if err := json.Unmarshal(failedRec.Body.Bytes(), &failedPayload); err != nil {
		t.Fatalf("decode failed filter response: %v", err)
	}
	if failedPayload.Total != 1 {
		t.Fatalf("failed alias filter total = %d, want 1", failedPayload.Total)
	}
}

func TestSunnyMailboxSummaryKeepsSecretKeyAvailability(t *testing.T) {
	s := newSunnySessionTestServer(t)
	credential := "https://mail.example/api/sunny/domain-mail/pickup?email=summary-sk%40example.com&token=dmsk_summary"
	mailbox := SunnyMailbox{
		Email: "summary-sk@example.com", MailboxType: "domain", MailboxChannel: "domain_api",
		AccessKey: credential, Raw: "summary-sk@example.com----" + credential, Status: "已注册", Enabled: true,
	}
	if err := s.db.Create(&mailbox).Error; err != nil {
		t.Fatalf("create mailbox: %v", err)
	}
	req := httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes?summary=true&page=1&page_size=10&q=summary-sk", nil)
	rec := httptest.NewRecorder()
	s.sunnyMailboxes(rec, req, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("list status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var payload struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(payload.Items) != 1 || payload.Items[0]["has_secret_key"] != true {
		t.Fatalf("summary SK availability = %#v", payload.Items)
	}
	if _, exists := payload.Items[0]["access_key"]; exists {
		t.Fatal("summary response must not expose access_key")
	}
}
