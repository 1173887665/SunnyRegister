package main

import (
	"database/sql"
	"fmt"
	"html"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
)

const sunnySubscriptionTaskType = "sunny_account_subscription_check"

var sunnyDetectSubscriptionMail = detectSunnySubscriptionMail

var sunnySubscriptionSubjectMarkers = []string{
	"your new plan", "your new subscription",
	"你的新套餐", "您的新套餐", "你的新方案", "您的新方案", "你的新計劃", "您的新計劃",
	"新しいプラン", "새로운 요금제", "새 요금제",
	"seu novo plano", "novo plano", "tu nuevo plan", "su nuevo plan",
	"votre nouveau forfait", "votre nouvel abonnement",
	"dein neuer tarif", "ihr neuer tarif", "neues abonnement",
	"il tuo nuovo piano", "paket baru anda", "आपकी नई योजना",
}

var sunnySubscriptionBodyMarkers = []string{
	"chatgpt plus subscription",
	"manage subscription", "manage your subscription",
	"管理订阅", "管理訂閱", "管理您的订阅", "管理您的訂閱",
	"サブスクリプションの管理", "正常に登録されました",
	"구독 관리", "구독을 관리",
	"gerenciar assinatura", "gerir subscrição", "gerir assinatura",
	"administrar suscripción", "gestionar suscripción",
	"gérer l'abonnement", "gérer votre abonnement", "gérer l’abonnement", "gérer votre abonnement",
	"abonnement verwalten", "gestisci abbonamento", "kelola langganan", "सदस्यता प्रबंधित करें",
}

type sunnySubscriptionCandidate struct {
	SessionID    uint
	Email        string
	MailboxType  string
	Channel      string
	AccessKey    string
	ClientID     string
	RefreshToken string
	Error        string
}

type sunnySubscriptionResult struct {
	SessionID  uint
	Email      string
	Subscribed bool
	Subject    string
	Error      string
}

func normalizeSunnySubscriptionText(value string) string {
	value = html.UnescapeString(value)
	value = strings.ReplaceAll(value, "\u00a0", " ")
	value = strings.ToLower(value)
	return strings.TrimSpace(regexp.MustCompile(`\s+`).ReplaceAllString(value, " "))
}

func sunnySubscriptionSubjectCandidate(subject string) bool {
	normalized := normalizeSunnySubscriptionText(subject)
	if !strings.Contains(normalized, "chatgpt") {
		return false
	}
	for _, marker := range sunnySubscriptionSubjectMarkers {
		if strings.Contains(normalized, normalizeSunnySubscriptionText(marker)) {
			return true
		}
	}
	return false
}

func sunnySubscriptionBodyConfirmed(body string) bool {
	normalized := normalizeSunnySubscriptionText(body)
	for _, marker := range sunnySubscriptionBodyMarkers {
		if strings.Contains(normalized, normalizeSunnySubscriptionText(marker)) {
			return true
		}
	}
	return false
}

func sunnySubscriptionMailItems(payload map[string]any) []map[string]any {
	if items, ok := payload["items"].([]map[string]any); ok {
		return items
	}
	rawItems, _ := payload["items"].([]any)
	items := make([]map[string]any, 0, len(rawItems))
	for _, raw := range rawItems {
		if item, ok := raw.(map[string]any); ok {
			items = append(items, item)
		}
	}
	return items
}

func sunnySubscriptionPayloadConfirmed(payload map[string]any) (bool, string) {
	for _, item := range sunnySubscriptionMailItems(payload) {
		subject := strings.TrimSpace(text(item["subject"]))
		if !sunnySubscriptionSubjectCandidate(subject) {
			continue
		}
		body := strings.Join([]string{text(item["body"]), text(item["body_preview"]), text(item["raw_html"])}, "\n")
		if sunnySubscriptionBodyConfirmed(body) {
			return true, subject
		}
	}
	return false, ""
}

func detectSunnySubscriptionMail(candidate sunnySubscriptionCandidate, proxyURL string) (bool, string, error) {
	var payload map[string]any
	var err error
	if candidate.MailboxType == "apple" && candidate.Channel == "url_api" {
		payload, err = fetchURLAPILatestMail(candidate.Email, candidate.AccessKey, 5, proxyURL)
		if err != nil {
			return false, "", err
		}
		matched, subject := sunnySubscriptionPayloadConfirmed(payload)
		return matched, subject, nil
	}

	var subjects []string
	if candidate.MailboxType == "apple" && candidate.Channel == "xbovo" {
		subjects, err = fetchXbovoMailSubjects(candidate.Email, candidate.AccessKey, 5, proxyURL)
	} else {
		subjects, err = sunnyFetchOutlookMailSubjects(candidate.Email, candidate.ClientID, candidate.RefreshToken, 5, proxyURL)
	}
	if err != nil {
		return false, "", err
	}
	hasCandidate := false
	for _, subject := range subjects {
		if sunnySubscriptionSubjectCandidate(subject) {
			hasCandidate = true
			break
		}
	}
	if !hasCandidate {
		return false, "", nil
	}
	if candidate.MailboxType == "apple" {
		payload, err = fetchXbovoLatestMail(candidate.Email, candidate.AccessKey, 5, proxyURL)
	} else {
		payload, err = fetchOutlookLatestMail(candidate.Email, candidate.ClientID, candidate.RefreshToken, 5, proxyURL)
	}
	if err != nil {
		return false, "", err
	}
	matched, subject := sunnySubscriptionPayloadConfirmed(payload)
	return matched, subject, nil
}

func (s *Server) sunnySubscriptionConcurrency() int {
	value := intValue(strings.TrimSpace(os.Getenv("SUNNY_SUBSCRIPTION_CONCURRENCY")), 4)
	if value < 1 {
		value = 1
	}
	if value > 12 {
		value = 12
	}
	return value
}

func (s *Server) sunnySubscriptionCandidates(ids []uint) ([]sunnySubscriptionCandidate, error) {
	if len(ids) == 0 {
		return nil, fmt.Errorf("请选择需要检测订阅的账户")
	}
	var sessions []SunnySession
	if err := s.db.Model(&SunnySession{}).Select("id", "email").Where("id IN ?", ids).Order("id asc").Find(&sessions).Error; err != nil {
		return nil, err
	}
	emails := make([]string, 0, len(sessions))
	for _, session := range sessions {
		emails = append(emails, session.Email)
	}
	var mailboxes []SunnyMailbox
	if len(emails) > 0 {
		if err := s.db.Where("email IN ?", emails).Find(&mailboxes).Error; err != nil {
			return nil, err
		}
	}
	mailboxByEmail := map[string]SunnyMailbox{}
	for _, mailbox := range mailboxes {
		mailboxByEmail[sunnyEmailKey(mailbox.Email)] = mailbox
	}
	candidates := make([]sunnySubscriptionCandidate, 0, len(sessions))
	for _, session := range sessions {
		mailbox, ok := mailboxByEmail[sunnyEmailKey(session.Email)]
		candidate := sunnySubscriptionCandidate{SessionID: session.ID, Email: session.Email}
		if !ok {
			candidate.Error = "邮箱凭证不存在"
			candidates = append(candidates, candidate)
			continue
		}
		candidate.Email = mailbox.Email
		candidate.MailboxType = normalizeSunnyMailboxType(mailbox.MailboxType)
		candidate.Channel = normalizeSunnyMailboxChannel(candidate.MailboxType, mailbox.MailboxChannel)
		candidate.AccessKey = mailbox.AccessKey
		candidate.ClientID = mailbox.ClientID
		candidate.RefreshToken = mailbox.RefreshToken
		if (candidate.MailboxType == "apple" && strings.TrimSpace(candidate.AccessKey) == "") ||
			(candidate.MailboxType == "microsoft" && (strings.TrimSpace(candidate.ClientID) == "" || strings.TrimSpace(candidate.RefreshToken) == "")) {
			candidate.Error = "邮箱凭证不完整"
		}
		candidates = append(candidates, candidate)
	}
	return candidates, nil
}

func (s *Server) createSunnySubscriptionTask(body map[string]any) (Task, error) {
	ids := uintSlice(body["session_ids"])
	if len(ids) == 0 {
		return Task{}, fmt.Errorf("请选择需要检测订阅的账户")
	}
	var active int64
	s.db.Model(&Task{}).Where("type = ? AND status NOT IN ?", sunnySubscriptionTaskType, []string{TaskSucceeded, TaskFailed, TaskInterrupted, TaskCancelled}).Count(&active)
	if active > 0 {
		return Task{}, fmt.Errorf("已有订阅检测任务正在执行，请稍候")
	}
	candidates, err := s.sunnySubscriptionCandidates(ids)
	if err != nil {
		return Task{}, err
	}
	if len(candidates) == 0 {
		return Task{}, fmt.Errorf("未找到需要检测订阅的账户")
	}
	return s.createTask(sunnySubscriptionTaskType, "sunny", map[string]any{"session_ids": ids}, len(candidates)), nil
}

func (s *Server) executeSunnySubscriptionTask(task *Task, payload map[string]any) {
	task.Status = TaskRunning
	task.StartedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	candidates, err := s.sunnySubscriptionCandidates(uintSlice(payload["session_ids"]))
	if err != nil {
		s.failSunnySubscriptionTask(task, err.Error())
		return
	}
	result := map[string]any{"requested": len(candidates), "subscribed": 0, "not_subscribed": 0, "failed": 0, "items": []any{}}
	if len(candidates) == 0 {
		s.completeSunnySubscriptionTask(task, result)
		return
	}
	proxyURL := s.sunnyMailboxProxyURL()
	jobs := make(chan sunnySubscriptionCandidate)
	results := make(chan sunnySubscriptionResult, len(candidates))
	var workers sync.WaitGroup
	for i := 0; i < s.sunnySubscriptionConcurrency() && i < len(candidates); i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for candidate := range jobs {
				if candidate.Error != "" {
					results <- sunnySubscriptionResult{SessionID: candidate.SessionID, Email: candidate.Email, Error: candidate.Error}
					continue
				}
				subscribed, subject, detectErr := sunnyDetectSubscriptionMail(candidate, proxyURL)
				outcome := sunnySubscriptionResult{SessionID: candidate.SessionID, Email: candidate.Email, Subscribed: subscribed, Subject: subject}
				if detectErr != nil {
					outcome.Error = detectErr.Error()
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

	items := make([]any, 0, len(candidates))
	for outcome := range results {
		item := map[string]any{"email": outcome.Email, "status": "not_subscribed"}
		if outcome.Error != "" {
			result["failed"] = result["failed"].(int) + 1
			item["status"] = "failed"
			item["error"] = outcome.Error
			s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s 订阅检测失败：%s", outcome.Email, outcome.Error), "log", "warning", nil)
		} else if outcome.Subscribed {
			result["subscribed"] = result["subscribed"].(int) + 1
			item["status"] = "subscribed"
			item["subject"] = outcome.Subject
			now := time.Now()
			tx := s.db.Begin()
			updateErr := tx.Model(&SunnyMailbox{}).Where("email = ?", outcome.Email).Updates(map[string]any{"account_type": "plus", "updated_at": now}).Error
			if updateErr == nil {
				updateErr = tx.Model(&SunnyAccount{}).Where("email = ?", outcome.Email).Updates(map[string]any{"account_type": "plus", "updated_at": now}).Error
			}
			if updateErr == nil {
				updateErr = tx.Commit().Error
			} else {
				tx.Rollback()
			}
			if updateErr != nil {
				result["subscribed"] = result["subscribed"].(int) - 1
				result["failed"] = result["failed"].(int) + 1
				item["status"] = "failed"
				item["error"] = updateErr.Error()
			} else {
				s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s 已确认订阅成功，套餐已更新为 Plus", outcome.Email), "log", "info", map[string]any{"subject": outcome.Subject})
			}
		} else {
			result["not_subscribed"] = result["not_subscribed"].(int) + 1
			s.appendTaskEvent(task.ID, fmt.Sprintf("账户 %s 未检测到订阅成功邮件", outcome.Email), "log", "info", nil)
		}
		items = append(items, item)
		task.ProgressCurrent++
		s.db.Model(&Task{}).Where("id = ?", task.ID).Updates(map[string]any{"progress_current": task.ProgressCurrent, "updated_at": time.Now()})
	}
	result["items"] = items
	s.completeSunnySubscriptionTask(task, result)
}

func (s *Server) failSunnySubscriptionTask(task *Task, message string) {
	task.Status = TaskFailed
	task.Error = message
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	task.ResultJSON = dumpJSON(map[string]any{"requested": task.ProgressTotal, "subscribed": 0, "not_subscribed": 0, "failed": task.ProgressTotal})
	s.db.Save(task)
	s.appendTaskEvent(task.ID, message, "log", "error", nil)
}

func (s *Server) completeSunnySubscriptionTask(task *Task, result map[string]any) {
	task.Status = TaskSucceeded
	task.SuccessCount = intValue(result["subscribed"], 0) + intValue(result["not_subscribed"], 0)
	task.ErrorCount = intValue(result["failed"], 0)
	task.ResultJSON = dumpJSON(result)
	task.FinishedAt = sql.NullTime{Time: time.Now(), Valid: true}
	s.db.Save(task)
	s.appendTaskEvent(task.ID, "账户订阅检测任务完成", "log", "info", result)
}
