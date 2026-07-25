package main

import (
	"bufio"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"net/mail"
	"net/textproto"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const sunnyHealthTaskType = "sunny_account_health_check"

var sunnyHealthBanMarker = regexp.MustCompile(`(?i)access\s+deactivated|\[\s*C-[A-Za-z0-9]{6,32}\s*\]`)
var sunnyFetchOutlookMailSubjects = fetchOutlookMailSubjects
var sunnyFetchMailSubjectsViaGraph = fetchMailSubjectsViaGraph
var sunnyFetchMailHeadersViaIMAP = fetchMailHeadersViaIMAP

type sunnyHealthCandidate struct {
	SessionID    uint
	Email        string
	ClientID     string
	RefreshToken string
	Error        string
}

type sunnyHealthMailHeader struct {
	Subject string
	Date    time.Time
}

type sunnyHealthResult struct {
	Email   string
	Banned  bool
	Checked bool
	Error   string
}

func (s *Server) sunnyHealthCheckConcurrency() int {
	value := intValue(strings.TrimSpace(os.Getenv("SUNNY_HEALTHCHECK_CONCURRENCY")), 6)
	if value < 1 {
		value = 1
	}
	if value > 16 {
		value = 16
	}
	return value
}

func sunnyHealthStatus(status string) string {
	return normalizeSunnyDisplayStatus(strings.TrimSpace(status))
}

func (s *Server) sunnyHealthCandidates(ids []uint, all bool) ([]sunnyHealthCandidate, int, error) {
	var sessions []SunnySession
	query := s.db.Model(&SunnySession{}).Select("id", "email")
	if len(ids) > 0 {
		query = query.Where("id IN ?", ids)
	} else if !all {
		return nil, 0, fmt.Errorf("请选择需要测活的账户")
	}
	if err := query.Order("id asc").Find(&sessions).Error; err != nil {
		return nil, 0, err
	}
	if len(sessions) == 0 {
		return []sunnyHealthCandidate{}, 0, nil
	}
	emails := make([]string, 0, len(sessions))
	for _, session := range sessions {
		emails = append(emails, session.Email)
	}
	var mailboxes []SunnyMailbox
	if err := s.db.Where("email IN ?", emails).Find(&mailboxes).Error; err != nil {
		return nil, 0, err
	}
	mailboxByEmail := map[string]SunnyMailbox{}
	for _, mailbox := range mailboxes {
		mailboxByEmail[sunnyEmailKey(mailbox.Email)] = mailbox
	}
	var accounts []SunnyAccount
	if err := s.db.Select("email", "status").Where("email IN ?", emails).Find(&accounts).Error; err != nil {
		return nil, 0, err
	}
	accountStatus := map[string]string{}
	for _, account := range accounts {
		accountStatus[sunnyEmailKey(account.Email)] = account.Status
	}
	candidates := make([]sunnyHealthCandidate, 0, len(sessions))
	skipped := 0
	for _, session := range sessions {
		key := sunnyEmailKey(session.Email)
		mailbox, ok := mailboxByEmail[key]
		if sunnyHealthStatus(mailbox.Status) == "已封禁" || sunnyHealthStatus(accountStatus[key]) == "已封禁" {
			skipped++
			continue
		}
		if !ok || strings.TrimSpace(mailbox.ClientID) == "" || strings.TrimSpace(mailbox.RefreshToken) == "" {
			candidates = append(candidates, sunnyHealthCandidate{SessionID: session.ID, Email: session.Email, Error: "邮箱凭证不完整"})
			continue
		}
		candidates = append(candidates, sunnyHealthCandidate{SessionID: session.ID, Email: session.Email, ClientID: mailbox.ClientID, RefreshToken: mailbox.RefreshToken})
	}
	return candidates, skipped, nil
}

func (s *Server) createSunnyHealthTask(body map[string]any) (Task, error) {
	var ids []uint
	if raw := uintSlice(body["session_ids"]); len(raw) > 0 {
		ids = raw
	}
	all := boolValue(body["scheduled"], false)
	if len(ids) == 0 && !all {
		return Task{}, fmt.Errorf("请选择需要测活的账户")
	}
	var active int64
	s.db.Model(&Task{}).Where("type = ? AND status NOT IN ?", sunnyHealthTaskType, []string{TaskSucceeded, TaskFailed, TaskInterrupted, TaskCancelled}).Count(&active)
	if active > 0 {
		return Task{}, fmt.Errorf("已有账户测活任务正在执行，请稍候")
	}
	candidates, skipped, err := s.sunnyHealthCandidates(ids, all)
	if err != nil {
		return Task{}, err
	}
	payload := map[string]any{"session_ids": ids, "scheduled": all, "skipped": skipped}
	total := len(candidates)
	task := s.createTask(sunnyHealthTaskType, "sunny", payload, total)
	return task, nil
}

func (s *Server) executeSunnyAccountHealthCheckTask(task *Task, payload map[string]any) {
	task.Status = TaskRunning
	task.StartedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	ids := uintSlice(payload["session_ids"])
	all := boolValue(payload["scheduled"], false)
	candidates, skipped, err := s.sunnyHealthCandidates(ids, all)
	if err != nil {
		s.failSunnyHealthTask(task, err.Error())
		return
	}
	result := map[string]any{"requested": len(candidates), "checked": 0, "alive": 0, "banned": 0, "failed": 0, "skipped": skipped, "items": []any{}}
	if len(candidates) == 0 {
		s.completeSunnyHealthTask(task, result)
		return
	}
	proxyURL := s.sunnyMailboxProxyURL()
	concurrency := s.sunnyHealthCheckConcurrency()
	jobs := make(chan sunnyHealthCandidate)
	results := make(chan sunnyHealthResult, len(candidates))
	var workers sync.WaitGroup
	for i := 0; i < concurrency; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for candidate := range jobs {
				if candidate.Error != "" {
					results <- sunnyHealthResult{Email: candidate.Email, Error: candidate.Error}
					continue
				}
				subjects, fetchErr := sunnyFetchOutlookMailSubjects(candidate.Email, candidate.ClientID, candidate.RefreshToken, 5, proxyURL)
				if fetchErr != nil {
					results <- sunnyHealthResult{Email: candidate.Email, Error: fetchErr.Error()}
					continue
				}
				banned := false
				for _, subject := range subjects {
					if sunnyHealthBanMarker.MatchString(subject) {
						banned = true
						break
					}
				}
				results <- sunnyHealthResult{Email: candidate.Email, Banned: banned, Checked: true}
			}
		}()
	}
	for _, candidate := range candidates {
		jobs <- candidate
	}
	close(jobs)
	workers.Wait()
	close(results)
	items := make([]any, 0, len(candidates))
	for outcome := range results {
		item := map[string]any{"email": outcome.Email, "status": "alive"}
		if outcome.Error != "" {
			result["failed"] = result["failed"].(int) + 1
			item["status"] = "failed"
			item["error"] = outcome.Error
			s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s 测活失败：%s", outcome.Email, outcome.Error), "log", "warning", nil)
		} else {
			result["checked"] = result["checked"].(int) + 1
			now := time.Now()
			if outcome.Banned {
				result["banned"] = result["banned"].(int) + 1
				item["status"] = "banned"
				s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s：已封禁", outcome.Email), "log", "warning", nil)
				s.db.Model(&SunnyMailbox{}).Where("email = ?", outcome.Email).UpdateColumns(map[string]any{
					"last_health_checked_at": now, "status": "已封禁", "status_changed_at": now,
					"last_error": "测活邮件标题命中账户封禁标记", "updated_at": now,
				})
				s.db.Model(&SunnyAccount{}).Where("email = ?", outcome.Email).UpdateColumns(map[string]any{
					"last_health_checked_at": now, "status": "已封禁", "status_changed_at": now,
					"last_error": "测活邮件标题命中账户封禁标记", "updated_at": now,
				})
			} else {
				result["alive"] = result["alive"].(int) + 1
				s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s：存活", outcome.Email), "log", "info", nil)
				// A successful health check is not a mailbox edit or an account status change.
				s.db.Model(&SunnyMailbox{}).Where("email = ?", outcome.Email).UpdateColumn("last_health_checked_at", now)
				s.db.Model(&SunnyAccount{}).Where("email = ?", outcome.Email).UpdateColumn("last_health_checked_at", now)
			}
		}
		items = append(items, item)
		current := task.ProgressCurrent + 1
		task.ProgressCurrent = current
		s.db.Model(&Task{}).Where("id = ?", task.ID).Updates(map[string]any{"progress_current": current, "updated_at": time.Now()})
	}
	result["items"] = items
	s.completeSunnyHealthTask(task, result)
}

func (s *Server) failSunnyHealthTask(task *Task, message string) {
	task.Status = TaskFailed
	task.Error = message
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	task.ResultJSON = dumpJSON(map[string]any{"requested": task.ProgressTotal, "checked": 0, "alive": 0, "banned": 0, "failed": task.ProgressTotal, "skipped": 0})
	s.db.Save(task)
	s.appendTaskEvent(task.ID, message, "log", "error", nil)
}

func (s *Server) completeSunnyHealthTask(task *Task, result map[string]any) {
	task.Status = TaskSucceeded
	task.SuccessCount = intValue(result["alive"], 0) + intValue(result["banned"], 0)
	task.ErrorCount = intValue(result["failed"], 0)
	task.ResultJSON = dumpJSON(result)
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	s.appendTaskEvent(task.ID, "账户测活任务完成", "log", "info", result)
}

func fetchOutlookMailSubjects(emailAddr, clientID, refreshToken string, limit int, proxyURL string) ([]string, error) {
	errors := []string{}
	for _, endpoint := range hotmailGraphTokenEndpoints {
		token, err := refreshHotmailAccessTokenFromEndpoint(clientID, refreshToken, endpoint, proxyURL)
		if err != nil {
			errors = append(errors, endpoint.Name+" token: "+err.Error())
			continue
		}
		subjects, err := sunnyFetchMailSubjectsViaGraph(token, limit, proxyURL)
		if err == nil {
			return subjects, nil
		}
		errors = append(errors, endpoint.Name+" Graph: "+err.Error())
	}
	for _, endpoint := range hotmailTokenEndpoints {
		token, err := refreshHotmailAccessTokenFromEndpoint(clientID, refreshToken, endpoint, proxyURL)
		if err != nil {
			errors = append(errors, endpoint.Name+" token: "+err.Error())
			continue
		}
		headers, err := sunnyFetchMailHeadersViaIMAP(emailAddr, token, limit, proxyURL)
		if err == nil {
			return headers, nil
		}
		errors = append(errors, endpoint.Name+" IMAP: "+err.Error())
	}
	return nil, fmt.Errorf("Outlook Graph/IMAP subject query failed: %s", strings.Join(errors, " | "))
}

func fetchMailSubjectsViaGraph(accessToken string, limit int, proxyURL string) ([]string, error) {
	if limit < 1 {
		limit = 5
	}
	if limit > 20 {
		limit = 20
	}
	endpoint, err := url.Parse(outlookGraphMessagesURL)
	if err != nil {
		return nil, fmt.Errorf("invalid Graph messages URL: %w", err)
	}
	query := endpoint.Query()
	query.Set("$top", strconv.Itoa(limit))
	query.Set("$orderby", "receivedDateTime desc")
	query.Set("$select", "subject")
	endpoint.RawQuery = query.Encode()

	client := &http.Client{Timeout: 15 * time.Second}
	if strings.TrimSpace(proxyURL) != "" {
		proxy, parseErr := url.Parse(proxyURL)
		if parseErr != nil {
			return nil, fmt.Errorf("invalid Graph proxy URL: %w", parseErr)
		}
		client.Transport = &http.Transport{Proxy: http.ProxyURL(proxy)}
	}
	req, err := http.NewRequest(http.MethodGet, endpoint.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Bearer "+accessToken)
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Graph request failed: %w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, fmt.Errorf("Graph response read failed: %w", err)
	}
	var payload struct {
		Value []struct {
			Subject string `json:"subject"`
		} `json:"value"`
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, fmt.Errorf("Graph returned invalid JSON (HTTP %d)", resp.StatusCode)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		detail := strings.TrimSpace(strings.Join([]string{payload.Error.Code, payload.Error.Message}, ": "))
		return nil, fmt.Errorf("Graph HTTP %d: %s", resp.StatusCode, fallback(detail, string(raw[:min(len(raw), 300)])))
	}
	subjects := make([]string, 0, len(payload.Value))
	for _, message := range payload.Value {
		subjects = append(subjects, message.Subject)
	}
	return subjects, nil
}

func fetchMailHeadersViaIMAP(emailAddr, accessToken string, limit int, proxyURL string) ([]string, error) {
	if limit < 1 {
		limit = 5
	}
	if limit > 5 {
		limit = 5
	}
	conn, err := dialOutlookIMAPS(proxyURL)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	reader := bufio.NewReader(conn)
	if _, err := reader.ReadString('\n'); err != nil {
		return nil, fmt.Errorf("IMAP greeting failed: %w", err)
	}
	write := func(format string, args ...any) error {
		_, err := fmt.Fprintf(conn, format+"\r\n", args...)
		return err
	}
	readUntil := func(tag string) (string, error) {
		var b strings.Builder
		for {
			line, err := reader.ReadString('\n')
			if err != nil {
				return b.String(), err
			}
			b.WriteString(line)
			if strings.HasPrefix(line, tag+" ") {
				return b.String(), nil
			}
		}
	}
	auth := base64.StdEncoding.EncodeToString([]byte(fmt.Sprintf("user=%s\x01auth=Bearer %s\x01\x01", emailAddr, accessToken)))
	if err := write("A1 AUTHENTICATE XOAUTH2 %s", auth); err != nil {
		return nil, err
	}
	var authOut strings.Builder
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return nil, fmt.Errorf("IMAP XOAUTH2 response failed: %w", err)
		}
		authOut.WriteString(line)
		if strings.HasPrefix(line, "+") {
			if err := write(""); err != nil {
				return nil, err
			}
			continue
		}
		if strings.HasPrefix(line, "A1 ") {
			break
		}
	}
	if !strings.Contains(authOut.String(), "A1 OK") {
		return nil, fmt.Errorf("IMAP XOAUTH2 authentication failed: %s", strings.TrimSpace(authOut.String()))
	}
	allHeaders := make([]sunnyHealthMailHeader, 0, limit*2)
	for index, folder := range []string{"INBOX", "Junk", "Junk Email"} {
		selectTag := fmt.Sprintf("S%d", index+1)
		quotedFolder := folder
		if folder != "INBOX" {
			quotedFolder = `"` + folder + `"`
		}
		if err := write("%s SELECT %s", selectTag, quotedFolder); err != nil {
			return nil, err
		}
		selectOut, err := readUntil(selectTag)
		if err != nil || !strings.Contains(selectOut, selectTag+" OK") {
			continue
		}
		total := 0
		for _, line := range strings.Split(selectOut, "\n") {
			fields := strings.Fields(strings.TrimSpace(line))
			if len(fields) >= 3 && fields[0] == "*" && fields[2] == "EXISTS" {
				total, _ = strconv.Atoi(fields[1])
				break
			}
		}
		if total <= 0 {
			continue
		}
		start := total - limit + 1
		if start < 1 {
			start = 1
		}
		fetchTag := fmt.Sprintf("F%d", index+1)
		if err := write("%s FETCH %d:%d BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)]", fetchTag, start, total); err != nil {
			return nil, err
		}
		raw, err := readUntil(fetchTag)
		if err != nil {
			return nil, err
		}
		for sequence := start; sequence <= total; sequence++ {
			if header, ok := extractSunnyHeader(raw, sequence, fetchTag); ok {
				allHeaders = append(allHeaders, header)
			}
		}
	}
	if err := write("ZZ LOGOUT"); err == nil {
		_, _ = readUntil("ZZ")
	}
	sort.SliceStable(allHeaders, func(i, j int) bool { return allHeaders[i].Date.After(allHeaders[j].Date) })
	if len(allHeaders) > limit {
		allHeaders = allHeaders[:limit]
	}
	result := make([]string, 0, len(allHeaders))
	for _, header := range allHeaders {
		result = append(result, header.Subject)
	}
	return result, nil
}

func extractSunnyHeader(raw string, sequence int, _ string) (sunnyHealthMailHeader, bool) {
	marker := fmt.Sprintf("* %d FETCH", sequence)
	start := strings.Index(raw, marker)
	if start < 0 {
		return sunnyHealthMailHeader{}, false
	}
	literalMarker := strings.Index(raw[start:], "}\r\n")
	if literalMarker < 0 {
		return sunnyHealthMailHeader{}, false
	}
	literalMarker += start
	openBrace := strings.LastIndex(raw[start:literalMarker], "{")
	if openBrace < 0 {
		return sunnyHealthMailHeader{}, false
	}
	openBrace += start
	literalLength, err := strconv.Atoi(strings.TrimSpace(raw[openBrace+1 : literalMarker]))
	literalStart := literalMarker + 3
	if err != nil || literalLength < 1 || literalStart+literalLength > len(raw) {
		return sunnyHealthMailHeader{}, false
	}
	headerRaw := raw[literalStart : literalStart+literalLength]
	mimeHeader, err := textproto.NewReader(bufio.NewReader(strings.NewReader(headerRaw))).ReadMIMEHeader()
	if err != nil {
		return sunnyHealthMailHeader{}, false
	}
	subject := mimeHeader.Get("Subject")
	if decoded, decodeErr := (&mime.WordDecoder{CharsetReader: mailCharsetReader}).DecodeHeader(subject); decodeErr == nil {
		subject = decoded
	}
	date, _ := mail.ParseDate(mimeHeader.Get("Date"))
	return sunnyHealthMailHeader{Subject: subject, Date: date}, true
}

func (s *Server) sunnyAccountHealthScheduleLoop() {
	interval := time.Minute
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		s.sunnyMaybeScheduleHealthCheck()
		select {
		case <-s.stop:
			return
		case <-ticker.C:
		}
	}
}

func (s *Server) sunnyMaybeScheduleHealthCheck() {
	if raw := strings.TrimSpace(os.Getenv("SUNNY_HEALTHCHECK_ENABLED")); raw != "" && !boolValue(raw, true) {
		return
	}
	location := applicationLocation()
	now := time.Now().In(location)
	timeText := fallback(strings.TrimSpace(os.Getenv("SUNNY_HEALTHCHECK_TIME")), "06:00")
	scheduled, err := time.ParseInLocation("15:04", timeText, location)
	if err != nil || now.Hour() < scheduled.Hour() || (now.Hour() == scheduled.Hour() && now.Minute() < scheduled.Minute()) {
		return
	}
	var tasks []Task
	s.db.Where("type = ?", sunnyHealthTaskType).Order("created_at desc").Limit(20).Find(&tasks)
	today := now.Format("2006-01-02")
	for _, task := range tasks {
		payload := jsonMap(task.PayloadJSON)
		if boolValue(payload["scheduled"], false) && task.CreatedAt.In(location).Format("2006-01-02") == today {
			return
		}
	}
	if _, err := s.createSunnyHealthTask(map[string]any{"scheduled": true}); err != nil {
		if !strings.Contains(err.Error(), "正在执行") {
			log.Printf("scheduled account health check skipped: %v", err)
		}
	}
}
