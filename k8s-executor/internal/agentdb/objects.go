// Package agentdb —— 对象 DDL / 导出 / 导入（MySQL）。
package agentdb

import (
	"context"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

const (
	maxExportRows  = 5000
	maxExportBytes = 5 << 20 // 5 MB：超过则截断，避免撑爆隧道
	maxImportBytes = 5 << 20
)

func openByKey(key string) (*sql.DB, *DBConfig, error) {
	cfgs, err := LoadConfigs(configPath)
	if err != nil {
		return nil, nil, err
	}
	var cfg *DBConfig
	for i := range cfgs {
		if cfgs[i].Key == key {
			cfg = &cfgs[i]
			break
		}
	}
	if cfg == nil {
		return nil, nil, fmt.Errorf("agent 配置里没有 key=%s", key)
	}
	if cfg.Type != "mysql" {
		return nil, nil, fmt.Errorf("暂不支持 db_type=%s（当前仅 mysql）", cfg.Type)
	}
	db, err := openMySQL(connParams{
		DBType: cfg.Type, Host: cfg.Host, Port: cfg.Port, Database: cfg.Database,
		Username: cfg.Username, Password: cfg.Password,
	})
	if err != nil {
		return nil, nil, err
	}
	db.SetMaxOpenConns(1)
	return db, cfg, nil
}

func quoteIdent(s string) string { return "`" + strings.ReplaceAll(s, "`", "``") + "`" }

func sqlValue(v any) string {
	switch t := v.(type) {
	case nil:
		return "NULL"
	case []byte:
		return "'" + strings.ReplaceAll(strings.ReplaceAll(string(t), "\\", "\\\\"), "'", "\\'") + "'"
	case string:
		return "'" + strings.ReplaceAll(strings.ReplaceAll(t, "\\", "\\\\"), "'", "\\'") + "'"
	case time.Time:
		return "'" + t.Format("2006-01-02 15:04:05") + "'"
	default:
		return fmt.Sprintf("%v", v)
	}
}

// ObjectDDLByKey 返回表/视图/函数/存储过程/事件的建表(创建)语句。
func ObjectDDLByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key  string `json:"key"`
		Kind string `json:"kind"` // table / view / function / procedure / event
		DB   string `json:"db"`
		Name string `json:"name"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Key == "" || req.Name == "" {
		return nil, fmt.Errorf("key 和 name 必填")
	}
	db, _, err := openByKey(req.Key)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	qctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	full := quoteIdent(req.Name)
	if req.DB != "" {
		full = quoteIdent(req.DB) + "." + full
	}
	var stmt string
	switch strings.ToLower(req.Kind) {
	case "table":
		stmt = "SHOW CREATE TABLE " + full
	case "view":
		stmt = "SHOW CREATE VIEW " + full
	case "function":
		stmt = "SHOW CREATE FUNCTION " + full
	case "procedure":
		stmt = "SHOW CREATE PROCEDURE " + full
	case "event":
		stmt = "SHOW CREATE EVENT " + full
	default:
		return nil, fmt.Errorf("不支持的 kind=%s", req.Kind)
	}

	rows, err := db.QueryContext(qctx, stmt)
	if err != nil {
		return nil, fmt.Errorf("获取 DDL 失败: %w", err)
	}
	defer rows.Close()
	cols, _ := rows.Columns()
	out := map[string]any{"kind": req.Kind, "name": req.Name}
	if rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err == nil {
			// 通常第 2 列是 "Create xxx"
			ddl := ""
			for i, c := range cols {
				if strings.HasPrefix(strings.ToLower(c), "create ") {
					ddl = fmt.Sprint(normalize(vals[i]))
					break
				}
			}
			if ddl == "" && len(vals) > 1 {
				ddl = fmt.Sprint(normalize(vals[1]))
			}
			out["ddl"] = ddl + ";"
		}
	}
	b, _ := json.Marshal(out)
	return b, nil
}

// ExportByKey 导出库/表为 SQL（CREATE + INSERT），上限 5MB / 单表 5000 行。
func ExportByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key   string `json:"key"`
		DB    string `json:"db"`
		Table string `json:"table"`
		Rows  int    `json:"rows"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Rows <= 0 || req.Rows > maxExportRows {
		req.Rows = 1000
	}
	db, cfg, err := openByKey(req.Key)
	if err != nil {
		return nil, err
	}
	defer db.Close()
	if req.DB == "" {
		req.DB = cfg.Database
	}
	if req.DB == "" {
		return nil, fmt.Errorf("db 不能为空")
	}

	qctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()

	// 表清单：指定表 or 库内全部 BASE TABLE
	tables := []string{}
	if req.Table != "" {
		tables = append(tables, req.Table)
	} else {
		rows, err := db.QueryContext(qctx, `
			SELECT table_name FROM information_schema.tables
			WHERE table_schema = ? AND table_type = 'BASE TABLE'
			ORDER BY table_name`, req.DB)
		if err != nil {
			return nil, fmt.Errorf("查询表清单失败: %w", err)
		}
		for rows.Next() {
			var t string
			if rows.Scan(&t) == nil {
				tables = append(tables, t)
			}
		}
		rows.Close()
	}

	var sb strings.Builder
	sb.WriteString("-- AutoHealOps export\n")
	sb.WriteString(fmt.Sprintf("-- database: %s  time: %s\n", req.DB, time.Now().Format("2006-01-02 15:04:05")))
	sb.WriteString("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")

	totalRows, truncated := 0, false
	for _, tbl := range tables {
		if sb.Len() > maxExportBytes {
			truncated = true
			break
		}
		full := quoteIdent(req.DB) + "." + quoteIdent(tbl)
		var name, ddl string
		if err := db.QueryRowContext(qctx, "SHOW CREATE TABLE "+full).Scan(&name, &ddl); err != nil {
			continue
		}
		sb.WriteString(fmt.Sprintf("-- ---------- table %s ----------\n", tbl))
		sb.WriteString(ddl + ";\n\n")

		rows, err := db.QueryContext(qctx, fmt.Sprintf("SELECT * FROM %s LIMIT %d", full, req.Rows))
		if err != nil {
			sb.WriteString(fmt.Sprintf("-- skip data: %v\n\n", err))
			continue
		}
		cols, _ := rows.Columns()
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
				continue
			}
			parts := make([]string, len(vals))
			for i, v := range vals {
				parts[i] = sqlValue(normalize(v))
			}
			sb.WriteString(prefix + "(" + strings.Join(parts, ",") + ");\n")
			totalRows++
			if sb.Len() > maxExportBytes {
				truncated = true
				break
			}
		}
		rows.Close()
		sb.WriteString("\n")
		if truncated {
			break
		}
	}
	sb.WriteString("SET FOREIGN_KEY_CHECKS=1;\n")

	out, _ := json.Marshal(map[string]any{
		"db": req.DB, "table": req.Table, "tables": len(tables), "rows": totalRows,
		"bytes": sb.Len(), "truncated": truncated, "sql": sb.String(),
	})
	return out, nil
}

// ImportByKey 导入 SQL 脚本或 CSV（仅写操作；平台侧已做 admin + confirm + 审计）。
func ImportByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key     string `json:"key"`
		DB      string `json:"db"`
		Table   string `json:"table"`
		Format  string `json:"format"` // sql / csv
		Content string `json:"content"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if len(req.Content) == 0 {
		return nil, fmt.Errorf("content 不能为空")
	}
	if len(req.Content) > maxImportBytes {
		return nil, fmt.Errorf("内容超过 5MB，请拆分后导入")
	}
	db, cfg, err := openByKey(req.Key)
	if err != nil {
		return nil, err
	}
	defer db.Close()
	if req.DB == "" {
		req.DB = cfg.Database
	}
	qctx, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	if req.DB != "" {
		if _, err := db.ExecContext(qctx, "USE "+quoteIdent(req.DB)); err != nil {
			return nil, fmt.Errorf("切换数据库失败: %w", err)
		}
	}

	executed, skipped := 0, 0
	switch strings.ToLower(req.Format) {
	case "csv":
		if req.Table == "" {
			return nil, fmt.Errorf("CSV 导入必须指定表名")
		}
		rd := csv.NewReader(strings.NewReader(strings.TrimPrefix(req.Content, "\ufeff")))
		rd.FieldsPerRecord = -1
		records, err := rd.ReadAll()
		if err != nil {
			return nil, fmt.Errorf("解析 CSV 失败: %w", err)
		}
		if len(records) < 2 {
			return nil, fmt.Errorf("CSV 至少需要表头 + 1 行数据")
		}
		header := records[0]
		cols := make([]string, len(header))
		ph := make([]string, len(header))
		for i, h := range header {
			cols[i] = quoteIdent(strings.TrimSpace(h))
			ph[i] = "?"
		}
		stmt := "INSERT INTO " + quoteIdent(req.Table) + " (" + strings.Join(cols, ",") + ") VALUES (" + strings.Join(ph, ",") + ")"
		for _, rec := range records[1:] {
			if len(rec) == 0 {
				continue
			}
			args := make([]any, len(header))
			for i := range header {
				if i < len(rec) {
					args[i] = rec[i]
				} else {
					args[i] = ""
				}
			}
			if _, err := db.ExecContext(qctx, stmt, args...); err != nil {
				return nil, fmt.Errorf("CSV 第 %d 行导入失败: %w", executed+2, err)
			}
			executed++
		}
	case "sql", "":
		for _, stmt := range splitSQL(req.Content) {
			s := strings.TrimSpace(stmt)
			if s == "" || strings.HasPrefix(s, "--") {
				skipped++
				continue
			}
			if _, err := db.ExecContext(qctx, s); err != nil {
				return nil, fmt.Errorf("第 %d 条语句执行失败: %w", executed+1, err)
			}
			executed++
		}
	default:
		return nil, fmt.Errorf("不支持的 format=%s（sql/csv）", req.Format)
	}

	out, _ := json.Marshal(map[string]any{
		"format": req.Format, "executed": executed, "skipped": skipped, "bytes": len(req.Content),
	})
	return out, nil
}

// splitSQL 按分号拆分（忽略引号内的分号；不处理 DELIMITER，存储过程请单独执行）
func splitSQL(s string) []string {
	var out []string
	var cur strings.Builder
	var quote rune
	for _, r := range s {
		switch {
		case quote != 0:
			cur.WriteRune(r)
			if r == quote {
				quote = 0
			}
		case r == '\'' || r == '"' || r == '`':
			quote = r
			cur.WriteRune(r)
		case r == ';':
			out = append(out, cur.String())
			cur.Reset()
		default:
			cur.WriteRune(r)
		}
	}
	if strings.TrimSpace(cur.String()) != "" {
		out = append(out, cur.String())
	}
	return out
}
