package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestParseSunnyMailboxLineForURLAPI(t *testing.T) {
	const secret = "JBSWY3DPEHPK3PXP"
	tests := []struct {
		line, password, url, totp string
	}{
		{"alias@icloud.com", "", "", ""},
		{"alias@icloud.com----chatgpt-password", "chatgpt-password", "", ""},
		{"alias@icloud.com----https://mail.example.test/latest", "", "https://mail.example.test/latest", ""},
		{"alias@icloud.com----chatgpt-password----" + secret, "chatgpt-password", "", secret},
		{"alias@icloud.com----chatgpt-password----https://mail.example.test/latest", "chatgpt-password", "https://mail.example.test/latest", ""},
		{"alias@icloud.com----chatgpt-password----https://mail.example.test/latest----" + secret, "chatgpt-password", "https://mail.example.test/latest", secret},
		{"alias@icloud.com----https://mail.example.test/latest----" + secret, "", "https://mail.example.test/latest", secret},
	}
	for _, test := range tests {
		parsed, err := parseSunnyMailboxLineForProvider(test.line, "apple", "url_api")
		if err != nil {
			t.Fatalf("parse %q: %v", test.line, err)
		}
		if parsed["email"] != "alias@icloud.com" || parsed["chatgpt_password"] != test.password || parsed["access_key"] != test.url || parsed["totp_secret"] != test.totp {
			t.Fatalf("unexpected parsed mailbox for %q: %#v", test.line, parsed)
		}
		wantCredential := parsed["email"]
		if parsed["access_key"] != "" {
			wantCredential += "----" + parsed["access_key"]
		}
		if got := sunnyURLAPIRaw(parsed["email"], parsed["access_key"]); got != wantCredential {
			t.Fatalf("canonical credential mismatch: got %q want %q", got, wantCredential)
		}
	}
	for _, invalid := range []string{
		"alias@icloud.com----password----not-base32",
		"alias@icloud.com----https://one.example.test----https://two.example.test",
		"alias@icloud.com----password----https://mail.example.test--------" + secret,
	} {
		if _, err := parseSunnyMailboxLineForProvider(invalid, "apple", "url_api"); err == nil {
			t.Fatalf("expected %q to fail", invalid)
		}
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

func TestURLAPIHTTPStatusErrorClassifiesForbiddenBody(t *testing.T) {
	tests := []struct {
		name, body, code string
	}{
		{"password", "403 forbidden: temporary key file requires password", "mailbox_password_protected"},
		{"expired", "token expired", "mailbox_credential_expired"},
		{"ip", "access denied by ip region policy", "mailbox_ip_restricted"},
		{"unknown", "access denied", "mailbox_access_forbidden"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "https://mail.example.test/inbox", nil)
			err := urlAPIHTTPStatusError(&http.Response{StatusCode: http.StatusForbidden, Body: io.NopCloser(strings.NewReader(test.body)), Request: req}, "url_api")
			mailErr, ok := err.(*outlookMailError)
			if !ok || mailErr.Code != test.code || !mailErr.Terminal {
				t.Fatalf("expected code %q, got %#v", test.code, err)
			}
		})
	}
}

func TestFetchURLAPI404WithoutMatchingMailIsRetryable(t *testing.T) {
	urlAPIAllowPrivateForTests = true
	defer func() { urlAPIAllowPrivateForTests = false }()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"message":"最近5封邮件中没有找到该邮箱的邮件","found":false,"ok":false}`))
	}))
	defer server.Close()

	_, err := fetchURLAPILatestMail("alias@icloud.com", server.URL, 1, "")
	mailErr, ok := err.(*outlookMailError)
	if !ok || mailErr.Code != "mailbox_provider_failed" || mailErr.Terminal {
		t.Fatalf("expected retryable empty-inbox response, got %#v", err)
	}
}

func TestFetchURLAPI404ExplicitMissingMailboxIsTerminal(t *testing.T) {
	urlAPIAllowPrivateForTests = true
	defer func() { urlAPIAllowPrivateForTests = false }()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"message":"邮箱不存在"}`))
	}))
	defer server.Close()

	_, err := fetchURLAPILatestMail("alias@icloud.com", server.URL, 1, "")
	mailErr, ok := err.(*outlookMailError)
	if !ok || mailErr.Code != "mailbox_not_found" || !mailErr.Terminal {
		t.Fatalf("expected terminal missing-mailbox response, got %#v", err)
	}
}

func TestFetchMCZeroURLAPILatestMailParsesJSONCodes(t *testing.T) {
	urlAPIAllowPrivateForTests = true
	defer func() { urlAPIAllowPrivateForTests = false }()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("format") != "json" || r.URL.Query().Get("refresh") != "1" {
			t.Fatalf("unexpected query: %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"state":"ready","message":{"id":"message-1","date":"2026-08-15T12:18:30Z","from":"ChatGPT <noreply@icloud.com>","subject":"ChatGPT verification code","codes":["978744","978744"],"preview":"<p>ChatGPT verification code</p><p>978744</p>"}}`))
	}))
	defer server.Close()

	payload, err := fetchMCZeroURLAPILatestMail("alias@icloud.com", server.URL+"/s/token/alias@icloud.com", "")
	if err != nil {
		t.Fatalf("fetch mczero url_api mail: %v", err)
	}
	items := payload["items"].([]map[string]any)
	if len(items) != 1 || items[0]["otp"] != "978744" || items[0]["source"] != "url_api" {
		t.Fatalf("unexpected normalized item: %#v", items)
	}
}

func TestURLAPIDomainStrategy(t *testing.T) {
	if got := urlAPIDomainStrategy("https://mail.mczero.top/s/key/user@icloud.com"); got != "mczero" {
		t.Fatalf("expected mczero strategy, got %q", got)
	}
	if got := urlAPIDomainStrategy("https://mail.example.test/latest"); got != "generic" {
		t.Fatalf("expected generic strategy, got %q", got)
	}
	if got := urlAPIDomainStrategy("https://mail.ai1998.xyz/messages/key/user%40icloud.com"); got != "ai1998" {
		t.Fatalf("expected ai1998 strategy, got %q", got)
	}
}

func TestURLAPIAI1998UsesFirstLatestMailCard(t *testing.T) {
	raw := `<html><body>
<article class="mail-card"><span class="subject">ChatGPT latest</span><span class="date">2026-08-20 10:36:05</span><div class="meta">sender: noreply_602613@icloud.com</div><div class="body body-rich">Verification code: 904540</div></article>
<article class="mail-card"><span class="subject">ChatGPT old</span><div class="body body-rich">Verification code: 111111</div></article>
</body></html>`
	latest := urlAPILatestMessageHTML("ai1998", raw)
	if !strings.Contains(latest, "904540") || strings.Contains(latest, "111111") {
		t.Fatalf("unexpected latest mail card: %s", latest)
	}
	if got := urlAPILatestMessageHTML("generic", raw); got != raw {
		t.Fatal("generic strategy must preserve the complete response")
	}
	if got := urlAPILatestOTP("ai1998", latest, urlAPIText(latest)); got != "904540" {
		t.Fatalf("expected body OTP 904540 instead of sender noise, got %q", got)
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

func TestValidateURLAPIMailAddressRejectsPrivateTargets(t *testing.T) {
	for _, raw := range []string{
		"http://127.0.0.1/inbox",
		"http://10.0.0.5/inbox",
		"http://[::1]/inbox",
		"http://service.local/inbox",
		"http://metadata/inbox",
	} {
		if _, err := validateURLAPIMailAddress(raw); err == nil {
			t.Fatalf("private URL %q was accepted", raw)
		}
	}
}

func TestValidateURLAPIMailAddressRejectsURLCredentials(t *testing.T) {
	if _, err := validateURLAPIMailAddress("https://user:pass@example.test/inbox"); err == nil {
		t.Fatal("URL userinfo must not be accepted as a mailbox credential")
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

func TestFetchMCZeroURLAPIPreviewHTMLUsesJSONMailPreview(t *testing.T) {
	urlAPIAllowPrivateForTests = true
	defer func() { urlAPIAllowPrivateForTests = false }()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("format") != "json" || r.URL.Query().Get("refresh") != "1" {
			t.Fatalf("unexpected query: %s", r.URL.RawQuery)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"state":"ready","message":{"id":"message-1","preview":"<html><body><h1>ChatGPT</h1><p>验证码 <b>978744</b></p><script>alert('remove')</script></body></html>"}}`))
	}))
	defer server.Close()

	page, err := fetchMCZeroURLAPIPreviewHTML(server.URL+"/s/token/alias@icloud.com", "", 12)
	if err != nil {
		t.Fatalf("fetch mczero preview: %v", err)
	}
	if !strings.Contains(page, "978744") || strings.Contains(page, "alert('remove')") || !strings.Contains(page, `"mailboxId":12`) {
		t.Fatalf("unexpected mczero preview: %s", page)
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
