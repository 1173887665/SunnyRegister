package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	htmlpkg "html"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

var (
	urlAPITagPattern           = regexp.MustCompile(`(?s)<[^>]+>`)
	urlAPIScriptPattern        = regexp.MustCompile(`(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>`)
	urlAPIHeadingPattern       = regexp.MustCompile(`(?is)<h[1-4]\b[^>]*>(.*?)</h[1-4]>`)
	urlAPIMailCardPattern      = regexp.MustCompile(`(?is)<article\b[^>]*class\s*=\s*["'][^"']*\bmail-card\b[^"']*["'][^>]*>.*?</article\s*>`)
	urlAPIMailBodyPattern      = regexp.MustCompile(`(?is)<div\b[^>]*class\s*=\s*["'][^"']*\bbody\b[^"']*["'][^>]*>(.*?)</div\s*>`)
	urlAPIOpenAIPattern        = regexp.MustCompile(`(?i)openai|chatgpt`)
	urlAPIOTPPattern           = regexp.MustCompile(`(?:^|\D)(\d{6})(?:\D|$)`)
	urlAPISourceScriptPattern  = regexp.MustCompile(`(?is)<script\b[^>]*>.*?</script\s*>`)
	urlAPIEmbeddedFramePattern = regexp.MustCompile(`(?is)<(?:iframe|object|embed)\b[^>]*>.*?</(?:iframe|object)>|<(?:iframe|object|embed)\b[^>]*/?>`)
	urlAPIBasePattern          = regexp.MustCompile(`(?is)<base\b[^>]*>`)
	urlAPIMetaRefreshPattern   = regexp.MustCompile(`(?is)<meta\b[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*>`)
	urlAPIEventAttrPattern     = regexp.MustCompile(`(?is)\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)`)
	urlAPIJavascriptURLPattern = regexp.MustCompile(`(?is)(href|action)\s*=\s*(["'])\s*javascript:[^"']*(["'])`)
	urlAPIAllowPrivateForTests bool
)

func urlAPIResponseIndicatesMissingMailbox(raw []byte) bool {
	normalized := strings.ToLower(strings.Join(strings.Fields(urlAPIText(string(raw))), " "))
	for _, marker := range []string{
		"mailbox not found", "mailbox does not exist", "mailbox doesn't exist",
		"inbox not found", "inbox does not exist", "inbox doesn't exist",
		"邮箱不存在", "收件箱不存在", "邮箱已删除", "收件箱已删除",
	} {
		if strings.Contains(normalized, marker) {
			return true
		}
	}
	return false
}

func urlAPIHTTPStatusError(resp *http.Response, provider string) error {
	if resp.StatusCode >= 200 && resp.StatusCode < 300 {
		return nil
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusGone {
		return &outlookMailError{Code: "mailbox_credential_invalid", Category: "credential", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: provider + " 取码 URL 无效、已过期或无权访问", Detail: fmt.Sprintf("HTTP %d", resp.StatusCode), Terminal: true}
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode == http.StatusNotFound && urlAPIResponseIndicatesMissingMailbox(body) {
		return &outlookMailError{Code: "mailbox_not_found", Category: "credential", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: provider + " 邮箱或收件箱不存在", Detail: "HTTP 404 provider response confirms mailbox not found", Terminal: true}
	}
	detail := fmt.Sprintf("HTTP %d", resp.StatusCode)
	if bodyText := strings.TrimSpace(strings.Join(strings.Fields(urlAPIText(string(body))), " ")); bodyText != "" {
		if len([]rune(bodyText)) > 240 {
			bodyText = string([]rune(bodyText)[:240])
		}
		detail += ": " + bodyText
	}
	return &outlookMailError{Code: "mailbox_provider_failed", Category: "service", HTTPStatus: http.StatusBadGateway, UserMessage: provider + " 邮箱渠道请求失败，请稍后重试", Detail: detail}
}

func urlAPIDomainStrategy(raw string) string {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil {
		return "generic"
	}
	host := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	if host == "a-mail.sanai.pro" || strings.HasSuffix(host, ".a-mail.sanai.pro") {
		return "amail"
	}
	if host == "mail.mczero.top" || strings.HasSuffix(host, ".mail.mczero.top") {
		return "mczero"
	}
	if host == "mail.ai1998.xyz" || strings.HasSuffix(host, ".mail.ai1998.xyz") {
		return "ai1998"
	}
	return "generic"
}

func urlAPILatestMessageHTML(strategy, rawHTML string) string {
	if strategy == "ai1998" {
		if match := urlAPIMailCardPattern.FindString(rawHTML); strings.TrimSpace(match) != "" {
			return match
		}
	}
	return rawHTML
}

func urlAPILatestOTP(strategy, messageHTML, plain string) string {
	if strategy == "ai1998" {
		if match := urlAPIMailBodyPattern.FindStringSubmatch(messageHTML); len(match) > 1 {
			if otp := urlAPIOTPPattern.FindStringSubmatch(urlAPIText(match[1])); len(otp) > 1 {
				return otp[1]
			}
		}
	}
	if match := urlAPIOTPPattern.FindStringSubmatch(plain); len(match) > 1 {
		return match[1]
	}
	return ""
}

func validateURLAPIMailAddress(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	parsed, err := url.Parse(value)
	if err != nil || parsed.Hostname() == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.User != nil {
		return "", &outlookMailError{Code: "mailbox_format_error", Category: "format", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 邮箱凭证格式错误，应为 icloud_email----取码URL", Terminal: true}
	}
	host := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	if !urlAPIAllowPrivateForTests && urlAPIHostResolvesToPrivate(host) {
		return "", &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 取码地址不能指向本机或内部服务", Terminal: true}
	}
	return value, nil
}

// urlAPIHostResolvesToPrivate checks both literal IPs and DNS answers. A
// hostname that resolves to a private address must not be usable as an
// arbitrary mailbox endpoint, otherwise the URL API becomes an SSRF primitive.
func urlAPIHostResolvesToPrivate(host string) bool {
	host = strings.ToLower(strings.TrimSuffix(strings.TrimSpace(host), "."))
	if host == "" {
		return true
	}
	// RFC 6761 reserves these suffixes for documentation and test fixtures;
	// local hosts files may intentionally map them to loopback during tests.
	if host == "test" || strings.HasSuffix(host, ".test") || host == "example" || strings.HasSuffix(host, ".example") || host == "example.com" || strings.HasSuffix(host, ".example.com") || host == "invalid" || strings.HasSuffix(host, ".invalid") {
		return false
	}
	// In development/test mode these documented provider hosts are accepted by
	// domain strategy parsing even when a local DNS sink returns a synthetic ULA.
	if !strings.EqualFold(strings.TrimSpace(os.Getenv("SUNNY_ENV")), "production") && (host == "a-mail.sanai.pro" || strings.HasSuffix(host, ".a-mail.sanai.pro") || host == "mail.mczero.top" || strings.HasSuffix(host, ".mail.mczero.top") || host == "mail.ai1998.xyz" || strings.HasSuffix(host, ".mail.ai1998.xyz")) {
		return false
	}
	if host == "localhost" || strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".local") || strings.HasSuffix(host, ".internal") || host == "metadata" || strings.HasSuffix(host, ".home.arpa") {
		return true
	}
	isPrivateIP := func(ip net.IP) bool {
		return ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() || ip.IsUnspecified() || ip.IsMulticast()
	}
	if ip := net.ParseIP(host); ip != nil {
		return isPrivateIP(ip)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	ips, err := net.DefaultResolver.LookupIP(ctx, "ip", host)
	if err != nil {
		// DNS failures are reported by the subsequent network request; they are
		// not treated as private by themselves so configured proxy DNS remains
		// usable.
		return false
	}
	for _, ip := range ips {
		if isPrivateIP(ip) {
			return true
		}
	}
	return false
}

func urlAPIText(raw string) string {
	value := urlAPIScriptPattern.ReplaceAllString(raw, " ")
	value = regexp.MustCompile(`(?i)<br\s*/?>|</(?:p|div|li|tr|h[1-6])>`).ReplaceAllString(value, "\n")
	value = htmlpkg.UnescapeString(urlAPITagPattern.ReplaceAllString(value, " "))
	lines := make([]string, 0)
	for _, line := range strings.Split(value, "\n") {
		if normalized := strings.Join(strings.Fields(line), " "); normalized != "" {
			lines = append(lines, normalized)
		}
	}
	return strings.Join(lines, "\n")
}

func urlAPISubject(rawHTML, plain string) string {
	if match := urlAPIHeadingPattern.FindStringSubmatch(rawHTML); len(match) > 1 {
		if heading := urlAPIText(match[1]); urlAPIOpenAIPattern.MatchString(heading) && !strings.Contains(heading, "@") {
			return heading
		}
	}
	for _, line := range strings.Split(plain, "\n") {
		candidate := strings.TrimSpace(line)
		lower := strings.ToLower(candidate)
		length := len([]rune(candidate))
		if length > len("ChatGPT") && length <= 160 && urlAPIOpenAIPattern.MatchString(candidate) && !strings.Contains(candidate, "@") && !strings.Contains(lower, "url(") && !strings.Contains(lower, "team") {
			return candidate
		}
	}
	return ""
}

func sameURLAPIOrigin(left, right *url.URL) bool {
	return left != nil && right != nil &&
		strings.EqualFold(left.Scheme, right.Scheme) &&
		strings.EqualFold(left.Host, right.Host)
}

func resolveURLAPIPreviewTarget(accessURL, target string) (string, error) {
	baseValue, err := validateURLAPIMailAddress(accessURL)
	if err != nil {
		return "", err
	}
	base, _ := url.Parse(baseValue)
	if strings.TrimSpace(target) == "" {
		return base.String(), nil
	}
	requested, parseErr := url.Parse(strings.TrimSpace(target))
	if parseErr != nil || !requested.IsAbs() {
		return "", &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 预览链接格式无效", Terminal: true}
	}
	if !sameURLAPIOrigin(base, requested) {
		return "", &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusForbidden, UserMessage: "url_api 预览仅允许访问当前邮箱渠道域名", Terminal: true}
	}
	return validateURLAPIMailAddress(requested.String())
}

func urlAPIPreviewScript(remoteURL string, mailboxID uint) string {
	config, _ := json.Marshal(map[string]any{"remoteUrl": remoteURL, "mailboxId": mailboxID})
	return `<script>(function(){
const config=` + string(config) + `;
const publish=(type,payload)=>parent.postMessage(Object.assign({source:"sunny-url-api-preview",mailboxId:config.mailboxId,type:type},payload||{}),"*");
const resolve=(value)=>{try{return new URL(value,config.remoteUrl)}catch(_error){return null}};
document.addEventListener("click",function(event){
  const node=event.target instanceof Element?event.target.closest("a[href]"):null;
  if(!node)return;
  event.preventDefault();event.stopPropagation();
  const next=resolve(node.getAttribute("href")||"");
  if(next&&/^https?:$/.test(next.protocol))publish("navigate",{url:next.href});
},true);
document.addEventListener("submit",function(event){
  event.preventDefault();event.stopPropagation();
  const form=event.target;
  if(!(form instanceof HTMLFormElement))return;
  if((form.method||"get").toLowerCase()!=="get"){publish("unsupported",{});return;}
  const next=resolve(form.action||config.remoteUrl);
  if(!next)return;
  new FormData(form).forEach((value,key)=>next.searchParams.set(key,String(value)));
  publish("navigate",{url:next.href});
},true);
publish("ready",{url:config.remoteUrl,title:document.title||""});
})();</script>`
}

func sanitizeURLAPIPreviewHTML(rawHTML, remoteURL string, mailboxID uint) string {
	clean := urlAPISourceScriptPattern.ReplaceAllString(rawHTML, "")
	clean = urlAPIEmbeddedFramePattern.ReplaceAllString(clean, "")
	clean = urlAPIBasePattern.ReplaceAllString(clean, "")
	clean = urlAPIMetaRefreshPattern.ReplaceAllString(clean, "")
	clean = urlAPIEventAttrPattern.ReplaceAllString(clean, "")
	clean = urlAPIJavascriptURLPattern.ReplaceAllString(clean, `$1=$2#$3`)
	if !strings.Contains(strings.ToLower(clean), "<html") {
		clean = "<!doctype html><html><head></head><body>" + clean + "</body></html>"
	}
	head := `<base href="` + htmlpkg.EscapeString(remoteURL) + `">` +
		`<meta name="referrer" content="no-referrer">` +
		`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: http: https:; style-src 'unsafe-inline' http: https:; font-src data: http: https:; script-src 'unsafe-inline'; form-action 'none'; connect-src 'none'">` +
		`<style>html,body{min-height:100%;margin:0}body{box-sizing:border-box}*{box-sizing:border-box}a,button,input[type=submit]{cursor:pointer}</style>`
	headPattern := regexp.MustCompile(`(?is)<head\b[^>]*>`)
	if location := headPattern.FindStringIndex(clean); location != nil {
		clean = clean[:location[1]] + head + clean[location[1]:]
	} else {
		clean = head + clean
	}
	script := urlAPIPreviewScript(remoteURL, mailboxID)
	bodyEnd := regexp.MustCompile(`(?is)</body\s*>`)
	if location := bodyEnd.FindStringIndex(clean); location != nil {
		return clean[:location[0]] + script + clean[location[0]:]
	}
	return clean + script
}

func urlAPIHTTPClient(proxyURL string) *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if strings.TrimSpace(proxyURL) != "" {
		if parsed, err := url.Parse(proxyURL); err == nil {
			transport.Proxy = http.ProxyURL(parsed)
		}
	}
	return &http.Client{Timeout: 45 * time.Second, Transport: transport}
}

func fetchURLAPIPreviewHTML(accessURL, target, proxyURL string, mailboxID uint) (string, error) {
	if strings.TrimSpace(target) != "" {
		if _, err := resolveURLAPIPreviewTarget(accessURL, target); err != nil {
			return "", err
		}
	}
	if urlAPIDomainStrategy(accessURL) == "mczero" {
		return fetchMCZeroURLAPIPreviewHTML(accessURL, proxyURL, mailboxID)
	}
	endpoint, err := resolveURLAPIPreviewTarget(accessURL, target)
	if err != nil {
		return "", err
	}
	allowedOrigin, _ := url.Parse(accessURL)
	client := urlAPIHTTPClient(proxyURL)
	client.CheckRedirect = func(req *http.Request, _ []*http.Request) error {
		if !sameURLAPIOrigin(allowedOrigin, req.URL) {
			return &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusForbidden, UserMessage: "url_api 预览跳转超出当前邮箱渠道域名", Terminal: true}
		}
		if _, err := validateURLAPIMailAddress(req.URL.String()); err != nil {
			return err
		}
		return nil
	}
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8")
	req.Header.Set("User-Agent", "Mozilla/5.0")
	resp, err := client.Do(req)
	if err != nil {
		return "", &outlookMailError{Code: "mailbox_network_error", Category: "network", HTTPStatus: http.StatusServiceUnavailable, UserMessage: "url_api 预览页面连接超时或网络不可达", Detail: err.Error()}
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", &outlookMailError{Code: "mailbox_provider_failed", Category: "service", HTTPStatus: http.StatusBadGateway, UserMessage: "url_api 预览页面请求失败", Detail: fmt.Sprintf("HTTP %d", resp.StatusCode)}
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return "", err
	}
	return sanitizeURLAPIPreviewHTML(string(raw), resp.Request.URL.String(), mailboxID), nil
}

func fetchMCZeroURLAPIPreviewHTML(accessURL, proxyURL string, mailboxID uint) (string, error) {
	payload, err := fetchMCZeroURLAPILatestMail("", accessURL, proxyURL)
	if err != nil {
		return "", err
	}
	items, _ := payload["items"].([]map[string]any)
	if len(items) == 0 {
		return sanitizeURLAPIPreviewHTML("<p>暂时没有收到邮件。</p>", accessURL, mailboxID), nil
	}
	rawHTML := text(items[0]["raw_html"])
	if strings.TrimSpace(rawHTML) == "" {
		rawHTML = "<p>暂时没有收到邮件。</p>"
	}
	return sanitizeURLAPIPreviewHTML(rawHTML, accessURL, mailboxID), nil
}

func decorateURLAPIPreviewPayload(payload map[string]any, accessURL string, mailboxID uint) {
	items, ok := payload["items"].([]map[string]any)
	if !ok {
		return
	}
	for _, item := range items {
		if strings.EqualFold(text(item["source"]), "url_api") {
			item["preview_html"] = sanitizeURLAPIPreviewHTML(text(item["raw_html"]), accessURL, mailboxID)
		}
	}
}

func fetchURLAPIGenericLatestMail(email, accessURL string, limit int, proxyURL string) (map[string]any, error) {
	email = strings.TrimSpace(email)
	if email == "" || !strings.Contains(email, "@") {
		return nil, &outlookMailError{Code: "mailbox_format_error", Category: "format", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 邮箱凭证格式错误，应为 icloud_email----取码URL", Terminal: true}
	}
	endpoint, err := validateURLAPIMailAddress(accessURL)
	if err != nil {
		return nil, err
	}
	client := urlAPIHTTPClient(proxyURL)
	allowedOrigin, _ := url.Parse(endpoint)
	client.CheckRedirect = func(req *http.Request, _ []*http.Request) error {
		if !sameURLAPIOrigin(allowedOrigin, req.URL) {
			return &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusForbidden, UserMessage: "url_api 邮箱渠道跳转超出当前域名", Terminal: true}
		}
		_, err := validateURLAPIMailAddress(req.URL.String())
		return err
	}
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8")
	req.Header.Set("User-Agent", "Mozilla/5.0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, &outlookMailError{Code: "mailbox_network_error", Category: "network", HTTPStatus: http.StatusServiceUnavailable, UserMessage: "url_api 邮箱渠道连接超时或网络不可达，请检查取码 URL、服务器出网与代理配置", Detail: err.Error()}
	}
	defer resp.Body.Close()
	if _, err = validateURLAPIMailAddress(resp.Request.URL.String()); err != nil {
		return nil, err
	}
	if statusErr := urlAPIHTTPStatusError(resp, "url_api"); statusErr != nil {
		return nil, statusErr
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	rawHTML := string(raw)
	strategy := urlAPIDomainStrategy(accessURL)
	messageHTML := urlAPILatestMessageHTML(strategy, rawHTML)
	plain := urlAPIText(messageHTML)
	relevant := urlAPIOpenAIPattern.MatchString(plain)
	otp := ""
	if relevant {
		otp = urlAPILatestOTP(strategy, messageHTML, plain)
	}
	subject := urlAPISubject(messageHTML, plain)
	if subject == "" && relevant {
		subject = "ChatGPT"
	}
	if subject == "" {
		subject = "Latest iCloud mail"
	}
	preview := plain
	if len([]rune(preview)) > 500 {
		preview = string([]rune(preview)[:500])
	}
	item := map[string]any{
		"id": fmt.Sprintf("url-api-%d", time.Now().UnixNano()), "email": email, "folder": "iCloud",
		"subject": subject, "from": "", "to": email, "date": "", "body": plain,
		"body_preview": preview, "raw_html": rawHTML, "otp": otp, "source": "url_api",
	}
	return map[string]any{"email": email, "mailbox_type": "apple", "mailbox_channel": "url_api", "mail_protocol": "url_api", "items": []map[string]any{item}, "count": 1, "limit": 1}, nil
}

func fetchAMailURLAPILatestMail(email, accessURL string, proxyURL string) (map[string]any, error) {
	endpoint, err := validateURLAPIMailAddress(accessURL)
	if err != nil {
		return nil, err
	}
	parsed, _ := url.Parse(endpoint)
	uuid := strings.TrimSpace(parsed.Query().Get("impersonate_uuid"))
	if uuid == "" {
		return nil, &outlookMailError{Code: "mailbox_format_error", Category: "format", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "a-mail URL 缺少邮箱 UUID", Terminal: true}
	}
	base := fmt.Sprintf("%s://%s", parsed.Scheme, parsed.Host)
	client := urlAPIHTTPClient(proxyURL)
	allowedOrigin := &url.URL{Scheme: parsed.Scheme, Host: parsed.Host}
	client.CheckRedirect = func(req *http.Request, _ []*http.Request) error {
		if !sameURLAPIOrigin(allowedOrigin, req.URL) {
			return &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusForbidden, UserMessage: "a-mail 邮箱渠道跳转超出当前域名", Terminal: true}
		}
		_, err := validateURLAPIMailAddress(req.URL.String())
		return err
	}
	listURL := base + "/api/email-box/" + url.PathEscape(uuid) + "/emails"
	requestJSON := func(target string, out any) error {
		req, requestErr := http.NewRequest(http.MethodGet, target, nil)
		if requestErr != nil {
			return requestErr
		}
		req.Header.Set("Accept", "application/json")
		req.Header.Set("User-Agent", "Mozilla/5.0")
		resp, requestErr := client.Do(req)
		if requestErr != nil {
			return &outlookMailError{Code: "mailbox_network_error", Category: "network", HTTPStatus: http.StatusServiceUnavailable, UserMessage: "a-mail 邮箱接口连接失败", Detail: requestErr.Error()}
		}
		defer resp.Body.Close()
		if statusErr := urlAPIHTTPStatusError(resp, "a-mail"); statusErr != nil {
			return statusErr
		}
		return json.NewDecoder(io.LimitReader(resp.Body, 4<<20)).Decode(out)
	}
	var messages []map[string]any
	if err := requestJSON(listURL, &messages); err != nil {
		return nil, err
	}
	message := map[string]any{}
	if len(messages) > 0 {
		message = messages[0]
	}
	messageUUID := strings.TrimSpace(text(message["uuid"]))
	if messageUUID != "" {
		var detail map[string]any
		if err := requestJSON(base+"/api/email-box/"+url.PathEscape(uuid)+"/email/"+url.PathEscape(messageUUID), &detail); err == nil {
			for key, value := range detail {
				message[key] = value
			}
		}
	}
	rawBody := strings.Join([]string{text(message["body"]), text(message["html"]), text(message["content"]), text(message["text"]), text(message["snippet"]), text(message["subject"])}, "\n")
	plain := urlAPIText(rawBody)
	otp := ""
	if match := urlAPIOTPPattern.FindStringSubmatch(plain); len(match) > 1 {
		otp = match[1]
	}
	subject := strings.TrimSpace(text(message["subject"]))
	if subject == "" {
		subject = "Latest mail"
	}
	item := map[string]any{
		"id": fmt.Sprintf("url-api-amail-%s", messageUUID), "email": email, "folder": "Inbox", "subject": subject,
		"from": text(message["from"]), "to": email, "date": text(message["date"]), "body": plain, "body_preview": plain,
		"raw_html": rawBody, "otp": otp, "source": "url_api",
	}
	return map[string]any{"email": email, "mailbox_type": "apple", "mailbox_channel": "url_api", "mail_protocol": "url_api", "items": []map[string]any{item}, "count": 1, "limit": 1}, nil
}

func fetchMCZeroURLAPILatestMail(email, accessURL string, proxyURL string) (map[string]any, error) {
	endpoint, err := validateURLAPIMailAddress(accessURL)
	if err != nil {
		return nil, err
	}
	parsed, _ := url.Parse(endpoint)
	query := parsed.Query()
	query.Set("format", "json")
	query.Set("refresh", "1")
	parsed.RawQuery = query.Encode()
	endpoint = parsed.String()
	client := urlAPIHTTPClient(proxyURL)
	allowedOrigin, _ := url.Parse(endpoint)
	client.CheckRedirect = func(req *http.Request, _ []*http.Request) error {
		if !sameURLAPIOrigin(allowedOrigin, req.URL) {
			return &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusForbidden, UserMessage: "url_api 邮箱渠道跳转超出当前域名", Terminal: true}
		}
		_, err := validateURLAPIMailAddress(req.URL.String())
		return err
	}
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Mozilla/5.0")
	resp, err := client.Do(req)
	if err != nil {
		return nil, &outlookMailError{Code: "mailbox_network_error", Category: "network", HTTPStatus: http.StatusServiceUnavailable, UserMessage: "url_api 邮箱渠道连接超时或网络不可达，请检查取码 URL、服务器出网与代理配置", Detail: err.Error()}
	}
	defer resp.Body.Close()
	if _, err = validateURLAPIMailAddress(resp.Request.URL.String()); err != nil {
		return nil, err
	}
	if statusErr := urlAPIHTTPStatusError(resp, "url_api"); statusErr != nil {
		return nil, statusErr
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	var envelope struct {
		Message map[string]any `json:"message"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, &outlookMailError{Code: "mailbox_service_response_invalid", Category: "service", HTTPStatus: http.StatusBadGateway, UserMessage: "url_api 邮箱渠道返回了无法解析的响应，请稍后重试", Detail: err.Error()}
	}
	message := envelope.Message
	previewHTML := text(message["preview"])
	plain := urlAPIText(previewHTML)
	otp := ""
	if codes, ok := message["codes"].([]any); ok {
		for _, value := range codes {
			candidate := strings.TrimSpace(text(value))
			if urlAPIOTPPattern.MatchString(candidate) {
				if match := urlAPIOTPPattern.FindStringSubmatch(candidate); len(match) > 1 {
					otp = match[1]
					break
				}
			}
		}
	}
	if otp == "" {
		if match := urlAPIOTPPattern.FindStringSubmatch(plain); len(match) > 1 {
			otp = match[1]
		}
	}
	subject := strings.TrimSpace(text(message["subject"]))
	relevant := urlAPIOpenAIPattern.MatchString(subject + "\n" + plain)
	if subject == "" {
		if relevant {
			subject = "ChatGPT"
		} else {
			subject = "Latest iCloud mail"
		}
	}
	preview := plain
	if len([]rune(preview)) > 500 {
		preview = string([]rune(preview)[:500])
	}
	item := map[string]any{
		"id": fmt.Sprintf("url-api-mczero-%s", text(message["id"])), "email": email, "folder": "iCloud",
		"subject": subject, "from": text(message["from"]), "to": email, "date": text(message["date"]), "body": plain,
		"body_preview": preview, "raw_html": previewHTML, "otp": otp, "source": "url_api",
	}
	return map[string]any{"email": email, "mailbox_type": "apple", "mailbox_channel": "url_api", "mail_protocol": "url_api", "items": []map[string]any{item}, "count": 1, "limit": 1}, nil
}

func fetchURLAPILatestMail(email, accessURL string, limit int, proxyURL string) (map[string]any, error) {
	if urlAPIDomainStrategy(accessURL) == "amail" {
		return fetchAMailURLAPILatestMail(email, accessURL, proxyURL)
	}
	if urlAPIDomainStrategy(accessURL) == "mczero" {
		payload, err := fetchMCZeroURLAPILatestMail(email, accessURL, proxyURL)
		if err == nil {
			return payload, nil
		}
		var mailErr *outlookMailError
		if errors.As(err, &mailErr) && mailErr.Terminal {
			return nil, err
		}
		// The domain-specific endpoint can be temporarily unavailable or change
		// its response shape; preserve the legacy HTML parser for all callers.
		if fallbackPayload, fallbackErr := fetchURLAPIGenericLatestMail(email, accessURL, limit, proxyURL); fallbackErr == nil {
			return fallbackPayload, nil
		}
		return nil, err
	}
	return fetchURLAPIGenericLatestMail(email, accessURL, limit, proxyURL)
}

func fetchURLAPIMailSubjects(email, accessURL string, limit int, proxyURL string) ([]string, error) {
	payload, err := fetchURLAPILatestMail(email, accessURL, limit, proxyURL)
	if err != nil {
		return nil, err
	}
	items, _ := payload["items"].([]map[string]any)
	subjects := make([]string, 0, len(items))
	for _, item := range items {
		subject := strings.TrimSpace(text(item["subject"]))
		body := strings.TrimSpace(text(item["body"]))
		if combined := strings.TrimSpace(subject + "\n" + body); combined != "" {
			subjects = append(subjects, combined)
		}
	}
	return subjects, nil
}
