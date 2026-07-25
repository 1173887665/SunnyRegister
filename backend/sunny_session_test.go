package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func newSunnySessionTestServer(t *testing.T) *Server {
	t.Helper()
	dsn := "file:" + strings.ReplaceAll(t.Name(), "/", "-") + "?mode=memory&cache=shared"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatalf("open test database: %v", err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatalf("get test database: %v", err)
	}
	sqlDB.SetMaxOpenConns(1)
	if err := db.AutoMigrate(&SunnyMailboxGroup{}, &SunnyMailbox{}, &SunnyAccount{}, &SunnySession{}, &Task{}, &TaskEvent{}, &SunnyKVConfig{}, &SunnyProxy{}); err != nil {
		t.Fatalf("migrate test database: %v", err)
	}
	now := time.Now()
	mailbox := SunnyMailbox{
		Email: "session@example.com", Password: "mailbox-password", ClientID: "client-id",
		RefreshToken: "mailbox-refresh-token", Raw: "session@example.com----mailbox-password----client-id----mailbox-refresh-token",
		AccountType: "plus", Status: "已注册", Enabled: true, CreatedAt: now, UpdatedAt: now,
	}
	if err := db.Create(&mailbox).Error; err != nil {
		t.Fatalf("create mailbox: %v", err)
	}
	account := SunnyAccount{
		MailboxID: mailbox.ID, Email: mailbox.Email, Status: "registered", AccountType: "plus",
		AccessToken: "account-access-token", OpenAIRT: "account-refresh-token", CreatedAt: now, UpdatedAt: now,
	}
	if err := db.Create(&account).Error; err != nil {
		t.Fatalf("create account: %v", err)
	}
	if err := db.Create(&SunnySession{
		AccountID: account.ID, Email: mailbox.Email, AccessToken: "session-access-token", RefreshToken: "session-refresh-token",
		SessionJSON: `{"accessToken":"session-access-token"}`, RawMailboxLine: mailbox.Raw, CreatedAt: now, UpdatedAt: now,
	}).Error; err != nil {
		t.Fatalf("create session: %v", err)
	}
	return &Server{db: db}
}

func TestSunnySessionListDoesNotReturnSecrets(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodGet, "/api/sunny/sessions?page=1&page_size=10", nil)
	rec := httptest.NewRecorder()
	s.sunnySessions(rec, req, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("list status = %d, body = %s", rec.Code, rec.Body.String())
	}
	body := rec.Body.String()
	for _, secret := range []string{"session-access-token", "session-refresh-token", "mailbox-password", "client-id"} {
		if strings.Contains(body, secret) {
			t.Fatalf("session list returned secret %q: %s", secret, body)
		}
	}
	var payload struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	if len(payload.Items) != 1 {
		t.Fatalf("list item count = %d", len(payload.Items))
	}
	item := payload.Items[0]
	if item["has_access_token"] != true || item["has_refresh_token"] != true || item["has_secret_key"] != true {
		t.Fatalf("secret presence flags are incorrect: %#v", item)
	}
	if item["plan_type"] != "plus" || item["email"] != "session@example.com" {
		t.Fatalf("summary fields are incorrect: %#v", item)
	}
}

func TestSunnySessionFieldIsLoadedOnDemand(t *testing.T) {
	s := newSunnySessionTestServer(t)
	for _, test := range []struct {
		field string
		want  string
	}{
		{field: "access_token", want: "session-access-token"},
		{field: "refresh_token", want: "session-refresh-token"},
		{field: "secret_key", want: "session@example.com----mailbox-password----client-id----mailbox-refresh-token"},
	} {
		req := httptest.NewRequest(http.MethodGet, "/api/sunny/sessions/1/field?name="+test.field, nil)
		rec := httptest.NewRecorder()
		s.sunnySessions(rec, req, []string{"1", "field"})
		if rec.Code != http.StatusOK {
			t.Fatalf("field %s status = %d, body = %s", test.field, rec.Code, rec.Body.String())
		}
		var payload map[string]string
		if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
			t.Fatalf("decode field %s: %v", test.field, err)
		}
		if payload["value"] != test.want {
			t.Fatalf("field %s = %q, want %q", test.field, payload["value"], test.want)
		}
		if rec.Header().Get("Cache-Control") != "no-store" {
			t.Fatalf("field %s response is cacheable", test.field)
		}
	}
}

func TestSunnyHealthBanMarkers(t *testing.T) {
	for _, title := range []string{
		"Access Deactivated",
		"Your account [C-75ROCz5moZsB] has been deactivated",
		"[c-Ab12CD34] verification notice",
	} {
		if !sunnyHealthBanMarker.MatchString(title) {
			t.Fatalf("title %q was not recognized as banned", title)
		}
	}
	for _, title := range []string{"Welcome to ChatGPT", "Access restored"} {
		if sunnyHealthBanMarker.MatchString(title) {
			t.Fatalf("title %q was incorrectly recognized as banned", title)
		}
	}
}

func TestSunnyScheduledHealthCandidatesIncludeRegisteredMailboxesAcrossGroups(t *testing.T) {
	s := newSunnySessionTestServer(t)
	groupA := SunnyMailboxGroup{Name: "分组 A"}
	groupB := SunnyMailboxGroup{Name: "分组 B"}
	s.db.Create(&groupA)
	s.db.Create(&groupB)

	rows := []SunnyMailbox{
		{GroupID: groupA.ID, Email: "group-a@example.com", ClientID: "client-a", RefreshToken: "refresh-a", Status: "已注册", Enabled: true},
		{GroupID: groupB.ID, Email: "group-b@example.com", ClientID: "client-b", RefreshToken: "refresh-b", Status: "已接码", Enabled: true},
		{GroupID: groupB.ID, Email: "unused@example.com", ClientID: "client-u", RefreshToken: "refresh-u", Status: "未注册", Enabled: true},
		{GroupID: groupB.ID, Email: "banned@example.com", ClientID: "client-x", RefreshToken: "refresh-x", Status: "已封禁", Enabled: true},
	}
	for index := range rows {
		if err := s.db.Create(&rows[index]).Error; err != nil {
			t.Fatalf("create mailbox: %v", err)
		}
	}

	candidates, skipped, err := s.sunnyHealthCandidates(nil, true)
	if err != nil {
		t.Fatalf("scheduled candidates: %v", err)
	}
	found := map[string]bool{}
	for _, candidate := range candidates {
		found[candidate.Email] = true
	}
	if !found["group-a@example.com"] || !found["group-b@example.com"] {
		t.Fatalf("registered mailboxes in non-default groups were omitted: %#v", candidates)
	}
	if found["unused@example.com"] || found["banned@example.com"] {
		t.Fatalf("ineligible mailbox was scheduled: %#v", candidates)
	}
	if skipped < 1 {
		t.Fatalf("banned account should be reported as skipped")
	}
}

func TestSunnyHealthBatchSizeBounds(t *testing.T) {
	s := &Server{}
	t.Setenv("SUNNY_HEALTHCHECK_BATCH_SIZE", "100")
	if got := s.sunnyHealthCheckBatchSize(); got != 100 {
		t.Fatalf("batch size = %d, want 100", got)
	}
	t.Setenv("SUNNY_HEALTHCHECK_BATCH_SIZE", "1")
	if got := s.sunnyHealthCheckBatchSize(); got != 10 {
		t.Fatalf("minimum batch size = %d, want 10", got)
	}
	t.Setenv("SUNNY_HEALTHCHECK_BATCH_SIZE", "999")
	if got := s.sunnyHealthCheckBatchSize(); got != 500 {
		t.Fatalf("maximum batch size = %d, want 500", got)
	}
}

func TestSunnyRefreshTaskRejectsEmptySelection(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/tasks/refresh-session", strings.NewReader(`{"session_ids":[]}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnyTasks(rec, req, []string{"refresh-session"})
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("empty refresh selection status = %d, body = %s", rec.Code, rec.Body.String())
	}
}

func TestExtractSunnyHeaderReadsSubjectOnly(t *testing.T) {
	headerText := "Subject: Access Deactivated\r\nDate: Tue, 21 Jul 2026 06:00:00 +0800\r\n\r\n"
	raw := "* 5 FETCH (BODY[HEADER.FIELDS (SUBJECT DATE)] {" + strconv.Itoa(len(headerText)) + "}\r\n" + headerText + ")\r\nF1 OK FETCH completed\r\n"
	header, ok := extractSunnyHeader(raw, 5, "F1")
	if !ok {
		t.Fatalf("header was not parsed")
	}
	if header.Subject != "Access Deactivated" || header.Date.IsZero() {
		t.Fatalf("unexpected parsed header: %#v", header)
	}
}

func TestSunnyHealthTaskMarksAccountBanned(t *testing.T) {
	s := newSunnySessionTestServer(t)
	previousFetch := sunnyFetchOutlookMailSubjects
	sunnyFetchOutlookMailSubjects = func(email, clientID, refreshToken string, limit int, proxyURL string) ([]string, error) {
		if email != "session@example.com" || limit != 5 {
			t.Fatalf("unexpected health query: email=%s limit=%d", email, limit)
		}
		return []string{"Account notice [C-75ROCz5moZsB]"}, nil
	}
	defer func() { sunnyFetchOutlookMailSubjects = previousFetch }()

	var session SunnySession
	if err := s.db.Where("email = ?", "session@example.com").First(&session).Error; err != nil {
		t.Fatalf("load session: %v", err)
	}
	task := s.createTask(sunnyHealthTaskType, "sunny", map[string]any{"session_ids": []uint{session.ID}}, 1)
	s.executeSunnyAccountHealthCheckTask(&task, map[string]any{"session_ids": []uint{session.ID}})

	var mailbox SunnyMailbox
	var account SunnyAccount
	if err := s.db.Where("email = ?", session.Email).First(&mailbox).Error; err != nil {
		t.Fatalf("load mailbox: %v", err)
	}
	if err := s.db.Where("email = ?", session.Email).First(&account).Error; err != nil {
		t.Fatalf("load account: %v", err)
	}
	if mailbox.Status != "已封禁" || account.Status != "已封禁" {
		t.Fatalf("banned status not synchronized: mailbox=%q account=%q", mailbox.Status, account.Status)
	}
	if mailbox.LastHealthCheckedAt == nil || account.LastHealthCheckedAt == nil {
		t.Fatalf("last health timestamps were not persisted")
	}
	if mailbox.StatusChangedAt == nil || account.StatusChangedAt == nil {
		t.Fatalf("status change timestamps were not persisted")
	}
	if err := s.db.First(&task, "id = ?", task.ID).Error; err != nil {
		t.Fatalf("reload task: %v", err)
	}
	result := jsonMap(task.ResultJSON)
	if task.Status != TaskSucceeded || intValue(result["banned"], 0) != 1 || intValue(result["alive"], 0) != 0 {
		t.Fatalf("unexpected health task result: status=%s result=%#v", task.Status, result)
	}
}

func TestSunnyHealthTaskAliveDoesNotChangeEditOrStatusTime(t *testing.T) {
	s := newSunnySessionTestServer(t)
	previousFetch := sunnyFetchOutlookMailSubjects
	sunnyFetchOutlookMailSubjects = func(email, clientID, refreshToken string, limit int, proxyURL string) ([]string, error) {
		return []string{"Your weekly account update"}, nil
	}
	defer func() { sunnyFetchOutlookMailSubjects = previousFetch }()

	var session SunnySession
	var beforeMailbox SunnyMailbox
	var beforeAccount SunnyAccount
	s.db.Where("email = ?", "session@example.com").First(&session)
	s.db.Where("email = ?", session.Email).First(&beforeMailbox)
	s.db.Where("email = ?", session.Email).First(&beforeAccount)
	statusTime := beforeMailbox.UpdatedAt.Add(-time.Hour)
	s.db.Model(&SunnyMailbox{}).Where("id = ?", beforeMailbox.ID).UpdateColumn("status_changed_at", statusTime)
	s.db.Model(&SunnyAccount{}).Where("id = ?", beforeAccount.ID).UpdateColumn("status_changed_at", statusTime)

	task := s.createTask(sunnyHealthTaskType, "sunny", map[string]any{"session_ids": []uint{session.ID}}, 1)
	s.executeSunnyAccountHealthCheckTask(&task, map[string]any{"session_ids": []uint{session.ID}})

	var afterMailbox SunnyMailbox
	var afterAccount SunnyAccount
	s.db.First(&afterMailbox, beforeMailbox.ID)
	s.db.First(&afterAccount, beforeAccount.ID)
	if !afterMailbox.UpdatedAt.Equal(beforeMailbox.UpdatedAt) || !afterAccount.UpdatedAt.Equal(beforeAccount.UpdatedAt) {
		t.Fatalf("alive health check changed edit time: mailbox=%v/%v account=%v/%v", beforeMailbox.UpdatedAt, afterMailbox.UpdatedAt, beforeAccount.UpdatedAt, afterAccount.UpdatedAt)
	}
	if afterMailbox.StatusChangedAt == nil || !afterMailbox.StatusChangedAt.Equal(statusTime) || afterAccount.StatusChangedAt == nil || !afterAccount.StatusChangedAt.Equal(statusTime) {
		t.Fatalf("alive health check changed status time: mailbox=%v account=%v", afterMailbox.StatusChangedAt, afterAccount.StatusChangedAt)
	}
	if afterMailbox.LastHealthCheckedAt == nil || afterAccount.LastHealthCheckedAt == nil {
		t.Fatalf("alive health check did not persist health time")
	}
}
