package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestSunnyProxyCreatePreservesRepeatedGatewaySlotsWithoutSynchronousNetworkCheck(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/proxy-config/pool", strings.NewReader(`{
		"addresses":[
			"http://unreachable-one.example:8080",
			"http://unreachable-one.example:8080",
			"http://unreachable-two.example:8080"
		],
		"country":"JP",
		"purpose_tags":["register"],
		"status":"启用",
		"enabled":true
	}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyProxyPool(recorder, req, nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("create proxies status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var rows []SunnyProxy
	if err := s.db.Order("id asc").Find(&rows).Error; err != nil {
		t.Fatalf("load proxies: %v", err)
	}
	if len(rows) != 3 {
		t.Fatalf("created %d proxies, want one row per input slot", len(rows))
	}
	if rows[0].Address != rows[1].Address || rows[1].Address == rows[2].Address {
		t.Fatalf("repeated gateway slots were not preserved: %#v", rows)
	}
	for _, row := range rows {
		if !row.Enabled || row.Status != "enabled" {
			t.Fatalf("new proxy state=%q enabled=%v", row.Status, row.Enabled)
		}
		if row.LastCheckedAt != nil || row.LastCheckOK || row.LastError != "" {
			t.Fatalf("new proxy was unexpectedly checked: %#v", row)
		}
	}
}

func TestSunnyProxyCreatePreservesExplicitDisabledState(t *testing.T) {
	s := newSunnySessionTestServer(t)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/proxy-config/pool", strings.NewReader(`{
		"addresses":["http://disabled-one.example:8080","http://disabled-two.example:8080"],
		"country":"JP",
		"purpose_tags":["register"],
		"status":"停用",
		"enabled":false
	}`))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyProxyPool(recorder, req, nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("create disabled proxies status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var rows []SunnyProxy
	if err := s.db.Order("id asc").Find(&rows).Error; err != nil {
		t.Fatalf("load proxies: %v", err)
	}
	if len(rows) != 2 {
		t.Fatalf("created %d proxies, want two", len(rows))
	}
	for _, row := range rows {
		if row.Status != "disabled" || row.Enabled {
			t.Fatalf("disabled proxy state diverged: %#v", row)
		}
	}
}

func TestSunnyProxyBatchDeleteUsesSingleRequest(t *testing.T) {
	s := newSunnySessionTestServer(t)
	rows := []SunnyProxy{
		{Address: "http://batch-delete-one.example:8080", Status: "enabled", Enabled: true},
		{Address: "http://batch-delete-two.example:8080", Status: "enabled", Enabled: true},
		{Address: "http://batch-delete-three.example:8080", Status: "enabled", Enabled: true},
	}
	if err := s.db.Create(&rows).Error; err != nil {
		t.Fatalf("create proxies: %v", err)
	}
	body := fmt.Sprintf(`{"ids":[%d,%d]}`, rows[0].ID, rows[2].ID)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/proxy-config/pool/batch-delete", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyProxyPool(recorder, req, []string{"batch-delete"})
	if recorder.Code != http.StatusOK {
		t.Fatalf("batch delete status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if got := toInt(response["deleted"]); got != 2 {
		t.Fatalf("deleted=%d response=%#v", got, response)
	}
	var remaining []SunnyProxy
	if err := s.db.Find(&remaining).Error; err != nil {
		t.Fatalf("load remaining proxies: %v", err)
	}
	if len(remaining) != 1 || remaining[0].ID != rows[1].ID {
		t.Fatalf("remaining proxies=%#v", remaining)
	}
}

func TestSunnyProxyBatchUpdateUsesSingleStatement(t *testing.T) {
	s := newSunnySessionTestServer(t)
	rows := []SunnyProxy{
		{Address: "http://batch-update-one.example:8080", Country: "JP", Status: "disabled", Enabled: false},
		{Address: "http://batch-update-two.example:8080", Country: "JP", Status: "disabled", Enabled: false},
		{Address: "http://batch-update-three.example:8080", Country: "JP", Status: "disabled", Enabled: false},
	}
	if err := s.db.Create(&rows).Error; err != nil {
		t.Fatalf("create proxies: %v", err)
	}
	if err := s.db.Model(&SunnyProxy{}).Where("id IN ?", []uint{rows[0].ID, rows[1].ID, rows[2].ID}).Update("enabled", false).Error; err != nil {
		t.Fatalf("mark fixtures disabled: %v", err)
	}
	body := fmt.Sprintf(`{"ids":[%d,%d],"status":"启用","enabled":true}`, rows[0].ID, rows[2].ID)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/proxy-config/pool/batch-update", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyProxyPool(recorder, req, []string{"batch-update"})
	if recorder.Code != http.StatusOK {
		t.Fatalf("batch update status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var response map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if got := toInt(response["updated"]); got != 2 {
		t.Fatalf("updated=%d response=%#v", got, response)
	}
	var saved []SunnyProxy
	if err := s.db.Order("id asc").Find(&saved).Error; err != nil {
		t.Fatalf("load proxies: %v", err)
	}
	if !saved[0].Enabled || saved[0].Status != "enabled" || saved[1].Enabled || saved[2].Status != "enabled" {
		t.Fatalf("unexpected proxy states: %#v", saved)
	}
}

func TestSunnyProxySingleCheckRouteIsHandledBeforeItemMutation(t *testing.T) {
	s := newSunnySessionTestServer(t)
	proxy := SunnyProxy{Address: "http://127.0.0.1:1", Status: "enabled", Enabled: true}
	if err := s.db.Create(&proxy).Error; err != nil {
		t.Fatalf("create proxy: %v", err)
	}
	id := fmt.Sprint(proxy.ID)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/proxy-config/pool/"+id+"/check", nil)
	recorder := httptest.NewRecorder()
	s.sunnyProxyPool(recorder, req, []string{id, "check"})
	if recorder.Code != http.StatusOK {
		t.Fatalf("single check status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var saved SunnyProxy
	if err := s.db.First(&saved, proxy.ID).Error; err != nil {
		t.Fatalf("reload proxy: %v", err)
	}
	if saved.LastCheckedAt == nil {
		t.Fatalf("single check did not persist check timestamp")
	}
}

func TestCheckSunnyProxyBatchRunsWithBoundedConcurrency(t *testing.T) {
	proxies := make([]SunnyProxy, 12)
	for index := range proxies {
		proxies[index] = SunnyProxy{Address: fmt.Sprintf("http://proxy-%d.example:8080", index), Status: "enabled", Enabled: true}
	}
	var active atomic.Int32
	var maximum atomic.Int32
	checker := func(address string) map[string]any {
		current := active.Add(1)
		for {
			previous := maximum.Load()
			if current <= previous || maximum.CompareAndSwap(previous, current) {
				break
			}
		}
		time.Sleep(10 * time.Millisecond)
		active.Add(-1)
		return map[string]any{"proxy": address, "ok": true, "latency_ms": 10}
	}
	if available := checkSunnyProxyBatch(proxies, 4, checker); available != len(proxies) {
		t.Fatalf("available=%d want=%d", available, len(proxies))
	}
	if got := maximum.Load(); got < 2 || got > 4 {
		t.Fatalf("maximum concurrent checks=%d, want 2..4", got)
	}
	for _, proxy := range proxies {
		if !proxy.LastCheckOK || proxy.LastCheckedAt == nil {
			t.Fatalf("proxy check result was not applied: %#v", proxy)
		}
	}
}

func TestSunnyProxyCheckLimitsAreConfigurableAndClamped(t *testing.T) {
	t.Setenv("SUNNY_PROXY_CHECK_TIMEOUT_SECONDS", "1")
	if got := sunnyProxyCheckTimeout(); got != 2*time.Second {
		t.Fatalf("minimum timeout=%s", got)
	}
	t.Setenv("SUNNY_PROXY_CHECK_TIMEOUT_SECONDS", "45")
	if got := sunnyProxyCheckTimeout(); got != 30*time.Second {
		t.Fatalf("maximum timeout=%s", got)
	}
	t.Setenv("SUNNY_PROXY_CHECK_CONCURRENCY", "128")
	if got := sunnyProxyCheckConcurrency(); got != 64 {
		t.Fatalf("maximum concurrency=%d", got)
	}
}
