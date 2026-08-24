package main

import "testing"

func TestConfiguredSub2APINotes(t *testing.T) {
	mailbox := SunnyMailbox{
		Email:           "ls@example.com",
		ChatGPTPassword: "ChatGPT-pass",
		TOTPSecret:      "JBSWY3DPEHPK3PXP",
	}
	secretKey := "sk@example.com----mail----client----refresh"

	if got := sunnySub2NotesWithConfig(mailbox, secretKey, defaultSub2APIConfig()); got != "" {
		t.Fatalf("disabled sub2api notes = %q", got)
	}

	cfg := mergeConfig(defaultSub2APIConfig(), map[string]any{
		"notes_include_sk":     true,
		"notes_include_ls":     true,
		"notes_include_custom": true,
		"notes_custom_text":    "自定义备注",
	})
	want := "邮箱凭证：" + secretKey + "\n密码2FA：ls@example.com----ChatGPT-pass----JBSWY3DPEHPK3PXP\n自定义备注"
	if got := sunnySub2NotesWithConfig(mailbox, secretKey, cfg); got != want {
		t.Fatalf("configured sub2api notes = %q, want %q", got, want)
	}

	if got := sunnySub2NotesWithConfig(mailbox, secretKey, map[string]any{"notes_include_ls": true}); got != "密码2FA：ls@example.com----ChatGPT-pass----JBSWY3DPEHPK3PXP" {
		t.Fatalf("configured LS-only sub2api notes = %q", got)
	}
}

func TestBuildSunnySub2AccountPayloadUsesConfiguredNotes(t *testing.T) {
	session := SunnySession{
		Email:          "user@example.com",
		AccessToken:    "access-token",
		RefreshToken:   "refresh-token",
		RawMailboxLine: "user@example.com----mail----client----refresh",
	}
	mailbox := SunnyMailbox{
		Email:           session.Email,
		ChatGPTPassword: "ChatGPT-pass",
		TOTPSecret:      "JBSWY3DPEHPK3PXP",
	}
	cfg := mergeConfig(defaultSub2APIConfig(), map[string]any{
		"notes_include_sk":     true,
		"notes_include_custom": true,
		"notes_custom_text":    "batch-01",
	})

	payload := buildSunnySub2AccountPayload(session, cfg, mailbox)
	want := "邮箱凭证：user@example.com----mail----client----refresh\nbatch-01"
	if got := text(payload["notes"]); got != want {
		t.Fatalf("payload notes = %q, want %q", got, want)
	}
}
