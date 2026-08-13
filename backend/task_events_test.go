package main

import (
	"strings"
	"testing"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func newTaskEventTestServer(t *testing.T) *Server {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "-")+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open database: %v", err)
	}
	if err := db.AutoMigrate(&TaskEvent{}); err != nil {
		t.Fatalf("migrate database: %v", err)
	}
	return &Server{db: db}
}

func TestAppendTaskEventInfersStructuredAccountContext(t *testing.T) {
	s := newTaskEventTestServer(t)
	ev := s.appendTaskEvent(
		"task-1",
		"[User.Example@Example.COM] [接码] received OTP 123456 using Bearer abcdefghijklmnopqrstuvwxyz",
		"log",
		"warning",
		map[string]any{"sms_provider": "firefox"},
	)
	if ev.Email != "User.Example@Example.COM" || ev.SubjectKey != "user.example@example.com" {
		t.Fatalf("unexpected email context: %#v", ev)
	}
	if ev.Scope != "account" || ev.SubjectType != "account" || ev.Module != "sms" || ev.Action != "sms.event" {
		t.Fatalf("unexpected event metadata: %#v", ev)
	}
	if ev.OperationID != "task-1:user.example@example.com:sms" {
		t.Fatalf("unexpected operation id: %q", ev.OperationID)
	}
	if strings.Contains(ev.Message, "123456") || strings.Contains(ev.Message, "abcdefghijklmnop") {
		t.Fatalf("message was not sanitized: %s", ev.Message)
	}
	serialized := serializeEvent(ev)
	if serialized["email"] != ev.Email || serialized["module"] != "sms" || serialized["scope"] != "account" {
		t.Fatalf("serialized metadata missing: %#v", serialized)
	}
}

func TestAppendAccountTaskEventUsesExplicitAction(t *testing.T) {
	s := newTaskEventTestServer(t)
	ev := s.appendAccountTaskEvent("task-2", "account@example.com", "health", "health.alive", "账户存活", "info", map[string]any{"account_id": 12})
	if ev.Email != "account@example.com" || ev.AccountID != 12 || ev.Module != "health" || ev.Action != "health.alive" {
		t.Fatalf("unexpected explicit account event: %#v", ev)
	}
	if detail := jsonMap(ev.DetailJSON); detail["email"] != "account@example.com" || detail["action"] != "health.alive" {
		t.Fatalf("compatibility detail missing metadata: %#v", detail)
	}
}
