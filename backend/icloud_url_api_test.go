package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
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

func TestURLAPIPreviewRejectsCrossOriginNavigation(t *testing.T) {
	urlAPIAllowPrivateForTests = true
	defer func() { urlAPIAllowPrivateForTests = false }()
	_, err := resolveURLAPIPreviewTarget("https://mail.example.test/inbox/key", "https://other.example.test/page")
	if err == nil {
		t.Fatal("expected cross-origin preview navigation to be rejected")
	}
}

func TestSanitizeURLAPIPreviewHTMLKeepsLayoutAndAddsNavigationBridge(t *testing.T) {
	raw := `<html><head><style>.mail{color:blue}</style><script>alert("bad")</script></head><body onclick="bad()"><iframe src="https://evil.test"></iframe><a href="/all">All mail</a><form action="/search" method="get"><input name="q" value="otp"><button>Search</button></form></body></html>`
	page := sanitizeURLAPIPreviewHTML(raw, "https://mail.example.test/inbox/key", 42)
	for _, forbidden := range []string{"alert(\"bad\")", "onclick=", "<iframe"} {
		if strings.Contains(page, forbidden) {
			t.Fatalf("preview retained unsafe content %q: %s", forbidden, page)
		}
	}
	for _, expected := range []string{".mail{color:blue}", `<base href="https://mail.example.test/inbox/key">`, `sunny-url-api-preview`, `"mailboxId":42`, `<a href="/all">`} {
		if !strings.Contains(page, expected) {
			t.Fatalf("preview missing %q: %s", expected, page)
		}
	}
}

func TestFetchURLAPIPreviewHTMLSupportsSameOriginLinks(t *testing.T) {
	urlAPIAllowPrivateForTests = true
	defer func() { urlAPIAllowPrivateForTests = false }()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write([]byte(`<html><body><h1>` + r.URL.Path + `</h1><a href="/all">All</a></body></html>`))
	}))
	defer server.Close()

	page, err := fetchURLAPIPreviewHTML(server.URL+"/inbox", server.URL+"/all", "", 7)
	if err != nil {
		t.Fatalf("fetch preview navigation: %v", err)
	}
	if !strings.Contains(page, "<h1>/all</h1>") || !strings.Contains(page, `"mailboxId":7`) {
		t.Fatalf("unexpected preview page: %s", page)
	}
}

func TestDecorateURLAPIPreviewPayloadOnlyChangesURLAPIItems(t *testing.T) {
	urlItem := map[string]any{"source": "url_api", "raw_html": `<html><body><a href="/all">All</a></body></html>`}
	graphItem := map[string]any{"source": "graph", "raw_html": `<html><body>Graph</body></html>`}
	payload := map[string]any{"items": []map[string]any{urlItem, graphItem}}
	decorateURLAPIPreviewPayload(payload, "https://mail.example.test/inbox", 9)
	if strings.TrimSpace(text(urlItem["preview_html"])) == "" {
		t.Fatal("expected url_api item to receive browser preview html")
	}
	if _, exists := graphItem["preview_html"]; exists {
		t.Fatal("non-url_api mail item must remain unchanged")
	}
}
