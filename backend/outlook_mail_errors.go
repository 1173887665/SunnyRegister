package main

import (
	"errors"
	"fmt"
	"net/http"
	"strings"
)

type outlookMailError struct {
	Code        string
	Category    string
	HTTPStatus  int
	UserMessage string
	Detail      string
	Terminal    bool
}

func (e *outlookMailError) Error() string {
	if strings.TrimSpace(e.Detail) != "" {
		return e.UserMessage + ": " + e.Detail
	}
	return e.UserMessage
}

func classifyOutlookMailError(err error) *outlookMailError {
	var typed *outlookMailError
	if errors.As(err, &typed) {
		return typed
	}
	return &outlookMailError{
		Code: "mailbox_auth_failed", Category: "authentication", HTTPStatus: http.StatusUnprocessableEntity,
		UserMessage: "邮箱凭证无法通过 Graph 或 IMAP 验证，请检查凭证类型、授权范围与有效期",
		Detail:      err.Error(),
	}
}

func isTerminalOutlookMailError(err error) bool {
	return classifyOutlookMailError(err).Terminal
}

func newOutlookTokenError(status int, payload map[string]any) error {
	errorCode := strings.ToLower(strings.TrimSpace(text(payload["error"])))
	detail := strings.TrimSpace(firstText(payload["error_description"], payload["error"], fmt.Sprintf("HTTP %d", status)))
	lower := strings.ToLower(detail)

	switch {
	case strings.Contains(lower, "grant is expired"),
		strings.Contains(lower, "refresh token has expired"),
		strings.Contains(lower, "token was revoked"),
		strings.Contains(lower, "sign in again"):
		return &outlookMailError{
			Code: "mailbox_credential_expired", Category: "credential", HTTPStatus: http.StatusUnprocessableEntity,
			UserMessage: "邮箱 OAuth 凭证已过期或被撤销，请重新授权或更换 Refresh Token",
			Detail:      detail, Terminal: true,
		}
	case errorCode == "invalid_client", strings.Contains(lower, "client secret is invalid"):
		return &outlookMailError{
			Code: "mailbox_client_invalid", Category: "credential", HTTPStatus: http.StatusUnprocessableEntity,
			UserMessage: "邮箱 OAuth 客户端配置无效，请检查 client_id 与凭证来源",
			Detail:      detail, Terminal: true,
		}
	case errorCode == "invalid_grant" && (strings.Contains(lower, "malformed") || strings.Contains(lower, "invalid refresh token")):
		return &outlookMailError{
			Code: "mailbox_credential_invalid", Category: "credential", HTTPStatus: http.StatusUnprocessableEntity,
			UserMessage: "邮箱 OAuth 凭证无效，请检查 client_id 与 Refresh Token",
			Detail:      detail, Terminal: true,
		}
	case errorCode == "invalid_scope",
		strings.Contains(lower, "scope") && (strings.Contains(lower, "unauthorized") || strings.Contains(lower, "consent")):
		return &outlookMailError{
			Code: "mailbox_scope_mismatch", Category: "permission", HTTPStatus: http.StatusUnprocessableEntity,
			UserMessage: "邮箱凭证权限类型不匹配，正在尝试 Graph 与 IMAP 兼容授权",
			Detail:      detail,
		}
	default:
		return &outlookMailError{
			Code: "mailbox_auth_failed", Category: "authentication", HTTPStatus: http.StatusUnprocessableEntity,
			UserMessage: "邮箱 OAuth 凭证验证失败，请检查凭证类型、授权范围与有效期",
			Detail:      detail,
		}
	}
}

func newOutlookMailAggregateError(details []string) error {
	joined := strings.Join(details, " | ")
	lower := strings.ToLower(joined)
	if strings.Contains(lower, "timeout") || strings.Contains(lower, "connection refused") ||
		strings.Contains(lower, "connection reset") || strings.Contains(lower, "network is unreachable") ||
		strings.Contains(lower, "no such host") || strings.Contains(lower, "tls handshake") {
		return &outlookMailError{
			Code: "mailbox_network_error", Category: "network", HTTPStatus: http.StatusServiceUnavailable,
			UserMessage: "邮箱服务网络连接失败，请检查服务器出网、代理与 Microsoft 服务连通性",
			Detail:      joined,
		}
	}
	if strings.Contains(lower, "scope") || strings.Contains(lower, "permission") || strings.Contains(lower, "audience") {
		return &outlookMailError{
			Code: "mailbox_scope_mismatch", Category: "permission", HTTPStatus: http.StatusUnprocessableEntity,
			UserMessage: "邮箱凭证权限类型不匹配，Graph 与 IMAP 均未获得可用授权",
			Detail:      joined,
		}
	}
	return &outlookMailError{
		Code: "mailbox_auth_failed", Category: "authentication", HTTPStatus: http.StatusUnprocessableEntity,
		UserMessage: "邮箱凭证无法通过 Graph 或 IMAP 验证，请检查凭证类型、授权范围与有效期",
		Detail:      joined,
	}
}
