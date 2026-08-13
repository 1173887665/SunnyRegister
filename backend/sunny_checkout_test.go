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
