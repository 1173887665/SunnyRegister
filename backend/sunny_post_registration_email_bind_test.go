package main

import "testing"

func TestPrepareSunnyPostRegistrationEmailBindUsesDedicatedPayload(t *testing.T) {
	body := map[string]any{
		sunnyEmailBindEnabledKey:  true,
		sunnyEmailBindCategoryKey: "microsoft",
		sunnyEmailBindTargetsKey: []any{map[string]any{
			"email":           "target@example.com",
			"mailbox_api":     "mail-password----11111111-1111-1111-1111-111111111111----refresh-token",
			"mailbox_type":    "microsoft",
			"mailbox_channel": "outlook",
		}},
	}
	if err := prepareSunnyPostRegistrationEmailBind(body, 1); err != nil {
		t.Fatalf("prepare email bind: %v", err)
	}
	targets, ok := body[sunnyEmailBindTargetsKey].([]map[string]any)
	if !ok || len(targets) != 1 || text(targets[0]["email"]) != "target@example.com" {
		t.Fatalf("validated targets = %#v", body[sunnyEmailBindTargetsKey])
	}
	if _, mixed := body["target_mailboxes"]; mixed {
		t.Fatal("dedicated phone registration payload leaked into generic rebind field")
	}
}

func TestPrepareSunnyPostRegistrationEmailBindRequiresEnoughTargets(t *testing.T) {
	body := map[string]any{
		sunnyEmailBindEnabledKey:  true,
		sunnyEmailBindCategoryKey: "microsoft",
		sunnyEmailBindTargetsKey:  []any{},
	}
	if err := prepareSunnyPostRegistrationEmailBind(body, 1); err == nil {
		t.Fatal("expected insufficient binding targets to fail")
	}
}
