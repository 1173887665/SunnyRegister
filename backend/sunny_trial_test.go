package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSunnyTrialCheckAPIResponses(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s", r.Method)
		}
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		switch body["access_token"] {
		case "eligible-token":
			writeJSON(w, http.StatusOK, map[string]any{"eligible": true, "message": "有试用资格", "query_count": 1})
		case "ineligible-token":
			writeJSON(w, http.StatusOK, map[string]any{"eligible": false, "message": "无试用资格", "query_count": 2})
		default:
			writeJSON(w, http.StatusUnauthorized, map[string]any{"detail": "accessToken 无效或已过期"})
		}
	}))
	defer server.Close()
	previousEndpoint := sunnyTrialCheckEndpoint
	sunnyTrialCheckEndpoint = server.URL
	t.Cleanup(func() { sunnyTrialCheckEndpoint = previousEndpoint })

	tests := []struct {
		name, token       string
		eligible, invalid bool
		wantError         bool
	}{
		{name: "eligible", token: "eligible-token", eligible: true},
		{name: "ineligible", token: "ineligible-token"},
		{name: "invalid", token: "invalid-token", invalid: true, wantError: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			eligible, _, invalid, err := checkSunnyTrialEligibility(context.Background(), test.token)
			if eligible != test.eligible || invalid != test.invalid || (err != nil) != test.wantError {
				t.Fatalf("eligible=%v invalid=%v err=%v", eligible, invalid, err)
			}
		})
	}
}

func prepareSunnyTrialAccount(t *testing.T, s *Server) SunnySession {
	t.Helper()
	if err := s.db.Model(&SunnyAccount{}).Where("email = ?", "session@example.com").Updates(map[string]any{
		"status": "registered", "account_type": "free", "trial_eligibility": sunnyTrialUnknown,
	}).Error; err != nil {
		t.Fatalf("prepare account: %v", err)
	}
	if err := s.db.Model(&SunnyMailbox{}).Where("email = ?", "session@example.com").Updates(map[string]any{
		"status": "已注册", "account_type": "free", "trial_eligibility": sunnyTrialUnknown,
	}).Error; err != nil {
		t.Fatalf("prepare mailbox: %v", err)
	}
	var session SunnySession
	if err := s.db.Where("email = ?", "session@example.com").First(&session).Error; err != nil {
		t.Fatalf("load session: %v", err)
	}
	return session
}

func TestSunnyTrialTaskPersistsAndFiltersEligibility(t *testing.T) {
	s := newSunnySessionTestServer(t)
	session := prepareSunnyTrialAccount(t, s)
	if filter := normalizeSunnyTrialFilter("unsupported"); filter != "" {
		t.Fatalf("unsupported trial filter = %q", filter)
	}
	unknownRecorder := httptest.NewRecorder()
	unknownRequest := httptest.NewRequest(http.MethodGet, "/api/sunny/sessions?trial_eligibility=unknown", nil)
	s.sunnySessions(unknownRecorder, unknownRequest, nil)
	if unknownRecorder.Code != http.StatusOK || !strings.Contains(unknownRecorder.Body.String(), `"trial_eligibility":"unknown"`) {
		t.Fatalf("unknown filter status=%d body=%s", unknownRecorder.Code, unknownRecorder.Body.String())
	}

	previousCheck := sunnyCheckTrialEligibility
	sunnyCheckTrialEligibility = func(context.Context, string) (bool, string, bool, error) {
		return true, "该账号有 ChatGPT Plus 0 元试用资格", false, nil
	}
	t.Cleanup(func() { sunnyCheckTrialEligibility = previousCheck })

	task := s.createTask(sunnyTrialTaskType, "sunny", map[string]any{"session_ids": []uint{session.ID}}, 1)
	s.executeSunnyTrialTask(&task, map[string]any{"session_ids": []uint{session.ID}})
	var account SunnyAccount
	var mailbox SunnyMailbox
	s.db.Where("email = ?", session.Email).First(&account)
	s.db.Where("email = ?", session.Email).First(&mailbox)
	if account.TrialEligibility != sunnyTrialEligible || mailbox.TrialEligibility != sunnyTrialEligible || account.TrialCheckedAt == nil || mailbox.TrialCheckedAt == nil {
		t.Fatalf("trial state not synchronized: account=%#v mailbox=%#v", account, mailbox)
	}

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/sunny/sessions?trial_eligibility=eligible", nil)
	s.sunnySessions(recorder, request, nil)
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"trial_eligibility":"eligible"`) {
		t.Fatalf("eligible filter status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestSunnyTrialInvalidTokenClearsEligibilityAndMarksATInvalid(t *testing.T) {
	s := newSunnySessionTestServer(t)
	session := prepareSunnyTrialAccount(t, s)
	if err := s.db.Model(&SunnyAccount{}).Where("email = ?", session.Email).Update("trial_eligibility", sunnyTrialEligible).Error; err != nil {
		t.Fatal(err)
	}
	previousCheck := sunnyCheckTrialEligibility
	sunnyCheckTrialEligibility = func(context.Context, string) (bool, string, bool, error) {
		return false, "accessToken 无效或已过期", true, fmt.Errorf("accessToken 无效或已过期")
	}
	t.Cleanup(func() { sunnyCheckTrialEligibility = previousCheck })

	task := s.createTask(sunnyTrialTaskType, "sunny", map[string]any{"session_ids": []uint{session.ID}}, 1)
	s.executeSunnyTrialTask(&task, map[string]any{"session_ids": []uint{session.ID}})
	var account SunnyAccount
	s.db.Where("email = ?", session.Email).First(&account)
	s.db.First(&session, session.ID)
	if account.TrialEligibility != sunnyTrialUnknown || session.AccessTokenStatus != "invalid" || !strings.Contains(session.AccessTokenError, "无效或已过期") {
		t.Fatalf("invalid token state account=%#v session=%#v", account, session)
	}
	var renewal Task
	if err := s.db.Where("type = ?", "sunny_refresh_session").First(&renewal).Error; err != nil {
		t.Fatalf("renewal task was not queued: %v", err)
	}
	renewalPayload := jsonMap(renewal.PayloadJSON)
	if ids := uintSlice(renewalPayload["account_ids"]); len(ids) != 1 || ids[0] != session.AccountID {
		t.Fatalf("unexpected renewal payload: %#v", renewalPayload)
	}
	if text(renewalPayload["source"]) != "trial_check" || text(renewalPayload["source_task_id"]) != task.ID {
		t.Fatalf("unexpected renewal source: %#v", renewalPayload)
	}
	result := jsonMap(task.ResultJSON)
	if text(result["renewal_task_id"]) != renewal.ID || intValue(result["renewal_queued"], 0) != 1 {
		t.Fatalf("renewal result missing: %#v", result)
	}
}

func TestSunnyTrialEligibilityCanBeEditedFromSessionAndMailbox(t *testing.T) {
	s := newSunnySessionTestServer(t)
	session := prepareSunnyTrialAccount(t, s)
	put := httptest.NewRequest(http.MethodPut, fmt.Sprintf("/api/sunny/sessions/%d", session.ID), strings.NewReader(`{"trial_eligibility":"ineligible"}`))
	put.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnySessions(recorder, put, []string{fmt.Sprint(session.ID)})
	if recorder.Code != http.StatusOK {
		t.Fatalf("session update status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var mailbox SunnyMailbox
	s.db.Where("email = ?", session.Email).First(&mailbox)
	if mailbox.TrialEligibility != sunnyTrialIneligible {
		t.Fatalf("mailbox eligibility = %q", mailbox.TrialEligibility)
	}

	mailboxPut := httptest.NewRequest(http.MethodPut, fmt.Sprintf("/api/sunny/mailboxes/%d", mailbox.ID), strings.NewReader(`{"trial_eligibility":"eligible"}`))
	mailboxPut.Header.Set("Content-Type", "application/json")
	mailboxRecorder := httptest.NewRecorder()
	s.sunnyMailboxes(mailboxRecorder, mailboxPut, []string{fmt.Sprint(mailbox.ID)})
	if mailboxRecorder.Code != http.StatusOK {
		t.Fatalf("mailbox update status=%d body=%s", mailboxRecorder.Code, mailboxRecorder.Body.String())
	}
	var account SunnyAccount
	s.db.Where("email = ?", session.Email).First(&account)
	if account.TrialEligibility != sunnyTrialEligible {
		t.Fatalf("account eligibility = %q", account.TrialEligibility)
	}
}

func TestSunnyTrialRouteCreatesLocalTask(t *testing.T) {
	s := newSunnySessionTestServer(t)
	session := prepareSunnyTrialAccount(t, s)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/sessions/trial-check", strings.NewReader(fmt.Sprintf(`{"session_ids":[%d]}`, session.ID)))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnySessions(recorder, req, []string{"trial-check"})
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("route status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var task Task
	if err := s.db.Order("created_at desc").First(&task).Error; err != nil || task.Type != sunnyTrialTaskType {
		t.Fatalf("trial task missing: task=%#v err=%v", task, err)
	}
}

func TestSunnyCommerceWorkerResponse(t *testing.T) {
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/probe-commerce" || r.Method != http.MethodPost {
			t.Fatalf("unexpected worker request: %s %s", r.Method, r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer worker-secret" {
			t.Fatalf("authorization = %q", got)
		}
		var requestBody struct {
			PromotionProxyURL string `json:"promotion_proxy_url"`
			CheckoutProxyURL  string `json:"checkout_proxy_url"`
		}
		if err := json.NewDecoder(r.Body).Decode(&requestBody); err != nil {
			t.Fatalf("decode worker request: %v", err)
		}
		if requestBody.PromotionProxyURL != "http://promotion-proxy" || requestBody.CheckoutProxyURL != "http://checkout-proxy" {
			t.Fatalf("unexpected proxy routing: %#v", requestBody)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"trial":    map[string]any{"state": "eligible", "http": 200, "error": ""},
			"checkout": map[string]any{"kind": "oaics", "payment_methods": []string{"card", "paypal"}, "http": 200, "error": ""},
		})
	}))
	defer worker.Close()
	t.Setenv("PYTHON_WORKER_URL", worker.URL)
	t.Setenv("PYTHON_WORKER_TOKEN", "worker-secret")

	result, ok := probeSunnyCommerceViaWorker(context.Background(), "secret-at", "http://promotion-proxy", "http://checkout-proxy")
	if !ok || result.Eligibility != sunnyTrialEligible || result.CheckoutKind != "oaics" {
		t.Fatalf("unexpected result: ok=%v result=%#v", ok, result)
	}
	if got := strings.Join(result.PaymentMethods, ","); got != "card,paypal" {
		t.Fatalf("payment methods = %q", got)
	}
}

func TestSunnyCommerceWorkerPreservesNonJSONChallengeDetail(t *testing.T) {
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{
			"trial":    map[string]any{"state": "", "http": 403, "error": "HTTP 403 returned text/html content"},
			"checkout": map[string]any{"kind": "", "payment_methods": []string{}, "http": 403, "error": "HTTP 403 returned text/html content"},
		})
	}))
	defer worker.Close()
	t.Setenv("PYTHON_WORKER_URL", worker.URL)

	result, ok := probeSunnyCommerceViaWorker(context.Background(), "secret-at", "")
	if !ok || !strings.Contains(result.TrialError, "HTTP 403") || !strings.Contains(result.CheckoutError, "text/html") {
		t.Fatalf("unexpected result: ok=%v result=%#v", ok, result)
	}
}
