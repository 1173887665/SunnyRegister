package main

import (
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const gopayRequestBodyLimit = 2 << 20

func (s *Server) handlePayments(w http.ResponseWriter, r *http.Request, rest string) {
	if !strings.HasPrefix(rest, "/gopay/") {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	gopayPath := strings.TrimPrefix(rest, "/gopay/")
	if !validGoPayPath(gopayPath) {
		writeError(w, http.StatusNotFound, "not found")
		return
	}
	s.proxyGoPayWorker(w, r, gopayPath)
}

func validGoPayPath(value string) bool {
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
	workerURL := strings.TrimRight(strings.TrimSpace(os.Getenv("PYTHON_WORKER_URL")), "/")
	if workerURL == "" {
		workerURL = "http://127.0.0.1:8765"
	}
	target, err := url.Parse(workerURL + "/gopay/" + strings.TrimLeft(rest, "/"))
	if err != nil {
		writeError(w, http.StatusBadGateway, "GoPay 服务地址无效")
		return
	}
	target.RawQuery = r.URL.RawQuery
	var body io.Reader
	if r.Method == http.MethodPost {
		body = io.LimitReader(r.Body, gopayRequestBodyLimit)
	}
	req, err := http.NewRequestWithContext(r.Context(), r.Method, target.String(), body)
	if err != nil {
		writeError(w, http.StatusBadGateway, "无法创建 GoPay 请求")
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
		writeError(w, http.StatusBadGateway, "无法连接 GoPay 服务")
		return
	}
	defer resp.Body.Close()
	w.Header().Set("Content-Type", fallback(resp.Header.Get("Content-Type"), "application/json; charset=utf-8"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, io.LimitReader(resp.Body, 8<<20))
}
