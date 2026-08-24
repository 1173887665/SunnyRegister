package main

import (
	"fmt"
	"strings"
)

func (s *Server) createSunnyRebindTask(body map[string]any) (Task, error) {
	cfg := mergeConfig(defaultDomainMailboxConfig(), s.sunnyGetConfig(sunnyCfgDomainMailbox, defaultDomainMailboxConfig()))
	if !boolValue(cfg["enabled"], true) {
		return Task{}, fmt.Errorf("自建域名邮箱池已关闭，请先在邮箱配置中启用")
	}
	if !boolValue(cfg["enabled_for_rebinding"], false) {
		return Task{}, fmt.Errorf("自建域名邮箱未启用邮箱换绑，请先在邮箱配置中启用")
	}
	if strings.TrimSpace(text(cfg["base_url"])) == "" || strings.TrimSpace(text(cfg["auth_token"])) == "" || strings.TrimSpace(text(cfg["domain"])) == "" {
		return Task{}, fmt.Errorf("自建域名邮箱配置不完整，请先配置 API 地址、Token 和域名")
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
	body = s.sunnyTaskProxySnapshot(body)
	return s.createTask("sunny_rebind", "sunny", body, len(accountIDs)), nil
}
