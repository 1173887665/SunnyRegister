package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestSunnySubscriptionMailMarkers(t *testing.T) {
	tests := []struct {
		name    string
		subject string
		body    string
		want    bool
	}{
		{name: "Japanese", subject: "ChatGPT - 新しいプラン", body: "サブスクリプションの管理\nChatGPT Plus Subscription", want: true},
		{name: "Chinese", subject: "ChatGPT - 你的新套餐", body: "管理订阅", want: true},
		{name: "English", subject: "ChatGPT - Your new plan", body: "Manage your subscription", want: true},
		{name: "Korean", subject: "ChatGPT - 새로운 요금제", body: "구독 관리", want: true},
		{name: "Portuguese", subject: "ChatGPT - Seu novo plano", body: "Gerenciar assinatura", want: true},
		{name: "Candidate without confirmation", subject: "ChatGPT - Your new plan", body: "A generic product announcement", want: false},
		{name: "Unrelated mail", subject: "Weekly account update", body: "ChatGPT Plus Subscription", want: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			payload := map[string]any{"items": []map[string]any{{"subject": test.subject, "body": test.body}}}
			got, _ := sunnySubscriptionPayloadConfirmed(payload)
			if got != test.want {
				t.Fatalf("confirmed=%v, want %v", got, test.want)
			}
		})
	}
}

func TestSunnySubscriptionTaskUpdatesMailboxAndAccountPlan(t *testing.T) {
	s := newSunnySessionTestServer(t)
	s.db.Model(&SunnyMailbox{}).Where("email = ?", "session@example.com").Update("account_type", "free")
	s.db.Model(&SunnyAccount{}).Where("email = ?", "session@example.com").Update("account_type", "free")
	var session SunnySession
	if err := s.db.Where("email = ?", "session@example.com").First(&session).Error; err != nil {
		t.Fatalf("load session: %v", err)
	}
	previousDetect := sunnyDetectSubscriptionMail
	sunnyDetectSubscriptionMail = func(candidate sunnySubscriptionCandidate, _ string) (bool, string, error) {
		if candidate.Email != session.Email || candidate.ClientID != "client-id" || candidate.RefreshToken != "mailbox-refresh-token" {
			t.Fatalf("unexpected candidate: %#v", candidate)
		}
		return true, "ChatGPT - 新しいプラン", nil
	}
	t.Cleanup(func() { sunnyDetectSubscriptionMail = previousDetect })

	task := s.createTask(sunnySubscriptionTaskType, "sunny", map[string]any{"session_ids": []uint{session.ID}}, 1)
	s.executeSunnySubscriptionTask(&task, map[string]any{"session_ids": []uint{session.ID}})

	var mailbox SunnyMailbox
	var account SunnyAccount
	s.db.Where("email = ?", session.Email).First(&mailbox)
	s.db.Where("email = ?", session.Email).First(&account)
	if mailbox.AccountType != "plus" || account.AccountType != "plus" {
		t.Fatalf("plan was not synchronized: mailbox=%q account=%q", mailbox.AccountType, account.AccountType)
	}
	if err := s.db.First(&task, "id = ?", task.ID).Error; err != nil {
		t.Fatalf("reload task: %v", err)
	}
	result := jsonMap(task.ResultJSON)
	if task.Status != TaskSucceeded || intValue(result["subscribed"], 0) != 1 || intValue(result["failed"], 0) != 0 {
		t.Fatalf("unexpected task result: status=%s result=%#v", task.Status, result)
	}
}

func TestSunnySubscriptionTaskKeepsPlanWhenNoMailMatches(t *testing.T) {
	s := newSunnySessionTestServer(t)
	s.db.Model(&SunnyMailbox{}).Where("email = ?", "session@example.com").Update("account_type", "team")
	s.db.Model(&SunnyAccount{}).Where("email = ?", "session@example.com").Update("account_type", "team")
	var session SunnySession
	s.db.Where("email = ?", "session@example.com").First(&session)
	previousDetect := sunnyDetectSubscriptionMail
	sunnyDetectSubscriptionMail = func(sunnySubscriptionCandidate, string) (bool, string, error) { return false, "", nil }
	t.Cleanup(func() { sunnyDetectSubscriptionMail = previousDetect })

	task := s.createTask(sunnySubscriptionTaskType, "sunny", map[string]any{"session_ids": []uint{session.ID}}, 1)
	s.executeSunnySubscriptionTask(&task, map[string]any{"session_ids": []uint{session.ID}})
	var mailbox SunnyMailbox
	var account SunnyAccount
	s.db.Where("email = ?", session.Email).First(&mailbox)
	s.db.Where("email = ?", session.Email).First(&account)
	if mailbox.AccountType != "team" || account.AccountType != "team" {
		t.Fatalf("unmatched check changed plan: mailbox=%q account=%q", mailbox.AccountType, account.AccountType)
	}
}

func TestSunnySubscriptionRouteCreatesLocalTask(t *testing.T) {
	s := newSunnySessionTestServer(t)
	var session SunnySession
	s.db.Where("email = ?", "session@example.com").First(&session)
	req := httptest.NewRequest(http.MethodPost, "/api/sunny/sessions/subscription-check", strings.NewReader(fmt.Sprintf(`{"session_ids":[%d]}`, session.ID)))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.sunnySessions(rec, req, []string{"subscription-check"})
	if rec.Code != http.StatusAccepted {
		t.Fatalf("subscription route status=%d body=%s", rec.Code, rec.Body.String())
	}
	var task Task
	if err := s.db.Order("created_at desc").First(&task).Error; err != nil {
		t.Fatalf("load task: %v", err)
	}
	if task.Type != sunnySubscriptionTaskType || !sunnyGoTaskType(task.Type) {
		t.Fatalf("subscription task was not local: type=%q local=%v", task.Type, sunnyGoTaskType(task.Type))
	}
}

func TestFetchXbovoMailSubjectsDoesNotFetchBodies(t *testing.T) {
	rawRequests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/messages":
			fmt.Fprint(w, `{"ok":true,"messages":[{"id":12,"subject":"ChatGPT - Your new plan"}]}`)
		case "/api/v1/message/raw":
			rawRequests++
			fmt.Fprint(w, `{"ok":true,"text":"Manage subscription"}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	previousURL := xbovoAPIBaseURL
	xbovoAPIBaseURL = server.URL
	t.Cleanup(func() { xbovoAPIBaseURL = previousURL })

	subjects, err := fetchXbovoMailSubjects("user@icloud.com", "key", 5, "")
	if err != nil || strings.Join(subjects, "|") != "ChatGPT - Your new plan" {
		t.Fatalf("subjects=%#v err=%v", subjects, err)
	}
	if rawRequests != 0 {
		t.Fatalf("subject-only query fetched %d message bodies", rawRequests)
	}
}
