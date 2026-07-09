package main

import (
	"context"
	"embed"
	"errors"
	"io/fs"
	"log"
	"net/http"
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
	db        *gorm.DB
	adminUser string
	adminPass string
	authToken string
	staticFS  http.FileSystem
	wake      chan struct{}
	stop      chan struct{}
	running   map[string]bool
	runtimeMu sync.Mutex
}

func main() {
	loadDotEnv(".env")
	loadDotEnv("../.env")
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
	seedProviderDefinitions(db)
	markInterrupted(db)
	staticFS := resolveStaticFS()
	adminUser := fallback(strings.TrimSpace(os.Getenv("ADMIN_USERNAME")), "admin")
	adminPass := ensureAdminPassword()
	s := &Server{
		db: db, adminUser: adminUser, adminPass: adminPass, authToken: randomID("auth"), staticFS: staticFS,
		wake: make(chan struct{}, 1), stop: make(chan struct{}), running: map[string]bool{},
	}
	go s.sunnyWarmSMSProviderOptions()
	log.Printf("admin login enabled: username=%s password_file=%s", adminUser, adminPasswordFile())
	go s.runtimeLoop()
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.serveHTTP)
	addr := ":" + fallback(os.Getenv("PORT"), "8000")
	log.Printf("SunnyRegister Go backend listening on %s", addr)
	httpServer := &http.Server{Addr: addr, Handler: mux}
	errCh := make(chan error, 1)
	go func() {
		errCh <- httpServer.ListenAndServe()
	}()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	select {
	case sig := <-sigCh:
		log.Printf("shutdown requested: %s", sig)
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
	auth := r.Header.Get("Authorization")
	if strings.HasPrefix(auth, "Bearer ") && strings.TrimPrefix(auth, "Bearer ") == s.authToken {
		return true
	}
	if c, err := r.Cookie("_auth"); err == nil && c.Value == s.authToken {
		return true
	}
	return false
}

func adminPasswordFile() string {
	if p := strings.TrimSpace(os.Getenv("ADMIN_PASSWORD_FILE")); p != "" {
		return p
	}
	dbPath := normalizeDatabasePath(os.Getenv("ACCOUNT_MANAGER_DATABASE_URL"))
	if dbPath != "" {
		return filepath.Join(filepath.Dir(dbPath), "admin_password.txt")
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

func (s *Server) routeAPI(w http.ResponseWriter, r *http.Request) {
	p := strings.TrimPrefix(r.URL.Path, "/api")
	switch {
	case p == "/health" && r.Method == http.MethodGet:
		writeJSON(w, 200, map[string]any{"ok": true, "time": time.Now().Format(time.RFC3339)})
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
		http.FileServer(s.staticFS).ServeHTTP(w, r)
		return
	}
	r2 := *r
	r2.URL.Path = "/index.html"
	http.FileServer(s.staticFS).ServeHTTP(w, &r2)
}
