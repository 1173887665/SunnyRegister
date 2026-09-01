package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHandlePaymentsProxiesGoPayRequest(t *testing.T) {
	var receivedPath, receivedQuery, receivedAuth, receivedBody string
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path
		receivedQuery = r.URL.RawQuery
		receivedAuth = r.Header.Get("Authorization")
		body, _ := io.ReadAll(r.Body)
		receivedBody = string(body)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer worker.Close()
	t.Setenv("PYTHON_WORKER_URL", worker.URL)
	t.Setenv("PYTHON_WORKER_TOKEN", "worker-secret")

	req := httptest.NewRequest(http.MethodPost, "/api/payments/gopay/payment?trace=1", strings.NewReader(`{"phone":"6281"}`))
	recorder := httptest.NewRecorder()
	(&Server{}).handlePayments(recorder, req, "/gopay/payment")

	if recorder.Code != http.StatusCreated {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusCreated)
	}
	if receivedPath != "/gopay/payment" || receivedQuery != "trace=1" {
		t.Fatalf("worker target = %s?%s", receivedPath, receivedQuery)
	}
	if receivedAuth != "Bearer worker-secret" {
		t.Fatalf("authorization = %q", receivedAuth)
	}
	if receivedBody != `{"phone":"6281"}` {
		t.Fatalf("body = %q", receivedBody)
	}
	var response map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil || response["ok"] != true {
		t.Fatalf("unexpected response: %s (%v)", recorder.Body.String(), err)
	}
}

func TestHandlePaymentsProxiesMomoRequest(t *testing.T) {
	var receivedPath string
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"ok":true,"provider":"momo"}`))
	}))
	defer worker.Close()
	t.Setenv("PYTHON_WORKER_URL", worker.URL)

	req := httptest.NewRequest(http.MethodPost, "/api/payments/momo/payment", strings.NewReader(`{"phone":"+84901234567","qr_payload":"momo://fixture"}`))
	recorder := httptest.NewRecorder()
	(&Server{}).handlePayments(recorder, req, "/momo/payment")

	if recorder.Code != http.StatusCreated {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusCreated)
	}
	if receivedPath != "/momo/payment" {
		t.Fatalf("worker target = %s", receivedPath)
	}
}

func TestHandlePaymentsRejectsUnknownProvider(t *testing.T) {
	recorder := httptest.NewRecorder()
	(&Server{}).handlePayments(recorder, httptest.NewRequest(http.MethodGet, "/api/payments/paypal/accounts", nil), "/paypal/accounts")
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusNotFound)
	}
}

func TestHandlePaymentsRejectsPathTraversal(t *testing.T) {
	for _, path := range []string{"../health", `accounts\..\health`, "accounts/./status"} {
		recorder := httptest.NewRecorder()
		(&Server{}).handlePayments(recorder, httptest.NewRequest(http.MethodGet, "/api/payments/gopay/"+path, nil), "/gopay/"+path)
		if recorder.Code != http.StatusNotFound {
			t.Fatalf("path %q returned %d, want %d", path, recorder.Code, http.StatusNotFound)
		}
	}
}

func TestHandlePaymentsRejectsOversizedBodyWithoutForwarding(t *testing.T) {
	forwarded := false
	worker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		forwarded = true
		w.WriteHeader(http.StatusOK)
	}))
	defer worker.Close()
	t.Setenv("PYTHON_WORKER_URL", worker.URL)

	req := httptest.NewRequest(http.MethodPost, "/api/payments/gopay/payment", strings.NewReader(`{"payload":"`+strings.Repeat("x", paymentRequestBodyLimit)+`"}`))
	recorder := httptest.NewRecorder()
	(&Server{}).handlePayments(recorder, req, "/gopay/payment")
	if recorder.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusRequestEntityTooLarge)
	}
	if forwarded {
		t.Fatal("oversized payment request was forwarded")
	}
}
