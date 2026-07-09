package main

import (
	"database/sql"
	"time"
)

type ConfigItem struct {
	Key   string `gorm:"primaryKey;size:255" json:"key"`
	Value string `gorm:"type:text" json:"value"`
}

func (ConfigItem) TableName() string { return "configs" }

type Account struct {
	ID        uint      `gorm:"primaryKey" json:"id"`
	Platform  string    `gorm:"index" json:"platform"`
	Email     string    `gorm:"index" json:"email"`
	Password  string    `json:"password"`
	UserID    string    `json:"user_id"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

func (Account) TableName() string { return "accounts" }

type AccountOverview struct {
	AccountID       uint         `gorm:"primaryKey" json:"account_id"`
	LifecycleStatus string       `gorm:"index;default:registered" json:"lifecycle_status"`
	ValidityStatus  string       `gorm:"index;default:unknown" json:"validity_status"`
	PlanState       string       `gorm:"index;default:unknown" json:"plan_state"`
	PlanName        string       `json:"plan_name"`
	DisplayStatus   string       `gorm:"index;default:registered" json:"display_status"`
	RemoteEmail     string       `json:"remote_email"`
	CheckedAt       sql.NullTime `json:"checked_at"`
	SummaryJSON     string       `gorm:"type:text;default:'{}'" json:"summary_json"`
	CreatedAt       time.Time    `json:"created_at"`
	UpdatedAt       time.Time    `json:"updated_at"`
}

func (AccountOverview) TableName() string { return "account_overviews" }

type AccountCredential struct {
	ID             uint      `gorm:"primaryKey" json:"id"`
	AccountID      uint      `gorm:"index" json:"account_id"`
	Scope          string    `gorm:"index;default:platform" json:"scope"`
	ProviderName   string    `gorm:"index" json:"provider_name"`
	CredentialType string    `gorm:"index;default:secret" json:"credential_type"`
	Key            string    `gorm:"index" json:"key"`
	Value          string    `gorm:"type:text" json:"value"`
	IsPrimary      bool      `json:"is_primary"`
	Source         string    `json:"source"`
	MetadataJSON   string    `gorm:"type:text;default:'{}'" json:"metadata_json"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

func (AccountCredential) TableName() string { return "account_credentials" }

type ProviderAccount struct {
	ID              uint      `gorm:"primaryKey" json:"id"`
	AccountID       uint      `gorm:"index" json:"account_id"`
	ProviderType    string    `gorm:"index;default:mailbox" json:"provider_type"`
	ProviderName    string    `gorm:"index" json:"provider_name"`
	LoginIdentifier string    `gorm:"index" json:"login_identifier"`
	DisplayName     string    `json:"display_name"`
	CredentialsJSON string    `gorm:"type:text;default:'{}'" json:"credentials_json"`
	MetadataJSON    string    `gorm:"type:text;default:'{}'" json:"metadata_json"`
	CreatedAt       time.Time `json:"created_at"`
	UpdatedAt       time.Time `json:"updated_at"`
}

func (ProviderAccount) TableName() string { return "provider_accounts" }

type ProviderResource struct {
	ID                 uint      `gorm:"primaryKey" json:"id"`
	AccountID          uint      `gorm:"index" json:"account_id"`
	ProviderType       string    `gorm:"index;default:mailbox" json:"provider_type"`
	ProviderName       string    `gorm:"index" json:"provider_name"`
	ResourceType       string    `gorm:"index;default:resource" json:"resource_type"`
	ResourceIdentifier string    `gorm:"index" json:"resource_identifier"`
	Handle             string    `json:"handle"`
	DisplayName        string    `json:"display_name"`
	MetadataJSON       string    `gorm:"type:text;default:'{}'" json:"metadata_json"`
	CreatedAt          time.Time `json:"created_at"`
	UpdatedAt          time.Time `json:"updated_at"`
}

func (ProviderResource) TableName() string { return "provider_resources" }

type ProviderDefinition struct {
	ID              uint      `gorm:"primaryKey" json:"id"`
	ProviderType    string    `gorm:"uniqueIndex:uq_provider_definitions_type_key;index" json:"provider_type"`
	ProviderKey     string    `gorm:"uniqueIndex:uq_provider_definitions_type_key;index" json:"provider_key"`
	Label           string    `json:"label"`
	Description     string    `gorm:"type:text" json:"description"`
	DriverType      string    `json:"driver_type"`
	DefaultAuthMode string    `json:"default_auth_mode"`
	Enabled         bool      `gorm:"default:true" json:"enabled"`
	IsBuiltin       bool      `json:"is_builtin"`
	Category        string    `json:"category"`
	AuthModesJSON   string    `gorm:"type:text;default:'[]'" json:"auth_modes_json"`
	FieldsJSON      string    `gorm:"type:text;default:'[]'" json:"fields_json"`
	MetadataJSON    string    `gorm:"type:text;default:'{}'" json:"metadata_json"`
	CreatedAt       time.Time `json:"created_at"`
	UpdatedAt       time.Time `json:"updated_at"`
}

func (ProviderDefinition) TableName() string { return "provider_definitions" }

type ProviderSetting struct {
	ID           uint      `gorm:"primaryKey" json:"id"`
	ProviderType string    `gorm:"uniqueIndex:uq_provider_settings_type_key;index" json:"provider_type"`
	ProviderKey  string    `gorm:"uniqueIndex:uq_provider_settings_type_key;index" json:"provider_key"`
	DisplayName  string    `json:"display_name"`
	AuthMode     string    `json:"auth_mode"`
	Enabled      bool      `gorm:"default:true" json:"enabled"`
	IsDefault    bool      `json:"is_default"`
	ConfigJSON   string    `gorm:"type:text;default:'{}'" json:"config_json"`
	AuthJSON     string    `gorm:"type:text;default:'{}'" json:"auth_json"`
	MetadataJSON string    `gorm:"type:text;default:'{}'" json:"metadata_json"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

func (ProviderSetting) TableName() string { return "provider_settings" }

type PlatformCapabilityOverride struct {
	ID               uint      `gorm:"primaryKey" json:"id"`
	PlatformName     string    `gorm:"uniqueIndex" json:"platform_name"`
	CapabilitiesJSON string    `gorm:"type:text;default:'{}'" json:"capabilities_json"`
	CreatedAt        time.Time `json:"created_at"`
	UpdatedAt        time.Time `json:"updated_at"`
}

func (PlatformCapabilityOverride) TableName() string { return "platform_capability_overrides" }

type TaskLog struct {
	ID         uint      `gorm:"primaryKey" json:"id"`
	Platform   string    `json:"platform"`
	Email      string    `json:"email"`
	Status     string    `json:"status"`
	Error      string    `gorm:"type:text" json:"error"`
	DetailJSON string    `gorm:"type:text;default:'{}'" json:"detail_json"`
	CreatedAt  time.Time `json:"created_at"`
}

func (TaskLog) TableName() string { return "task_logs" }

type Task struct {
	ID              string       `gorm:"primaryKey;size:64" json:"id"`
	Type            string       `gorm:"index" json:"type"`
	Platform        string       `gorm:"index" json:"platform"`
	Status          string       `gorm:"index;default:pending" json:"status"`
	PayloadJSON     string       `gorm:"type:text;default:'{}'" json:"payload_json"`
	ResultJSON      string       `gorm:"type:text;default:'{}'" json:"result_json"`
	ProgressCurrent int          `json:"progress_current"`
	ProgressTotal   int          `json:"progress_total"`
	SuccessCount    int          `json:"success_count"`
	ErrorCount      int          `json:"error_count"`
	Error           string       `gorm:"type:text" json:"error"`
	StartedAt       sql.NullTime `json:"started_at"`
	FinishedAt      sql.NullTime `json:"finished_at"`
	CreatedAt       time.Time    `json:"created_at"`
	UpdatedAt       time.Time    `json:"updated_at"`
}

func (Task) TableName() string { return "tasks" }

type TaskEvent struct {
	ID         uint      `gorm:"primaryKey" json:"id"`
	TaskID     string    `gorm:"index" json:"task_id"`
	Type       string    `gorm:"index;default:log" json:"type"`
	Level      string    `json:"level"`
	Message    string    `gorm:"type:text" json:"message"`
	DetailJSON string    `gorm:"type:text;default:'{}'" json:"detail_json"`
	CreatedAt  time.Time `json:"created_at"`
}

func (TaskEvent) TableName() string { return "task_events" }

type Proxy struct {
	ID           uint         `gorm:"primaryKey" json:"id"`
	URL          string       `gorm:"uniqueIndex" json:"url"`
	Region       string       `json:"region"`
	SuccessCount int          `json:"success_count"`
	FailCount    int          `json:"fail_count"`
	IsActive     bool         `gorm:"default:true" json:"is_active"`
	LastChecked  sql.NullTime `json:"last_checked"`
}

func (Proxy) TableName() string { return "proxies" }

type SmsPoolBlacklist struct {
	ID               uint      `gorm:"primaryKey" json:"id"`
	PhoneE164        string    `gorm:"uniqueIndex;index" json:"phone_e164"`
	RelayURL         string    `gorm:"type:text" json:"relay_url"`
	RelayHost        string    `gorm:"index" json:"relay_host"`
	Reason           string    `json:"reason"`
	ErrorCode        string    `json:"error_code"`
	TaskID           string    `json:"task_id"`
	FailCount        int       `gorm:"default:1" json:"fail_count"`
	LastErrorMessage string    `gorm:"type:text" json:"last_error_message"`
	CreatedAt        time.Time `json:"created_at"`
	LastAttemptedAt  time.Time `json:"last_attempted_at"`
}

func (SmsPoolBlacklist) TableName() string { return "sms_pool_blacklist" }
