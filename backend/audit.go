package main

import (
	"archive/zip"
	"bytes"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"gorm.io/gorm"
)

const auditExportZipThreshold = int64(2 * 1024 * 1024)

type auditResponseWriter struct {
	http.ResponseWriter
	status int
}

func (w *auditResponseWriter) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *auditResponseWriter) Write(data []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(data)
}

func (w *auditResponseWriter) Flush() {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (s *Server) auditMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/") {
			if r.Method == http.MethodGet && (r.URL.Path == "/" || strings.Contains(strings.ToLower(r.Header.Get("Accept")), "text/html")) {
				s.auditPageAccess(next, w, r)
				return
			}
			next.ServeHTTP(w, r)
			return
		}
		if !isMutation(r.Method) {
			next.ServeHTTP(w, r)
			return
		}
		started := time.Now()
		requestID := fallback(strings.TrimSpace(r.Header.Get("X-Request-ID")), randomID("req"))
		w.Header().Set("X-Request-ID", requestID)
		actor := "Anonymous"
		actorType := "anonymous"
		if s.hasValidSession(r) {
			actor, actorType = s.adminUser, "user"
		}
		body := s.readAuditRequestBody(r)
		if strings.HasSuffix(r.URL.Path, "/auth/login") {
			if username := firstText(body["username"], body["user"]); username != "" {
				actor = username
			}
		}
		wrapped := &auditResponseWriter{ResponseWriter: w}
		defer func() {
			if recovered := recover(); recovered != nil {
				s.recordAudit(AuditLog{
					OccurredAt: started, ActorType: actorType, Actor: actor, IP: s.loginClientKey(r),
					LogType: "system", Category: "system", Action: "panic", Level: "error", Status: "failed",
					Source: "go-backend", Method: r.Method, Path: r.URL.Path, RequestID: requestID,
					Summary: "系统处理请求时发生异常", DetailsJSON: dumpJSON(map[string]any{"panic": sanitizePersistedString(fmt.Sprint(recovered))}),
					HTTPStatus: http.StatusInternalServerError, DurationMS: time.Since(started).Milliseconds(),
				})
				if wrapped.status == 0 {
					writeError(wrapped, http.StatusInternalServerError, "Internal server error")
				}
				return
			}
			status := wrapped.status
			if status == 0 {
				status = http.StatusOK
			}
			meta := auditMetaForRequest(r, body, status)
			if strings.HasSuffix(r.URL.Path, "/auth/login") && status < 400 {
				actorType = "user"
			}
			s.recordAudit(AuditLog{
				OccurredAt: started, ActorType: actorType, Actor: actor, IP: s.loginClientKey(r),
				UserAgent: truncateAuditText(r.UserAgent(), 512), LogType: meta.LogType, Category: meta.Category,
				Action: meta.Action, Level: meta.Level, Status: meta.Status, Source: "http-api", Method: r.Method,
				Path: truncateAuditText(r.URL.Path, 512), RequestID: requestID, TaskID: meta.TaskID,
				EntityType: meta.EntityType, EntityID: meta.EntityID, EntityName: meta.EntityName,
				Summary: meta.Summary, DetailsJSON: dumpJSON(auditSanitizeBody(body)), HTTPStatus: status,
				DurationMS: time.Since(started).Milliseconds(), Count: meta.Count,
			})
		}()
		next.ServeHTTP(wrapped, r)
	})
}

func (s *Server) auditPageAccess(next http.Handler, w http.ResponseWriter, r *http.Request) {
	started := time.Now()
	wrapped := &auditResponseWriter{ResponseWriter: w}
	next.ServeHTTP(wrapped, r)
	status := wrapped.status
	if status == 0 {
		status = http.StatusOK
	}
	actor, actorType := "Visitor", "anonymous"
	if s.hasValidSession(r) {
		actor, actorType = s.adminUser, "user"
	}
	result, level := "success", "info"
	if status >= 400 {
		result, level = "failed", "warning"
	}
	s.recordAudit(AuditLog{OccurredAt: started, Actor: actor, ActorType: actorType, IP: s.loginClientKey(r), UserAgent: r.UserAgent(), LogType: "access", Category: "system", Action: "page_access", Level: level, Status: result, Source: "web", Method: r.Method, Path: r.URL.Path, HTTPStatus: status, DurationMS: time.Since(started).Milliseconds(), Summary: "访问 SunnyRegister 页面"})
}

func (s *Server) readAuditRequestBody(r *http.Request) map[string]any {
	if r.Body == nil || strings.Contains(strings.ToLower(r.Header.Get("Content-Type")), "multipart/") {
		return map[string]any{}
	}
	const maxAuditBody = 4 * 1024 * 1024
	if r.ContentLength > maxAuditBody {
		return map[string]any{}
	}
	data, err := io.ReadAll(io.LimitReader(r.Body, maxAuditBody+1))
	if err != nil {
		return map[string]any{}
	}
	if len(data) > maxAuditBody {
		r.Body = io.NopCloser(io.MultiReader(bytes.NewReader(data), r.Body))
		return map[string]any{}
	}
	r.Body = io.NopCloser(bytes.NewReader(data))
	var body map[string]any
	if json.Unmarshal(data, &body) != nil || body == nil {
		return map[string]any{}
	}
	return body
}

type auditRequestMeta struct {
	LogType, Category, Action, Level, Status, TaskID string
	EntityType, EntityID, EntityName, Summary        string
	Count                                            int
}

func auditMetaForRequest(r *http.Request, body map[string]any, status int) auditRequestMeta {
	path := strings.ToLower(r.URL.Path)
	meta := auditRequestMeta{LogType: "operation", Category: "system", Action: auditAction(path, r.Method), Level: "info", Status: "success"}
	if status >= 400 {
		meta.Status, meta.Level = "failed", "error"
	}
	switch {
	case strings.Contains(path, "/auth/"):
		meta.LogType, meta.Category, meta.EntityType = "security", "authentication", "session"
	case strings.Contains(path, "mailbox-groups"):
		meta.Category, meta.EntityType = "mailbox", "mailbox_group"
	case strings.Contains(path, "/mailboxes"):
		meta.Category, meta.EntityType = "mailbox", "mailbox"
	case strings.Contains(path, "/phones") || strings.Contains(path, "/sms"):
		meta.Category, meta.EntityType = "sms", "phone_or_provider"
	case strings.Contains(path, "sub2api") || strings.Contains(path, "/integrations"):
		meta.Category, meta.EntityType = "reverse_proxy", "reverse_proxy_config"
	case strings.Contains(path, "/proxies") || strings.Contains(path, "proxy-config"):
		meta.Category, meta.EntityType = "proxy", "proxy"
	case strings.Contains(path, "/sessions") || strings.Contains(path, "/health"):
		meta.Category, meta.EntityType = "account", "account"
	case strings.Contains(path, "/tasks"):
		meta.LogType, meta.Category, meta.EntityType = "task", "registration_task", "task"
	case strings.Contains(path, "/audit"):
		meta.Category, meta.EntityType = "audit", "audit_log"
	case strings.Contains(path, "/config") || strings.Contains(path, "/provider-settings"):
		meta.Category, meta.EntityType = "configuration", "configuration"
	}
	meta.EntityID = auditPathID(r.URL.Path)
	meta.EntityName = truncateAuditText(firstText(body["email"], body["number"], body["name"], body["display_name"], body["provider_key"]), 512)
	meta.TaskID = firstText(body["task_id"], body["id"])
	meta.Count = auditBodyCount(body)
	meta.Summary = auditSummary(meta.Category, meta.Action, meta.Status, meta.Count, meta.EntityName)
	return meta
}

func auditAction(path, method string) string {
	for marker, action := range map[string]string{
		"/login": "login", "/logout": "logout", "/import": "import", "/export": "export",
		"/health": "health_check", "/check": "check", "/refresh": "refresh", "/cancel": "cancel",
		"/batch": "batch_update", "/toggle": "toggle", "/test": "test", "/restart": "restart",
	} {
		if strings.Contains(path, marker) {
			return action
		}
	}
	switch method {
	case http.MethodPost:
		return "create"
	case http.MethodPut, http.MethodPatch:
		return "update"
	case http.MethodDelete:
		return "delete"
	default:
		return strings.ToLower(method)
	}
}

func auditSummary(category, action, status string, count int, entity string) string {
	categoryLabels := map[string]string{"authentication": "系统认证", "mailbox": "邮箱配置", "sms": "接码配置", "reverse_proxy": "反代配置", "proxy": "代理配置", "account": "账户管理", "registration_task": "注册任务", "audit": "日志管理", "configuration": "系统配置", "system": "系统"}
	actionLabels := map[string]string{"login": "登录", "logout": "退出登录", "create": "新增", "update": "修改", "delete": "删除", "import": "导入", "export": "导出", "health_check": "测活", "check": "检测", "refresh": "刷新", "cancel": "取消", "batch_update": "批量修改", "toggle": "启用状态切换", "test": "测试", "restart": "重启"}
	result := "成功"
	if status != "success" {
		result = "失败"
	}
	summary := fallback(categoryLabels[category], category) + "：" + fallback(actionLabels[action], action) + result
	if count > 0 {
		summary += fmt.Sprintf("，数量 %d", count)
	}
	if entity != "" {
		summary += "，对象 " + entity
	}
	return summary
}

func auditPathID(path string) string {
	parts := strings.Split(strings.Trim(path, "/"), "/")
	for i := len(parts) - 1; i >= 0; i-- {
		if _, err := strconv.ParseUint(parts[i], 10, 64); err == nil {
			return parts[i]
		}
	}
	return ""
}

func auditBodyCount(body map[string]any) int {
	for _, key := range []string{"mailbox_ids", "account_ids", "session_ids", "proxy_ids", "phone_ids", "ids", "selected_ids"} {
		if values, ok := body[key].([]any); ok {
			return len(values)
		}
	}
	if lines := text(body["lines"]); lines != "" {
		count := 0
		for _, line := range strings.Split(lines, "\n") {
			if strings.TrimSpace(line) != "" {
				count++
			}
		}
		return count
	}
	return intValue(body["count"], intValue(body["total"], 0))
}

func auditSanitizeBody(body map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range body {
		normalized := strings.ToLower(strings.TrimSpace(key))
		if normalized == "lines" {
			lines := strings.Split(text(value), "\n")
			suffixes := map[string]bool{}
			count := 0
			for _, line := range lines {
				line = strings.TrimSpace(line)
				if line == "" {
					continue
				}
				count++
				first := strings.Split(line, "----")[0]
				if at := strings.LastIndex(first, "@"); at >= 0 {
					suffixes[strings.ToLower(first[at+1:])] = true
				}
			}
			list := make([]string, 0, len(suffixes))
			for suffix := range suffixes {
				list = append(list, suffix)
			}
			out["lines_count"], out["email_suffixes"] = count, list
			continue
		}
		if normalized == "raw" || normalized == "content" || normalized == "session_json" || normalized == "storage_state_json" {
			out[key] = "[OMITTED]"
			continue
		}
		out[key] = sanitizePersistedValue(value, key)
	}
	return out
}

func truncateAuditText(value string, limit int) string {
	value = strings.TrimSpace(value)
	if len(value) <= limit {
		return value
	}
	return value[:limit]
}

func (s *Server) recordAudit(item AuditLog) {
	if item.OccurredAt.IsZero() {
		item.OccurredAt = time.Now()
	}
	item.DetailsJSON = sanitizePersistedJSON(item.DetailsJSON)
	item.Summary = sanitizePersistedString(item.Summary)
	item.UserAgent = truncateAuditText(item.UserAgent, 512)
	if item.Actor == "" {
		item.Actor = "System"
	}
	if item.ActorType == "" {
		item.ActorType = "system"
	}
	if item.Source == "" {
		item.Source = "go-backend"
	}
	if item.Status == "" {
		item.Status = "success"
	}
	if item.Level == "" {
		item.Level = "info"
	}
	if item.DedupeKey != "" {
		var count int64
		s.db.Model(&AuditLog{}).Where("dedupe_key = ?", item.DedupeKey).Count(&count)
		if count > 0 {
			return
		}
	}
	if err := s.db.Create(&item).Error; err != nil {
		log.Printf("write audit log failed: %v", err)
	}
}

func serializeAuditLog(item AuditLog) map[string]any {
	return map[string]any{
		"id": item.ID, "occurred_at": formatTime(item.OccurredAt), "actor_type": item.ActorType, "actor": item.Actor,
		"ip": item.IP, "user_agent": item.UserAgent, "log_type": item.LogType, "category": item.Category,
		"action": item.Action, "level": item.Level, "status": item.Status, "source": item.Source,
		"method": item.Method, "path": item.Path, "request_id": item.RequestID, "task_id": item.TaskID,
		"entity_type": item.EntityType, "entity_id": item.EntityID, "entity_name": item.EntityName,
		"summary": item.Summary, "details": jsonMap(item.DetailsJSON), "http_status": item.HTTPStatus,
		"duration_ms": item.DurationMS, "count": item.Count,
	}
}

func (s *Server) handleAudit(w http.ResponseWriter, r *http.Request, rest string) {
	switch {
	case rest == "/logs" && r.Method == http.MethodGet:
		s.handleAuditList(w, r)
	case rest == "/options" && r.Method == http.MethodGet:
		s.handleAuditOptions(w)
	case rest == "/stats" && r.Method == http.MethodGet:
		s.handleAuditStats(w)
	case rest == "/settings" && r.Method == http.MethodGet:
		writeJSON(w, 200, s.auditSettings())
	case rest == "/settings" && r.Method == http.MethodPut:
		s.updateAuditSettings(w, r)
	case rest == "/logs" && r.Method == http.MethodDelete:
		s.deleteAuditLogs(w, r)
	case rest == "/exports" && r.Method == http.MethodPost:
		s.createAuditExport(w, r)
	case strings.HasPrefix(rest, "/exports/"):
		s.handleAuditExport(w, r, strings.TrimPrefix(rest, "/exports/"))
	default:
		writeError(w, http.StatusNotFound, "audit endpoint not found")
	}
}

func auditQueryValues(r *http.Request) map[string]string {
	values := map[string]string{}
	for _, key := range []string{"search", "log_type", "category", "action", "actor", "ip", "level", "status", "source", "entity_type", "task_id", "request_id", "date_from", "date_to"} {
		values[key] = strings.TrimSpace(r.URL.Query().Get(key))
	}
	return values
}

func applyAuditFilters(query *gorm.DB, filters map[string]string, ids []uint) *gorm.DB {
	if len(ids) > 0 {
		query = query.Where("id IN ?", ids)
	}
	if search := filters["search"]; search != "" {
		like := "%" + search + "%"
		query = query.Where("summary LIKE ? OR entity_name LIKE ? OR details_json LIKE ? OR path LIKE ? OR task_id LIKE ? OR request_id LIKE ?", like, like, like, like, like, like)
	}
	for _, key := range []string{"log_type", "category", "action", "actor", "ip", "level", "status", "source", "entity_type", "task_id", "request_id"} {
		if value := filters[key]; value != "" {
			query = query.Where(key+" = ?", value)
		}
	}
	if value := filters["date_from"]; value != "" {
		if parsed, ok := parseAuditTime(value, false); ok {
			query = query.Where("occurred_at >= ?", parsed)
		}
	}
	if value := filters["date_to"]; value != "" {
		if parsed, ok := parseAuditTime(value, true); ok {
			query = query.Where("occurred_at <= ?", parsed)
		}
	}
	return query
}

func parseAuditTime(value string, endOfDay bool) (time.Time, bool) {
	for _, layout := range []string{time.RFC3339, "2006-01-02T15:04", "2006-01-02 15:04:05", "2006-01-02"} {
		if parsed, err := time.ParseInLocation(layout, value, time.Local); err == nil {
			if layout == "2006-01-02" && endOfDay {
				parsed = parsed.Add(24*time.Hour - time.Nanosecond)
			}
			return parsed, true
		}
	}
	return time.Time{}, false
}

func (s *Server) handleAuditList(w http.ResponseWriter, r *http.Request) {
	page, size := max(1, intValue(r.URL.Query().Get("page"), 1)), intValue(r.URL.Query().Get("page_size"), 20)
	if size < 10 {
		size = 10
	}
	if size > 100 {
		size = 100
	}
	query := applyAuditFilters(s.db.Model(&AuditLog{}), auditQueryValues(r), nil)
	var total int64
	query.Count(&total)
	order := "occurred_at DESC, id DESC"
	if strings.EqualFold(r.URL.Query().Get("sort_order"), "asc") {
		order = "occurred_at ASC, id ASC"
	}
	var rows []AuditLog
	query.Order(order).Offset((page - 1) * size).Limit(size).Find(&rows)
	items := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		items = append(items, serializeAuditLog(row))
	}
	writeJSON(w, 200, map[string]any{"items": items, "total": total, "page": page, "page_size": size})
}

func (s *Server) handleAuditOptions(w http.ResponseWriter) {
	result := map[string]any{}
	for _, key := range []string{"log_type", "category", "action", "actor", "ip", "level", "status", "source", "entity_type"} {
		var values []string
		s.db.Model(&AuditLog{}).Distinct(key).Where(key+" <> ''").Order(key).Pluck(key, &values)
		result[key] = values
	}
	writeJSON(w, 200, result)
}

func (s *Server) handleAuditStats(w http.ResponseWriter) {
	now := time.Now()
	start := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.Local)
	var total, today, failed, system int64
	s.db.Model(&AuditLog{}).Count(&total)
	s.db.Model(&AuditLog{}).Where("occurred_at >= ?", start).Count(&today)
	s.db.Model(&AuditLog{}).Where("status = ? OR level = ?", "failed", "error").Count(&failed)
	s.db.Model(&AuditLog{}).Where("actor_type = ?", "system").Count(&system)
	writeJSON(w, 200, map[string]any{"total": total, "today": today, "failed": failed, "system": system})
}

func (s *Server) auditSettings() AuditSetting {
	setting := AuditSetting{ID: 1, RetentionDays: 7, CleanupHour: 3, Enabled: true}
	if err := s.db.First(&setting, 1).Error; err != nil {
		s.db.Create(&setting)
	}
	return setting
}

func (s *Server) updateAuditSettings(w http.ResponseWriter, r *http.Request) {
	body, _ := parseBody(r)
	days := intValue(body["retention_days"], 7)
	allowed := map[int]bool{1: true, 3: true, 7: true, 14: true, 30: true}
	if !allowed[days] {
		writeError(w, http.StatusBadRequest, "retention_days must be one of 1,3,7,14,30")
		return
	}
	setting := s.auditSettings()
	setting.RetentionDays = days
	if value, ok := body["enabled"]; ok {
		setting.Enabled = boolValue(value, true)
	}
	s.db.Save(&setting)
	writeJSON(w, 200, setting)
}

func uintsFromAny(value any) []uint {
	items, _ := value.([]any)
	result := make([]uint, 0, len(items))
	for _, item := range items {
		if id := uint(intValue(item, 0)); id > 0 {
			result = append(result, id)
		}
	}
	return result
}

func stringMapFromAny(value any) map[string]string {
	result := map[string]string{}
	for key, item := range mapFromAny(value) {
		result[key] = text(item)
	}
	return result
}

func (s *Server) deleteAuditLogs(w http.ResponseWriter, r *http.Request) {
	body, _ := parseBody(r)
	ids := uintsFromAny(body["ids"])
	filters := stringMapFromAny(body["filters"])
	query := applyAuditFilters(s.db.Model(&AuditLog{}), filters, ids)
	if len(ids) == 0 && len(filters) == 0 {
		writeError(w, http.StatusBadRequest, "select logs or provide filters")
		return
	}
	result := query.Delete(&AuditLog{})
	writeJSON(w, 200, map[string]any{"ok": result.Error == nil, "deleted": result.RowsAffected})
}

func (s *Server) createAuditExport(w http.ResponseWriter, r *http.Request) {
	body, _ := parseBody(r)
	format := strings.ToLower(fallback(text(body["format"]), "csv"))
	if format != "csv" && format != "txt" {
		writeError(w, http.StatusBadRequest, "format must be csv or txt")
		return
	}
	job := AuditExportJob{ID: randomID("audit_export"), Status: "queued", Format: format, FiltersJSON: dumpJSON(stringMapFromAny(body["filters"])), SelectedJSON: dumpJSON(uintsFromAny(body["ids"])), Actor: s.adminUser}
	if err := s.db.Create(&job).Error; err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	go s.runAuditExport(job.ID)
	writeJSON(w, http.StatusAccepted, job)
}

func (s *Server) handleAuditExport(w http.ResponseWriter, r *http.Request, rest string) {
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "export job not found")
		return
	}
	var job AuditExportJob
	if err := s.db.First(&job, "id = ?", parts[0]).Error; err != nil {
		writeError(w, http.StatusNotFound, "export job not found")
		return
	}
	if len(parts) == 1 && r.Method == http.MethodGet {
		writeJSON(w, 200, job)
		return
	}
	if len(parts) == 2 && parts[1] == "download" && r.Method == http.MethodGet {
		if job.Status != "completed" || job.FilePath == "" {
			writeError(w, http.StatusConflict, "export is not ready")
			return
		}
		file, err := os.Open(job.FilePath)
		if err != nil {
			writeError(w, http.StatusNotFound, "export file not found")
			return
		}
		defer file.Close()
		stat, _ := file.Stat()
		w.Header().Set("Content-Type", fallback(job.ContentType, "application/octet-stream"))
		w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, strings.ReplaceAll(job.FileName, `"`, "")))
		http.ServeContent(w, r, job.FileName, stat.ModTime(), file)
		return
	}
	writeError(w, http.StatusNotFound, "export endpoint not found")
}

func (s *Server) auditExportDir() string {
	dbPath := normalizeDatabasePath(os.Getenv("ACCOUNT_MANAGER_DATABASE_URL"))
	if dbPath == "" {
		dbPath = filepath.Join("data", "sunnyregister.db")
	}
	return filepath.Join(filepath.Dir(dbPath), "audit_exports")
}

func (s *Server) runAuditExport(jobID string) {
	var job AuditExportJob
	if s.db.First(&job, "id = ?", jobID).Error != nil {
		return
	}
	s.db.Model(&job).Updates(map[string]any{"status": "running", "updated_at": time.Now()})
	dir := s.auditExportDir()
	if err := os.MkdirAll(dir, 0750); err != nil {
		s.failAuditExport(&job, err)
		return
	}
	base := "audit_logs_" + time.Now().Format("20060102_150405")
	rawPath := filepath.Join(dir, base+"."+job.Format)
	file, err := os.Create(rawPath)
	if err != nil {
		s.failAuditExport(&job, err)
		return
	}
	filters := map[string]string{}
	_ = json.Unmarshal([]byte(job.FiltersJSON), &filters)
	ids := []uint{}
	_ = json.Unmarshal([]byte(job.SelectedJSON), &ids)
	query := applyAuditFilters(s.db.Model(&AuditLog{}), filters, ids).Order("occurred_at DESC, id DESC")
	rows, err := query.Rows()
	if err != nil {
		file.Close()
		s.failAuditExport(&job, err)
		return
	}
	headers := []string{"id", "time", "type", "category", "action", "level", "status", "actor", "ip", "source", "entity_type", "entity_id", "entity_name", "task_id", "request_id", "method", "path", "http_status", "duration_ms", "count", "summary", "details"}
	count := 0
	if job.Format == "csv" {
		writer := csv.NewWriter(file)
		_ = writer.Write(headers)
		for rows.Next() {
			var item AuditLog
			s.db.ScanRows(rows, &item)
			_ = writer.Write(auditExportRecord(item))
			count++
		}
		writer.Flush()
		err = writer.Error()
	} else {
		_, _ = file.WriteString(strings.Join(headers, "\t") + "\n")
		for rows.Next() {
			var item AuditLog
			s.db.ScanRows(rows, &item)
			_, err = file.WriteString(strings.Join(auditExportRecord(item), "\t") + "\n")
			if err != nil {
				break
			}
			count++
		}
	}
	rows.Close()
	file.Close()
	if err != nil {
		s.failAuditExport(&job, err)
		return
	}
	finalPath, finalName, contentType := rawPath, filepath.Base(rawPath), map[string]string{"csv": "text/csv; charset=utf-8", "txt": "text/plain; charset=utf-8"}[job.Format]
	if stat, statErr := os.Stat(rawPath); statErr == nil && stat.Size() >= auditExportZipThreshold {
		zipPath := filepath.Join(dir, base+".zip")
		if zipErr := zipAuditExport(rawPath, zipPath); zipErr == nil {
			_ = os.Remove(rawPath)
			finalPath, finalName, contentType = zipPath, filepath.Base(zipPath), "application/zip"
		}
	}
	stat, err := os.Stat(finalPath)
	if err != nil {
		s.failAuditExport(&job, err)
		return
	}
	now := time.Now()
	s.db.Model(&job).Updates(map[string]any{"status": "completed", "file_path": finalPath, "file_name": finalName, "content_type": contentType, "file_size": stat.Size(), "record_count": count, "completed_at": now, "updated_at": now})
}

func auditExportRecord(item AuditLog) []string {
	return []string{strconv.FormatUint(uint64(item.ID), 10), formatTime(item.OccurredAt), item.LogType, item.Category, item.Action, item.Level, item.Status, item.Actor, item.IP, item.Source, item.EntityType, item.EntityID, item.EntityName, item.TaskID, item.RequestID, item.Method, item.Path, strconv.Itoa(item.HTTPStatus), strconv.FormatInt(item.DurationMS, 10), strconv.Itoa(item.Count), item.Summary, item.DetailsJSON}
}

func zipAuditExport(source, destination string) error {
	out, err := os.Create(destination)
	if err != nil {
		return err
	}
	defer out.Close()
	archive := zip.NewWriter(out)
	defer archive.Close()
	entry, err := archive.Create(filepath.Base(source))
	if err != nil {
		return err
	}
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	_, err = io.Copy(entry, in)
	return err
}

func (s *Server) failAuditExport(job *AuditExportJob, err error) {
	now := time.Now()
	s.db.Model(job).Updates(map[string]any{"status": "failed", "error": sanitizePersistedString(err.Error()), "completed_at": now, "updated_at": now})
}

func (s *Server) auditMaintenanceLoop() {
	taskTicker := time.NewTicker(15 * time.Second)
	cleanupTicker := time.NewTicker(time.Hour)
	metricTicker := time.NewTicker(time.Hour)
	defer taskTicker.Stop()
	defer cleanupTicker.Stop()
	defer metricTicker.Stop()
	for {
		select {
		case <-s.stop:
			return
		case <-taskTicker.C:
			s.auditCompletedTasks()
		case <-cleanupTicker.C:
			s.auditRetentionCleanup()
		case <-metricTicker.C:
			s.auditRuntimeMetrics()
		}
	}
}

func (s *Server) auditCompletedTasks() {
	var tasks []Task
	s.db.Where("updated_at >= ?", time.Now().Add(-24*time.Hour)).Order("updated_at ASC").Limit(500).Find(&tasks)
	for _, task := range tasks {
		payload := jsonMap(task.PayloadJSON)
		actor, actorType, logType, category := "System", "system", "task", "registration_task"
		scheduled := boolValue(payload["scheduled"], false)
		if scheduled {
			logType, category = "scheduler", "scheduled_task"
		}
		if scheduled && !terminalTaskStatuses[task.Status] {
			s.recordAudit(AuditLog{
				OccurredAt: task.CreatedAt, Actor: actor, ActorType: actorType, LogType: logType, Category: category,
				Action: "task_started", Level: "info", Status: "running", Source: "task-runtime", TaskID: task.ID,
				EntityType: "task", EntityID: task.ID, EntityName: task.Type,
				Summary:     fmt.Sprintf("定时任务 %s 已启动，计划处理 %d 条记录", task.Type, task.ProgressTotal),
				DetailsJSON: dumpJSON(map[string]any{"task_type": task.Type, "platform": task.Platform, "task_status": task.Status, "total": task.ProgressTotal}),
				Count:       task.ProgressTotal, DedupeKey: "task:" + task.ID + ":started",
			})
			continue
		}
		if !terminalTaskStatuses[task.Status] {
			continue
		}
		status, level := "success", "info"
		if task.Status != TaskSucceeded {
			status, level = "failed", "error"
		}
		s.recordAudit(AuditLog{
			OccurredAt: task.UpdatedAt, Actor: actor, ActorType: actorType, LogType: logType, Category: category,
			Action: "task_completed", Level: level, Status: status, Source: "task-runtime", TaskID: task.ID,
			EntityType: "task", EntityID: task.ID, EntityName: task.Type,
			Summary:     fmt.Sprintf("任务 %s 执行完成：成功 %d，失败 %d，总数 %d", task.Type, task.SuccessCount, task.ErrorCount, task.ProgressTotal),
			DetailsJSON: dumpJSON(map[string]any{"task_type": task.Type, "platform": task.Platform, "task_status": task.Status, "success": task.SuccessCount, "failed": task.ErrorCount, "total": task.ProgressTotal, "error": sanitizePersistedString(task.Error)}),
			Count:       task.ProgressTotal, DedupeKey: "task:" + task.ID + ":" + task.Status,
		})
	}
}

func (s *Server) auditRetentionCleanup() {
	setting := s.auditSettings()
	if !setting.Enabled || setting.RetentionDays < 1 || time.Now().Hour() != setting.CleanupHour {
		return
	}
	cutoff := time.Now().AddDate(0, 0, -setting.RetentionDays)
	result := s.db.Where("occurred_at < ?", cutoff).Delete(&AuditLog{})
	if result.Error == nil && result.RowsAffected > 0 {
		s.recordAudit(AuditLog{LogType: "scheduler", Category: "audit", Action: "retention_cleanup", Status: "success", Summary: fmt.Sprintf("日志保留策略清理完成，删除 %d 条过期日志", result.RowsAffected), Count: int(result.RowsAffected), DetailsJSON: dumpJSON(map[string]any{"retention_days": setting.RetentionDays, "cutoff": formatTime(cutoff)})})
	}
	var jobs []AuditExportJob
	s.db.Where("created_at < ?", time.Now().Add(-24*time.Hour)).Find(&jobs)
	for _, job := range jobs {
		if job.FilePath != "" {
			_ = os.Remove(job.FilePath)
		}
		s.db.Delete(&job)
	}
}

func (s *Server) auditRuntimeMetrics() {
	var stats runtime.MemStats
	runtime.ReadMemStats(&stats)
	allocatedMB := int64(stats.Alloc / 1024 / 1024)
	limitMB := int64(intValue(os.Getenv("SUNNY_MEMORY_WARNING_MB"), 0))
	level, status := "info", "success"
	if limitMB > 0 && allocatedMB >= limitMB {
		level, status = "warning", "warning"
	}
	s.recordAudit(AuditLog{LogType: "system", Category: "system", Action: "runtime_metrics", Level: level, Status: status, Source: "go-runtime", Summary: fmt.Sprintf("系统运行指标：内存 %d MiB，协程 %d", allocatedMB, runtime.NumGoroutine()), DetailsJSON: dumpJSON(map[string]any{"memory_allocated_mb": allocatedMB, "memory_system_mb": stats.Sys / 1024 / 1024, "memory_warning_mb": limitMB, "goroutines": runtime.NumGoroutine(), "gc_cycles": stats.NumGC})})
}
