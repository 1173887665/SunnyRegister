package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func newFreePPConfigTestServer(t *testing.T) *Server {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "-")+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&ConfigItem{}); err != nil {
		t.Fatal(err)
	}
	return &Server{db: db}
}

func TestFreePPBranchConfigPersistsPerProject(t *testing.T) {
	s := newFreePPConfigTestServer(t)
	update := httptest.NewRequest(http.MethodPost, "/api/freepp/config/branch", strings.NewReader(`{
		"branch":"momo",
		"billing_country":"VN",
		"billing_currency":"VND",
		"attempts":7,
		"stages":{"checkout":{"countries":["VN"],"retry":5}}
	}`))
	update.Header.Set("Content-Type", "application/json")
	updated := httptest.NewRecorder()
	s.handleFreePPConfig(updated, update, "/config/branch")
	if updated.Code != http.StatusOK {
		t.Fatalf("update status = %d, body = %s", updated.Code, updated.Body.String())
	}

	get := httptest.NewRequest(http.MethodGet, "/api/freepp/config", nil)
	result := httptest.NewRecorder()
	s.handleFreePPConfig(result, get, "/config")
	if result.Code != http.StatusOK {
		t.Fatalf("get status = %d, body = %s", result.Code, result.Body.String())
	}
	body := result.Body.String()
	for _, expected := range []string{`"momo"`, `"billing_country":"VN"`, `"billing_currency":"VND"`, `"attempts":7`, `"retry":5`} {
		if !strings.Contains(body, expected) {
			t.Fatalf("response missing %s: %s", expected, body)
		}
	}
	if !strings.Contains(body, `"gopay"`) || !strings.Contains(body, `"pix"`) {
		t.Fatalf("other project defaults missing: %s", body)
	}
}

func TestFreePPBillingTemplatesIncludeProjectCurrencies(t *testing.T) {
	s := newFreePPConfigTestServer(t)
	req := httptest.NewRequest(http.MethodGet, "/api/freepp/billing/templates", nil)
	rec := httptest.NewRecorder()
	s.handleFreePPConfig(rec, req, "/billing/templates")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"country":"VN"`) || !strings.Contains(rec.Body.String(), `"currency":"VND"`) {
		t.Fatalf("unexpected templates response: %d %s", rec.Code, rec.Body.String())
	}
}
