package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

func TestSunnyProxyUpdateKeepsCountryWhenCountryIsBlank(t *testing.T) {
	s := newSunnySessionTestServer(t)
	proxy := SunnyProxy{Address: "http://127.0.0.1:7897", Country: "JP", Status: "enabled", Enabled: true}
	if err := s.db.Create(&proxy).Error; err != nil {
		t.Fatalf("create proxy: %v", err)
	}

	id := strconv.FormatUint(uint64(proxy.ID), 10)
	req := httptest.NewRequest(http.MethodPut, "/api/sunny/proxy-config/pool/"+id, strings.NewReader(`{"country":"","status":"停用"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnyProxyPool(rec, req, []string{id})
	if rec.Code != http.StatusOK {
		t.Fatalf("blank country update status = %d, body = %s", rec.Code, rec.Body.String())
	}
	if err := s.db.First(&proxy, proxy.ID).Error; err != nil {
		t.Fatalf("reload proxy: %v", err)
	}
	if proxy.Country != "JP" || proxy.Status != "disabled" {
		t.Fatalf("blank country update = country %q, status %q", proxy.Country, proxy.Status)
	}

	req = httptest.NewRequest(http.MethodPut, "/api/sunny/proxy-config/pool/"+id, strings.NewReader(`{"country":"US","status":"启用"}`))
	req.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	s.sunnyProxyPool(rec, req, []string{id})
	if rec.Code != http.StatusOK {
		t.Fatalf("explicit country update status = %d, body = %s", rec.Code, rec.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode update response: %v", err)
	}
	if payload["country"] != "US" {
		t.Fatalf("explicit country update response = %#v", payload)
	}

	req = httptest.NewRequest(http.MethodPut, "/api/sunny/proxy-config/pool/"+id, strings.NewReader(`{"country":"","status":"失效","enabled":false}`))
	req.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	s.sunnyProxyPool(rec, req, []string{id})
	if rec.Code != http.StatusOK {
		t.Fatalf("invalid status update = %d, body = %s", rec.Code, rec.Body.String())
	}
	if err := s.db.First(&proxy, proxy.ID).Error; err != nil {
		t.Fatalf("reload invalid proxy: %v", err)
	}
	if proxy.Country != "US" || proxy.Status != "invalid" {
		t.Fatalf("invalid update = country %q, status %q", proxy.Country, proxy.Status)
	}
}
