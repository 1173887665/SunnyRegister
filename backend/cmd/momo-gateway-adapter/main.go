package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const maxBodyBytes = 1 << 20

var orderIDPattern = regexp.MustCompile(`^[0-9A-Za-z](?:[-_.]*[0-9A-Za-z]+)*$`)

type config struct {
	ListenAddr         string
	BaseURL            string
	PartnerCode        string
	AccessKey          string
	SecretKey          string
	IPNURL             string
	RedirectURL        string
	AdapterAPIToken    string
	SunnyWebhookSecret string
}

type server struct {
	cfg    config
	client *http.Client
}

type createInput struct {
	Amount      int64             `json:"amount"`
	OrderID     string            `json:"order_id"`
	RequestID   string            `json:"request_id"`
	OrderInfo   string            `json:"order_info"`
	ExtraData   map[string]string `json:"extra_data"`
	Lang        string            `json:"lang"`
	PartnerName string            `json:"partner_name"`
	StoreID     string            `json:"store_id"`
}

type createPayload struct {
	PartnerCode string `json:"partnerCode"`
	PartnerName string `json:"partnerName,omitempty"`
	StoreID     string `json:"storeId,omitempty"`
	RequestType string `json:"requestType"`
	IPNURL      string `json:"ipnUrl"`
	RedirectURL string `json:"redirectUrl"`
	OrderID     string `json:"orderId"`
	Amount      int64  `json:"amount"`
	OrderInfo   string `json:"orderInfo"`
	RequestID   string `json:"requestId"`
	ExtraData   string `json:"extraData"`
	Lang        string `json:"lang"`
	AutoCapture bool   `json:"autoCapture"`
	Signature   string `json:"signature"`
}

type momoIPN struct {
	PartnerCode  string `json:"partnerCode"`
	OrderID      string `json:"orderId"`
	RequestID    string `json:"requestId"`
	Amount       int64  `json:"amount"`
	OrderInfo    string `json:"orderInfo"`
	OrderType    string `json:"orderType"`
	TransID      int64  `json:"transId"`
	ResultCode   int    `json:"resultCode"`
	Message      string `json:"message"`
	PayType      string `json:"payType"`
	ResponseTime int64  `json:"responseTime"`
	ExtraData    string `json:"extraData"`
	Signature    string `json:"signature"`
}

func main() {
	cfg, err := loadConfig()
	if err != nil {
		log.Fatal(err)
	}
	s := &server{cfg: cfg, client: &http.Client{Timeout: 35 * time.Second}}
	httpServer := &http.Server{
		Addr:              cfg.ListenAddr,
		Handler:           s.routes(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       40 * time.Second,
		WriteTimeout:      40 * time.Second,
		IdleTimeout:       90 * time.Second,
	}
	log.Printf("MoMo gateway adapter listening on %s", cfg.ListenAddr)
	log.Fatal(httpServer.ListenAndServe())
}

func loadConfig() (config, error) {
	cfg := config{
		ListenAddr:         envOr("MOMO_ADAPTER_LISTEN", "127.0.0.1:8788"),
		BaseURL:            strings.TrimRight(envOr("MOMO_BASE_URL", "https://test-payment.momo.vn"), "/"),
		PartnerCode:        strings.TrimSpace(os.Getenv("MOMO_PARTNER_CODE")),
		AccessKey:          strings.TrimSpace(os.Getenv("MOMO_ACCESS_KEY")),
		SecretKey:          strings.TrimSpace(os.Getenv("MOMO_SECRET_KEY")),
		IPNURL:             strings.TrimSpace(os.Getenv("MOMO_IPN_URL")),
		RedirectURL:        strings.TrimSpace(os.Getenv("MOMO_REDIRECT_URL")),
		AdapterAPIToken:    strings.TrimSpace(os.Getenv("MOMO_ADAPTER_API_TOKEN")),
		SunnyWebhookSecret: strings.TrimSpace(os.Getenv("SUNNY_WEBHOOK_SECRET")),
	}
	for name, value := range map[string]string{
		"MOMO_PARTNER_CODE": cfg.PartnerCode, "MOMO_ACCESS_KEY": cfg.AccessKey,
		"MOMO_SECRET_KEY": cfg.SecretKey, "MOMO_IPN_URL": cfg.IPNURL,
		"MOMO_REDIRECT_URL": cfg.RedirectURL, "MOMO_ADAPTER_API_TOKEN": cfg.AdapterAPIToken,
		"SUNNY_WEBHOOK_SECRET": cfg.SunnyWebhookSecret,
	} {
		if value == "" {
			return config{}, fmt.Errorf("%s is required", name)
		}
	}
	if err := validateMoMoBaseURL(cfg.BaseURL); err != nil {
		return config{}, err
	}
	if err := validateHTTPSURL("MOMO_IPN_URL", cfg.IPNURL); err != nil {
		return config{}, err
	}
	if err := validateHTTPSURL("MOMO_REDIRECT_URL", cfg.RedirectURL); err != nil {
		return config{}, err
	}
	return cfg, nil
}

func envOr(name, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func validateMoMoBaseURL(raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Path != "" {
		return errors.New("MOMO_BASE_URL must be an HTTPS origin")
	}
	host := strings.ToLower(u.Hostname())
	if host != "test-payment.momo.vn" && host != "payment.momo.vn" {
		return errors.New("MOMO_BASE_URL host must be test-payment.momo.vn or payment.momo.vn")
	}
	return nil
}

func validateHTTPSURL(name, raw string) error {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" {
		return fmt.Errorf("%s must be an absolute HTTPS URL", name)
	}
	return nil
}

func (s *server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	})
	mux.HandleFunc("POST /payments/momo/create", s.createPayment)
	mux.HandleFunc("POST /payments/momo/ipn", s.receiveMoMoIPN)
	mux.HandleFunc("POST /webhooks/sunny", s.receiveSunnyWebhook)
	return mux
}

func (s *server) createPayment(w http.ResponseWriter, r *http.Request) {
	if !bearerMatches(r.Header.Get("Authorization"), s.cfg.AdapterAPIToken) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid adapter token"})
		return
	}
	var input createInput
	if err := decodeJSON(r, &input); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if input.Amount < 1000 || input.Amount > 50_000_000 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "amount must be between 1000 and 50000000 VND"})
		return
	}
	if input.OrderID == "" {
		input.OrderID = newID("ORDER")
	}
	if input.RequestID == "" {
		input.RequestID = newID("REQ")
	}
	if !orderIDPattern.MatchString(input.OrderID) || len(input.OrderID) > 200 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid order_id"})
		return
	}
	if !orderIDPattern.MatchString(input.RequestID) || len(input.RequestID) > 50 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request_id"})
		return
	}
	input.OrderInfo = strings.TrimSpace(input.OrderInfo)
	if input.OrderInfo == "" || len(input.OrderInfo) > 255 {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "order_info is required and must be at most 255 characters"})
		return
	}
	extraJSON, err := json.Marshal(input.ExtraData)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid extra_data"})
		return
	}
	extraData := ""
	if len(input.ExtraData) > 0 {
		extraData = base64.StdEncoding.EncodeToString(extraJSON)
	}
	lang := strings.ToLower(strings.TrimSpace(input.Lang))
	if lang != "vi" {
		lang = "en"
	}
	payload := createPayload{
		PartnerCode: s.cfg.PartnerCode, PartnerName: input.PartnerName, StoreID: input.StoreID,
		RequestType: "captureWallet", IPNURL: s.cfg.IPNURL, RedirectURL: s.cfg.RedirectURL,
		OrderID: input.OrderID, Amount: input.Amount, OrderInfo: input.OrderInfo,
		RequestID: input.RequestID, ExtraData: extraData, Lang: lang, AutoCapture: true,
	}
	payload.Signature = signSHA256(s.cfg.SecretKey, createSignatureSource(s.cfg.AccessKey, payload))
	body, _ := json.Marshal(payload)
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, s.cfg.BaseURL+"/v2/gateway/api/create", bytes.NewReader(body))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": "failed to build upstream request"})
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	resp, err := s.client.Do(req)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": "MoMo request failed: " + err.Error()})
		return
	}
	defer resp.Body.Close()
	upstream, err := io.ReadAll(io.LimitReader(resp.Body, maxBodyBytes))
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": "failed to read MoMo response"})
		return
	}
	var result map[string]any
	if json.Unmarshal(upstream, &result) != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": "MoMo returned non-JSON response", "status": resp.StatusCode})
		return
	}
	status := http.StatusOK
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		status = http.StatusBadGateway
	}
	writeJSON(w, status, result)
}

func createSignatureSource(accessKey string, p createPayload) string {
	return fmt.Sprintf("accessKey=%s&amount=%d&extraData=%s&ipnUrl=%s&orderId=%s&orderInfo=%s&partnerCode=%s&redirectUrl=%s&requestId=%s&requestType=%s",
		accessKey, p.Amount, p.ExtraData, p.IPNURL, p.OrderID, p.OrderInfo,
		p.PartnerCode, p.RedirectURL, p.RequestID, p.RequestType)
}

func (s *server) receiveMoMoIPN(w http.ResponseWriter, r *http.Request) {
	var payload momoIPN
	if err := decodeJSON(r, &payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	if payload.PartnerCode != s.cfg.PartnerCode || !hmac.Equal([]byte(strings.ToLower(payload.Signature)), []byte(signSHA256(s.cfg.SecretKey, ipnSignatureSource(s.cfg.AccessKey, payload)))) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid MoMo signature"})
		return
	}
	// Persist orderId, transId and resultCode in the application's order store here.
	log.Printf("verified MoMo IPN order=%s transaction=%d result=%d", payload.OrderID, payload.TransID, payload.ResultCode)
	w.WriteHeader(http.StatusNoContent)
}

func ipnSignatureSource(accessKey string, p momoIPN) string {
	return fmt.Sprintf("accessKey=%s&amount=%d&extraData=%s&message=%s&orderId=%s&orderInfo=%s&orderType=%s&partnerCode=%s&payType=%s&requestId=%s&responseTime=%d&resultCode=%d&transId=%d",
		accessKey, p.Amount, p.ExtraData, p.Message, p.OrderID, p.OrderInfo,
		p.OrderType, p.PartnerCode, p.PayType, p.RequestID, p.ResponseTime, p.ResultCode, p.TransID)
}

func (s *server) receiveSunnyWebhook(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid webhook body"})
		return
	}
	ts := strings.TrimSpace(r.Header.Get("X-Sunny-Timestamp"))
	unixTime, err := strconv.ParseInt(ts, 10, 64)
	if err != nil || time.Since(time.Unix(unixTime, 0)) > 5*time.Minute || time.Until(time.Unix(unixTime, 0)) > time.Minute {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid webhook timestamp"})
		return
	}
	expected := "sha256=" + signSHA256(s.cfg.SunnyWebhookSecret, ts+"."+string(body))
	provided := strings.ToLower(strings.TrimSpace(r.Header.Get("X-Sunny-Signature")))
	if !hmac.Equal([]byte(provided), []byte(expected)) {
		writeJSON(w, http.StatusUnauthorized, map[string]any{"error": "invalid Sunny signature"})
		return
	}
	var envelope map[string]any
	if json.Unmarshal(body, &envelope) != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "webhook body must be JSON"})
		return
	}
	log.Printf("verified Sunny webhook event=%s delivery=%s", r.Header.Get("X-Sunny-Event"), r.Header.Get("X-Sunny-Delivery"))
	w.WriteHeader(http.StatusNoContent)
}

func signSHA256(secret, value string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(value))
	return hex.EncodeToString(mac.Sum(nil))
}

func bearerMatches(header, expected string) bool {
	provided := strings.TrimSpace(strings.TrimPrefix(header, "Bearer "))
	if provided == "" || len(provided) != len(expected) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(provided), []byte(expected)) == 1
}

func newID(prefix string) string {
	raw := make([]byte, 8)
	_, _ = rand.Read(raw)
	return fmt.Sprintf("%s_%d_%s", prefix, time.Now().UnixMilli(), hex.EncodeToString(raw))
}

func decodeJSON(r *http.Request, target any) error {
	defer r.Body.Close()
	body, err := io.ReadAll(io.LimitReader(r.Body, maxBodyBytes+1))
	if err != nil {
		return fmt.Errorf("read JSON: %w", err)
	}
	if len(body) > maxBodyBytes {
		return errors.New("request body is too large")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("invalid JSON: %w", err)
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return errors.New("request body must contain one JSON object")
	}
	return nil
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
