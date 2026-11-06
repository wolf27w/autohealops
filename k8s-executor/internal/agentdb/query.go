// Package agentdb agent 侧「查对方集群数据库」：收到平台下发的查询请求（method=QueryDatabase），
// 在集群内连接目标数据库执行 SQL 并回传结果。当前支持 MySQL；PostgreSQL 预留。
package agentdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

type queryRequest struct {
	DBType     string `json:"db_type"`
	Host       string `json:"host"`
	Port       int    `json:"port"`
	Database   string `json:"database"`
	Username   string `json:"username"`
	Password   string `json:"password"`
	SQL        string `json:"sql"`
	Limit      int    `json:"limit"`       // 返回行数上限（默认 200，最多 5000；导出用大值）
	AllowWrite bool   `json:"allow_write"` // 允许 DML/DDL（平台侧仅 admin + 二次确认才会置 true）
}

// 返回行数上限（防大结果集撑爆隧道）：默认 200，导出可放宽到 5000
const (
	defaultMaxRows = 200
	hardMaxRows    = 5000
)

// isWriteSQL 判断是否写操作（第一条语句不是只读类型即视为写）
func isWriteSQL(sql string) bool {
	head := ""
	for _, f := range strings.Fields(sql) {
		head = strings.ToLower(f)
		break
	}
	switch head {
	case "select", "show", "desc", "describe", "explain", "with":
		return false
	}
	return true
}

// HandleQuery 处理平台下发的数据库查询，返回 JSON 响应字节（columns + rows）或错误。
func HandleQuery(ctx context.Context, payload []byte) ([]byte, error) {
	var req queryRequest
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析查询请求失败: %w", err)
	}
	if req.SQL == "" {
		return nil, fmt.Errorf("sql 不能为空")
	}
	if req.Host == "" {
		return nil, fmt.Errorf("host 不能为空")
	}
	if req.DBType == "" {
		req.DBType = "mysql"
	}
	if req.Port == 0 {
		req.Port = 3306
	}
	if req.DBType != "mysql" {
		return nil, fmt.Errorf("暂不支持 db_type=%s（当前仅 mysql）", req.DBType)
	}
	limit := req.Limit
	if limit <= 0 {
		limit = defaultMaxRows
	}
	if limit > hardMaxRows {
		limit = hardMaxRows
	}

	dsn := fmt.Sprintf("%s:%s@tcp(%s:%d)/%s?charset=utf8mb4&parseTime=true&timeout=10s&readTimeout=30s",
		req.Username, req.Password, req.Host, req.Port, req.Database)
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, fmt.Errorf("打开连接失败: %w", err)
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	qctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	// 写操作：平台侧已做 admin + 二次确认 + 审计；这里只执行并回传影响行数
	if isWriteSQL(req.SQL) {
		if !req.AllowWrite {
			return nil, fmt.Errorf("只读模式不允许执行写操作（需要在平台侧以管理员身份开启「执行」模式）")
		}
		res, err := db.ExecContext(qctx, req.SQL)
		if err != nil {
			return nil, fmt.Errorf("执行失败: %w", err)
		}
		affected, _ := res.RowsAffected()
		lastID, _ := res.LastInsertId()
		out, _ := json.Marshal(map[string]any{
			"columns": []string{}, "rows": [][]any{}, "truncated": false,
			"affected_rows": affected, "last_insert_id": lastID, "write": true,
		})
		return out, nil
	}

	rows, err := db.QueryContext(qctx, req.SQL)
	if err != nil {
		return nil, fmt.Errorf("查询失败: %w", err)
	}
	defer rows.Close()

	cols, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	outRows := make([][]any, 0, 32)
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if err := rows.Scan(ptrs...); err != nil {
			return nil, err
		}
		for i := range vals {
			vals[i] = normalize(vals[i])
		}
		outRows = append(outRows, vals)
		if len(outRows) >= limit {
			break
		}
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	resp, _ := json.Marshal(map[string]any{
		"columns":   cols,
		"rows":      outRows,
		"truncated": len(outRows) >= limit,
	})
	return resp, nil
}

// normalize 把数据库驱动返回的 []byte / time.Time 转成 JSON 友好的 string，
// 避免 []byte 被 json.Marshal 编码成 base64。
func normalize(v any) any {
	switch t := v.(type) {
	case []byte:
		return string(t)
	case time.Time:
		return t.Format(time.RFC3339)
	default:
		return v
	}
}
