package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func TestReadAuditRequestBodyPreservesOversizedPayload(t *testing.T) {
	s := newAuditTestServer(t)
	payload := `{"lines":"` + strings.Repeat("x", 4*1024*1024) + `"}`
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/mailboxes/import", strings.NewReader(payload))
	req.ContentLength = -1
	if body := s.readAuditRequestBody(req); len(body) != 0 {
		t.Fatalf("oversized body should not be audited: %#v", body)
	}
	restored, err := io.ReadAll(req.Body)
	if err != nil {
		t.Fatalf("read restored request body: %v", err)
	}
	if string(restored) != payload {
		t.Fatalf("request body changed: got %d bytes, want %d", len(restored), len(payload))
	}
}

func newAuditTestServer(t *testing.T) *Server {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+filepath.ToSlash(filepath.Join(t.TempDir(), "audit.db"))+"?cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open audit database: %v", err)
	}
	if err := db.AutoMigrate(&AuditLog{}, &AuditSetting{}, &AuditExportJob{}, &Task{}); err != nil {
		t.Fatalf("migrate audit database: %v", err)
	}
	if sqlDB, err := db.DB(); err == nil {
		t.Cleanup(func() { _ = sqlDB.Close() })
	}
	return &Server{db: db, adminUser: "admin", sessions: map[string]time.Time{}, loginFailures: map[string]*loginFailure{}, stop: make(chan struct{})}
}

func TestAuditMiddlewareRecordsMutationsAndRedactsSecrets(t *testing.T) {
	s := newAuditTestServer(t)
	handler := s.auditMiddleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { writeJSON(w, http.StatusOK, map[string]any{"ok": true}) }))
	body := `{"lines":"first@example.com----secret----client----refresh\nsecond@outlook.com----secret----client----refresh","access_token":"eyJsecret.payload.signature"}`
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/mailboxes/import", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("mutation status = %d", rec.Code)
	}
	var item AuditLog
	if err := s.db.First(&item).Error; err != nil {
		t.Fatalf("audit log missing: %v", err)
	}
	if item.Category != "mailbox" || item.Action != "import" || item.Count != 2 {
		t.Fatalf("unexpected audit classification: %#v", item)
	}
	if strings.Contains(item.DetailsJSON, "secret") || strings.Contains(item.DetailsJSON, "first@example.com") {
		t.Fatalf("audit details leaked secrets: %s", item.DetailsJSON)
	}
	var details map[string]any
	if json.Unmarshal([]byte(item.DetailsJSON), &details) != nil || intValue(details["lines_count"], 0) != 2 {
		t.Fatalf("audit import summary missing: %s", item.DetailsJSON)
	}

	getReq := httptest.NewRequest(http.MethodGet, "/api/sunny/mailboxes", nil)
	handler.ServeHTTP(httptest.NewRecorder(), getReq)
	var count int64
	s.db.Model(&AuditLog{}).Count(&count)
	if count != 1 {
		t.Fatalf("read-only request was audited, count=%d", count)
	}
}

func TestAuditRetentionCleanup(t *testing.T) {
	s := newAuditTestServer(t)
	now := time.Now()
	s.db.Create(&AuditSetting{ID: 1, RetentionDays: 7, CleanupHour: now.Hour(), Enabled: true})
	s.db.Create(&AuditLog{OccurredAt: now.AddDate(0, 0, -8), Summary: "old"})
	s.db.Create(&AuditLog{OccurredAt: now.AddDate(0, 0, -1), Summary: "recent"})
	s.auditRetentionCleanup()
	var oldCount, recentCount int64
	s.db.Model(&AuditLog{}).Where("summary = ?", "old").Count(&oldCount)
	s.db.Model(&AuditLog{}).Where("summary = ?", "recent").Count(&recentCount)
	if oldCount != 0 || recentCount != 1 {
		t.Fatalf("unexpected retention result old=%d recent=%d", oldCount, recentCount)
	}
}

func TestAuditExportJobCreatesDownload(t *testing.T) {
	s := newAuditTestServer(t)
	t.Setenv("ACCOUNT_MANAGER_DATABASE_URL", filepath.Join(t.TempDir(), "sunnyregister.db"))
	s.db.Create(&AuditLog{OccurredAt: time.Now(), Actor: "admin", LogType: "operation", Category: "mailbox", Action: "update", Status: "success", Summary: "updated mailbox"})
	job := AuditExportJob{ID: "audit_export_test", Status: "queued", Format: "csv", FiltersJSON: "{}", SelectedJSON: "[]", Actor: "admin"}
	s.db.Create(&job)
	s.runAuditExport(job.ID)
	if err := s.db.First(&job, "id = ?", job.ID).Error; err != nil {
		t.Fatalf("reload export job: %v", err)
	}
	if job.Status != "completed" || job.RecordCount != 1 || job.FilePath == "" {
		t.Fatalf("unexpected export result: %#v", job)
	}
	if _, err := os.Stat(job.FilePath); err != nil {
		t.Fatalf("export file missing: %v", err)
	}
}
