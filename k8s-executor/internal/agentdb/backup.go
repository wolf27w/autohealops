// Package agentdb —— 整库/多库逻辑备份（SHOW CREATE TABLE + SELECT *，不依赖 mysqldump）。
package agentdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

const maxBackupBytes = 50 << 20 // 备份输出上限 50MB

// BackupByKey 逻辑备份（MySQL）。payload:
//
//	{"key","databases":["a","b"],"schema_only":bool}
//
// 响应：{"sql":"...","bytes":n}；总输出超过 50MB 返回错误「导出超过 50MB，请按库/表分批」。
func BackupByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key        string   `json:"key"`
		Databases  []string `json:"databases"`
		SchemaOnly bool     `json:"schema_only"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Key == "" {
		return nil, fmt.Errorf("key 必填")
	}
	db, cfg, err := openByKey(req.Key)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	if len(req.Databases) == 0 {
		if cfg.Database == "" {
			return nil, fmt.Errorf("databases 不能为空")
		}
		req.Databases = []string{cfg.Database}
	}
	for _, d := range req.Databases {
		if err := validateIdent(d); err != nil {
			return nil, err
		}
	}

	qctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()

	var sb strings.Builder
	sb.WriteString("-- AutoHealOps logical backup\n")
	sb.WriteString(fmt.Sprintf("-- time: %s  schema_only: %v\n", time.Now().Format("2006-01-02 15:04:05"), req.SchemaOnly))
	sb.WriteString("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")

	for _, d := range req.Databases {
		if sb.Len() > maxBackupBytes {
			return nil, fmt.Errorf("导出超过 50MB，请按库/表分批")
		}
		tables, err := listBaseTables(qctx, db, d)
		if err != nil {
			return nil, err
		}
		sb.WriteString(fmt.Sprintf("-- ========== database %s ==========\n\n", d))
		for _, t := range tables {
			if err := dumpTable(qctx, db, d, t, req.SchemaOnly, &sb); err != nil {
				return nil, err
			}
			if sb.Len() > maxBackupBytes {
				return nil, fmt.Errorf("导出超过 50MB，请按库/表分批")
			}
		}
	}
	sb.WriteString("SET FOREIGN_KEY_CHECKS=1;\n")

	out, _ := json.Marshal(map[string]any{
		"sql":         sb.String(),
		"bytes":       sb.Len(),
		"databases":   req.Databases,
		"schema_only": req.SchemaOnly,
	})
	return out, nil
}

// listBaseTables 列出指定库下的 BASE TABLE（按表名排序）。
func listBaseTables(ctx context.Context, db *sql.DB, schema string) ([]string, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT table_name FROM information_schema.tables
		WHERE table_schema = ? AND table_type = 'BASE TABLE'
		ORDER BY table_name`, schema)
	if err != nil {
		return nil, fmt.Errorf("查询表清单失败: %w", err)
	}
	defer rows.Close()
	tables := make([]string, 0, 64)
	for rows.Next() {
		var t string
		if rows.Scan(&t) == nil {
			tables = append(tables, t)
		}
	}
	return tables, rows.Err()
}

// dumpTable 把单张表（结构 + 数据）写入 sb；schemaOnly 时只写结构。
func dumpTable(ctx context.Context, db *sql.DB, schema, table string, schemaOnly bool, sb *strings.Builder) error {
	full := quoteIdent(schema) + "." + quoteIdent(table)
	var name, ddl string
	if err := db.QueryRowContext(ctx, "SHOW CREATE TABLE "+full).Scan(&name, &ddl); err != nil {
		return fmt.Errorf("获取 %s 建表语句失败: %w", full, err)
	}
	sb.WriteString(fmt.Sprintf("-- ---------- table %s ----------\n", table))
	sb.WriteString(ddl + ";\n\n")
	if schemaOnly {
		return nil
	}

	rows, err := db.QueryContext(ctx, "SELECT * FROM "+full)
	if err != nil {
		return fmt.Errorf("读取 %s 数据失败: %w", full, err)
	}
	defer rows.Close()
	cols, err := rows.Columns()
	if err != nil {
		return err
	}
	colList := make([]string, len(cols))
	for i, c := range cols {
		colList[i] = quoteIdent(c)
	}
	prefix := "INSERT INTO " + full + " (" + strings.Join(colList, ",") + ") VALUES "
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return err
		}
		parts := make([]string, len(vals))
		for i, v := range vals {
			parts[i] = sqlValue(normalize(v))
		}
		sb.WriteString(prefix + "(" + strings.Join(parts, ",") + ");\n")
	}
	sb.WriteString("\n")
	return rows.Err()
}
