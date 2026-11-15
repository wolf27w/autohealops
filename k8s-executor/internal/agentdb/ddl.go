// Package agentdb —— 建库 / 删库（MySQL；PG 暂不支持，openByKey 会直接拒绝非 mysql）。
package agentdb

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"time"
)

// identRe 库名/表名白名单：仅字母/数字/下划线，长度 1-64（拼进 SQL 前必须通过，防注入）。
var identRe = regexp.MustCompile(`^[A-Za-z0-9_]{1,64}$`)

// validateIdent 校验数据库/表标识符，不合法则报错（避免拼进 SQL 造成注入）。
func validateIdent(name string) error {
	if !identRe.MatchString(name) {
		return fmt.Errorf("非法名称 %q：只允许字母/数字/下划线，长度 1-64", name)
	}
	return nil
}

// CreateDatabaseByKey 建库。payload: {"key","name"} → {"ok":true}
func CreateDatabaseByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key  string `json:"key"`
		Name string `json:"name"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Key == "" || req.Name == "" {
		return nil, fmt.Errorf("key 和 name 必填")
	}
	if err := validateIdent(req.Name); err != nil {
		return nil, err
	}
	db, _, err := openByKey(req.Key)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	qctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	stmt := "CREATE DATABASE IF NOT EXISTS " + quoteIdent(req.Name) +
		" DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
	if _, err := db.ExecContext(qctx, stmt); err != nil {
		return nil, fmt.Errorf("建库失败: %w", err)
	}
	out, _ := json.Marshal(map[string]any{"ok": true})
	return out, nil
}

// DropDatabaseByKey 删库。payload: {"key","name"} → {"ok":true}
func DropDatabaseByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key  string `json:"key"`
		Name string `json:"name"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Key == "" || req.Name == "" {
		return nil, fmt.Errorf("key 和 name 必填")
	}
	if err := validateIdent(req.Name); err != nil {
		return nil, err
	}
	db, _, err := openByKey(req.Key)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	qctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	stmt := "DROP DATABASE IF EXISTS " + quoteIdent(req.Name)
	if _, err := db.ExecContext(qctx, stmt); err != nil {
		return nil, fmt.Errorf("删库失败: %w", err)
	}
	out, _ := json.Marshal(map[string]any{"ok": true})
	return out, nil
}
