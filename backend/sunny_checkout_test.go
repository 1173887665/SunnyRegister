package main

import (
	"strings"
	"testing"
)

func TestSplitCheckoutPoolNormalizesAndLimits(t *testing.T) {
	items, err := splitCheckoutPool("http://127.0.0.1:8000\nhttp://127.0.0.1:8000\n")
	if err != nil || len(items) != 1 || items[0] != "http://127.0.0.1:8000" {
		t.Fatalf("pool=%#v err=%v", items, err)
	}
	if _, err := splitCheckoutPool(""); err == nil {
		t.Fatal("empty pool should fail")
	}
	items, err = splitCheckoutPool("host:8080:user:pass\nuser:pass@host:8080\nsocks5://host:1080")
	if err != nil || len(items) != 2 || !strings.Contains(items[0], "http://user:pass@host:8080") {
		t.Fatalf("credential proxy normalization=%#v err=%v", items, err)
	}
}

func TestCheckoutProviderDefaultsIncludeAllCurrentPaths(t *testing.T) {
	if len(checkoutProviders) != 10 {
		t.Fatalf("providers=%d", len(checkoutProviders))
	}
	for _, value := range []string{"hosted", "ph_short", "paypal", "ideal", "twint", "upi", "pix", "momo", "gcash", "kakao"} {
		country, currency := checkoutProviderDefaults(value)
		if country == "" || currency == "" {
			t.Fatalf("missing defaults for %s", value)
		}
	}
}

func TestNormalizeGCashRequestUsesSinglePHCheckoutPool(t *testing.T) {
	in := sunnyCheckoutRequest{
		Plan:             "plus",
		LinkType:         "gcash",
		Country:          "US",
		Currency:         "USD",
		CheckoutProxies:  "http://ph-proxy.example:8080",
		PromotionProxies: "http://vn-proxy.example:8080",
	}

	normalized, checkout, promotion, err := normalizeCheckoutRequest(in)
	if err != nil {
		t.Fatalf("normalize GCash request: %v", err)
	}
	if normalized.Country != "PH" || normalized.Currency != "PHP" || normalized.PromoCountry != "PH" {
		t.Fatalf("unexpected GCash region: %#v", normalized)
	}
	if len(checkout) != 1 || len(promotion) != 1 || checkout[0] != promotion[0] {
		t.Fatalf("checkout=%#v promotion=%#v", checkout, promotion)
	}
}

func TestNormalizeGCashRequestDoesNotRequirePromotionPool(t *testing.T) {
	_, checkout, promotion, err := normalizeCheckoutRequest(sunnyCheckoutRequest{
		Plan:            "plus",
		LinkType:        "gcash",
		CheckoutProxies: "http://ph-proxy.example:8080",
	})
	if err != nil {
		t.Fatalf("normalize GCash request without promotion pool: %v", err)
	}
	if len(checkout) != 1 || len(promotion) != 1 || checkout[0] != promotion[0] {
		t.Fatalf("checkout=%#v promotion=%#v", checkout, promotion)
	}
}

func TestParseCheckoutExternalAT(t *testing.T) {
	token, email := parseCheckoutExternalAT("eyJhbGciOiJub25lIn0.payload.signature user@example.com")
	if token == "" || email != "user@example.com" {
		t.Fatalf("token=%q email=%q", token, email)
	}
	token, email = parseCheckoutExternalAT(`{"access_token":"eyJabc.def.ghi","email":"json@example.com"}`)
	if token == "" || email != "json@example.com" {
		t.Fatalf("json token=%q email=%q", token, email)
	}
	if token, _ = parseCheckoutExternalAT("not-an-at"); token != "" {
		t.Fatalf("invalid token=%q", token)
	}
	expired := "eyJhbGciOiJub25lIn0.eyJleHAiOjF9.signature user@example.com"
	if token, _ = parseCheckoutExternalAT(expired); token != "" {
		t.Fatal("expired JWT should be rejected")
	}
}

func TestExtractSunnyCheckoutResult(t *testing.T) {
	result := extractSunnyCheckoutResult(map[string]any{"checkout_session_id": "cs_live_123", "redirect_url": "https://pay.example/approve", "qr_data": "upi://pay/x"}, "upi")
	if result["checkout_session_id"] != "cs_live_123" || result["payment_link"] != "https://pay.example/approve" || result["qr_data"] != "upi://pay/x" {
		t.Fatalf("result=%#v", result)
	}
}

func TestRecordSunnyCheckoutResultPersistsPartialTaskItems(t *testing.T) {
	task := Task{Status: TaskRunning, ProgressTotal: 2, ResultJSON: "{}"}
	result := map[string]any{"requested": 2, "success": 0, "failed": 0, "items": []any{}}
	item := map[string]any{
		"email": "success@example.com", "status": "succeeded", "link_type": "paypal",
		"payment_link": "https://www.paypal.com/agreements/approve?ba_token=BA-test",
	}

	recordSunnyCheckoutResult(&task, result, item)

	serialized := serializeTask(task)
	partial := serialized["result"].(map[string]any)
	items := partial["items"].([]any)
	if len(items) != 1 || text(items[0].(map[string]any)["email"]) != "success@example.com" {
		t.Fatalf("partial task items=%#v", items)
	}
	if task.ProgressCurrent != 1 || task.SuccessCount != 1 || task.ErrorCount != 0 {
		t.Fatalf("task progress=%d success=%d failed=%d", task.ProgressCurrent, task.SuccessCount, task.ErrorCount)
	}
}
