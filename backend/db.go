package main

import (
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func openDB() *gorm.DB {
	raw := os.Getenv("ACCOUNT_MANAGER_DATABASE_URL")
	if raw == "" {
		raw = os.Getenv("ACCOUNT_MANAGER_DB")
	}
	path := normalizeDatabasePath(raw)
	if err := ensureDir(path); err != nil {
		log.Fatalf("create database dir failed: %v", err)
	}
	gormLogger := logger.New(
		log.New(os.Stdout, "", log.LstdFlags),
		logger.Config{
			SlowThreshold:             200 * time.Millisecond,
			LogLevel:                  logger.Warn,
			IgnoreRecordNotFoundError: true,
			Colorful:                  true,
		},
	)
	db, err := gorm.Open(sqlite.Open(path), &gorm.Config{Logger: gormLogger})
	if err != nil {
		log.Fatalf("open sqlite failed: %v", err)
	}
	sqlDB, _ := db.DB()
	if sqlDB != nil {
		maxOpen := intValue(os.Getenv("SUNNY_DB_MAX_OPEN_CONNS"), 4)
		if maxOpen < 1 {
			maxOpen = 1
		}
		sqlDB.SetMaxOpenConns(maxOpen)
		sqlDB.SetMaxIdleConns(maxOpen)
		sqlDB.SetConnMaxIdleTime(5 * time.Minute)
	}
	db.Exec("PRAGMA busy_timeout=5000")
	db.Exec("PRAGMA journal_mode=WAL")
	db.Exec("PRAGMA synchronous=NORMAL")
	db.Exec("PRAGMA temp_store=MEMORY")
	db.Exec("PRAGMA cache_size=-32768")
	db.Exec("PRAGMA wal_autocheckpoint=1000")
	db.Exec("PRAGMA foreign_keys=ON")
	if err := db.AutoMigrate(
		&ConfigItem{}, &Account{}, &AccountOverview{}, &AccountCredential{},
		&ProviderAccount{}, &ProviderResource{}, &ProviderDefinition{}, &ProviderSetting{},
		&PlatformCapabilityOverride{}, &TaskLog{}, &Task{}, &TaskEvent{}, &Proxy{}, &SmsPoolBlacklist{},
		&SunnyMailboxGroup{}, &SunnyMailbox{}, &SunnyPhone{}, &SunnyProxy{}, &SunnyAccount{},
		&SunnySession{}, &SunnyKVConfig{}, &SunnySMSProviderOption{},
		&AuditLog{}, &AuditSetting{}, &AuditExportJob{},
	); err != nil {
		log.Fatalf("migrate sqlite failed: %v", err)
	}
	ensureSunnySchema(db)
	ensureShanghaiTimestampStorage(db)
	ensureSunnyStatusTriggers(db)
	ensureSunnyIndexes(db)
	sanitizeHistoricalTaskData(db)
	return db
}

func ensureSunnyStatusTriggers(db *gorm.DB) {
	for _, table := range []string{"sunny_mailboxes", "sunny_accounts"} {
		updateTrigger := "trg_" + table + "_status_changed"
		db.Exec("DROP TRIGGER IF EXISTS " + updateTrigger)
		updateStatement := "CREATE TRIGGER IF NOT EXISTS " + updateTrigger +
			" AFTER UPDATE OF status ON " + table +
			" WHEN OLD.status IS NOT NEW.status BEGIN UPDATE " + table +
			" SET status_changed_at = CASE WHEN NEW.updated_at IS NOT OLD.updated_at THEN NEW.updated_at ELSE strftime('%Y-%m-%d %H:%M:%S','now','+8 hours') || '+08:00' END" +
			" WHERE id = NEW.id; END"
		if err := db.Exec(updateStatement).Error; err != nil {
			log.Printf("create status timestamp trigger failed: %v", err)
		}
		insertTrigger := "trg_" + table + "_status_created"
		db.Exec("DROP TRIGGER IF EXISTS " + insertTrigger)
		insertStatement := "CREATE TRIGGER IF NOT EXISTS " + insertTrigger +
			" AFTER INSERT ON " + table +
			" WHEN NEW.status_changed_at IS NULL BEGIN UPDATE " + table +
			" SET status_changed_at = COALESCE(NEW.created_at, NEW.updated_at, strftime('%Y-%m-%d %H:%M:%S','now','+8 hours') || '+08:00') WHERE id = NEW.id; END"
		if err := db.Exec(insertStatement).Error; err != nil {
			log.Printf("create initial status timestamp trigger failed: %v", err)
		}
	}
}

type sqliteTableName struct {
	Name string `gorm:"column:name"`
}

type sqliteColumnInfo struct {
	Name string `gorm:"column:name"`
	Type string `gorm:"column:type"`
}

func quoteSQLiteIdentifier(value string) string {
	return `"` + strings.ReplaceAll(value, `"`, `""`) + `"`
}

// Older worker builds wrote Shanghai wall-clock values without an offset. The
// SQLite driver parses those values as UTC, so make the intended timezone
// explicit before values are read by GORM.
func ensureShanghaiTimestampStorage(db *gorm.DB) {
	const migrationKey = "timezone_storage_asia_shanghai_v1"
	var migrated int64
	if db.Model(&SunnyKVConfig{}).Where("key = ?", migrationKey).Count(&migrated).Error == nil && migrated > 0 {
		return
	}
	var tables []sqliteTableName
	if err := db.Raw("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").Scan(&tables).Error; err != nil {
		log.Printf("list timestamp tables failed: %v", err)
		return
	}
	for _, table := range tables {
		var columns []sqliteColumnInfo
		if err := db.Raw("PRAGMA table_info(" + quoteSQLiteIdentifier(table.Name) + ")").Scan(&columns).Error; err != nil {
			log.Printf("list timestamp columns for %s failed: %v", table.Name, err)
			continue
		}
		for _, column := range columns {
			columnType := strings.ToLower(strings.TrimSpace(column.Type))
			if !strings.Contains(columnType, "date") && !strings.Contains(columnType, "time") {
				continue
			}
			tableName := quoteSQLiteIdentifier(table.Name)
			columnName := quoteSQLiteIdentifier(column.Name)
			statement := fmt.Sprintf(`UPDATE %s SET %s = trim(%s) || '+08:00'
				WHERE typeof(%s) = 'text' AND length(trim(%s)) >= 16
				AND (substr(trim(%s), 11, 1) = ' ' OR substr(trim(%s), 11, 1) = 'T')
				AND upper(substr(trim(%s), -1, 1)) <> 'Z'
				AND instr(substr(trim(%s), 11), '+') = 0
				AND instr(substr(trim(%s), 11), '-') = 0`,
				tableName, columnName, columnName, columnName, columnName,
				columnName, columnName, columnName, columnName, columnName)
			if err := db.Exec(statement).Error; err != nil {
				log.Printf("normalize Shanghai timestamps for %s.%s failed: %v", table.Name, column.Name, err)
			}
		}
	}
	now := time.Now().In(applicationLocation())
	marker := SunnyKVConfig{Key: migrationKey, ValueJSON: `{"timezone":"Asia/Shanghai"}`, CreatedAt: now, UpdatedAt: now}
	if err := db.Create(&marker).Error; err != nil && !strings.Contains(strings.ToLower(err.Error()), "unique") {
		log.Printf("save Shanghai timestamp migration marker failed: %v", err)
	}
}

func ensureSunnyIndexes(db *gorm.DB) {
	indexes := []string{
		"CREATE INDEX IF NOT EXISTS idx_sunny_mailboxes_enabled_updated ON sunny_mailboxes(enabled, updated_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_mailboxes_group_status_enabled ON sunny_mailboxes(group_id, status, enabled)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_sessions_updated ON sunny_sessions(updated_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_sessions_email ON sunny_sessions(email)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_accounts_email ON sunny_accounts(email)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_accounts_health_checked ON sunny_accounts(last_health_checked_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_accounts_checkout_kind ON sunny_accounts(checkout_kind)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_accounts_commerce_checked ON sunny_accounts(commerce_checked_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_mailboxes_health_checked ON sunny_mailboxes(last_health_checked_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_accounts_status_changed ON sunny_accounts(status_changed_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_mailboxes_status_changed ON sunny_mailboxes(status_changed_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_proxies_status_enabled_checked ON sunny_proxies(status, enabled, last_checked_at)",
		"CREATE INDEX IF NOT EXISTS idx_sunny_proxies_country_status ON sunny_proxies(country, status)",
		"CREATE INDEX IF NOT EXISTS idx_task_events_task_id_id ON task_events(task_id, id)",
		"CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at)",
		"CREATE INDEX IF NOT EXISTS idx_audit_logs_time_type ON audit_logs(occurred_at DESC, log_type)",
		"CREATE INDEX IF NOT EXISTS idx_audit_logs_category_action ON audit_logs(category, action, occurred_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_ip ON audit_logs(actor, ip, occurred_at DESC)",
		"CREATE INDEX IF NOT EXISTS idx_audit_logs_task_entity ON audit_logs(task_id, entity_type, entity_id)",
		"CREATE INDEX IF NOT EXISTS idx_audit_export_jobs_status_created ON audit_export_jobs(status, created_at DESC)",
	}
	for _, statement := range indexes {
		if err := db.Exec(statement).Error; err != nil {
			log.Printf("create performance index failed: %v", err)
		}
	}
}

func sanitizeHistoricalTaskData(db *gorm.DB) {
	var events []TaskEvent
	if db.Select("id", "message", "detail_json").Find(&events).Error == nil {
		for _, event := range events {
			message := sanitizePersistedString(event.Message)
			detail := sanitizePersistedJSON(event.DetailJSON)
			if message != event.Message || detail != event.DetailJSON {
				db.Model(&TaskEvent{}).Where("id = ?", event.ID).Updates(map[string]any{"message": message, "detail_json": detail})
			}
		}
	}
	var tasks []Task
	terminal := []string{"succeeded", "failed", "cancelled", "interrupted"}
	if db.Select("id", "status", "payload_json", "result_json", "error").Where("status IN ?", terminal).Find(&tasks).Error == nil {
		for _, task := range tasks {
			payload := sanitizePersistedJSON(task.PayloadJSON)
			result := sanitizePersistedJSON(task.ResultJSON)
			errorText := sanitizePersistedString(task.Error)
			if payload != task.PayloadJSON || result != task.ResultJSON || errorText != task.Error {
				db.Model(&Task{}).Where("id = ?", task.ID).Updates(map[string]any{"payload_json": payload, "result_json": result, "error": errorText})
			}
		}
	}
}

func ensureSunnySchema(db *gorm.DB) {
	required := map[string]map[string]string{
		"sunny_accounts": {
			"mailbox_id":             "integer DEFAULT 0",
			"group_name":             "text DEFAULT ''",
			"status":                 "text DEFAULT 'pending'",
			"account_type":           "text DEFAULT 'free'",
			"trial_eligibility":      "text DEFAULT 'unknown'",
			"trial_check_error":      "text DEFAULT ''",
			"trial_checked_at":       "datetime",
			"openai_rt":              "text DEFAULT ''",
			"access_token":           "text DEFAULT ''",
			"phone_number":           "text DEFAULT ''",
			"sub2api_status":         "text DEFAULT ''",
			"sub2api_id":             "text DEFAULT ''",
			"last_error":             "text DEFAULT ''",
			"metadata_json":          "text DEFAULT '{}'",
			"last_health_checked_at": "datetime",
			"status_changed_at":      "datetime",
		},
		"sunny_mailboxes": {
			"chat_gpt_password":      "text DEFAULT ''",
			"totp_secret":            "text DEFAULT ''",
			"openai_rt":              "text DEFAULT ''",
			"trial_eligibility":      "text DEFAULT 'unknown'",
			"trial_check_error":      "text DEFAULT ''",
			"trial_checked_at":       "datetime",
			"registered_at":          "datetime",
			"last_error":             "text DEFAULT ''",
			"last_health_checked_at": "datetime",
			"status_changed_at":      "datetime",
		},
		"sunny_sessions": {
			"refresh_token":           "text DEFAULT ''",
			"id_token":                "text DEFAULT ''",
			"session_json":            "text DEFAULT '{}'",
			"storage_state_json":      "text DEFAULT '{}'",
			"raw_mailbox_line":        "text DEFAULT ''",
			"access_token_status":     "text DEFAULT 'unknown'",
			"access_token_error":      "text DEFAULT ''",
			"access_token_checked_at": "datetime",
			"health_check_status":     "text DEFAULT 'unknown'",
			"health_check_error":      "text DEFAULT ''",
			"last_refresh_at":         "datetime",
		},
	}
	for table, cols := range required {
		for col, ddl := range cols {
			if !db.Migrator().HasColumn(table, col) {
				db.Exec("ALTER TABLE " + table + " ADD COLUMN " + col + " " + ddl)
			}
		}
	}
	for _, table := range []string{"sunny_accounts", "sunny_mailboxes"} {
		if db.Migrator().HasColumn(table, "open_airt") && db.Migrator().HasColumn(table, "openai_rt") {
			db.Exec("UPDATE " + table + " SET openai_rt = open_airt WHERE coalesce(openai_rt,'') = '' AND coalesce(open_airt,'') <> ''")
		}
		db.Exec("UPDATE " + table + " SET status_changed_at = updated_at WHERE status_changed_at IS NULL")
	}
	db.Exec("UPDATE sunny_mailboxes SET status = '已接码' WHERE status = 'PLUS试用中'")
	db.Exec("UPDATE sunny_accounts SET status = 'phone_bound' WHERE status = 'PLUS试用中'")
}

func seedProviderDefinitions(db *gorm.DB) {
	seeds := []map[string]any{
		{
			"provider_type": "mailbox", "provider_key": "cfworker_admin_api", "label": "CF Worker / cloud-mail", "description": "Cloudflare Worker 自建域名邮箱", "driver_type": "cfworker_admin_api", "default_auth_mode": "token", "category": "selfhost",
			"auth_modes": []map[string]any{{"value": "token", "label": "Token 认证"}},
			"fields":     []map[string]any{{"key": "cfworker_api_url", "label": "API 地址", "placeholder": "https://your-worker.example.com", "category": "connection"}, {"key": "cfworker_admin_token", "label": "Admin Token", "secret": true, "category": "auth"}, {"key": "cfworker_domain", "label": "邮箱域名", "placeholder": "example.com", "category": "connection"}},
		},
		{
			"provider_type": "mailbox", "provider_key": "moemail_api", "label": "MoeMail", "description": "自部署临时邮箱服务", "driver_type": "moemail_api", "default_auth_mode": "password", "category": "selfhost",
			"auth_modes": []map[string]any{{"value": "password", "label": "账号密码"}, {"value": "token", "label": "Session Token"}},
			"fields":     []map[string]any{{"key": "moemail_api_url", "label": "API 地址", "placeholder": "https://moemail.example.com", "category": "connection"}, {"key": "moemail_username", "label": "用户名", "category": "auth"}, {"key": "moemail_password", "label": "密码", "secret": true, "category": "auth"}, {"key": "moemail_session_token", "label": "Session Token", "secret": true, "category": "auth"}},
		},
		{"provider_type": "mailbox", "provider_key": "tempmail_lol_api", "label": "TempMail.lol", "description": "免费临时邮箱", "driver_type": "tempmail_lol_api", "category": "free", "auth_modes": []map[string]any{}, "fields": []map[string]any{}},
		{
			"provider_type": "mailbox", "provider_key": "outlook_email_api", "label": "Outlook Email Pool", "description": "对接 Outlook/Hotmail 邮箱池 API", "driver_type": "outlook_email_api", "default_auth_mode": "apikey", "category": "selfhost",
			"auth_modes": []map[string]any{{"value": "apikey", "label": "API Key"}},
			"fields":     []map[string]any{{"key": "outlook_email_api_url", "label": "服务地址", "placeholder": "https://outlook-email.example.com", "category": "connection"}, {"key": "outlook_email_api_key", "label": "API Key", "secret": true, "category": "auth"}, {"key": "outlook_email_fixed_email", "label": "固定邮箱", "placeholder": "user@outlook.com", "category": "identity"}},
		},
		{"provider_type": "mailbox", "provider_key": "generic_http_mailbox", "label": "通用 HTTP 邮箱", "description": "通过 HTTP 配置对接任意邮箱 API", "driver_type": "generic_http_mailbox", "category": "custom", "auth_modes": []map[string]any{}, "fields": []map[string]any{}},
		{"provider_type": "captcha", "provider_key": "yescaptcha_api", "label": "YesCaptcha", "description": "云端验证码识别服务", "driver_type": "yescaptcha_api", "default_auth_mode": "apikey", "category": "thirdparty", "auth_modes": []map[string]any{{"value": "apikey", "label": "API Key"}}, "fields": []map[string]any{{"key": "yescaptcha_key", "label": "Client Key", "secret": true}}},
		{"provider_type": "captcha", "provider_key": "twocaptcha_api", "label": "2Captcha", "description": "云端验证码识别服务", "driver_type": "twocaptcha_api", "default_auth_mode": "apikey", "category": "thirdparty", "auth_modes": []map[string]any{{"value": "apikey", "label": "API Key"}}, "fields": []map[string]any{{"key": "twocaptcha_key", "label": "API Key", "secret": true}}},
		{"provider_type": "captcha", "provider_key": "local_solver", "label": "本地验证码求解器", "description": "调用本地 solver 服务", "driver_type": "local_solver", "category": "local", "auth_modes": []map[string]any{}, "fields": []map[string]any{{"key": "solver_url", "label": "Solver 地址", "placeholder": "http://localhost:8889"}}},
		{"provider_type": "captcha", "provider_key": "manual", "label": "人工处理", "description": "等待人工完成验证码", "driver_type": "manual", "category": "manual", "auth_modes": []map[string]any{}, "fields": []map[string]any{}},
		{
			"provider_type": "sms", "provider_key": "herosms_api", "label": "HeroSMS", "description": "HeroSMS 接码平台", "driver_type": "herosms_api", "default_auth_mode": "apikey", "category": "thirdparty",
			"auth_modes": []map[string]any{{"value": "apikey", "label": "API Key"}},
			"fields":     []map[string]any{{"key": "herosms_api_key", "label": "API Key", "secret": true, "category": "auth"}, {"key": "herosms_default_country", "label": "默认国家", "type": "async-select", "asyncUrl": "/sms/herosms/countries", "asyncValueKey": "id", "asyncLabelKey": "chn"}, {"key": "herosms_default_service", "label": "默认服务", "type": "async-select", "asyncUrl": "/sms/herosms/services", "asyncValueKey": "code", "asyncLabelKey": "name"}, {"key": "herosms_max_price", "label": "最高价格", "placeholder": "-1"}, {"key": "register_phone_extra_max", "label": "号码复用额外上限", "placeholder": "3"}, {"key": "register_reuse_phone_to_max", "label": "复用号码至最大", "type": "toggle"}},
		},
		{
			"provider_type": "sms", "provider_key": "smsbower_api", "label": "SMSBower", "description": "SMSBower 接码平台", "driver_type": "smsbower_api", "default_auth_mode": "apikey", "category": "thirdparty",
			"auth_modes": []map[string]any{{"value": "apikey", "label": "API Key"}},
			"fields":     []map[string]any{{"key": "smsbower_api_key", "label": "API Key", "secret": true, "category": "auth"}, {"key": "smsbower_default_country", "label": "默认国家", "type": "async-select", "asyncUrl": "/sms/smsbower/countries", "asyncValueKey": "id", "asyncLabelKey": "chn"}, {"key": "smsbower_default_service", "label": "默认服务", "type": "async-select", "asyncUrl": "/sms/smsbower/services", "asyncValueKey": "code", "asyncLabelKey": "name"}, {"key": "smsbower_max_price", "label": "最高价格", "placeholder": "-1"}},
		},
		{"provider_type": "proxy", "provider_key": "api_extract", "label": "API 提取代理", "description": "通过 HTTP API 动态提取代理列表", "driver_type": "api_extract", "category": "thirdparty", "auth_modes": []map[string]any{}, "fields": []map[string]any{{"key": "proxy_api_url", "label": "API 地址"}, {"key": "proxy_protocol", "label": "协议"}}},
	}

	for _, seed := range seeds {
		pt := text(seed["provider_type"])
		pk := text(seed["provider_key"])
		if pt == "" || pk == "" {
			continue
		}
		var item ProviderDefinition
		err := db.Where("provider_type = ? AND provider_key = ?", pt, pk).First(&item).Error
		if err != nil {
			item = ProviderDefinition{ProviderType: pt, ProviderKey: pk}
		}
		item.Label = text(seed["label"])
		item.Description = text(seed["description"])
		item.DriverType = text(seed["driver_type"])
		item.DefaultAuthMode = text(seed["default_auth_mode"])
		item.Enabled = true
		item.IsBuiltin = true
		item.Category = text(seed["category"])
		item.AuthModesJSON = dumpJSON(seed["auth_modes"])
		item.FieldsJSON = dumpJSON(seed["fields"])
		if strings.TrimSpace(item.MetadataJSON) == "" {
			item.MetadataJSON = "{}"
		}
		db.Save(&item)
	}
}

func markInterrupted(db *gorm.DB) {
	reason := "服务重启，任务已中断"
	var tasks []Task
	db.Where("status IN ?", []string{"pending", "claimed", "running", "cancel_requested"}).Find(&tasks)
	for _, task := range tasks {
		if !strings.HasPrefix(task.Type, "sunny_") {
			continue
		}
		payload := jsonMap(task.PayloadJSON)
		mailboxIDs := uintSlice(payload["mailbox_ids"])
		if len(mailboxIDs) == 0 {
			continue
		}
		db.Model(&SunnyMailbox{}).
			Where("id IN ? AND status IN ?", mailboxIDs, []string{"注册中", "登录刷新"}).
			Updates(map[string]any{"status": "失败", "last_error": reason, "updated_at": time.Now()})
	}
	db.Model(&Task{}).
		Where("status IN ?", []string{"pending", "claimed", "running", "cancel_requested"}).
		Updates(map[string]any{"status": "interrupted", "error": reason, "finished_at": time.Now(), "updated_at": time.Now()})
}
