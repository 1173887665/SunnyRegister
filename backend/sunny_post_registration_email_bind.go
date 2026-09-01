package main

import (
	"fmt"
	"strings"
)

const (
	sunnyEmailBindEnabledKey  = "bind_email_after_registration"
	sunnyEmailBindCategoryKey = "bind_mailbox_category"
	sunnyEmailBindTargetsKey  = "bind_target_mailboxes"
)

// prepareSunnyPostRegistrationEmailBind validates the SunnyRegister phone-
// registration email-binding contract. It deliberately uses its own payload
// keys so this flow cannot be mistaken for the generic account rebind task.
func prepareSunnyPostRegistrationEmailBind(body map[string]any, mailboxCount int) error {
	if !boolValue(body[sunnyEmailBindEnabledKey], false) {
		body[sunnyEmailBindEnabledKey] = false
		delete(body, sunnyEmailBindCategoryKey)
		delete(body, sunnyEmailBindTargetsKey)
		return nil
	}
	category := strings.ToLower(strings.TrimSpace(text(body[sunnyEmailBindCategoryKey])))
	if category == "" {
		return fmt.Errorf("请选择注册后绑定邮箱类目")
	}
	rawTargets, ok := body[sunnyEmailBindTargetsKey].([]any)
	if !ok || len(rawTargets) < mailboxCount {
		return fmt.Errorf("所选绑定邮箱数量不足")
	}
	validatedTargets := make([]map[string]any, 0, len(rawTargets))
	for _, raw := range rawTargets {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		email, api := strings.TrimSpace(text(item["email"])), strings.TrimSpace(text(item["mailbox_api"]))
		typ := normalizeSunnyMailboxType(text(item["mailbox_type"]))
		channel := normalizeSunnyMailboxChannel(typ, text(item["mailbox_channel"]))
		itemCategory := typ
		if typ == "apple" && channel == "url_api" && !strings.HasSuffix(strings.ToLower(email), "@icloud.com") {
			itemCategory = "generic"
		}
		if itemCategory != category || email == "" || api == "" {
			continue
		}
		if err := validateImportedRebindMailbox(api, email, typ, channel); err != nil {
			return err
		}
		validatedTargets = append(validatedTargets, map[string]any{
			"email": email, "mailbox_api": api, "mailbox_type": typ, "mailbox_channel": channel,
		})
	}
	if len(validatedTargets) < mailboxCount {
		return fmt.Errorf("所选绑定邮箱没有有效取件凭证")
	}
	body[sunnyEmailBindTargetsKey] = validatedTargets
	body[sunnyEmailBindCategoryKey] = category
	return nil
}
