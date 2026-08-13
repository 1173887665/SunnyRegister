package main

import "time"

type AuditLog struct {
	ID          uint      `gorm:"primaryKey" json:"id"`
	OccurredAt  time.Time `gorm:"index" json:"occurred_at"`
	ActorType   string    `gorm:"index;size:32" json:"actor_type"`
	Actor       string    `gorm:"index;size:128" json:"actor"`
	IP          string    `gorm:"index;size:64" json:"ip"`
	UserAgent   string    `gorm:"size:512" json:"user_agent"`
	LogType     string    `gorm:"index;size:32" json:"log_type"`
	Category    string    `gorm:"index;size:64" json:"category"`
	Action      string    `gorm:"index;size:64" json:"action"`
	Level       string    `gorm:"index;size:16" json:"level"`
	Status      string    `gorm:"index;size:24" json:"status"`
	Source      string    `gorm:"index;size:64" json:"source"`
	Method      string    `gorm:"size:16" json:"method"`
	Path        string    `gorm:"index;size:512" json:"path"`
	RequestID   string    `gorm:"index;size:96" json:"request_id"`
	TaskID      string    `gorm:"index;size:96" json:"task_id"`
	Email       string    `gorm:"type:text" json:"email"`
	SubjectKey  string    `gorm:"type:text" json:"subject_key"`
	EntityType  string    `gorm:"index;size:64" json:"entity_type"`
	EntityID    string    `gorm:"index;size:128" json:"entity_id"`
	EntityName  string    `gorm:"size:512" json:"entity_name"`
	Summary     string    `gorm:"type:text" json:"summary"`
	DetailsJSON string    `gorm:"type:text;default:'{}'" json:"details_json"`
	HTTPStatus  int       `gorm:"index" json:"http_status"`
	DurationMS  int64     `json:"duration_ms"`
	Count       int       `json:"count"`
	DedupeKey   string    `gorm:"index;size:191" json:"-"`
	CreatedAt   time.Time `gorm:"index" json:"created_at"`
}

func (AuditLog) TableName() string { return "audit_logs" }

type AuditSetting struct {
	ID            uint      `gorm:"primaryKey" json:"id"`
	RetentionDays int       `gorm:"default:7" json:"retention_days"`
	CleanupHour   int       `gorm:"default:3" json:"cleanup_hour"`
	Enabled       bool      `gorm:"default:true" json:"enabled"`
	UpdatedAt     time.Time `json:"updated_at"`
}

func (AuditSetting) TableName() string { return "audit_settings" }

type AuditExportJob struct {
	ID           string     `gorm:"primaryKey;size:96" json:"id"`
	Status       string     `gorm:"index;size:24" json:"status"`
	Format       string     `gorm:"size:16" json:"format"`
	FiltersJSON  string     `gorm:"type:text;default:'{}'" json:"filters_json"`
	SelectedJSON string     `gorm:"type:text;default:'[]'" json:"selected_json"`
	Actor        string     `gorm:"size:128" json:"actor"`
	FileName     string     `gorm:"size:512" json:"file_name"`
	FilePath     string     `gorm:"size:1024" json:"-"`
	ContentType  string     `gorm:"size:128" json:"content_type"`
	FileSize     int64      `json:"file_size"`
	RecordCount  int        `json:"record_count"`
	Error        string     `gorm:"type:text" json:"error"`
	CreatedAt    time.Time  `gorm:"index" json:"created_at"`
	UpdatedAt    time.Time  `json:"updated_at"`
	CompletedAt  *time.Time `json:"completed_at"`
}

func (AuditExportJob) TableName() string { return "audit_export_jobs" }
