package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	sunnyTrialTaskType   = "sunny_account_trial_check"
	sunnyTrialUnknown    = "unknown"
	sunnyTrialEligible   = "eligible"
	sunnyTrialIneligible = "ineligible"
)

var (
	sunnyTrialCheckEndpoint    = "https://tools.oai9.com/api/trial/check"
	sunnyCheckTrialEligibility = func(ctx context.Context, accessToken string) (bool, string, bool, error) {
		proxyURL, _ := ctx.Value(sunnyTrialProxyContextKey{}).(string)
		return checkSunnyTrialEligibility(ctx, accessToken, proxyURL)
	}
)

type sunnyTrialProxyContextKey struct{}

type sunnyTrialCandidate struct {
	SessionID   uint
	AccountID   uint
	Email       string
	AccessToken string
	SkipReason  string
	Error       string
}

type sunnyTrialResult struct {
	SessionID    uint
	AccountID    uint
	Email        string
	Eligibility  string
	Message      string
	SkipReason   string
	InvalidToken bool
	Error        string
}

func normalizeSunnyTrialEligibility(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case sunnyTrialEligible, "true", "yes", "有0元试用", "有试用资格":
		return sunnyTrialEligible
	case sunnyTrialIneligible, "false", "no", "无0元试用", "无试用资格":
		return sunnyTrialIneligible
	default:
		return sunnyTrialUnknown
	}
}

func normalizeSunnyTrialFilter(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "" {
		return ""
	}
	if value == sunnyTrialUnknown {
		return sunnyTrialUnknown
	}
	if value = normalizeSunnyTrialEligibility(value); value == sunnyTrialEligible || value == sunnyTrialIneligible {
		return value
	}
	return ""
}

func sunnyTrialEligibilityFor(accountValue, mailboxValue string) string {
	if value := normalizeSunnyTrialEligibility(accountValue); value != sunnyTrialUnknown {
		return value
	}
	return normalizeSunnyTrialEligibility(mailboxValue)
}

func sunnyManualTrialCheckedAt(eligibility string) *time.Time {
	if normalizeSunnyTrialEligibility(eligibility) == sunnyTrialUnknown {
		return nil
	}
	now := time.Now()
	return &now
}

func sunnyTrialApplies(status, plan string) bool {
	return normalizeSunnyDisplayStatus(status) == "已注册" && normalizeSunnyPlanType(plan) == "free"
}

func checkSunnyTrialEligibility(ctx context.Context, accessToken string, proxyURLs ...string) (bool, string, bool, error) {
	body, err := json.Marshal(map[string]string{"access_token": strings.TrimSpace(accessToken)})
	if err != nil {
		return false, "", false, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, sunnyTrialCheckEndpoint, bytes.NewReader(body))
	if err != nil {
		return false, "", false, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "SunnyRegister/1.0")
	client := &http.Client{Timeout: 20 * time.Second}
	if len(proxyURLs) > 0 {
		proxyText := strings.TrimSpace(proxyURLs[0])
		if proxyText != "" {
			if proxy, parseErr := url.Parse(proxyText); parseErr == nil && proxy.Scheme != "" && proxy.Host != "" {
				client.Transport = &http.Transport{Proxy: http.ProxyURL(proxy)}
			}
		}
	}
	resp, err := client.Do(req)
	if err != nil {
		return false, "", false, fmt.Errorf("试用资格检测站点连接失败: %w", err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if err != nil {
		return false, "", false, fmt.Errorf("读取试用资格检测结果失败: %w", err)
	}
	var payload struct {
		Eligible *bool  `json:"eligible"`
		Message  string `json:"message"`
		Detail   string `json:"detail"`
	}
	if len(bytes.TrimSpace(raw)) > 0 {
		_ = json.Unmarshal(raw, &payload)
	}
	message := strings.TrimSpace(firstText(payload.Message, payload.Detail))
	if resp.StatusCode == http.StatusUnauthorized {
		return false, message, true, fmt.Errorf("%s", fallback(message, "accessToken 无效或已过期"))
	}
	if resp.StatusCode != http.StatusOK {
		return false, message, false, fmt.Errorf("试用资格检测站点返回 HTTP %d: %s", resp.StatusCode, fallback(message, strings.TrimSpace(string(raw))))
	}
	if payload.Eligible == nil {
		return false, message, false, fmt.Errorf("试用资格检测站点返回格式无效")
	}
	return *payload.Eligible, message, false, nil
}

func (s *Server) sunnyTrialConcurrency() int {
	value := intValue(strings.TrimSpace(os.Getenv("SUNNY_TRIAL_CONCURRENCY")), 4)
	if value < 1 {
		return 1
	}
	if value > 10 {
		return 10
	}
	return value
}

func (s *Server) sunnyTrialCandidates(ids []uint) ([]sunnyTrialCandidate, error) {
	if len(ids) == 0 {
		return nil, fmt.Errorf("请选择需要检测试用资格的账户")
	}
	var sessions []SunnySession
	if err := s.db.Where("id IN ?", ids).Order("id asc").Find(&sessions).Error; err != nil {
		return nil, err
	}
	accounts, mailboxes := s.sunnySessionSidecars(sessions)
	candidates := make([]sunnyTrialCandidate, 0, len(sessions))
	for _, session := range sessions {
		account := accounts[sunnyEmailKey(session.Email)]
		item := s.serializeSunnySession(session, accounts, mailboxes)
		candidate := sunnyTrialCandidate{
			SessionID:   session.ID,
			AccountID:   firstUint(session.AccountID, account.ID),
			Email:       session.Email,
			AccessToken: firstText(session.AccessToken, sunnyAccessTokenFromSessionJSON(session.SessionJSON), account.AccessToken),
		}
		if !sunnyTrialApplies(text(item["status"]), text(item["plan_type"])) {
			candidate.SkipReason = "仅已注册且套餐为 free 的账户支持试用资格检测"
		} else if strings.TrimSpace(candidate.AccessToken) == "" {
			candidate.Error = "账户缺少 Access Token"
		}
		candidates = append(candidates, candidate)
	}
	return candidates, nil
}

func firstUint(values ...uint) uint {
	for _, value := range values {
		if value != 0 {
			return value
		}
	}
	return 0
}

func (s *Server) createSunnyTrialTask(body map[string]any) (Task, error) {
	ids := uintSlice(body["session_ids"])
	if len(ids) == 0 {
		return Task{}, fmt.Errorf("请选择需要检测试用资格的账户")
	}
	var active int64
	s.db.Model(&Task{}).Where("type = ? AND status NOT IN ?", sunnyTrialTaskType, []string{TaskSucceeded, TaskFailed, TaskInterrupted, TaskCancelled}).Count(&active)
	if active > 0 {
		return Task{}, fmt.Errorf("已有试用资格检测任务正在执行，请稍候")
	}
	candidates, err := s.sunnyTrialCandidates(ids)
	if err != nil {
		return Task{}, err
	}
	if len(candidates) == 0 {
		return Task{}, fmt.Errorf("未找到需要检测试用资格的账户")
	}
	return s.createTask(sunnyTrialTaskType, "sunny", map[string]any{"session_ids": ids}, len(candidates)), nil
}

func (s *Server) executeSunnyTrialTask(task *Task, payload map[string]any) {
	task.Status = TaskRunning
	task.StartedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	candidates, err := s.sunnyTrialCandidates(uintSlice(payload["session_ids"]))
	if err != nil {
		s.failSunnyTrialTask(task, err.Error())
		return
	}
	result := map[string]any{"requested": len(candidates), "eligible": 0, "ineligible": 0, "skipped": 0, "failed": 0, "items": []any{}}
	jobs := make(chan sunnyTrialCandidate)
	results := make(chan sunnyTrialResult, len(candidates))
	var workers sync.WaitGroup
	for i := 0; i < s.sunnyTrialConcurrency() && i < len(candidates); i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for candidate := range jobs {
				outcome := sunnyTrialResult{SessionID: candidate.SessionID, AccountID: candidate.AccountID, Email: candidate.Email, SkipReason: candidate.SkipReason, Error: candidate.Error}
				if outcome.SkipReason == "" && outcome.Error == "" {
					trialCtx := context.WithValue(context.Background(), sunnyTrialProxyContextKey{}, s.sunnyMailboxProxyURL())
					eligible, message, invalidToken, checkErr := sunnyCheckTrialEligibility(trialCtx, candidate.AccessToken)
					outcome.Message, outcome.InvalidToken = message, invalidToken
					if checkErr != nil {
						outcome.Error = checkErr.Error()
					} else if eligible {
						outcome.Eligibility = sunnyTrialEligible
					} else {
						outcome.Eligibility = sunnyTrialIneligible
					}
				}
				results <- outcome
			}
		}()
	}
	for _, candidate := range candidates {
		jobs <- candidate
	}
	close(jobs)
	workers.Wait()
	close(results)

	invalidAccounts := []uint{}
	invalidSessions := []uint{}
	seenAccounts := map[uint]bool{}
	items := make([]any, 0, len(candidates))
	for outcome := range results {
		item := map[string]any{"session_id": outcome.SessionID, "email": outcome.Email}
		now := time.Now()
		switch {
		case outcome.SkipReason != "":
			result["skipped"] = result["skipped"].(int) + 1
			item["status"], item["message"] = "skipped", outcome.SkipReason
		case outcome.Error != "":
			result["failed"] = result["failed"].(int) + 1
			item["status"], item["error"] = "failed", outcome.Error
			updates := map[string]any{"trial_eligibility": sunnyTrialUnknown, "trial_check_error": outcome.Error, "trial_checked_at": now}
			s.db.Model(&SunnyAccount{}).Where("email = ?", outcome.Email).Updates(updates)
			s.db.Model(&SunnyMailbox{}).Where("email = ?", outcome.Email).Updates(updates)
			if outcome.InvalidToken {
				s.db.Model(&SunnySession{}).Where("id = ?", outcome.SessionID).Updates(map[string]any{"access_token_status": "invalid", "access_token_error": outcome.Error, "access_token_checked_at": now})
				invalidSessions = append(invalidSessions, outcome.SessionID)
				if outcome.AccountID != 0 && !seenAccounts[outcome.AccountID] {
					seenAccounts[outcome.AccountID] = true
					invalidAccounts = append(invalidAccounts, outcome.AccountID)
				}
			}
			s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s 试用资格检测失败：%s", outcome.Email, outcome.Error), "log", "warning", nil)
		default:
			result[outcome.Eligibility] = result[outcome.Eligibility].(int) + 1
			item["status"], item["message"] = outcome.Eligibility, outcome.Message
			updates := map[string]any{"trial_eligibility": outcome.Eligibility, "trial_check_error": "", "trial_checked_at": now}
			tx := s.db.Begin()
			updateErr := tx.Model(&SunnyAccount{}).Where("email = ?", outcome.Email).Updates(updates).Error
			if updateErr == nil {
				updateErr = tx.Model(&SunnyMailbox{}).Where("email = ?", outcome.Email).Updates(updates).Error
			}
			if updateErr == nil {
				updateErr = tx.Commit().Error
			} else {
				tx.Rollback()
			}
			if updateErr != nil {
				result[outcome.Eligibility] = result[outcome.Eligibility].(int) - 1
				result["failed"] = result["failed"].(int) + 1
				item["status"], item["error"] = "failed", updateErr.Error()
			} else {
				s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s 试用资格检测完成：%s", outcome.Email, outcome.Message), "log", "info", nil)
			}
		}
		items = append(items, item)
		task.ProgressCurrent++
		s.db.Model(&Task{}).Where("id = ?", task.ID).Updates(map[string]any{"progress_current": task.ProgressCurrent, "updated_at": now})
	}
	if len(invalidAccounts) > 0 {
		renewalTask := s.createSunnyAccessTokenRenewalTask(task, "trial_check", invalidAccounts)
		result["renewal_task_id"] = renewalTask.ID
		result["renewal_queued"] = len(invalidAccounts)
		result["invalid_session_ids"] = invalidSessions
	}
	result["items"] = items
	s.completeSunnyTrialTask(task, result)
}

func (s *Server) failSunnyTrialTask(task *Task, message string) {
	task.Status = TaskFailed
	task.Error = message
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	task.ResultJSON = dumpJSON(map[string]any{"requested": task.ProgressTotal, "eligible": 0, "ineligible": 0, "skipped": 0, "failed": task.ProgressTotal})
	s.db.Save(task)
	s.appendTaskEvent(task.ID, message, "log", "error", nil)
}

func (s *Server) completeSunnyTrialTask(task *Task, result map[string]any) {
	task.Status = TaskSucceeded
	task.SuccessCount = intValue(result["eligible"], 0) + intValue(result["ineligible"], 0)
	task.ErrorCount = intValue(result["failed"], 0)
	task.ResultJSON = dumpJSON(result)
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	s.appendTaskEvent(task.ID, "账户试用资格检测任务完成", "log", "info", result)
}
