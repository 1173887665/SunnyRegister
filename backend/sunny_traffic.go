package main

import (
	"context"
	"io"
	"net/http"
	"strings"
	"sync"

	"gorm.io/gorm"
)

type sunnyTrafficMeter struct {
	mu                  sync.Mutex
	Requests            int64
	RequestHeaderBytes  int64
	RequestBodyBytes    int64
	ResponseHeaderBytes int64
	ResponseBodyBytes   int64
}

type sunnyTrafficMeterKey struct{}

func withSunnyTrafficMeter(ctx context.Context, meter *sunnyTrafficMeter) context.Context {
	return context.WithValue(ctx, sunnyTrafficMeterKey{}, meter)
}

func sunnyTrafficMeterFromContext(ctx context.Context) *sunnyTrafficMeter {
	if ctx == nil {
		return nil
	}
	meter, _ := ctx.Value(sunnyTrafficMeterKey{}).(*sunnyTrafficMeter)
	return meter
}

func (m *sunnyTrafficMeter) record(req *http.Request, responseHeaders http.Header, status int, responseBody int64) {
	if m == nil || req == nil {
		return
	}
	requestHeaderBytes := int64(len(req.Method) + 1 + len(req.URL.RequestURI()) + len(" HTTP/1.1\r\n"))
	for key, values := range req.Header {
		for _, value := range values {
			requestHeaderBytes += int64(len(key) + len(value) + 4)
		}
	}
	requestBodyBytes := req.ContentLength
	if requestBodyBytes < 0 {
		requestBodyBytes = 0
	}
	responseHeaderBytes := int64(len("HTTP/1.1 ") + 3 + len("\r\n"))
	for key, values := range responseHeaders {
		for _, value := range values {
			responseHeaderBytes += int64(len(key) + len(value) + 4)
		}
	}
	m.mu.Lock()
	m.Requests++
	m.RequestHeaderBytes += requestHeaderBytes
	m.RequestBodyBytes += requestBodyBytes
	m.ResponseHeaderBytes += responseHeaderBytes
	m.ResponseBodyBytes += responseBody
	m.mu.Unlock()
	_ = status
}

func (m *sunnyTrafficMeter) totalBytes() int64 {
	if m == nil {
		return 0
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.RequestHeaderBytes + m.RequestBodyBytes + m.ResponseHeaderBytes + m.ResponseBodyBytes
}

type sunnyTrafficTransport struct {
	base  http.RoundTripper
	meter *sunnyTrafficMeter
}

func (t *sunnyTrafficTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	base := t.base
	if base == nil {
		base = http.DefaultTransport
	}
	response, err := base.RoundTrip(req)
	if err != nil || response == nil {
		return response, err
	}
	response.Body = &sunnyTrafficBody{ReadCloser: response.Body, meter: t.meter, response: response, request: req}
	return response, nil
}

type sunnyTrafficBody struct {
	io.ReadCloser
	meter    *sunnyTrafficMeter
	response *http.Response
	request  *http.Request
	read     int64
	once     sync.Once
}

func (b *sunnyTrafficBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	b.read += int64(n)
	if err == io.EOF {
		b.finish()
	}
	return n, err
}

func (b *sunnyTrafficBody) Close() error {
	err := b.ReadCloser.Close()
	b.finish()
	return err
}

func (b *sunnyTrafficBody) finish() {
	b.once.Do(func() {
		b.meter.record(b.request, b.response.Header, b.response.StatusCode, b.read)
	})
}

func (s *Server) recordSunnyProxyTraffic(email string, bytes int64) {
	if s == nil || strings.TrimSpace(email) == "" || bytes <= 0 {
		return
	}
	s.db.Model(&SunnyMailbox{}).Where("email = ?", email).UpdateColumn("proxy_traffic_bytes", gorm.Expr("coalesce(proxy_traffic_bytes,0) + ?", bytes))
}
