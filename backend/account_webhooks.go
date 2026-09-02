package main

// Account webhooks are intentionally isolated from the wallet and registration
// protocol modules. They observe the account table, build redacted events, and
// deliver them through an outbox with signed, retryable HTTP requests.
import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

var accountWebhookEvents = []string{
	"account.registered", "account.updated", "account.status_changed",
	"account.token_refreshed", "account.trial_changed", "account.subscription_changed", "account.payment_changed",
}

type AccountWebhook struct {
	ID          uint      `gorm:"primaryKey" json:"id"`
	Name        string    `gorm:"size:120;not null" json:"name"`
	URL         string    `gorm:"type:text;not null" json:"url"`
	Secret      string    `gorm:"type:text;not null" json:"-"`
	Enabled     bool      `gorm:"index;default:true" json:"enabled"`
	Scope       string    `gorm:"index;size:20;default:global" json:"scope"`
	ScopeValue  string    `gorm:"index;size:255" json:"scope_value"`
	EventsJSON  string    `gorm:"type:text;default:'[]'" json:"events_json"`
	TimeoutSec  int       `gorm:"default:15" json:"timeout_sec"`
	MaxAttempts int       `gorm:"default:5" json:"max_attempts"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

func (AccountWebhook) TableName() string { return "account_webhooks" }

type AccountWebhookDelivery struct {
	ID             uint       `gorm:"primaryKey" json:"id"`
	WebhookID      uint       `gorm:"index;not null" json:"webhook_id"`
	DeliveryID     string     `gorm:"uniqueIndex;size:80;not null" json:"delivery_id"`
	EventType      string     `gorm:"index;size:80;not null" json:"event_type"`
	AccountID      uint       `gorm:"index" json:"account_id"`
	PayloadJSON    string     `gorm:"type:text;not null" json:"payload_json"`
	Status         string     `gorm:"index;size:20;default:pending" json:"status"`
	Attempts       int        `json:"attempts"`
	NextAttemptAt  time.Time  `gorm:"index" json:"next_attempt_at"`
	LastAttemptAt  *time.Time `json:"last_attempt_at"`
	ResponseStatus int        `json:"response_status"`
	ResponseBody   string     `gorm:"type:text" json:"response_body"`
	Error          string     `gorm:"type:text" json:"error"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
}

func (AccountWebhookDelivery) TableName() string { return "account_webhook_deliveries" }

type accountWebhookSnapshot struct {
	Email, Status, GroupName, AccountType, Phone, Trial string
	AccessToken, PaymentMethods, CheckoutKind           string
	UpdatedAt, CreatedAt                                time.Time
}

type accountWebhookEnvelope struct {
	ID        string         `json:"id"`
	Event     string         `json:"event"`
	CreatedAt string         `json:"created_at"`
	Data      map[string]any `json:"data"`
}

func webhookEvents(raw string) []string {
	var values []string
	if json.Unmarshal([]byte(raw), &values) != nil || len(values) == 0 {
		return append([]string(nil), accountWebhookEvents...)
	}
	allowed := map[string]bool{}
	for _, event := range accountWebhookEvents {
		allowed[event] = true
	}
	out := make([]string, 0, len(values))
	seen := map[string]bool{}
	for _, event := range values {
		if allowed[event] && !seen[event] {
			out, seen[event] = append(out, event), true
		}
	}
	return out
}

func webhookJSONEvents(events []string) string { b, _ := json.Marshal(events); return string(b) }

func webhookPublic(w AccountWebhook) map[string]any {
	return map[string]any{"id": w.ID, "name": w.Name, "url": w.URL, "enabled": w.Enabled,
		"scope": w.Scope, "scope_value": w.ScopeValue, "events": webhookEvents(w.EventsJSON),
		"timeout_sec": w.TimeoutSec, "max_attempts": w.MaxAttempts, "secret_configured": w.Secret != "",
		"created_at": formatTime(w.CreatedAt), "updated_at": formatTime(w.UpdatedAt)}
}

func validateWebhookInput(body map[string]any, current *AccountWebhook) error {
	name, rawURL := text(body["name"]), text(body["url"])
	scope := text(body["scope"])
	scopeValue := text(body["scope_value"])
	if current != nil {
		if name == "" {
			name = current.Name
		}
		if rawURL == "" {
			rawURL = current.URL
		}
		if scope == "" {
			scope = current.Scope
		}
		if scopeValue == "" {
			scopeValue = current.ScopeValue
		}
	}
	if name == "" || rawURL == "" {
		return fmt.Errorf("name and url are required")
	}
	if len([]rune(name)) > 120 {
		return fmt.Errorf("name must be at most 120 characters")
	}
	u, err := url.ParseRequestURI(rawURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return fmt.Errorf("url must be an http or https URL")
	}
	if scope == "" {
		scope = "global"
	}
	if scope != "global" && scope != "group" && scope != "account" {
		return fmt.Errorf("scope must be global, group, or account")
	}
	if scope != "global" && scopeValue == "" {
		return fmt.Errorf("scope_value is required for group or account webhooks")
	}
	return nil
}

func (s *Server) handleAccountWebhooks(w http.ResponseWriter, r *http.Request, rest string) {
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, 404, "not found")
		return
	}
	if parts[0] == "webhooks" {
		s.handleAccountWebhookConfigs(w, r, parts[1:])
		return
	}
	if parts[0] == "webhook-deliveries" {
		s.handleAccountWebhookDeliveries(w, r, parts[1:])
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) handleAccountWebhookConfigs(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 0 {
		switch r.Method {
		case http.MethodGet:
			var rows []AccountWebhook
			s.db.Order("id desc").Find(&rows)
			items := make([]map[string]any, 0, len(rows))
			for _, row := range rows {
				items = append(items, webhookPublic(row))
			}
			writeJSON(w, 200, map[string]any{"items": items, "events": accountWebhookEvents})
			return
		case http.MethodPost:
			body, err := parseBody(r)
			if err != nil {
				writeError(w, 400, err.Error())
				return
			}
			if err := validateWebhookInput(body, nil); err != nil {
				writeError(w, 400, err.Error())
				return
			}
			secret := text(body["secret"])
			if secret == "" {
				secret = randomID("whsec")
			}
			row := AccountWebhook{Name: text(body["name"]), URL: text(body["url"]), Secret: secret, Enabled: boolValue(body["enabled"], true), Scope: fallback(text(body["scope"]), "global"), ScopeValue: text(body["scope_value"]), TimeoutSec: intValue(body["timeout_sec"], 15), MaxAttempts: intValue(body["max_attempts"], 5)}
			if row.TimeoutSec < 1 || row.TimeoutSec > 120 {
				row.TimeoutSec = 15
			}
			if row.MaxAttempts < 1 || row.MaxAttempts > 10 {
				row.MaxAttempts = 5
			}
			events := stringSlice(body["events"])
			if len(events) == 0 {
				events = append([]string(nil), accountWebhookEvents...)
			}
			row.EventsJSON = webhookJSONEvents(webhookEvents(dumpJSON(events)))
			if err := s.db.Create(&row).Error; err != nil {
				writeError(w, 400, err.Error())
				return
			}
			result := webhookPublic(row)
			result["secret"] = secret
			writeJSON(w, 201, result)
			return
		default:
			writeError(w, 405, "method not allowed")
			return
		}
	}
	id := uint(intValue(parts[0], 0))
	if id == 0 {
		writeError(w, 404, "webhook not found")
		return
	}
	var row AccountWebhook
	if s.db.First(&row, id).Error != nil {
		writeError(w, 404, "webhook not found")
		return
	}
	if len(parts) == 2 && parts[1] == "test" && r.Method == http.MethodPost {
		s.testAccountWebhook(w, r, row)
		return
	}
	if len(parts) != 1 {
		writeError(w, 404, "not found")
		return
	}
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, 200, webhookPublic(row))
	case http.MethodPut, http.MethodPatch:
		body, err := parseBody(r)
		if err != nil {
			writeError(w, 400, err.Error())
			return
		}
		if err := validateWebhookInput(body, &row); err != nil {
			writeError(w, 400, err.Error())
			return
		}
		if v, ok := body["name"]; ok {
			row.Name = text(v)
		}
		if v, ok := body["url"]; ok {
			row.URL = text(v)
		}
		if v, ok := body["enabled"]; ok {
			row.Enabled = boolValue(v, row.Enabled)
		}
		if v, ok := body["scope"]; ok {
			row.Scope = text(v)
		}
		if v, ok := body["scope_value"]; ok {
			row.ScopeValue = text(v)
		}
		if v, ok := body["events"]; ok {
			row.EventsJSON = webhookJSONEvents(webhookEvents(dumpJSON(v)))
		}
		if v, ok := body["timeout_sec"]; ok {
			row.TimeoutSec = intValue(v, row.TimeoutSec)
		}
		if v, ok := body["max_attempts"]; ok {
			row.MaxAttempts = intValue(v, row.MaxAttempts)
		}
		if row.TimeoutSec < 1 || row.TimeoutSec > 120 {
			row.TimeoutSec = 15
		}
		if row.MaxAttempts < 1 || row.MaxAttempts > 10 {
			row.MaxAttempts = 5
		}
		if v, ok := body["secret"]; ok && text(v) != "" {
			row.Secret = text(v)
		}
		if err := s.db.Save(&row).Error; err != nil {
			writeError(w, 400, err.Error())
			return
		}
		writeJSON(w, 200, webhookPublic(row))
	case http.MethodDelete:
		s.db.Delete(&row)
		writeJSON(w, 200, map[string]any{"ok": true})
	default:
		writeError(w, 405, "method not allowed")
	}
}

func (s *Server) testAccountWebhook(w http.ResponseWriter, r *http.Request, hook AccountWebhook) {
	envelope := s.buildWebhookEnvelope("account.updated", nil, 0)
	b, _ := json.Marshal(envelope)
	deliveryID := randomID("test")
	delivery := AccountWebhookDelivery{WebhookID: hook.ID, DeliveryID: deliveryID, EventType: "account.updated", PayloadJSON: string(b), Status: "sending", Attempts: 1, NextAttemptAt: time.Now()}
	_ = s.db.Create(&delivery).Error
	status, response, err := deliverAccountWebhook(hook, deliveryID, "account.updated", b)
	now := time.Now()
	delivery.LastAttemptAt, delivery.ResponseStatus, delivery.ResponseBody = &now, status, response
	if err == nil && status >= 200 && status < 300 {
		delivery.Status, delivery.Error = "succeeded", ""
	} else {
		delivery.Status, delivery.Error = "failed", fallback(errText(err), fmt.Sprintf("HTTP %d", status))
	}
	_ = s.db.Save(&delivery).Error
	result := map[string]any{"ok": err == nil && status >= 200 && status < 300, "status": status, "response": response}
	if err != nil {
		result["error"] = err.Error()
		writeJSON(w, 502, result)
		return
	}
	writeJSON(w, 200, result)
}

func (s *Server) handleAccountWebhookDeliveries(w http.ResponseWriter, r *http.Request, parts []string) {
	if len(parts) == 0 {
		if r.Method != http.MethodGet {
			writeError(w, 405, "method not allowed")
			return
		}
		limit := intValue(r.URL.Query().Get("limit"), 100)
		if limit < 1 {
			limit = 1
		}
		if limit > 500 {
			limit = 500
		}
		query := s.db.Order("created_at desc").Limit(limit)
		if id := intValue(r.URL.Query().Get("webhook_id"), 0); id > 0 {
			query = query.Where("webhook_id = ?", id)
		}
		var rows []AccountWebhookDelivery
		query.Find(&rows)
		writeJSON(w, 200, map[string]any{"items": rows})
		return
	}
	if len(parts) == 2 && parts[1] == "retry" && r.Method == http.MethodPost {
		id := uint(intValue(parts[0], 0))
		var d AccountWebhookDelivery
		if id == 0 || s.db.First(&d, id).Error != nil {
			writeError(w, 404, "delivery not found")
			return
		}
		d.Status, d.NextAttemptAt, d.Error = "pending", time.Now(), ""
		s.db.Save(&d)
		writeJSON(w, 200, map[string]any{"ok": true})
		return
	}
	writeError(w, 404, "not found")
}

func (s *Server) accountWebhookLoop() {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-ticker.C:
			s.accountWebhookObserve()
			s.accountWebhookDeliverDue()
		}
	}
}

func (s *Server) accountWebhookObserve() {
	var rows []SunnyAccount
	if s.db.Find(&rows).Error != nil {
		return
	}
	s.webhookMu.Lock()
	defer s.webhookMu.Unlock()
	if s.webhookSnapshots == nil {
		s.webhookSnapshots = map[uint]accountWebhookSnapshot{}
	}
	if !s.webhookSnapshotsReady {
		for _, a := range rows {
			s.webhookSnapshots[a.ID] = accountWebhookSnapshot{Email: a.Email, Status: a.Status, GroupName: a.GroupName, AccountType: a.AccountType, Phone: a.PhoneNumber, Trial: a.TrialEligibility, AccessToken: a.AccessToken, PaymentMethods: a.PaymentMethodsJSON, CheckoutKind: a.CheckoutKind, UpdatedAt: a.UpdatedAt, CreatedAt: a.CreatedAt}
		}
		s.webhookSnapshotsReady = true
		return
	}
	for _, a := range rows {
		next := accountWebhookSnapshot{Email: a.Email, Status: a.Status, GroupName: a.GroupName, AccountType: a.AccountType, Phone: a.PhoneNumber, Trial: a.TrialEligibility, AccessToken: a.AccessToken, PaymentMethods: a.PaymentMethodsJSON, CheckoutKind: a.CheckoutKind, UpdatedAt: a.UpdatedAt, CreatedAt: a.CreatedAt}
		prev, seen := s.webhookSnapshots[a.ID]
		s.webhookSnapshots[a.ID] = next
		if !seen {
			s.enqueueAccountWebhookEvent("account.registered", a.ID)
			continue
		}
		event := "account.updated"
		if prev.Status != next.Status {
			event = "account.status_changed"
		} else if prev.Trial != next.Trial {
			event = "account.trial_changed"
		} else if prev.AccessToken != next.AccessToken {
			event = "account.token_refreshed"
		} else if prev.PaymentMethods != next.PaymentMethods || prev.CheckoutKind != next.CheckoutKind {
			event = "account.payment_changed"
		} else if prev.AccountType != next.AccountType {
			event = "account.subscription_changed"
		}
		if prev.Email == next.Email && prev.Status == next.Status && prev.GroupName == next.GroupName && prev.AccountType == next.AccountType && prev.Phone == next.Phone && prev.Trial == next.Trial && prev.AccessToken == next.AccessToken && prev.PaymentMethods == next.PaymentMethods && prev.CheckoutKind == next.CheckoutKind && prev.UpdatedAt.Equal(next.UpdatedAt) {
			continue
		}
		s.enqueueAccountWebhookEvent(event, a.ID)
	}
}

func (s *Server) buildWebhookEnvelope(event string, account *SunnyAccount, accountID uint) accountWebhookEnvelope {
	data := map[string]any{"account": nil}
	if account != nil {
		data["account"] = map[string]any{"id": account.ID, "email": account.Email, "status": account.Status, "group_name": account.GroupName, "account_type": account.AccountType, "phone_number": account.PhoneNumber, "trial_eligibility": account.TrialEligibility, "created_at": formatTime(account.CreatedAt), "updated_at": formatTime(account.UpdatedAt)}
	}
	return accountWebhookEnvelope{ID: randomID("evt"), Event: event, CreatedAt: formatTime(time.Now()), Data: data}
}

func (s *Server) enqueueAccountWebhookEvent(event string, accountID uint) {
	var account SunnyAccount
	if accountID > 0 && s.db.First(&account, accountID).Error != nil {
		return
	}
	envelope := s.buildWebhookEnvelope(event, &account, accountID)
	payload, _ := json.Marshal(envelope)
	var hooks []AccountWebhook
	s.db.Where("enabled = ?", true).Find(&hooks)
	for _, hook := range hooks {
		if !webhookMatches(hook, event, account) {
			continue
		}
		d := AccountWebhookDelivery{WebhookID: hook.ID, DeliveryID: randomID("dlv"), EventType: event, AccountID: accountID, PayloadJSON: string(payload), Status: "pending", NextAttemptAt: time.Now()}
		s.db.Create(&d)
	}
}

func webhookMatches(h AccountWebhook, event string, a SunnyAccount) bool {
	selected := webhookEvents(h.EventsJSON)
	ok := false
	for _, item := range selected {
		if item == event {
			ok = true
			break
		}
	}
	if !ok {
		return false
	}
	switch h.Scope {
	case "account":
		return h.ScopeValue == strconv.FormatUint(uint64(a.ID), 10)
	case "group":
		return h.ScopeValue != "" && h.ScopeValue == a.GroupName
	default:
		return true
	}
}

func webhookSignature(secret, timestamp string, payload []byte) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(timestamp + "."))
	_, _ = mac.Write(payload)
	return "sha256=" + hex.EncodeToString(mac.Sum(nil))
}

func deliverAccountWebhook(hook AccountWebhook, deliveryID, event string, payload []byte) (int, string, error) {
	timeout := time.Duration(hook.TimeoutSec) * time.Second
	if timeout < time.Second {
		timeout = 15 * time.Second
	}
	req, err := http.NewRequest(http.MethodPost, hook.URL, bytes.NewReader(payload))
	if err != nil {
		return 0, "", err
	}
	ts := strconv.FormatInt(time.Now().Unix(), 10)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "SunnyRegister-Webhook/1.0")
	req.Header.Set("X-Sunny-Event", event)
	req.Header.Set("X-Sunny-Delivery", deliveryID)
	req.Header.Set("X-Sunny-Timestamp", ts)
	req.Header.Set("X-Sunny-Signature", webhookSignature(hook.Secret, ts, payload))
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(req)
	if err != nil {
		return 0, "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	return resp.StatusCode, string(body), nil
}

func (s *Server) accountWebhookDeliverDue() {
	// A process restart must not strand deliveries that were claimed just before
	// shutdown. They become eligible again after a short lease.
	s.db.Model(&AccountWebhookDelivery{}).Where("status = ? AND updated_at < ?", "sending", time.Now().Add(-2*time.Minute)).Updates(map[string]any{"status": "pending", "next_attempt_at": time.Now()})
	var d AccountWebhookDelivery
	if s.db.Where("status = ? AND next_attempt_at <= ?", "pending", time.Now()).Order("next_attempt_at asc").First(&d).Error != nil {
		return
	}
	if s.db.Model(&AccountWebhookDelivery{}).Where("id = ? AND status = ?", d.ID, "pending").Update("status", "sending").RowsAffected != 1 {
		return
	}
	var hook AccountWebhook
	if s.db.First(&hook, d.WebhookID).Error != nil {
		d.Status, d.Error = "failed", "webhook configuration was deleted"
		s.db.Save(&d)
		return
	}
	if hook.MaxAttempts < 1 {
		hook.MaxAttempts = 5
	}
	status, response, err := deliverAccountWebhook(hook, d.DeliveryID, d.EventType, []byte(d.PayloadJSON))
	now := time.Now()
	d.Attempts++
	d.LastAttemptAt = &now
	d.ResponseStatus, d.ResponseBody = status, response
	if err == nil && status >= 200 && status < 300 {
		d.Status, d.Error = "succeeded", ""
	} else {
		d.Error = fallback(errText(err), fmt.Sprintf("HTTP %d", status))
		if d.Attempts >= hook.MaxAttempts {
			d.Status = "failed"
		} else {
			d.Status = "pending"
			d.NextAttemptAt = now.Add(webhookRetryDelay(d.Attempts))
		}
	}
	s.db.Save(&d)
}

func errText(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
func webhookRetryDelay(attempt int) time.Duration {
	switch attempt {
	case 1:
		return 5 * time.Second
	case 2:
		return 30 * time.Second
	case 3:
		return 2 * time.Minute
	case 4:
		return 10 * time.Minute
	default:
		return time.Hour
	}
}

// Public helper for account code and tests that need to emit a lifecycle event.
func (s *Server) EmitAccountWebhook(event string, accountID uint) {
	for _, allowed := range accountWebhookEvents {
		if allowed == event {
			s.enqueueAccountWebhookEvent(event, accountID)
			return
		}
	}
}
