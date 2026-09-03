package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSunnyProxyBulkImportPreservesRepeatedGatewaySlots(t *testing.T) {
	s := newSunnySessionTestServer(t)
	const slotCount = 128
	addresses := make([]string, slotCount)
	for index := range addresses {
		addresses[index] = "http://user:pass@shared-gateway.example:8080"
	}
	payload, err := json.Marshal(map[string]any{
		"addresses":    addresses,
		"country":      "US",
		"purpose_tags": []string{"register"},
		"status":       "启用",
		"enabled":      true,
	})
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodPost, "/api/sunny/proxy-config/pool", strings.NewReader(string(payload)))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnyProxyPool(recorder, req, nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("bulk import status=%d body=%s", recorder.Code, recorder.Body.String())
	}

	var response map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if got := toInt(response["created"]); got != slotCount {
		t.Fatalf("created=%d, want %d independent slots", got, slotCount)
	}
	var rows []SunnyProxy
	if err := s.db.Order("id asc").Find(&rows).Error; err != nil {
		t.Fatalf("load imported proxy slots: %v", err)
	}
	if len(rows) != slotCount {
		t.Fatalf("saved=%d, want %d independent slots", len(rows), slotCount)
	}
	for index, row := range rows {
		if row.Address != addresses[index] {
			t.Fatalf("row %d address=%q", index, row.Address)
		}
		if !row.Enabled || row.Status != "enabled" || row.LastCheckedAt != nil {
			t.Fatalf("row %d has unexpected state: %#v", index, row)
		}
	}
}
