package main

import (
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func TestCancelPendingRenewalPreservesMailboxStatus(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+filepath.ToSlash(filepath.Join(t.TempDir(), "cancel-renewal.db"))+"?cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open database: %v", err)
	}
	if err := db.AutoMigrate(&Task{}, &TaskEvent{}, &SunnyMailbox{}); err != nil {
		t.Fatalf("migrate database: %v", err)
	}
	if sqlDB, err := db.DB(); err == nil {
		t.Cleanup(func() { _ = sqlDB.Close() })
	}
	s := &Server{db: db, sessions: map[string]time.Time{}, loginFailures: map[string]*loginFailure{}, stop: make(chan struct{})}
	mailbox := SunnyMailbox{Email: "registered@example.com", Status: "已注册", Enabled: true}
	if err := db.Create(&mailbox).Error; err != nil {
		t.Fatalf("create mailbox: %v", err)
	}
	task := Task{ID: "renewal-cancel", Type: "sunny_refresh_session", Platform: "sunny", Status: TaskPending, PayloadJSON: `{"account_ids":[7]}`, ProgressTotal: 1}
	if err := db.Create(&task).Error; err != nil {
		t.Fatalf("create task: %v", err)
	}

	recorder := httptest.NewRecorder()
	s.handleTasks(recorder, httptest.NewRequest(http.MethodPost, "/tasks/renewal-cancel/cancel", nil), "/renewal-cancel/cancel")
	if recorder.Code != http.StatusOK {
		t.Fatalf("cancel status = %d, body=%s", recorder.Code, recorder.Body.String())
	}
	if err := db.First(&task, "id = ?", task.ID).Error; err != nil {
		t.Fatalf("reload task: %v", err)
	}
	if task.Status != TaskCancelled {
		t.Fatalf("task status = %q, want %q", task.Status, TaskCancelled)
	}
	if err := db.First(&mailbox, mailbox.ID).Error; err != nil {
		t.Fatalf("reload mailbox: %v", err)
	}
	if mailbox.Status != "已注册" || mailbox.LastError != "" {
		t.Fatalf("renewal cancellation mutated mailbox: status=%q error=%q", mailbox.Status, mailbox.LastError)
	}
}
