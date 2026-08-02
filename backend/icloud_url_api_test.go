package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestParseSunnyMailboxLineForURLAPI(t *testing.T) {
	parsed, err := parseSunnyMailboxLineForProvider("alias@icloud.com----https://mail.example.test/latest", "apple", "url_api")
	if err != nil {
		t.Fatalf("parse url_api mailbox: %v", err)
	}
	if parsed["email"] != "alias@icloud.com" || parsed["access_key"] != "https://mail.example.test/latest" {
		t.Fatalf("unexpected parsed mailbox: %#v", parsed)
	}
	if _, err := parseSunnyMailboxLineForProvider("alias@icloud.com----not-a-url", "apple", "url_api"); err == nil {
		t.Fatal("expected invalid url_api URL to fail")
	}
}

func TestFetchURLAPILatestMailNormalizesHTML(t *testing.T) {
	urlAPIAllowPrivateForTests = true
	defer func() { urlAPIAllowPrivateForTests = false }()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write([]byte(`<html><h2>ChatGPT</h2><p>验证码 <b>123456</b></p></html>`))
	}))
	defer server.Close()

	payload, err := fetchURLAPILatestMail("alias@icloud.com", server.URL, 5, "")
	if err != nil {
		t.Fatalf("fetch url_api mail: %v", err)
	}
	items := payload["items"].([]map[string]any)
	if len(items) != 1 || items[0]["otp"] != "123456" || items[0]["source"] != "url_api" {
		t.Fatalf("unexpected normalized item: %#v", items)
	}
}

func TestURLAPISubjectSkipsMailboxHeading(t *testing.T) {
	raw := `<h2>person@icloud.com</h2><div>ChatGPT</div><div>Your ChatGPT temporary code</div>`
	if got := urlAPISubject(raw, urlAPIText(raw)); got != "Your ChatGPT temporary code" {
		t.Fatalf("unexpected subject %q", got)
	}
}
