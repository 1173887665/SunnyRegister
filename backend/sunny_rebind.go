package main

import (
	"fmt"
	"strings"
)

func (s *Server) createSunnyRebindTask(body map[string]any) (Task, error) {
	source := strings.ToLower(strings.TrimSpace(text(body["rebind_source"])))
	if source == "imported" {
		targetEmail := strings.TrimSpace(text(body["target_email"]))
		targetAPI := strings.TrimSpace(text(body["target_mailbox_api"]))
		targetType := normalizeSunnyMailboxType(text(body["target_mailbox_type"]))
		targetChannel := normalizeSunnyMailboxChannel(targetType, text(body["target_mailbox_channel"]))
		// A selected mailbox pool is assigned across the batch by the worker.
		// Keep the first target fields for compatibility with older workers.
		if rawTargets, ok := body["target_mailboxes"].([]any); ok && len(rawTargets) > 0 {
			validated := make([]map[string]any, 0, len(rawTargets))
			for _, raw := range rawTargets {
				item, ok := raw.(map[string]any)
				if !ok {
					continue
				}
				email := strings.TrimSpace(text(item["email"]))
				api := strings.TrimSpace(text(item["mailbox_api"]))
				typ := normalizeSunnyMailboxType(text(item["mailbox_type"]))
				channel := normalizeSunnyMailboxChannel(typ, text(item["mailbox_channel"]))
				if email == "" || api == "" {
					continue
				}
				if typ == "domain" && isSunnyHTTPURL(api) {
					detectedType, detectedChannel, classifyErr := classifySunnyRebindMailboxCredential(api, email)
					if classifyErr != nil {
						return Task{}, classifyErr
					}
					typ, channel = detectedType, detectedChannel
				}
				if err := validateImportedRebindMailbox(api, email, typ, channel); err != nil {
					return Task{}, err
				}
				validated = append(validated, map[string]any{"email": email, "mailbox_api": api, "mailbox_type": typ, "mailbox_channel": channel})
			}
			if len(validated) == 0 {
				return Task{}, fmt.Errorf("请选择有有效取件凭证的已导入邮箱")
			}
			body["target_mailboxes"] = validated
			targetEmail = text(validated[0]["email"])
			targetAPI = text(validated[0]["mailbox_api"])
			targetType = normalizeSunnyMailboxType(text(validated[0]["mailbox_type"]))
			targetChannel = normalizeSunnyMailboxChannel(targetType, text(validated[0]["mailbox_channel"]))
		}
		if targetEmail == "" || targetAPI == "" {
			return Task{}, fmt.Errorf("请选择有有效取件凭证的已导入邮箱")
		}
		if targetType == "domain" && isSunnyHTTPURL(targetAPI) {
			detectedType, detectedChannel, err := classifySunnyRebindMailboxCredential(targetAPI, targetEmail)
			if err != nil {
				return Task{}, err
			}
			targetType, targetChannel = detectedType, detectedChannel
		}
		if _, hasPool := body["target_mailboxes"]; !hasPool {
			if err := validateImportedRebindMailbox(targetAPI, targetEmail, targetType, targetChannel); err != nil {
				return Task{}, err
			}
		}
		body["rebind_source"] = "imported"
		body["target_email"] = targetEmail
		body["target_mailbox_api"] = targetAPI
		body["target_mailbox_type"] = targetType
		body["target_mailbox_channel"] = targetChannel
	} else {
		cfg := mergeConfig(defaultDomainMailboxConfig(), s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig()))
		if !boolValue(cfg["enabled"], true) {
			return Task{}, fmt.Errorf("自建域名邮箱池已关闭，请先在邮箱配置中启用")
		}
		if !boolValue(cfg["enabled_for_rebinding"], false) {
			return Task{}, fmt.Errorf("自建域名邮箱未启用邮箱换绑，请先在邮箱配置中启用")
		}
		domains, err := domainMailboxDomains(cfg)
		if err != nil {
			return Task{}, err
		}
		if domainMailboxProvider(cfg) == "moemail" {
			if _, err := newMoeMailClient(cfg); err != nil {
				return Task{}, err
			}
		} else if _, err := newDomainMailClient(cfg); err != nil {
			return Task{}, fmt.Errorf("自建域名邮箱配置不完整，请先配置 CloudMail API、PUBLIC_API_TOKEN 和域名：%w", err)
		}
		if _, err := domainMailboxPickupBaseURL(cfg); err != nil {
			return Task{}, err
		}
		body["rebind_source"] = "self"
		// Snapshot only the non-secret pool selection. The Python worker can then
		// finish a long task consistently even if the mailbox settings are edited.
		body["domain_mailbox_domains"] = domains
	}
	sessionIDs := uintSlice(body["session_ids"])
	accountIDs := uintSlice(body["account_ids"])
	if len(accountIDs) == 0 && len(sessionIDs) > 0 {
		var sessions []SunnySession
		if err := s.db.Select("id", "account_id", "email").Where("id IN ?", sessionIDs).Find(&sessions).Error; err != nil {
			return Task{}, err
		}
		seen := map[uint]bool{}
		for _, session := range sessions {
			accountID := session.AccountID
			if accountID == 0 {
				var account SunnyAccount
				if s.db.Select("id").Where("LOWER(email) = ?", sunnyEmailKey(session.Email)).First(&account).Error == nil {
					accountID = account.ID
				}
			}
			if accountID != 0 && !seen[accountID] {
				seen[accountID] = true
				accountIDs = append(accountIDs, accountID)
			}
		}
	}
	if len(accountIDs) == 0 {
		return Task{}, fmt.Errorf("请选择需要换绑的账户")
	}
	body["account_ids"] = accountIDs
	body["session_ids"] = sessionIDs
	body["concurrency"] = s.sunnyRebindConcurrency()
	body = s.sunnyTaskProxySnapshot(body)
	return s.createTask("sunny_rebind", "sunny", body, len(accountIDs)), nil
}

func validateImportedRebindMailbox(api, email, mailboxType, mailboxChannel string) error {
	switch {
	case mailboxType == "microsoft":
		parts := strings.Split(strings.TrimSpace(api), "----")
		if len(parts) != 3 || strings.TrimSpace(parts[0]) == "" || strings.TrimSpace(parts[1]) == "" || strings.TrimSpace(parts[2]) == "" {
			return fmt.Errorf("微软邮箱换绑凭证格式必须为 password----client_id----refresh_token")
		}
		return nil
	case mailboxType == "domain":
		return validateDomainMailboxAccessKey(api, email)
	case mailboxType == "apple" && mailboxChannel == "url_api":
		_, err := validateURLAPIMailAddress(api)
		return err
	case mailboxType == "remail":
		if strings.TrimSpace(api) == "" {
			return fmt.Errorf("Remail 取件凭证为空")
		}
		return nil
	default:
		return fmt.Errorf("该邮箱类目需要可调用的取件 URL")
	}
}
