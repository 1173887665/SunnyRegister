package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSunnyCheckoutProbePersistsOnlyCheckoutType(t *testing.T) {
	s := newSunnySessionTestServer(t)
	session := prepareSunnyTrialAccount(t, s)
	if err := s.db.Model(&SunnyAccount{}).Where("email = ?", session.Email).Updates(map[string]any{
		"trial_eligibility": sunnyTrialEligible, "payment_methods_json": `["card"]`,
	}).Error; err != nil {
		t.Fatal(err)
	}
	previousCheck := sunnyCheckCheckoutProbe
	sunnyCheckCheckoutProbe = func(context.Context, string) sunnyCommerceProbeResult {
		return sunnyCommerceProbeResult{CheckoutKind: "cs_live"}
	}
	t.Cleanup(func() { sunnyCheckCheckoutProbe = previousCheck })

	task := s.createTask(sunnyCheckoutProbeTaskType, "sunny", map[string]any{"session_ids": []uint{session.ID}}, 1)
	s.executeSunnyCheckoutProbeTask(&task, map[string]any{"session_ids": []uint{session.ID}})
	var account SunnyAccount
	s.db.Where("email = ?", session.Email).First(&account)
	if account.CheckoutKind != "cs_live" || account.TrialEligibility != sunnyTrialEligible || account.PaymentMethodsJSON != `["card"]` {
		t.Fatalf("unexpected account state after checkout probe: %#v", account)
	}
	result := jsonMap(task.ResultJSON)
	if intValue(result["detected"], 0) != 1 || intValue(result["failed"], 0) != 0 {
		t.Fatalf("unexpected checkout probe result: %#v", result)
	}
}

func TestSunnyCheckoutProbeRouteCreatesTask(t *testing.T) {
	s := newSunnySessionTestServer(t)
	session := prepareSunnyTrialAccount(t, s)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/sessions/checkout-probe", strings.NewReader(fmt.Sprintf(`{"session_ids":[%d]}`, session.ID)))
	req.Header.Set("Content-Type", "application/json")
	recorder := httptest.NewRecorder()
	s.sunnySessions(recorder, req, []string{"checkout-probe"})
	if recorder.Code != http.StatusAccepted {
		t.Fatalf("route status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var task Task
	if err := s.db.Order("created_at desc").First(&task).Error; err != nil || task.Type != sunnyCheckoutProbeTaskType {
		t.Fatalf("checkout probe task missing: task=%#v err=%v", task, err)
	}
}

func TestSunnyCheckoutProbeRetriesUnknownResult(t *testing.T) {
	previousCheck := sunnyCheckCheckoutProbe
	callCount := 0
	sunnyCheckCheckoutProbe = func(context.Context, string) sunnyCommerceProbeResult {
		callCount++
		if callCount == 1 {
			return sunnyCommerceProbeResult{CheckoutKind: sunnyCheckoutUnknown, CheckoutError: "temporary"}
		}
		return sunnyCommerceProbeResult{CheckoutKind: "oaics"}
	}
	t.Cleanup(func() { sunnyCheckCheckoutProbe = previousCheck })

	result, retried := checkSunnyCheckoutProbeWithRetry(context.Background(), "token")
	if !retried || callCount != 2 || result.CheckoutKind != "oaics" {
		t.Fatalf("retried=%v calls=%d result=%#v", retried, callCount, result)
	}
}

func TestSunnyCheckoutProbeSkipsAccountAlreadyInActiveProbe(t *testing.T) {
	s := newSunnySessionTestServer(t)
	session := prepareSunnyTrialAccount(t, s)
	first, err := s.createSunnyCheckoutProbeTask(map[string]any{"session_ids": []uint{session.ID}})
	if err != nil {
		t.Fatal(err)
	}
	second, err := s.createSunnyCheckoutProbeTask(map[string]any{"session_ids": []uint{session.ID}})
	if err != nil {
		t.Fatal(err)
	}
	payload := jsonMap(second.PayloadJSON)
	if ids := uintSlice(payload["skip_session_ids"]); len(ids) != 1 || ids[0] != session.ID {
		t.Fatalf("unexpected skipped checkout sessions: %#v", payload)
	}
	if first.Status != TaskPending || second.Status != TaskPending {
		t.Fatalf("unexpected task statuses: first=%q second=%q", first.Status, second.Status)
	}
}
