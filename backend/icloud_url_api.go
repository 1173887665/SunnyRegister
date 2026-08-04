package main

import (
	"encoding/json"
	"fmt"
	htmlpkg "html"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

var (
	urlAPITagPattern           = regexp.MustCompile(`(?s)<[^>]+>`)
	urlAPIScriptPattern        = regexp.MustCompile(`(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>`)
	urlAPIHeadingPattern       = regexp.MustCompile(`(?is)<h[1-4]\b[^>]*>(.*?)</h[1-4]>`)
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

func validateURLAPIMailAddress(raw string) (string, error) {
	value := strings.TrimSpace(raw)
	parsed, err := url.Parse(value)
	if err != nil || parsed.Hostname() == "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return "", &outlookMailError{Code: "mailbox_format_error", Category: "format", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 邮箱凭证格式错误，应为 icloud_email----取码URL", Terminal: true}
	}
	host := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	if !urlAPIAllowPrivateForTests && (host == "localhost" || strings.HasSuffix(host, ".localhost") || strings.HasSuffix(host, ".local")) {
		return "", &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 取码地址不能指向本机或内部服务", Terminal: true}
	}
	if ip := net.ParseIP(host); !urlAPIAllowPrivateForTests && ip != nil && (ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified()) {
		return "", &outlookMailError{Code: "mailbox_url_forbidden", Category: "security", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 取码地址不能指向私有网络", Terminal: true}
	}
	return value, nil
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

func fetchURLAPILatestMail(email, accessURL string, limit int, proxyURL string) (map[string]any, error) {
	email = strings.TrimSpace(email)
	if email == "" || !strings.Contains(email, "@") {
		return nil, &outlookMailError{Code: "mailbox_format_error", Category: "format", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 邮箱凭证格式错误，应为 icloud_email----取码URL", Terminal: true}
	}
	endpoint, err := validateURLAPIMailAddress(accessURL)
	if err != nil {
		return nil, err
	}
	client := urlAPIHTTPClient(proxyURL)
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
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusNotFound || resp.StatusCode == http.StatusGone {
		return nil, &outlookMailError{Code: "mailbox_credential_invalid", Category: "credential", HTTPStatus: http.StatusUnprocessableEntity, UserMessage: "url_api 取码 URL 无效、已过期或无权访问", Detail: fmt.Sprintf("HTTP %d", resp.StatusCode), Terminal: true}
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &outlookMailError{Code: "mailbox_provider_failed", Category: "service", HTTPStatus: http.StatusBadGateway, UserMessage: "url_api 邮箱渠道请求失败，请稍后重试", Detail: fmt.Sprintf("HTTP %d", resp.StatusCode)}
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		return nil, err
	}
	rawHTML := string(raw)
	plain := urlAPIText(rawHTML)
	relevant := urlAPIOpenAIPattern.MatchString(plain)
	otp := ""
	if relevant {
		if match := urlAPIOTPPattern.FindStringSubmatch(plain); len(match) > 1 {
			otp = match[1]
		}
	}
	subject := urlAPISubject(rawHTML, plain)
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
