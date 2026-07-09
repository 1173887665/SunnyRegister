package main

import (
	"database/sql"
	"time"
)

type SunnyMailboxGroup struct {
	ID          uint      `gorm:"primaryKey" json:"id"`
	Name        string    `gorm:"uniqueIndex;size:120" json:"name"`
	Description string    `gorm:"type:text" json:"description"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

func (SunnyMailboxGroup) TableName() string { return "sunny_mailbox_groups" }

type SunnyMailbox struct {
	ID             uint         `gorm:"primaryKey" json:"id"`
	GroupID        uint         `gorm:"index" json:"group_id"`
	Email          string       `gorm:"uniqueIndex;index" json:"email"`
	Password       string       `gorm:"type:text" json:"password"`
	ClientID       string       `gorm:"type:text" json:"client_id"`
	RefreshToken   string       `gorm:"type:text" json:"refresh_token"`
	OpenAIRT       string       `gorm:"column:openai_rt;type:text" json:"openai_rt"`
	Raw            string       `gorm:"type:text" json:"raw"`
	AccountType    string       `gorm:"index;default:free" json:"account_type"`
	Status         string       `gorm:"index;default:unused" json:"status"`
	Enabled        bool         `gorm:"default:true" json:"enabled"`
	LastError      string       `gorm:"type:text" json:"last_error"`
	LatestMailJSON string       `gorm:"type:text;default:'{}'" json:"latest_mail_json"`
	LastMailAt     sql.NullTime `json:"last_mail_at"`
	RegisteredAt   sql.NullTime `json:"registered_at"`
	CreatedAt      time.Time    `json:"created_at"`
	UpdatedAt      time.Time    `json:"updated_at"`
}

func (SunnyMailbox) TableName() string { return "sunny_mailboxes" }

type SunnyPhone struct {
	ID            uint         `gorm:"primaryKey" json:"id"`
	Number        string       `gorm:"uniqueIndex;index" json:"number"`
	SmsURL        string       `gorm:"type:text" json:"sms_url"`
	Status        string       `gorm:"index;default:available" json:"status"`
	Enabled       bool         `gorm:"default:true" json:"enabled"`
	SuccessCount  int          `gorm:"default:0" json:"success_count"`
	MaxSuccess    int          `gorm:"default:3" json:"max_success"`
	CooldownUntil sql.NullTime `gorm:"index" json:"cooldown_until"`
	LastCode      string       `json:"last_code"`
	LastError     string       `gorm:"type:text" json:"last_error"`
	LastUsedAt    sql.NullTime `json:"last_used_at"`
	CreatedAt     time.Time    `json:"created_at"`
	UpdatedAt     time.Time    `json:"updated_at"`
}

func (SunnyPhone) TableName() string { return "sunny_phones" }

type SunnyProxy struct {
	ID            uint       `gorm:"primaryKey" json:"id"`
	Address       string     `gorm:"type:text;index" json:"address"`
	Country       string     `gorm:"index;size:80" json:"country"`
	Status        string     `gorm:"index;default:enabled" json:"status"`
	Enabled       bool       `gorm:"default:true" json:"enabled"`
	LastCheckOK   bool       `gorm:"default:false" json:"last_check_ok"`
	LatencyMS     int64      `gorm:"default:0" json:"latency_ms"`
	LastError     string     `gorm:"type:text" json:"last_error"`
	LastCheckedAt *time.Time `json:"last_checked_at"`
	CreatedAt     time.Time  `json:"created_at"`
	UpdatedAt     time.Time  `json:"updated_at"`
}

func (SunnyProxy) TableName() string { return "sunny_proxies" }

type SunnyAccount struct {
	ID            uint      `gorm:"primaryKey" json:"id"`
	MailboxID     uint      `gorm:"index" json:"mailbox_id"`
	Email         string    `gorm:"uniqueIndex;index" json:"email"`
	GroupName     string    `gorm:"index" json:"group_name"`
	Status        string    `gorm:"index;default:pending" json:"status"`
	AccountType   string    `gorm:"index;default:free" json:"account_type"`
	OpenAIRT      string    `gorm:"column:openai_rt;type:text" json:"openai_rt"`
	AccessToken   string    `gorm:"type:text" json:"access_token"`
	PhoneNumber   string    `gorm:"index" json:"phone_number"`
	Sub2APIStatus string    `gorm:"index" json:"sub2api_status"`
	Sub2APIID     string    `json:"sub2api_id"`
	LastError     string    `gorm:"type:text" json:"last_error"`
	MetadataJSON  string    `gorm:"type:text;default:'{}'" json:"metadata_json"`
	CreatedAt     time.Time `json:"created_at"`
	UpdatedAt     time.Time `json:"updated_at"`
}

func (SunnyAccount) TableName() string { return "sunny_accounts" }

type SunnySession struct {
	ID               uint         `gorm:"primaryKey" json:"id"`
	AccountID        uint         `gorm:"index" json:"account_id"`
	Email            string       `gorm:"uniqueIndex;index" json:"email"`
	AccessToken      string       `gorm:"type:text" json:"access_token"`
	RefreshToken     string       `gorm:"type:text" json:"refresh_token"`
	IDToken          string       `gorm:"type:text" json:"id_token"`
	SessionJSON      string       `gorm:"type:text" json:"session_json"`
	StorageStateJSON string       `gorm:"type:text" json:"storage_state_json"`
	RawMailboxLine   string       `gorm:"type:text" json:"raw_mailbox_line"`
	ExpiresAt        sql.NullTime `json:"expires_at"`
	LastRefreshAt    sql.NullTime `json:"last_refresh_at"`
	CreatedAt        time.Time    `json:"created_at"`
	UpdatedAt        time.Time    `json:"updated_at"`
}

func (SunnySession) TableName() string { return "sunny_sessions" }

type SunnyKVConfig struct {
	Key       string    `gorm:"primaryKey;size:80" json:"key"`
	ValueJSON string    `gorm:"type:text;default:'{}'" json:"value_json"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

func (SunnyKVConfig) TableName() string { return "sunny_configs" }

type SunnySMSProviderOption struct {
	ID          uint      `gorm:"primaryKey" json:"id"`
	Provider    string    `gorm:"uniqueIndex:idx_sunny_sms_option;size:40;index" json:"provider"`
	Kind        string    `gorm:"uniqueIndex:idx_sunny_sms_option;size:20;index" json:"kind"`
	ParentValue string    `gorm:"uniqueIndex:idx_sunny_sms_option;size:80;default:''" json:"parent_value"`
	Value       string    `gorm:"uniqueIndex:idx_sunny_sms_option;size:120" json:"value"`
	Label       string    `gorm:"size:240" json:"label"`
	ExtraJSON   string    `gorm:"type:text;default:'{}'" json:"extra_json"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

func (SunnySMSProviderOption) TableName() string { return "sunny_sms_provider_options" }
