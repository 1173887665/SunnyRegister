package main

import (
	"bytes"
	"context"
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

func TestFetchFireFoxOptionsBuildsCountryAndPricedServiceLists(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/init.ashx" {
			if r.Method != http.MethodPost {
				t.Fatalf("unexpected country method: %s", r.Method)
			}
			_ = r.ParseForm()
			if r.Form.Get("act") != "PagCountry" {
				t.Fatalf("unexpected country action: %s", r.Form.Get("act"))
			}
			writeJSON(w, http.StatusOK, []map[string]any{
				{"Country_ID": "usa", "Country_Area": 1, "Country_Title": "+1/美国/usa", "Country_PhoneLenth": "10"},
				{"Country_ID": "idn", "Country_Area": 62, "Country_Title": "+62/印度尼西亚/indonesia", "Country_PhoneLenth": "8,9,10,11,12"},
			})
			return
		}
		if r.URL.Path != "/yhapi.ashx" {
			t.Fatalf("unexpected FireFox path: %s", r.URL.Path)
		}
		if r.URL.Query().Get("act") != "getItem" {
			t.Fatalf("unexpected action: %s", r.URL.Query().Get("act"))
		}
		writeJSON(w, http.StatusOK, []map[string]any{
			{"Item_ID": "1096", "Item_Name": "OpenAI / ChatGpt", "Item_UPrice": "0.6500", "Country_ID": "usa", "Country_Title": "+1/美国/usa"},
			{"Item_ID": "1008", "Item_Name": "WhatsApp", "Item_UPrice": "2.0000", "Country_ID": "usa", "Country_Title": "+1/美国/usa"},
			{"Item_ID": "1096", "Item_Name": "OpenAI / ChatGpt", "Item_UPrice": "0.4500", "Country_ID": "idn", "Country_Title": "+62/印度尼西亚/indonesia"},
		})
	}))
	t.Cleanup(provider.Close)
	cfg := mergeConfig(defaultPhoneConfig(), map[string]any{"firefox_base_url": provider.URL})

	countries, err := fetchFireFoxOptions(context.Background(), "country", "", cfg)
	if err != nil || len(countries) != 2 {
		t.Fatalf("countries = %#v, err = %v", countries, err)
	}
	if countries[1]["value"] != "usa" || countries[1]["label"] != "美国 / usa (+1)" {
		t.Fatalf("unexpected FireFox country: %#v", countries[1])
	}
	services, err := fetchFireFoxOptions(context.Background(), "service", "usa", cfg)
	if err != nil || len(services) != 2 {
		t.Fatalf("services = %#v, err = %v", services, err)
	}
	if services[0]["label"] != "OpenAI / ChatGpt · 0.6500" {
		t.Fatalf("unexpected FireFox service label: %#v", services[0])
	}
}

func TestSunnyFireFoxCountriesEndpointUsesCountryMetadata(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/init.ashx" || r.Method != http.MethodPost {
			t.Fatalf("country request used %s %s", r.Method, r.URL.Path)
		}
		_ = r.ParseForm()
		if r.Form.Get("act") != "PagCountry" {
			t.Fatalf("unexpected country action: %s", r.Form.Get("act"))
		}
		writeJSON(w, http.StatusOK, []map[string]any{
			{"Country_ID": "usa", "Country_Area": 1, "Country_Title": "+1/美国/usa"},
			{"Country_ID": "jpn", "Country_Area": 81, "Country_Title": "+81/日本/japan"},
		})
	}))
	t.Cleanup(provider.Close)
	s := newSunnySMSOptionsTestServer(t)
	s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), map[string]any{"firefox_base_url": provider.URL}))
	req := httptest.NewRequest(http.MethodGet, "/api/sunny/phones/provider-options?provider=firefox&kind=countries", nil)
	rec := httptest.NewRecorder()

	s.sunnySMSProviderOptions(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var response struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(response.Items) != 2 || response.Items[0]["kind"] != "country" {
		t.Fatalf("unexpected country endpoint response: %#v", response.Items)
	}
	var wrongKindRows int64
	if err := s.db.Model(&SunnySMSProviderOption{}).Where("provider = ? AND kind = ?", "firefox", "countrie").Count(&wrongKindRows).Error; err != nil || wrongKindRows != 0 {
		t.Fatalf("legacy typo cache rows = %d, err = %v", wrongKindRows, err)
	}
}

func TestSunnyCheckFireFoxUsesAPITokenDirectly(t *testing.T) {
	var calls atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Query().Get("act") != "myInfo" {
			t.Fatalf("unexpected action: %s", r.URL.Query().Get("act"))
		}
		if r.URL.Query().Get("token") != "stable-token_3" {
			t.Fatalf("unexpected token: %s", r.URL.Query().Get("token"))
		}
		if r.URL.Query().Has("ApiName") || r.URL.Query().Has("PassWord") {
			t.Fatalf("login credentials must not be sent: %s", r.URL.RawQuery)
		}
		_, _ = w.Write([]byte("1|12.34|1|0"))
	}))
	t.Cleanup(provider.Close)
	s := newSunnySMSOptionsTestServer(t)
	body, _ := json.Marshal(map[string]any{
		"firefox_base_url":  provider.URL,
		"firefox_api_token": "stable-token_3",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/phones/firefox/check", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()

	s.sunnyCheckFireFox(rec, req)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &response); err != nil || response["balance"] != "12.34" {
		t.Fatalf("unexpected response: %s, err = %v", rec.Body.String(), err)
	}
	if calls.Load() != 1 {
		t.Fatalf("calls = %d, want 1", calls.Load())
	}
}

func TestSunnyCheckFireFoxAcceptsLegacyPasswordFieldAsToken(t *testing.T) {
	var calls atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.URL.Query().Get("act") != "myInfo" || r.URL.Query().Get("token") != "legacy-token_3" {
			t.Fatalf("unexpected legacy token request: %s", r.URL.RawQuery)
		}
		_, _ = w.Write([]byte("1|5.67|1|0"))
	}))
	t.Cleanup(provider.Close)
	s := newSunnySMSOptionsTestServer(t)
	body, _ := json.Marshal(map[string]any{
		"firefox_base_url": provider.URL,
		"firefox_password": "legacy-token_3",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/phones/firefox/check", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()

	s.sunnyCheckFireFox(rec, req)

	if rec.Code != http.StatusOK || !bytes.Contains(rec.Body.Bytes(), []byte("5.67")) {
		t.Fatalf("unexpected legacy token response: status=%d body=%s", rec.Code, rec.Body.String())
	}
	if calls.Load() != 1 {
		t.Fatalf("legacy token called FireFox API %d times", calls.Load())
	}
}

func TestSunnyCheckFireFoxExplainsInvalidToken(t *testing.T) {
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("0|-2"))
	}))
	t.Cleanup(provider.Close)
	s := newSunnySMSOptionsTestServer(t)
	body, _ := json.Marshal(map[string]any{
		"firefox_base_url":  provider.URL + "/yhapi.ashx",
		"firefox_api_token": "invalid-token_3",
	})
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/phones/firefox/check", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()

	s.sunnyCheckFireFox(rec, req)

	if rec.Code != http.StatusBadRequest || !bytes.Contains(rec.Body.Bytes(), []byte("update the API Token")) {
		t.Fatalf("unexpected FireFox error response: status=%d body=%s", rec.Code, rec.Body.String())
	}
}

func TestSunnyPhoneConfigMigratesLegacyFireFoxToken(t *testing.T) {
	s := newSunnySMSOptionsTestServer(t)
	s.sunnySaveConfig(sunnyCfgPhone, mergeConfig(defaultPhoneConfig(), map[string]any{
		"firefox_api_name": "legacy-account",
		"firefox_password": "legacy-token_3",
	}))

	req := httptest.NewRequest(http.MethodGet, "/api/sunny/phones/config", nil)
	rec := httptest.NewRecorder()
	s.sunnyPhones(rec, req, []string{"config"})

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if response["firefox_api_token"] != "legacy-token_3" {
		t.Fatalf("legacy token was not migrated: %#v", response)
	}
	if response["firefox_api_name"] != "" || response["firefox_password"] != "" {
		t.Fatalf("legacy login fields must not be returned: %#v", response)
	}
}
