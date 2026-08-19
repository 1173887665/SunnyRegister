package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSunnyPaymentMethodNormalizationAndFilter(t *testing.T) {
	methods := normalizeSunnyPaymentMethods([]string{"cpmt_paypal", "credit-card", "KakaoPay", "paypal", "iDEAL"})
	if got := strings.Join(methods, ","); got != "paypal,card,kakao_pay,ideal" {
		t.Fatalf("methods=%q", got)
	}
	if !sunnyHasAllPaymentMethods(methods, []string{"paypal", "card"}) {
		t.Fatal("paypal + card should match")
	}
	if sunnyHasAllPaymentMethods(methods, []string{"paypal", "upi"}) {
		t.Fatal("paypal + upi should not match")
	}
}

func TestSunnyPaymentProbeTaskUnionsCountriesAndPersistsImmediately(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var session SunnySession
	if err := s.db.Where("email = ?", "session@example.com").First(&session).Error; err != nil {
		t.Fatal(err)
	}
	proxies := []SunnyProxy{
		{Address: "http://jp.example:8080", Country: "JP", PurposeTags: sunnyProxyPurposePayment, Status: "enabled", Enabled: true, LastCheckOK: true},
		{Address: "http://ph.example:8080", Country: "PH", PurposeTags: sunnyProxyPurposePayment, Status: "enabled", Enabled: true, LastCheckOK: true},
	}
	if err := s.db.Create(&proxies).Error; err != nil {
		t.Fatal(err)
	}
	previousProbe := sunnyProbePaymentMethods
	sunnyProbePaymentMethods = func(_ context.Context, token, country, currency, proxyURL string) sunnyPaymentProbeResponse {
		if token == "" || currency == "" || proxyURL == "" {
			return sunnyPaymentProbeResponse{Error: "missing routing data"}
		}
		if country == "JP" {
			return sunnyPaymentProbeResponse{Methods: []string{"paypal", "card", "link"}, HTTP: http.StatusOK}
		}
		return sunnyPaymentProbeResponse{Methods: []string{"card", "gcash"}, HTTP: http.StatusOK}
	}
	t.Cleanup(func() { sunnyProbePaymentMethods = previousProbe })

	task, err := s.createSunnyPaymentProbeTask(map[string]any{"session_ids": []uint{session.ID}})
	if err != nil {
		t.Fatalf("create payment probe task: %v", err)
	}
	s.executeSunnyPaymentProbeTask(&task, jsonMap(task.PayloadJSON))
	if task.Status != TaskSucceeded {
		t.Fatalf("task status=%q error=%q", task.Status, task.Error)
	}
	var account SunnyAccount
	if err := s.db.Where("email = ?", session.Email).First(&account).Error; err != nil {
		t.Fatal(err)
	}
	var methods []string
	if err := json.Unmarshal([]byte(account.PaymentMethodsJSON), &methods); err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(methods, ","); got != "paypal,card,link,gcash" {
		t.Fatalf("stored methods=%q", got)
	}
	if account.PaymentProbeMethodsJSON != account.PaymentMethodsJSON {
		t.Fatalf("dedicated methods=%s compatibility methods=%s", account.PaymentProbeMethodsJSON, account.PaymentMethodsJSON)
	}
	if account.PaymentProbedAt == nil || account.PaymentProbeError != "" || !strings.Contains(account.PaymentProbeResultsJSON, `"JP"`) || !strings.Contains(account.PaymentProbeResultsJSON, `"PH"`) {
		t.Fatalf("probe metadata not persisted: %#v", account)
	}
	if err := s.db.Model(&SunnyAccount{}).Where("id = ?", account.ID).Update("payment_methods_json", `["upi"]`).Error; err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	s.sunnySessions(recorder, httptest.NewRequest(http.MethodGet, "/api/sunny/sessions", nil), nil)
	if !strings.Contains(recorder.Body.String(), `"payment_methods":["paypal","card","link","gcash"]`) {
		t.Fatalf("dedicated payment methods were not preferred: %s", recorder.Body.String())
	}
}

func TestSunnyPaymentProbeTriesNextProxyAfterFailure(t *testing.T) {
	previousProbe := sunnyProbePaymentMethods
	calls := 0
	sunnyProbePaymentMethods = func(_ context.Context, _, _, _, _ string) sunnyPaymentProbeResponse {
		calls++
		if calls == 1 {
			return sunnyPaymentProbeResponse{Error: "proxy connection failed"}
		}
		return sunnyPaymentProbeResponse{Methods: []string{"momo"}, HTTP: http.StatusOK}
	}
	t.Cleanup(func() { sunnyProbePaymentMethods = previousProbe })
	s := &Server{}
	result := s.probeSunnyPaymentCountry(
		sunnyPaymentProbeCandidate{AccessToken: "token"},
		"VN",
		[]SunnyProxy{{ID: 1, Address: "http://first"}, {ID: 2, Address: "http://second"}},
	)
	if result.Error != "" || result.Attempts != 2 || strings.Join(result.Methods, ",") != "momo" {
		t.Fatalf("fallback result=%#v calls=%d", result, calls)
	}
}

func TestSunnyPaymentProbeTasksSkipOverlappingSessions(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var first SunnySession
	if err := s.db.Where("email = ?", "session@example.com").First(&first).Error; err != nil {
		t.Fatal(err)
	}
	mailbox := SunnyMailbox{Email: "second-payment@example.com", Status: "已注册", AccountType: "free", Enabled: true}
	if err := s.db.Create(&mailbox).Error; err != nil {
		t.Fatal(err)
	}
	account := SunnyAccount{MailboxID: mailbox.ID, Email: mailbox.Email, Status: "registered", AccountType: "free", AccessToken: "second-token"}
	if err := s.db.Create(&account).Error; err != nil {
		t.Fatal(err)
	}
	second := SunnySession{AccountID: account.ID, Email: account.Email, AccessToken: account.AccessToken}
	if err := s.db.Create(&second).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.db.Create(&SunnyProxy{Address: "http://jp.example:8080", Country: "JP", PurposeTags: sunnyProxyPurposePayment, Status: "enabled", Enabled: true}).Error; err != nil {
		t.Fatal(err)
	}
	if _, err := s.createSunnyPaymentProbeTask(map[string]any{"session_ids": []uint{first.ID}}); err != nil {
		t.Fatal(err)
	}
	task, err := s.createSunnyPaymentProbeTask(map[string]any{"session_ids": []uint{first.ID, second.ID}})
	if err != nil {
		t.Fatal(err)
	}
	skipped := uintSlice(jsonMap(task.PayloadJSON)["skip_session_ids"])
	if len(skipped) != 1 || skipped[0] != first.ID {
		t.Fatalf("skip_session_ids=%v", skipped)
	}
}

func TestSunnySessionPaymentMethodFilterUsesANDSemantics(t *testing.T) {
	s := newSunnySessionTestServer(t)
	if err := s.db.Model(&SunnyAccount{}).Where("email = ?", "session@example.com").Update("payment_methods_json", `["paypal","card"]`).Error; err != nil {
		t.Fatal(err)
	}
	mailbox := SunnyMailbox{Email: "upi@example.com", Status: "已注册", AccountType: "free", Enabled: true}
	if err := s.db.Create(&mailbox).Error; err != nil {
		t.Fatal(err)
	}
	account := SunnyAccount{MailboxID: mailbox.ID, Email: mailbox.Email, Status: "registered", AccountType: "free", AccessToken: "token", PaymentMethodsJSON: `["paypal","upi"]`}
	if err := s.db.Create(&account).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.db.Create(&SunnySession{AccountID: account.ID, Email: account.Email, AccessToken: account.AccessToken}).Error; err != nil {
		t.Fatal(err)
	}

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/sunny/sessions?payment_methods=paypal,card", nil)
	s.sunnySessions(recorder, request, nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Items []map[string]any `json:"items"`
		Total int              `json:"total"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Total != 1 || len(payload.Items) != 1 || payload.Items[0]["email"] != "session@example.com" {
		t.Fatalf("unexpected AND filter result: %#v", payload)
	}
}
