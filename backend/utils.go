package main

import (
	"archive/zip"
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	_ "time/tzdata"
	"unicode"
)

var (
	bearerSecretPattern  = regexp.MustCompile(`(?i)(Bearer\s+)[A-Za-z0-9._~+/-]{12,}`)
	jwtSecretPattern     = regexp.MustCompile(`\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b`)
	otpSecretPattern     = regexp.MustCompile(`(?i)(OTP|verification code|received code|验证码)(\s*[:=]?\s*)\d{4,8}`)
	urlCredentialPattern = regexp.MustCompile(`(?i)\b(https?|socks5h?)://[^/@\s]+@`)
)

var persistedSecretKeys = map[string]bool{
	"access_token": true, "refresh_token": true, "id_token": true, "openai_rt": true,
	"session_json": true, "password": true, "secret": true, "api_key": true,
	"admin_token": true, "authorization": true, "otp": true, "code": true,
}

func sanitizePersistedString(value string) string {
	value = bearerSecretPattern.ReplaceAllString(value, `${1}[REDACTED]`)
	value = jwtSecretPattern.ReplaceAllString(value, `[REDACTED_JWT]`)
	value = otpSecretPattern.ReplaceAllString(value, `${1}${2}[REDACTED]`)
	return urlCredentialPattern.ReplaceAllString(value, `${1}://[REDACTED]@`)
}

func sanitizePersistedValue(value any, key string) any {
	normalized := strings.ToLower(strings.TrimSpace(key))
	if persistedSecretKeys[normalized] || strings.HasSuffix(normalized, "_password") || strings.HasSuffix(normalized, "_secret") || strings.HasSuffix(normalized, "_token") || strings.HasSuffix(normalized, "_api_key") {
		if value == nil || text(value) == "" {
			return value
		}
		return "[REDACTED]"
	}
	switch item := value.(type) {
	case map[string]any:
		out := make(map[string]any, len(item))
		for childKey, childValue := range item {
			out[childKey] = sanitizePersistedValue(childValue, childKey)
		}
		return out
	case []any:
		out := make([]any, len(item))
		for i, childValue := range item {
			out[i] = sanitizePersistedValue(childValue, key)
		}
		return out
	case string:
		return sanitizePersistedString(item)
	default:
		return value
	}
}

func sanitizePersistedJSON(raw string) string {
	if strings.TrimSpace(raw) == "" {
		return raw
	}
	var value any
	if json.Unmarshal([]byte(raw), &value) != nil {
		return sanitizePersistedString(raw)
	}
	encoded, err := json.Marshal(sanitizePersistedValue(value, ""))
	if err != nil {
		return raw
	}
	return string(encoded)
}

func jsonMap(raw string) map[string]any {
	if strings.TrimSpace(raw) == "" {
		return map[string]any{}
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(raw), &out); err != nil || out == nil {
		return map[string]any{}
	}
	return out
}

func jsonList(raw string) []map[string]any {
	if strings.TrimSpace(raw) == "" {
		return []map[string]any{}
	}
	var out []map[string]any
	if err := json.Unmarshal([]byte(raw), &out); err != nil || out == nil {
		return []map[string]any{}
	}
	return out
}

func dumpJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(b)
}

func dumpJSONPretty(v any) string {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return "{}"
	}
	return string(b)
}

func text(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return strings.TrimSpace(x)
	case fmt.Stringer:
		return strings.TrimSpace(x.String())
	default:
		return strings.TrimSpace(fmt.Sprint(v))
	}
}

func boolValue(v any, fallback bool) bool {
	switch x := v.(type) {
	case bool:
		return x
	case string:
		if x == "" {
			return fallback
		}
		return x == "true" || x == "1" || strings.EqualFold(x, "yes") || strings.EqualFold(x, "on")
	case float64:
		return x != 0
	case int:
		return x != 0
	default:
		return fallback
	}
}

func intValue(v any, fallback int) int {
	switch x := v.(type) {
	case int:
		return x
	case int64:
		return int(x)
	case uint:
		return int(x)
	case float64:
		return int(x)
	case json.Number:
		i, err := x.Int64()
		if err == nil {
			return int(i)
		}
	case string:
		if strings.TrimSpace(x) == "" {
			return fallback
		}
		i, err := strconv.Atoi(strings.TrimSpace(x))
		if err == nil {
			return i
		}
	}
	return fallback
}

func uintSlice(v any) []uint {
	out := []uint{}
	switch x := v.(type) {
	case []uint:
		return x
	case []int:
		for _, n := range x {
			if n > 0 {
				out = append(out, uint(n))
			}
		}
	case []any:
		for _, item := range x {
			n := intValue(item, 0)
			if n > 0 {
				out = append(out, uint(n))
			}
		}
	}
	return out
}

func stringSlice(v any) []string {
	out := []string{}
	switch x := v.(type) {
	case []string:
		return x
	case []any:
		for _, item := range x {
			if s := text(item); s != "" {
				out = append(out, s)
			}
		}
	}
	return out
}

func parseBody(r *http.Request) (map[string]any, error) {
	defer r.Body.Close()
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	var body map[string]any
	if err := dec.Decode(&body); err != nil {
		if err == io.EOF {
			return map[string]any{}, nil
		}
		return nil, err
	}
	if body == nil {
		body = map[string]any{}
	}
	return body, nil
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]any{"detail": msg, "error": msg})
}

func writeTextFile(w http.ResponseWriter, filename, mediaType string, content []byte) {
	if mediaType == "" {
		mediaType = mime.TypeByExtension(filepath.Ext(filename))
	}
	if mediaType == "" {
		mediaType = "application/octet-stream"
	}
	w.Header().Set("Content-Type", mediaType)
	w.Header().Set("Content-Disposition", fmt.Sprintf("attachment; filename*=UTF-8''%s", urlPathEscape(filename)))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(content)
}

func urlPathEscape(s string) string {
	return strings.NewReplacer(" ", "%20", "\"", "", "\r", "", "\n", "").Replace(s)
}

func formatTime(t time.Time) string {
	if t.IsZero() {
		return ""
	}
	return t.In(applicationLocation()).Format(time.RFC3339)
}

var sunnyApplicationLocation = time.FixedZone("Asia/Shanghai", 8*60*60)

func applicationLocation() *time.Location {
	return sunnyApplicationLocation
}

func configureApplicationTimezone() {
	name := strings.TrimSpace(os.Getenv("SUNNY_TIMEZONE"))
	if name == "" {
		name = strings.TrimSpace(os.Getenv("TZ"))
	}
	if name == "" {
		name = "Asia/Shanghai"
	}
	location, err := time.LoadLocation(name)
	if err != nil {
		log.Printf("load timezone %s failed, using Asia/Shanghai: %v", name, err)
		location = time.FixedZone("Asia/Shanghai", 8*60*60)
	}
	sunnyApplicationLocation = location
	time.Local = location
	_ = os.Setenv("TZ", name)
	_ = os.Setenv("SUNNY_TIMEZONE", name)
}

func nullableTime(valid bool, t time.Time) any {
	if !valid || t.IsZero() {
		return nil
	}
	return formatTime(t)
}

func randomID(prefix string) string {
	var b [24]byte
	_, _ = rand.Read(b[:])
	return fmt.Sprintf("%s_%s", prefix, strings.TrimRight(base64.RawURLEncoding.EncodeToString(b[:]), "="))
}

func normalizeDatabasePath(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return filepath.Join("data", "account_manager.db")
	}
	if strings.HasPrefix(raw, "sqlite:///") {
		return strings.TrimPrefix(raw, "sqlite:///")
	}
	if strings.HasPrefix(raw, "sqlite://") {
		return strings.TrimPrefix(raw, "sqlite://")
	}
	return raw
}

func ensureDir(path string) error {
	dir := filepath.Dir(path)
	if dir == "." || dir == "" {
		return nil
	}
	return os.MkdirAll(dir, 0o755)
}

func csvLine(raw string) ([]string, error) {
	reader := csv.NewReader(strings.NewReader(raw))
	reader.FieldsPerRecord = -1
	return reader.Read()
}

func decodeImportToken(v string) string {
	v = strings.TrimSpace(v)
	if len(v) >= 2 {
		first, last := v[0], v[len(v)-1]
		if (first == '"' && last == '"') || (first == '\'' && last == '\'') {
			return v[1 : len(v)-1]
		}
	}
	return v
}

func splitImportLine(raw string) (email, password, extra string, ok bool) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", "", "", false
	}
	if strings.Contains(raw, ",") {
		row, err := csvLine(raw)
		if err == nil && len(row) >= 2 && strings.Contains(row[0], "@") {
			ex := ""
			if len(row) > 2 {
				ex = strings.Join(row[2:], " ")
			}
			return strings.TrimSpace(row[0]), row[1], ex, true
		}
	}
	tokens := []string{}
	var buf strings.Builder
	inQuote := rune(0)
	escaped := false
	for _, r := range raw {
		if escaped {
			buf.WriteRune(r)
			escaped = false
			continue
		}
		if r == '\\' {
			escaped = true
			continue
		}
		if inQuote != 0 {
			buf.WriteRune(r)
			if r == inQuote {
				inQuote = 0
			}
			continue
		}
		if r == '\'' || r == '"' {
			inQuote = r
			buf.WriteRune(r)
			continue
		}
		if unicode.IsSpace(r) {
			if buf.Len() > 0 {
				tokens = append(tokens, buf.String())
				buf.Reset()
			}
			continue
		}
		buf.WriteRune(r)
	}
	if buf.Len() > 0 {
		tokens = append(tokens, buf.String())
	}
	if len(tokens) < 2 {
		return "", "", "", false
	}
	if len(tokens) > 2 {
		extra = strings.Join(tokens[2:], " ")
	}
	return decodeImportToken(tokens[0]), decodeImportToken(tokens[1]), extra, true
}

func zipFiles(files map[string][]byte) []byte {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for name, content := range files {
		f, err := zw.Create(name)
		if err == nil {
			_, _ = f.Write(content)
		}
	}
	_ = zw.Close()
	return buf.Bytes()
}

func decodeJWTPayload(token string) map[string]any {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return map[string]any{}
	}
	payload := parts[1]
	b, err := base64.RawURLEncoding.DecodeString(payload)
	if err != nil {
		b, err = base64.URLEncoding.DecodeString(payload)
	}
	if err != nil {
		return map[string]any{}
	}
	var out map[string]any
	if json.Unmarshal(b, &out) != nil || out == nil {
		return map[string]any{}
	}
	return out
}

func timestampName(prefix, suffix string) string {
	return fmt.Sprintf("%s_%s.%s", prefix, time.Now().Format("20060102_150405"), suffix)
}
