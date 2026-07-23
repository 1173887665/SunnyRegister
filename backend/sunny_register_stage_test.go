package main

import "testing"

func TestSunnyRegistrationStageAcceptsAgentIdentityReverseProxy(t *testing.T) {
	tests := []struct {
		name string
		body map[string]any
		want string
	}{
		{name: "agent identity", body: map[string]any{"registration_stage": "agent_identity_reverse_proxy"}, want: "agent_identity_reverse_proxy"},
		{name: "phone bind", body: map[string]any{"registration_stage": "codex_phone_bind"}, want: "codex_phone_bind"},
		{name: "legacy reverse", body: map[string]any{"registration_stage": "import_reverse_proxy"}, want: "import_reverse_proxy"},
		{name: "unknown defaults", body: map[string]any{"registration_stage": "unknown"}, want: "register_only"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := sunnyRegistrationStage(tt.body); got != tt.want {
				t.Fatalf("sunnyRegistrationStage() = %q, want %q", got, tt.want)
			}
		})
	}
}
