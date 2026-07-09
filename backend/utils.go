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
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode"
)

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
	return t.UTC().Format(time.RFC3339)
}

func nullableTime(valid bool, t time.Time) any {
	if !valid || t.IsZero() {
		return nil
	}
	return formatTime(t)
}

func randomID(prefix string) string {
	var b [12]byte
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
