package main

import (
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const paymentRequestBodyLimit = 2 << 20

func (s *Server) handlePayments(w http.ResponseWriter, r *http.Request, rest string) {
	provider := ""
	switch {
	case strings.HasPrefix(rest, "/gopay/"):
		provider = "gopay"
	case strings.HasPrefix(rest, "/momo/"):
		provider = "momo"
	default:
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	providerPath := strings.TrimPrefix(rest, "/"+provider+"/")
	if !validPaymentPath(providerPath) {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	s.proxyPaymentWorker(w, r, provider, providerPath)
}

func validPaymentPath(value string) bool {
	if value == "" || strings.Contains(value, `\`) {
		return false
	}
	for _, segment := range strings.Split(value, "/") {
		if segment == "." || segment == ".." {
			return false
		}
	}
	return true
}

func (s *Server) proxyGoPayWorker(w http.ResponseWriter, r *http.Request, rest string) {
	s.proxyPaymentWorker(w, r, "gopay", rest)
}

func (s *Server) proxyPaymentWorker(w http.ResponseWriter, r *http.Request, provider, rest string) {
	workerURL := strings.TrimRight(strings.TrimSpace(os.Getenv("PYTHON_WORKER_URL")), "/")
	if workerURL == "" {
		workerURL = "http://127.0.0.1:8765"
	}
	target, err := url.Parse(workerURL + "/" + provider + "/" + strings.TrimLeft(rest, "/"))
	if err != nil {
		writeError(w, http.StatusBadGateway, provider+" 服务地址无效")
		return
	}
	target.RawQuery = r.URL.RawQuery
	var body io.Reader
	if r.Method == http.MethodPost {
	body = io.LimitReader(r.Body, paymentRequestBodyLimit)
	}
	req, err := http.NewRequestWithContext(r.Context(), r.Method, target.String(), body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "创建 "+provider+" 请求失败")
		return
	}
	if contentType := r.Header.Get("Content-Type"); contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if token := secretValue("PYTHON_WORKER_TOKEN", "PYTHON_WORKER_TOKEN_FILE"); token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := (&http.Client{Timeout: 5 * time.Minute}).Do(req)
	if err != nil {
		writeError(w, http.StatusBadGateway, "连接 "+provider+" 服务失败")
		return
	}
	defer resp.Body.Close()
	w.Header().Set("Content-Type", fallback(resp.Header.Get("Content-Type"), "application/json; charset=utf-8"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, io.LimitReader(resp.Body, 8<<20))
}
