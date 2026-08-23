package main

import (
	"encoding/json"
	"testing"
)

func TestRemailMailboxCredentialHelpers(t *testing.T) {
	parsed, err := parseSunnyMailboxLineForProvider("user@example.com----service-token", "remail", "remail_api")
	if err != nil {
		t.Fatalf("parse remail credential: %v", err)
	}
	if parsed["email"] != "user@example.com" || parsed["access_key"] != "service-token" {
		t.Fatalf("unexpected parsed remail credential: %#v", parsed)
	}
	if normalizeSunnyMailboxType("Remail邮箱") != "remail" || normalizeSunnyMailboxChannel("remail", "outlook") != "remail_api" {
		t.Fatalf("remail normalization failed")
	}
}

func TestRemailTokenPayloadRoundTrip(t *testing.T) {
	order := remailOrder{OrderNo: "R-1", ServiceToken: "st-1", ReceiveUntil: "2026-08-23T08:00:00Z"}
	raw := remailTokenPayload("https://remail.example", "secret", order)
	var payload map[string]any
	if err := json.Unmarshal([]byte(raw), &payload); err != nil {
		t.Fatalf("token payload is not JSON: %v", err)
	}
	if remailOrderNoFromAccessKey(raw) != "R-1" || remailServiceTokenFromAccessKey(raw) != "st-1" || remailBaseURLFromAccessKey(raw) != "https://remail.example" {
		t.Fatalf("token payload helpers returned unexpected values: %#v", payload)
	}
}
