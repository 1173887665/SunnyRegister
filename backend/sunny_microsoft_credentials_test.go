package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

const (
	testMicrosoftClientID     = "11111111-2222-4333-8444-555555555555"
	testMicrosoftRefreshToken = "M.C502_BL2.0.U.MsaArtifacts.-example-refresh-token-with-enough-length-for-field-detection!0123456789$"
)

func TestParseSunnyMailboxLineNormalizesMicrosoftCredentialOrder(t *testing.T) {
	orders := []string{
		"user@outlook.com----mail-password----" + testMicrosoftClientID + "----" + testMicrosoftRefreshToken,
		"user@outlook.com----mail-password----" + testMicrosoftRefreshToken + "----" + testMicrosoftClientID,
		"user@outlook.com----" + testMicrosoftClientID + "----mail-password----" + testMicrosoftRefreshToken,
		"user@outlook.com----" + testMicrosoftClientID + "----" + testMicrosoftRefreshToken + "----mail-password",
		"user@outlook.com----" + testMicrosoftRefreshToken + "----mail-password----" + testMicrosoftClientID,
		"user@outlook.com----" + testMicrosoftRefreshToken + "----" + testMicrosoftClientID + "----mail-password",
	}
	for _, line := range orders {
		parsed, err := parseSunnyMailboxLine(line)
		if err != nil {
			t.Fatalf("parse %q: %v", line, err)
		}
		if parsed["password"] != "mail-password" || parsed["client_id"] != testMicrosoftClientID || parsed["refresh_token"] != testMicrosoftRefreshToken {
			t.Fatalf("unexpected normalized credentials: %#v", parsed)
		}
	}
}

func TestParseSunnyMailboxLinePreservesLegacyPositionalCredentials(t *testing.T) {
	parsed, err := parseSunnyMailboxLine("user@outlook.com----password----client-id----refresh-token")
	if err != nil {
		t.Fatalf("parse legacy credential: %v", err)
	}
	if parsed["password"] != "password" || parsed["client_id"] != "client-id" || parsed["refresh_token"] != "refresh-token" {
		t.Fatalf("legacy fields changed: %#v", parsed)
	}
}

func TestSunnyMailboxImportStoresCanonicalMicrosoftCredential(t *testing.T) {
	s := newSunnySessionTestServer(t)
	line := "ordered@outlook.com----mail-password----" + testMicrosoftRefreshToken + "----" + testMicrosoftClientID
	body, _ := json.Marshal(map[string]any{"mailbox_type": "microsoft", "lines": line})
	recorder := httptest.NewRecorder()
	s.sunnyImportMailboxes(recorder, httptest.NewRequest(http.MethodPost, "/sunny/mailboxes/import", bytes.NewReader(body)))
	if recorder.Code != http.StatusOK {
		t.Fatalf("import status=%d body=%s", recorder.Code, recorder.Body.String())
	}
	var mailbox SunnyMailbox
	if err := s.db.Where("email = ?", "ordered@outlook.com").First(&mailbox).Error; err != nil {
		t.Fatalf("load imported mailbox: %v", err)
	}
	wantRaw := sunnyMicrosoftRaw(mailbox.Email, "mail-password", testMicrosoftClientID, testMicrosoftRefreshToken)
	if mailbox.Password != "mail-password" || mailbox.ClientID != testMicrosoftClientID || mailbox.RefreshToken != testMicrosoftRefreshToken || mailbox.Raw != wantRaw {
		t.Fatalf("mailbox was not canonicalized: %#v", mailbox)
	}
}
