package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func newSunnySMSOptionsTestServer(t *testing.T) *Server {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+filepath.ToSlash(filepath.Join(t.TempDir(), "sms-options.db"))+"?cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatalf("open sms options database: %v", err)
	}
	if err := db.AutoMigrate(&SunnyKVConfig{}, &SunnySMSProviderOption{}); err != nil {
		t.Fatalf("migrate sms options database: %v", err)
	}
	if sqlDB, err := db.DB(); err == nil {
		sqlDB.SetMaxOpenConns(1)
		t.Cleanup(func() { _ = sqlDB.Close() })
	}
	return &Server{db: db}
}

func TestSunnySMSProviderOptionsFetchesOnceAndThenUsesDatabaseCache(t *testing.T) {
	s := newSunnySMSOptionsTestServer(t)
	var calls atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		time.Sleep(40 * time.Millisecond)
		writeJSON(w, http.StatusOK, []map[string]any{{"id": "187", "eng": "Japan"}})
	}))
	t.Cleanup(provider.Close)
	s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), map[string]any{
		"smsbower_api_key": "test-key", "smsbower_base_url": provider.URL,
	}))

	const concurrentRequests = 8
	var workers sync.WaitGroup
	workers.Add(concurrentRequests)
	errors := make(chan string, concurrentRequests)
	for i := 0; i < concurrentRequests; i++ {
		go func() {
			defer workers.Done()
			req := httptest.NewRequest(http.MethodGet, "/api/sunny/phones/provider-options?provider=smsbower&kind=countries", nil)
			rec := httptest.NewRecorder()
			s.sunnySMSProviderOptions(rec, req)
			if rec.Code != http.StatusOK {
				errors <- rec.Body.String()
			}
		}()
	}
	workers.Wait()
	close(errors)
	for message := range errors {
		t.Fatalf("provider options request failed: %s", message)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("provider was called %d times, want 1", got)
	}
	var cachedRows int64
	if err := s.db.Model(&SunnySMSProviderOption{}).Count(&cachedRows).Error; err != nil || cachedRows != 1 {
		t.Fatalf("cached option rows = %d, err = %v", cachedRows, err)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/sunny/phones/provider-options?provider=smsbower&kind=countries", nil)
	rec := httptest.NewRecorder()
	s.sunnySMSProviderOptions(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("cached provider options status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &response); err != nil || response["cached"] != true {
		t.Fatalf("expected database cached response, got %s, err = %v", rec.Body.String(), err)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("cached request called provider again: %d", got)
	}
}
