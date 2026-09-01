package main

import "testing"

func TestPythonWorkerTypesIncludesSunnyRebind(t *testing.T) {
	t.Setenv("PYTHON_TASK_TYPES", "sunny_register,sunny_login,sunny_refresh_session,sunny_acquire_rt")
	types := pythonWorkerTypes()
	for _, taskType := range []string{"sunny_register", "sunny_phone_register", "sunny_login", "sunny_refresh_session", "sunny_acquire_rt", "sunny_rebind"} {
		if !types[taskType] {
			t.Fatalf("python worker task type %q must be enabled", taskType)
		}
	}
}
