package main

import (
	"compress/gzip"
	"context"
	"crypto/subtle"
	"embed"
	"errors"
	"io/fs"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"gorm.io/gorm"
)

//go:embed static/*
var embeddedStatic embed.FS

type Server struct {
	db             *gorm.DB
	adminUser      string
	adminPass      string
	staticFS       http.FileSystem
	wake           chan struct{}
	stop           chan struct{}
	running        map[string]bool
	runtimeMu      sync.Mutex
	atCheckMu      sync.Mutex
	trialCheckMu   sync.Mutex
	paymentProbeMu sync.Mutex
	maintenanceMu  sync.RWMutex
	maintenance    map[string]any
	smsOptionsMu   sync.Mutex
	smsOptionsRun  map[string]*sunnySMSOptionsFlight
	sessionMu      sync.Mutex
	sessions       map[string]time.Time
	loginMu        sync.Mutex
	loginFailures  map[string]*loginFailure
	sessionTTL     time.Duration
	secureCookies  bool
	production     bool
	checkoutMu     sync.Mutex
	checkoutCreds  map[string]checkoutSecret
}

type loginFailure struct {
	Count       int
	WindowStart time.Time
	BlockedTill time.Time
}

func main() {
	loadDotEnv(".env")
	loadDotEnv("../.env")
	configureApplicationTimezone()
	if len(os.Args) > 1 && os.Args[1] == "--healthcheck" {
		resp, err := http.Get("http://127.0.0.1:" + fallback(os.Getenv("PORT"), "8000") + "/api/health")
		if err != nil || resp.StatusCode >= 500 {
			os.Exit(1)
		}
		if resp != nil {
			_ = resp.Body.Close()
		}
		return
	}
	db := openDB()
	if len(os.Args) > 1 && os.Args[1] == "--migrate-only" {
		log.Printf("PostgreSQL schema migration completed")
		return
	}
	seedProviderDefinitions(db)
	markInterrupted(db)
	staticFS := resolveStaticFS()
	production := strings.EqualFold(strings.TrimSpace(os.Getenv("SUNNY_ENV")), "production")
	adminUser := fallback(strings.TrimSpace(os.Getenv("ADMIN_USERNAME")), "admin")
	adminPass := ensureAdminPassword()
	if production {
		validateProductionSecrets(adminPass)
	}
	secureCookies := production
	if raw := strings.TrimSpace(os.Getenv("SUNNY_SECURE_COOKIES")); raw != "" {
		secureCookies = raw == "1" || strings.EqualFold(raw, "true")
	}
	s := &Server{
		db: db, adminUser: adminUser, adminPass: adminPass, staticFS: staticFS,
		wake: make(chan struct{}, 1), stop: make(chan struct{}), running: map[string]bool{},
		sessions: map[string]time.Time{}, loginFailures: map[string]*loginFailure{},
		checkoutCreds: map[string]checkoutSecret{},
		sessionTTL:    12 * time.Hour, secureCookies: secureCookies, production: production,
	}
	s.maintenance = s.loadSunnyMaintenanceConfig()
	s.recordAudit(AuditLog{LogType: "system", Category: "system", Action: "startup", Status: "success", Summary: "SunnyRegister 后端服务启动", DetailsJSON: dumpJSON(map[string]any{"environment": fallback(os.Getenv("SUNNY_ENV"), "development"), "timezone": time.Local.String()})})
	go s.sunnyWarmSMSProviderOptions()
	go s.sunnyAccountHealthScheduleLoop()
	go s.auditMaintenanceLoop()
	log.Printf("admin login enabled: username=%s password_file=%s", adminUser, adminPasswordFile())
	go s.runtimeLoop()
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.serveHTTP)
	addr := ":" + fallback(os.Getenv("PORT"), "8000")
	log.Printf("SunnyRegister Go backend listening on %s", addr)
	httpServer := &http.Server{
		Addr: addr, Handler: s.gzipResponses(s.securityHeaders(s.auditMiddleware(mux))),
		ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 30 * time.Second,
		WriteTimeout: 5 * time.Minute, IdleTimeout: 60 * time.Second,
	}
	errCh := make(chan error, 1)
	go func() {
		errCh <- httpServer.ListenAndServe()
	}()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	select {
	case sig := <-sigCh:
		log.Printf("shutdown requested: %s", sig)
		s.recordAudit(AuditLog{LogType: "system", Category: "system", Action: "shutdown", Status: "success", Summary: "SunnyRegister 后端服务停止", DetailsJSON: dumpJSON(map[string]any{"signal": sig.String()})})
		close(s.stop)
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := httpServer.Shutdown(ctx); err != nil {
			log.Printf("http shutdown failed: %v", err)
		}
		log.Printf("SunnyRegister Go backend stopped")
	case err := <-errCh:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatal(err)
		}
	}
}

func loadDotEnv(filename string) {
	data, err := os.ReadFile(filename)
	if err != nil {
		return
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		k, v, _ := strings.Cut(line, "=")
		k = strings.TrimSpace(k)
		v = strings.Trim(strings.TrimSpace(v), `"'`)
		if k != "" && os.Getenv(k) == "" {
			_ = os.Setenv(k, v)
		}
	}
}

func resolveStaticFS() http.FileSystem {
	for _, dir := range []string{"./static", "../static", "./frontend/dist", "../frontend/dist"} {
		if st, err := os.Stat(dir); err == nil && st.IsDir() {
			return http.Dir(dir)
		}
	}
	sub, err := fs.Sub(embeddedStatic, "static")
	if err == nil {
		return http.FS(sub)
	}
	return http.Dir(".")
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/api/") {
		w.Header().Set("Cache-Control", "no-store")
		if isMutation(r.Method) && !s.validRequestOrigin(r) {
			writeError(w, http.StatusForbidden, "Invalid request origin")
			return
		}
		if !s.authorized(r) {
			writeError(w, 401, "Unauthorized")
			return
		}
		s.routeAPI(w, r)
		return
	}
	s.serveStatic(w, r)
}

func (s *Server) authorized(r *http.Request) bool {
	p := r.URL.Path
	if strings.HasPrefix(p, "/api/auth/") || p == "/api/health" || p == "/api/ready" {
		return true
	}
	return s.hasValidSession(r)
}

func (s *Server) hasValidSession(r *http.Request) bool {
	token := ""
	if c, err := r.Cookie(s.sessionCookieName()); err == nil {
		token = strings.TrimSpace(c.Value)
	}
	if token == "" {
		auth := r.Header.Get("Authorization")
		if strings.HasPrefix(auth, "Bearer ") {
			token = strings.TrimSpace(strings.TrimPrefix(auth, "Bearer "))
		}
	}
	if token == "" {
		return false
	}
	now := time.Now()
	s.sessionMu.Lock()
	defer s.sessionMu.Unlock()
	expires, ok := s.sessions[token]
	if !ok || !expires.After(now) {
		delete(s.sessions, token)
		return false
	}
	return true
}

func (s *Server) newSession() string {
	token := randomID("session")
	now := time.Now()
	s.sessionMu.Lock()
	for key, expires := range s.sessions {
		if !expires.After(now) {
			delete(s.sessions, key)
		}
	}
	s.sessions[token] = now.Add(s.sessionTTL)
	s.sessionMu.Unlock()
	return token
}

func (s *Server) deleteSession(token string) {
	s.sessionMu.Lock()
	delete(s.sessions, token)
	s.sessionMu.Unlock()
}

func (s *Server) setSessionCookie(w http.ResponseWriter, token string, maxAge int) {
	http.SetCookie(w, &http.Cookie{
		Name: s.sessionCookieName(), Value: token, Path: "/", MaxAge: maxAge,
		HttpOnly: true, Secure: s.secureCookies, SameSite: http.SameSiteStrictMode,
	})
}

func (s *Server) sessionCookieName() string {
	if s.secureCookies {
		return "__Host-sunny_session"
	}
	return "sunny_session"
}

func constantTimeEqual(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

func (s *Server) loginClientKey(r *http.Request) string {
	if raw := strings.TrimSpace(os.Getenv("SUNNY_TRUST_PROXY_HEADERS")); raw == "1" || strings.EqualFold(raw, "true") {
		if forwarded := strings.TrimSpace(strings.Split(r.Header.Get("X-Forwarded-For"), ",")[0]); net.ParseIP(forwarded) != nil {
			return forwarded
		}
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil && host != "" {
		return host
	}
	return r.RemoteAddr
}

func (s *Server) loginBlocked(key string) (bool, time.Duration) {
	now := time.Now()
	s.loginMu.Lock()
	defer s.loginMu.Unlock()
	entry := s.loginFailures[key]
	if entry == nil || !entry.BlockedTill.After(now) {
		return false, 0
	}
	return true, time.Until(entry.BlockedTill)
}

func (s *Server) recordLoginFailure(key string) {
	now := time.Now()
	s.loginMu.Lock()
	defer s.loginMu.Unlock()
	entry := s.loginFailures[key]
	if entry == nil || now.Sub(entry.WindowStart) > 15*time.Minute {
		entry = &loginFailure{WindowStart: now}
		s.loginFailures[key] = entry
	}
	entry.Count++
	if entry.Count >= 5 {
		entry.BlockedTill = now.Add(15 * time.Minute)
	}
}

func (s *Server) clearLoginFailures(key string) {
	s.loginMu.Lock()
	delete(s.loginFailures, key)
	s.loginMu.Unlock()
}

func isMutation(method string) bool {
	return method != http.MethodGet && method != http.MethodHead && method != http.MethodOptions
}

func (s *Server) validRequestOrigin(r *http.Request) bool {
	origin := strings.TrimSpace(r.Header.Get("Origin"))
	if origin == "" {
		return true
	}
	u, err := url.Parse(origin)
	if err != nil || u.Host == "" {
		return false
	}
	if strings.EqualFold(u.Host, r.Host) {
		return true
	}
	publicOrigin := strings.TrimRight(strings.TrimSpace(os.Getenv("SUNNY_PUBLIC_ORIGIN")), "/")
	return publicOrigin != "" && strings.EqualFold(strings.TrimRight(origin, "/"), publicOrigin)
}

func (s *Server) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
		if s.production {
			w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
			if s.secureCookies {
				w.Header().Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
			}
		}
		next.ServeHTTP(w, r)
	})
}

func adminPasswordFile() string {
	if p := strings.TrimSpace(os.Getenv("ADMIN_PASSWORD_FILE")); p != "" {
		return p
	}
	return filepath.Join("data", "admin_password.txt")
}

func ensureAdminPassword() string {
	if v := strings.TrimSpace(os.Getenv("ADMIN_PASSWORD")); v != "" {
		return v
	}
	if v := strings.TrimSpace(os.Getenv("APP_PASSWORD")); v != "" {
		return v
	}
	file := adminPasswordFile()
	if b, err := os.ReadFile(file); err == nil && strings.TrimSpace(string(b)) != "" {
		return strings.TrimSpace(string(b))
	}
	pass := randomID("admin")
	_ = ensureDir(file)
	_ = os.WriteFile(file, []byte(pass+"\n"), 0600)
	return pass
}

func secretValue(envKey, fileKey string) string {
	if file := strings.TrimSpace(os.Getenv(fileKey)); file != "" {
		if data, err := os.ReadFile(file); err == nil {
			return strings.TrimSpace(string(data))
		}
	}
	return strings.TrimSpace(os.Getenv(envKey))
}

func validateProductionSecrets(adminPass string) {
	lower := strings.ToLower(strings.TrimSpace(adminPass))
	if len(adminPass) < 16 || strings.Contains(lower, "change-me") || strings.Contains(lower, "password") {
		log.Fatal("production startup refused: ADMIN_PASSWORD must be a non-placeholder secret of at least 16 characters")
	}
	workerToken := secretValue("PYTHON_WORKER_TOKEN", "PYTHON_WORKER_TOKEN_FILE")
	if len(workerToken) < 32 || strings.Contains(strings.ToLower(workerToken), "change-me") {
		log.Fatal("production startup refused: PYTHON_WORKER_TOKEN must contain at least 32 random characters")
	}
}

func (s *Server) routeAPI(w http.ResponseWriter, r *http.Request) {
	p := strings.TrimPrefix(r.URL.Path, "/api")
	switch {
	case p == "/health" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"ok": true, "time": formatTime(time.Now())})
	case p == "/ready" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"ok": true})
	case strings.HasPrefix(p, "/auth"):
		s.handleAuth(w, r, strings.TrimPrefix(p, "/auth"))
	case strings.HasPrefix(p, "/accounts"):
		s.handleAccounts(w, r, strings.TrimPrefix(p, "/accounts"))
	case strings.HasPrefix(p, "/tasks"):
		s.handleTasks(w, r, strings.TrimPrefix(p, "/tasks"))
	case strings.HasPrefix(p, "/config"):
		s.handleConfig(w, r, strings.TrimPrefix(p, "/config"))
	case strings.HasPrefix(p, "/provider-definitions"):
		s.handleProviderDefinitions(w, r, strings.TrimPrefix(p, "/provider-definitions"))
	case strings.HasPrefix(p, "/provider-settings"):
		s.handleProviderSettings(w, r, strings.TrimPrefix(p, "/provider-settings"))
	case strings.HasPrefix(p, "/platforms"):
		s.handlePlatforms(w, r, strings.TrimPrefix(p, "/platforms"))
	case strings.HasPrefix(p, "/proxies"):
		s.handleProxies(w, r, strings.TrimPrefix(p, "/proxies"))
	case strings.HasPrefix(p, "/stats"):
		s.handleStats(w, r, strings.TrimPrefix(p, "/stats"))
	case strings.HasPrefix(p, "/sms-pool"):
		s.handleSmsPool(w, r, strings.TrimPrefix(p, "/sms-pool"))
	case strings.HasPrefix(p, "/sms"):
		s.handleSms(w, r, strings.TrimPrefix(p, "/sms"))
	case strings.HasPrefix(p, "/bitbrowser/profiles"):
		s.handleBitBrowserProfiles(w, r, strings.TrimPrefix(p, "/bitbrowser/profiles"))
	case strings.HasPrefix(p, "/system"):
		s.handleSystem(w, r, strings.TrimPrefix(p, "/system"))
	case p == "/solver/status":
		writeJSON(w, 200, map[string]any{"running": false, "status": "disabled"})
	case p == "/solver/restart" && r.Method == http.MethodPost:
		writeJSON(w, 200, map[string]any{"ok": true, "message": "solver restart accepted"})
	case strings.HasPrefix(p, "/integrations"):
		s.handleIntegrations(w, r, strings.TrimPrefix(p, "/integrations"))
	case strings.HasPrefix(p, "/sunny"):
		s.handleSunny(w, r, strings.TrimPrefix(p, "/sunny"))
	case strings.HasPrefix(p, "/actions"):
		s.handleActions(w, r, strings.TrimPrefix(p, "/actions"))
	case strings.HasPrefix(p, "/audit"):
		s.handleAudit(w, r, strings.TrimPrefix(p, "/audit"))
	default:
		writeError(w, 404, "not found")
	}
}

func (s *Server) serveStatic(w http.ResponseWriter, r *http.Request) {
	clean := path.Clean(strings.TrimPrefix(r.URL.Path, "/"))
	if clean == "." {
		clean = "index.html"
	}
	if f, err := s.staticFS.Open(clean); err == nil {
		_ = f.Close()
		if clean == "index.html" {
			w.Header().Set("Cache-Control", "no-cache")
		} else {
			w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
		}
		http.FileServer(s.staticFS).ServeHTTP(w, r)
		return
	}
	r2 := *r
	r2.URL.Path = "/index.html"
	w.Header().Set("Cache-Control", "no-cache")
	http.FileServer(s.staticFS).ServeHTTP(w, &r2)
}

type gzipResponseWriter struct {
	http.ResponseWriter
	gzipWriter  *gzip.Writer
	wroteHeader bool
}

func (w *gzipResponseWriter) WriteHeader(status int) {
	if w.wroteHeader {
		return
	}
	w.wroteHeader = true
	w.Header().Del("Content-Length")
	w.Header().Set("Content-Encoding", "gzip")
	w.Header().Add("Vary", "Accept-Encoding")
	w.ResponseWriter.WriteHeader(status)
}

func (w *gzipResponseWriter) Write(data []byte) (int, error) {
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	return w.gzipWriter.Write(data)
}

func (w *gzipResponseWriter) Flush() {
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	_ = w.gzipWriter.Flush()
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (s *Server) gzipResponses(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		acceptsGzip := strings.Contains(strings.ToLower(r.Header.Get("Accept-Encoding")), "gzip")
		excluded := r.Method == http.MethodHead || strings.Contains(r.URL.Path, "/export") || r.Header.Get("Range") != ""
		if !acceptsGzip || excluded {
			next.ServeHTTP(w, r)
			return
		}
		gz := gzip.NewWriter(w)
		wrapped := &gzipResponseWriter{ResponseWriter: w, gzipWriter: gz}
		defer gz.Close()
		next.ServeHTTP(wrapped, r)
	})
}
