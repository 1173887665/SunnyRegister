package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}

func TestCreateSignatureSourceOrder(t *testing.T) {
	payload := createPayload{
		PartnerCode: "PARTNER", RequestType: "captureWallet",
		IPNURL: "https://merchant.example/ipn", RedirectURL: "https://merchant.example/return",
		OrderID: "ORDER_1", Amount: 1000, OrderInfo: "test", RequestID: "REQ_1", ExtraData: "",
	}
	want := "accessKey=ACCESS&amount=1000&extraData=&ipnUrl=https://merchant.example/ipn&orderId=ORDER_1&orderInfo=test&partnerCode=PARTNER&redirectUrl=https://merchant.example/return&requestId=REQ_1&requestType=captureWallet"
	if got := createSignatureSource("ACCESS", payload); got != want {
		t.Fatalf("signature source mismatch\n got: %s\nwant: %s", got, want)
	}
}

func TestReceiveMoMoIPN(t *testing.T) {
	cfg := config{PartnerCode: "PARTNER", AccessKey: "ACCESS", SecretKey: "SECRET"}
	s := &server{cfg: cfg}
	payload := momoIPN{PartnerCode: "PARTNER", OrderID: "ORDER_1", RequestID: "REQ_1", Amount: 1000, OrderInfo: "test", OrderType: "momo_wallet", TransID: 7, ResultCode: 0, Message: "Successful.", PayType: "qr", ResponseTime: 1721720663942}
	payload.Signature = signSHA256(cfg.SecretKey, ipnSignatureSource(cfg.AccessKey, payload))
	body := []byte(`{"partnerCode":"PARTNER","orderId":"ORDER_1","requestId":"REQ_1","amount":1000,"orderInfo":"test","orderType":"momo_wallet","transId":7,"resultCode":0,"message":"Successful.","payType":"qr","responseTime":1721720663942,"extraData":"","signature":"` + payload.Signature + `"}`)
	req := httptest.NewRequest(http.MethodPost, "/payments/momo/ipn", bytes.NewReader(body))
	rec := httptest.NewRecorder()
	s.receiveMoMoIPN(rec, req)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
}

func TestCreatePaymentSendsSignedMoMoPayload(t *testing.T) {
	cfg := config{
		BaseURL: "https://test-payment.momo.vn", PartnerCode: "PARTNER", AccessKey: "ACCESS", SecretKey: "SECRET",
		IPNURL: "https://merchant.example/payments/momo/ipn", RedirectURL: "https://merchant.example/payments/momo/return", AdapterAPIToken: "adapter-token",
	}
	s := &server{cfg: cfg, client: &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.URL.Path != "/v2/gateway/api/create" {
			t.Fatalf("unexpected upstream path: %s", request.URL.Path)
		}
		var payload createPayload
		if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
			t.Fatalf("decode upstream payload: %v", err)
		}
		wantSignature := signSHA256(cfg.SecretKey, createSignatureSource(cfg.AccessKey, payload))
		if payload.Signature != wantSignature {
			t.Fatalf("signature = %s, want %s", payload.Signature, wantSignature)
		}
		return &http.Response{StatusCode: http.StatusOK, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(`{"resultCode":0,"payUrl":"https://test-payment.momo.vn/v2/gateway/pay?t=fixture"}`))}, nil
	})}}
	req := httptest.NewRequest(http.MethodPost, "/payments/momo/create", strings.NewReader(`{"amount":1000,"order_id":"ORDER_1","request_id":"REQ_1","order_info":"test","extra_data":{"source":"fixture"}}`))
	req.Header.Set("Authorization", "Bearer adapter-token")
	rec := httptest.NewRecorder()
	s.createPayment(rec, req)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "payUrl") {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
}

func TestReceiveSunnyWebhook(t *testing.T) {
	secret := "sunny-secret"
	body := []byte(`{"event":"account.updated"}`)
	ts := strconv.FormatInt(time.Now().Unix(), 10)
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(ts + "."))
	_, _ = mac.Write(body)
	signature := "sha256=" + hex.EncodeToString(mac.Sum(nil))
	s := &server{cfg: config{SunnyWebhookSecret: secret}}
	req := httptest.NewRequest(http.MethodPost, "/webhooks/sunny", bytes.NewReader(body))
	req.Header.Set("X-Sunny-Timestamp", ts)
	req.Header.Set("X-Sunny-Signature", signature)
	req.Header.Set("X-Sunny-Event", "account.updated")
	rec := httptest.NewRecorder()
	s.receiveSunnyWebhook(rec, req)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("status = %d, body = %s", rec.Code, rec.Body.String())
	}
}

func TestBearerMatches(t *testing.T) {
	if !bearerMatches("Bearer token-value", "token-value") {
		t.Fatal("expected matching bearer token")
	}
	if bearerMatches("Bearer wrong", "token-value") {
		t.Fatal("unexpected bearer token match")
	}
}
