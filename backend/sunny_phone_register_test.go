package main

import "testing"

func TestValidateImportedRebindMailboxAcceptsMicrosoftOAuthCredential(t *testing.T) {
	err := validateImportedRebindMailbox(
		"mail-password----11111111-1111-1111-1111-111111111111----refresh-token",
		"target@example.com",
		"microsoft",
		"outlook",
	)
	if err != nil {
		t.Fatalf("expected Microsoft target credential to validate: %v", err)
	}
}

func TestValidateImportedRebindMailboxRejectsIncompleteMicrosoftOAuthCredential(t *testing.T) {
	if err := validateImportedRebindMailbox("mail-password----client-id", "target@example.com", "microsoft", "outlook"); err == nil {
		t.Fatal("expected incomplete Microsoft target credential to be rejected")
	}
}
