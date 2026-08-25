package main

import (
	"database/sql"
	"fmt"
	"html"
	"os"
	"regexp"
	"strings"
	"time"

	"gorm.io/gorm"
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
	AccountID    uint
	Email        string
	MailEmail    string
	MailboxType  string
	Channel      string
	AccessKey    string
	ClientID     string
	RefreshToken string
	AccessToken  string
	Error        string
}

type sunnySubscriptionResult struct {
	SessionID  uint
	AccountID  uint
	Email      string
	Subscribed bool
	Subject    string
	Error      string
}

type sunnySubscriptionATResult struct {
	SessionID uint
	AccountID uint
	Email     string
	Status    string
	PlanType  string
	Error     string
}

var sunnyProbeSubscriptionAT = func(s *Server, candidate sunnySubscriptionCandidate, proxyURL string) sunnySubscriptionATResult {
	return s.sunnySubscriptionProbeAT(candidate, proxyURL)
}

func sunnySubscriptionPlanTypeFromAccessToken(accessToken string) string {
	claims := decodeJWTPayload(strings.TrimSpace(accessToken))
	auth, _ := claims["https://api.openai.com/auth"].(map[string]any)
	return normalizeSunnyPlanType(firstText(
		auth["chatgpt_plan_type"], claims["chatgpt_plan_type"],
		auth["plan_type"], claims["plan_type"], auth["plan"], claims["plan"],
	))
}

func (s *Server) updateSunnySubscriptionPlan(email, planType string) error {
	plan := normalizeSunnyPlanType(planType)
	if plan == "" {
		plan = "free"
	}
	now := time.Now()
	return s.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Model(&SunnyMailbox{}).Where("email = ?", email).Updates(map[string]any{"account_type": plan, "updated_at": now}).Error; err != nil {
			return err
		}
		return tx.Model(&SunnyAccount{}).Where("email = ?", email).Updates(map[string]any{"account_type": plan, "updated_at": now}).Error
	})
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

func (s *Server) detectSunnySubscriptionMail(candidate sunnySubscriptionCandidate, proxyURL string) (bool, string, error) {
	if candidate.MailboxType == "domain" && candidate.Channel == "domain_api" {
		payload, err := s.domainMailLatestMail(candidate.AccessKey, candidate.MailEmail, 5)
		if err != nil {
			return false, "", err
		}
		matched, subject := sunnySubscriptionPayloadConfirmed(payload)
		return matched, subject, nil
	}
	return sunnyDetectSubscriptionMail(candidate, proxyURL)
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
	if err := s.db.Model(&SunnySession{}).Select("id", "account_id", "email", "access_token", "session_json").Where("id IN ?", ids).Order("id asc").Find(&sessions).Error; err != nil {
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
	accountByEmail := map[string]SunnyAccount{}
	for _, mailbox := range mailboxes {
		mailboxByEmail[sunnyEmailKey(mailbox.Email)] = mailbox
	}
	if len(emails) > 0 {
		var accounts []SunnyAccount
		if err := s.db.Select("id", "email", "access_token").Where("email IN ?", emails).Find(&accounts).Error; err != nil {
			return nil, err
		}
		for _, account := range accounts {
			accountByEmail[sunnyEmailKey(account.Email)] = account
		}
	}
	candidates := make([]sunnySubscriptionCandidate, 0, len(sessions))
	for _, session := range sessions {
		mailbox, ok := mailboxByEmail[sunnyEmailKey(session.Email)]
		account := accountByEmail[sunnyEmailKey(session.Email)]
		accountID := session.AccountID
		if accountID == 0 {
			accountID = account.ID
		}
		candidate := sunnySubscriptionCandidate{
			SessionID: session.ID, AccountID: accountID, Email: session.Email, MailEmail: session.Email,
			AccessToken: sunnyPreferredAccessToken(session.AccessToken, sunnyAccessTokenFromSessionJSON(session.SessionJSON), account.AccessToken),
		}
		if !ok {
			candidate.Error = "邮箱凭证不存在"
			candidates = append(candidates, candidate)
			continue
		}
		candidate.Email = mailbox.Email
		candidate.MailEmail = mailbox.Email
		candidate.MailboxType = normalizeSunnyMailboxType(mailbox.MailboxType)
		candidate.Channel = normalizeSunnyMailboxChannel(candidate.MailboxType, mailbox.MailboxChannel)
		candidate.AccessKey = mailbox.AccessKey
		candidate.ClientID = mailbox.ClientID
		candidate.RefreshToken = mailbox.RefreshToken
		if strings.TrimSpace(mailbox.RebindEmail) != "" || strings.TrimSpace(mailbox.RebindMailboxAPI) != "" {
			if strings.TrimSpace(mailbox.RebindEmail) == "" || strings.TrimSpace(mailbox.RebindMailboxAPI) == "" {
				candidate.Error = "换绑邮箱凭证不完整"
			} else {
				candidate.MailEmail = strings.TrimSpace(mailbox.RebindEmail)
				candidate.MailboxType = "domain"
				candidate.Channel = "domain_api"
				candidate.AccessKey = strings.TrimSpace(mailbox.RebindMailboxAPI)
				candidate.ClientID = ""
				candidate.RefreshToken = ""
			}
		}
		if (candidate.MailboxType == "apple" && strings.TrimSpace(candidate.AccessKey) == "") ||
			(candidate.MailboxType == "microsoft" && (strings.TrimSpace(candidate.ClientID) == "" || strings.TrimSpace(candidate.RefreshToken) == "")) {
			candidate.Error = "邮箱凭证不完整"
		}
		candidates = append(candidates, candidate)
	}
	return candidates, nil
}

func (s *Server) sunnySubscriptionProbeAT(candidate sunnySubscriptionCandidate, proxyURL string) sunnySubscriptionATResult {
	outcome := sunnySubscriptionATResult{SessionID: candidate.SessionID, AccountID: candidate.AccountID, Email: candidate.Email}
	if strings.TrimSpace(candidate.AccessToken) == "" {
		outcome.Status = "invalid"
		outcome.Error = "账户没有可用的 Access Token"
		return outcome
	}
	meter := &sunnyTrafficMeter{}
	status, probeErr := s.sunnyProbeAccessToken(candidate.AccessToken, proxyURL, meter)
	s.recordSunnyProxyTraffic(candidate.Email, meter.totalBytes())
	outcome.Status = status
	if probeErr != nil {
		outcome.Error = probeErr.Error()
	}
	if status == "valid" {
		outcome.PlanType = sunnySubscriptionPlanTypeFromAccessToken(candidate.AccessToken)
	}
	return outcome
}

func (s *Server) sunnySubscriptionRenewalTimeout() time.Duration {
	seconds := intValue(strings.TrimSpace(os.Getenv("SUNNY_SUBSCRIPTION_RENEWAL_TIMEOUT_SECONDS")), 20*60)
	if seconds < 30 {
		seconds = 30
	}
	if seconds > 60*60 {
		seconds = 60 * 60
	}
	return time.Duration(seconds) * time.Second
}

func (s *Server) waitSunnySubscriptionRenewal(taskID string) (Task, error) {
	if strings.TrimSpace(taskID) == "" {
		return Task{}, fmt.Errorf("未创建 AT 续期任务")
	}
	deadline := time.Now().Add(s.sunnySubscriptionRenewalTimeout())
	for time.Now().Before(deadline) {
		var task Task
		if err := s.db.First(&task, "id = ?", taskID).Error; err != nil {
			return Task{}, fmt.Errorf("读取 AT 续期任务失败：%w", err)
		}
		if terminalTaskStatuses[task.Status] {
			return task, nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return Task{}, fmt.Errorf("AT 续期任务等待超时（超过 %s）", s.sunnySubscriptionRenewalTimeout().Round(time.Second))
}

func (s *Server) activeSunnyRenewalTaskForAccounts(accountIDs []uint) Task {
	requested := map[uint]bool{}
	for _, accountID := range accountIDs {
		if accountID != 0 {
			requested[accountID] = true
		}
	}
	if len(requested) == 0 {
		return Task{}
	}
	var tasks []Task
	if err := s.db.Where("type = ? AND status NOT IN ?", "sunny_refresh_session", []string{TaskSucceeded, TaskFailed, TaskInterrupted, TaskCancelled}).Order("created_at asc").Find(&tasks).Error; err != nil {
		return Task{}
	}
	for _, task := range tasks {
		for _, accountID := range uintSlice(jsonMap(task.PayloadJSON)["account_ids"]) {
			if requested[accountID] {
				return task
			}
		}
	}
	return Task{}
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

func (s *Server) sunnySubscriptionBatchSize() int {
	return sunnyDetectionBatchSize("SUNNY_SUBSCRIPTION_BATCH_SIZE", 20, 100)
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
	items := make([]any, 0, len(candidates))
	candidateBySession := make(map[uint]sunnySubscriptionCandidate, len(candidates))
	for _, candidate := range candidates {
		candidateBySession[candidate.SessionID] = candidate
	}
	record := func(item map[string]any) {
		items = append(items, item)
		task.ProgressCurrent++
		s.db.Model(&Task{}).Where("id = ?", task.ID).Updates(map[string]any{"progress_current": task.ProgressCurrent, "updated_at": time.Now()})
	}
	confirmPlan := func(email, planType string, item map[string]any, detail map[string]any) {
		plan := normalizeSunnyPlanType(planType)
		if plan == "" {
			plan = "free"
		}
		item["plan_type"] = plan
		if updateErr := s.updateSunnySubscriptionPlan(email, plan); updateErr != nil {
			result["failed"] = result["failed"].(int) + 1
			item["status"] = "failed"
			item["error"] = updateErr.Error()
			s.appendAccountTaskEvent(task.ID, email, "subscription", "subscription.check_failed", fmt.Sprintf("账户 %s 订阅结果保存失败：%s", email, updateErr), "warning", map[string]any{"error": updateErr.Error()})
			return
		}
		if plan == "plus" {
			result["subscribed"] = result["subscribed"].(int) + 1
			item["status"] = "subscribed"
			s.appendAccountTaskEvent(task.ID, email, "subscription", "subscription.confirmed", fmt.Sprintf("账户 %s 已确认订阅成功，套餐已更新为 Plus", email), "info", detail)
			return
		}
		result["not_subscribed"] = result["not_subscribed"].(int) + 1
		item["status"] = "not_subscribed"
		s.appendAccountTaskEvent(task.ID, email, "subscription", "subscription.not_found", fmt.Sprintf("账户 %s 当前套餐为 %s，未订阅 Plus", email, plan), "info", detail)
	}

	noMailCandidates := make([]sunnySubscriptionCandidate, 0, len(candidates))
	batchSize := s.sunnySubscriptionBatchSize()
	concurrency := s.sunnySubscriptionConcurrency()
	for start := 0; start < len(candidates); start += batchSize {
		end := start + batchSize
		if end > len(candidates) {
			end = len(candidates)
		}
		results := streamSunnyDetectionBatch(candidates[start:end], concurrency, func(candidate sunnySubscriptionCandidate) sunnySubscriptionResult {
			if candidate.Error != "" {
				return sunnySubscriptionResult{SessionID: candidate.SessionID, Email: candidate.Email, Error: candidate.Error}
			}
			subscribed, subject, detectErr := s.detectSunnySubscriptionMail(candidate, proxyURL)
			outcome := sunnySubscriptionResult{SessionID: candidate.SessionID, Email: candidate.Email, Subscribed: subscribed, Subject: subject}
			if detectErr != nil {
				outcome.Error = detectErr.Error()
			}
			return outcome
		})
		for outcome := range results {
			item := map[string]any{"email": outcome.Email}
			if outcome.Error != "" {
				candidate := candidateBySession[outcome.SessionID]
				if strings.TrimSpace(candidate.AccessToken) != "" {
					noMailCandidates = append(noMailCandidates, candidate)
					s.appendAccountTaskEvent(task.ID, outcome.Email, "subscription", "subscription.mail_fallback", fmt.Sprintf("账户 %s 邮箱订阅检测未完成，改用 AT 兜底：%s", outcome.Email, outcome.Error), "warning", map[string]any{"error": outcome.Error})
					continue
				}
				result["failed"] = result["failed"].(int) + 1
				item["status"] = "failed"
				item["error"] = outcome.Error
				s.appendAccountTaskEvent(task.ID, outcome.Email, "subscription", "subscription.check_failed", fmt.Sprintf("账户 %s 订阅检测失败：%s", outcome.Email, outcome.Error), "warning", map[string]any{"error": outcome.Error})
				record(item)
				continue
			}
			if outcome.Subscribed {
				item["subject"] = outcome.Subject
				confirmPlan(outcome.Email, "plus", item, map[string]any{"subject": outcome.Subject, "source": "mail"})
				record(item)
				continue
			}
			noMailCandidates = append(noMailCandidates, candidateBySession[outcome.SessionID])
		}
	}

	// Mailbox lookup remains the first source of truth. Only accounts with no
	// matching mail reach the AT fallback, which avoids renewing accounts that
	// already have a definitive subscription confirmation.
	invalidForRenewal := make([]sunnySubscriptionATResult, 0)
	for start := 0; start < len(noMailCandidates); start += batchSize {
		end := start + batchSize
		if end > len(noMailCandidates) {
			end = len(noMailCandidates)
		}
		results := streamSunnyDetectionBatch(noMailCandidates[start:end], concurrency, func(candidate sunnySubscriptionCandidate) sunnySubscriptionATResult {
			if candidate.Error != "" {
				return sunnySubscriptionATResult{SessionID: candidate.SessionID, AccountID: candidate.AccountID, Email: candidate.Email, Status: "failed", Error: candidate.Error}
			}
			return sunnyProbeSubscriptionAT(s, candidate, proxyURL)
		})
		for outcome := range results {
			item := map[string]any{"email": outcome.Email, "source": "access_token"}
			switch outcome.Status {
			case "valid":
				confirmPlan(outcome.Email, outcome.PlanType, item, map[string]any{"source": "access_token"})
				record(item)
			case "invalid":
				invalidForRenewal = append(invalidForRenewal, outcome)
				s.appendAccountTaskEvent(task.ID, outcome.Email, "subscription", "subscription.at_invalid", fmt.Sprintf("账户 %s 邮件未检测到订阅，AT 已失效，准备续期", outcome.Email), "warning", map[string]any{"error": outcome.Error})
			default:
				result["failed"] = result["failed"].(int) + 1
				item["status"] = "failed"
				item["error"] = outcome.Error
				s.appendAccountTaskEvent(task.ID, outcome.Email, "subscription", "subscription.check_failed", fmt.Sprintf("账户 %s AT 兜底检测失败：%s", outcome.Email, fallback(outcome.Error, "未得到有效 AT 响应")), "warning", map[string]any{"error": outcome.Error, "status": outcome.Status})
				record(item)
			}
		}
	}

	if len(invalidForRenewal) > 0 {
		accountIDs := make([]uint, 0, len(invalidForRenewal))
		seen := map[uint]bool{}
		for _, outcome := range invalidForRenewal {
			if outcome.AccountID != 0 && !seen[outcome.AccountID] {
				seen[outcome.AccountID] = true
				accountIDs = append(accountIDs, outcome.AccountID)
			}
		}
		renewalTask := s.createSunnyAccessTokenRenewalTask(task, "subscription_check", accountIDs)
		if renewalTask.ID == "" {
			renewalTask = s.activeSunnyRenewalTaskForAccounts(accountIDs)
		}
		var renewalTaskError string
		if renewalTask.ID == "" {
			renewalTaskError = "未能创建或找到 AT 续期任务"
		} else if completedTask, waitErr := s.waitSunnySubscriptionRenewal(renewalTask.ID); waitErr != nil {
			renewalTaskError = waitErr.Error()
		} else if completedTask.Status != TaskSucceeded {
			renewalTaskError = strings.TrimSpace(completedTask.Error)
			if renewalTaskError == "" {
				renewalTaskError = "AT 续期任务部分或全部失败"
			}
		}
		result["renewal_task_id"] = renewalTask.ID
		result["renewal_queued"] = len(accountIDs)

		for _, outcome := range invalidForRenewal {
			item := map[string]any{"email": outcome.Email, "source": "access_token_renewal"}
			previousToken := strings.TrimSpace(candidateBySession[outcome.SessionID].AccessToken)
			var session SunnySession
			loadErr := s.db.Where("id = ?", outcome.SessionID).First(&session).Error
			token := ""
			if loadErr == nil {
				token = sunnyPreferredAccessToken(session.AccessToken, sunnyAccessTokenFromSessionJSON(session.SessionJSON))
			}
			var account SunnyAccount
			var mailbox SunnyMailbox
			s.db.Where("email = ?", outcome.Email).First(&account)
			token = sunnyPreferredAccessToken(token, account.AccessToken)
			s.db.Where("email = ?", outcome.Email).First(&mailbox)
			if sunnyHealthBannedStatus(account.Status) || sunnyHealthBannedStatus(mailbox.Status) {
				result["failed"] = result["failed"].(int) + 1
				item["status"] = "failed"
				item["error"] = "账户已封禁"
				s.appendAccountTaskEvent(task.ID, outcome.Email, "subscription", "subscription.banned", fmt.Sprintf("账户 %s 已被封禁，结束订阅检测", outcome.Email), "warning", nil)
				record(item)
				continue
			}
			if loadErr != nil || strings.TrimSpace(token) == "" || (renewalTaskError != "" && strings.TrimSpace(token) == previousToken) {
				result["failed"] = result["failed"].(int) + 1
				item["status"] = "failed"
				item["error"] = fallback(renewalTaskError, "AT 续期后未获取到新的 Access Token")
				s.appendAccountTaskEvent(task.ID, outcome.Email, "subscription", "subscription.renewal_failed", fmt.Sprintf("账户 %s AT 续期后仍无法获取有效 AT：%s", outcome.Email, item["error"]), "warning", map[string]any{"renewal_task_id": renewalTask.ID})
				record(item)
				continue
			}
			confirmPlan(outcome.Email, sunnySubscriptionPlanTypeFromAccessToken(token), item, map[string]any{"source": "access_token_renewal", "renewal_task_id": renewalTask.ID})
			record(item)
		}
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
