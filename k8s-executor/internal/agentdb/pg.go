// Package agentdb —— PostgreSQL 方言（R1，2026-11-09）。
//
// 用 pgx/v5 的 database/sql 适配器（driver 名 "pgx"）接入：
// 只读查询/健康采集/元数据 与 MySQL 走同一套 database/sql 代码，
// 差异只在 DSN 拼装与 PG 特有的健康采集 SQL。
package agentdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"

	_ "github.com/jackc/pgx/v5/stdlib"
)

// pgDSN 拼 PG 连接串（URL 形式，凭据做百分号编码，避免特殊字符截断）。
func pgDSN(p connParams) string {
	u := url.URL{
		Scheme: "postgres",
		Host:   fmt.Sprintf("%s:%d", p.Host, p.Port),
		Path:   "/" + p.Database,
	}
	if p.Username != "" || p.Password != "" {
		u.User = url.UserPassword(p.Username, p.Password)
	}
	q := u.Query()
	q.Set("sslmode", "disable")
	q.Set("connect_timeout", "10")
	u.RawQuery = q.Encode()
	return u.String()
}

// openPG 打开 PG 连接（pgx stdlib）。
func openPG(p connParams) (*sql.DB, error) {
	db, err := sql.Open("pgx", pgDSN(p))
	if err != nil {
		return nil, fmt.Errorf("打开连接失败: %w", err)
	}
	db.SetMaxOpenConns(1)
	return db, nil
}

// openDB 按类型打开连接（mysql / pg）。
func openDB(p connParams) (*sql.DB, error) {
	switch p.DBType {
	case "pg", "postgres":
		return openPG(p)
	default:
		return openMySQL(p)
	}
}

// pgMetrics 采集 PG 健康概览（连接/事务/锁/库表大小/慢查询）。
func pgMetrics(ctx context.Context, db *sql.DB, p connParams, withSlow bool) ([]byte, error) {
	out := map[string]any{"db_type": "pg", "database": p.Database}

	// 1. 版本
	var version string
	_ = db.QueryRowContext(ctx, "SELECT version()").Scan(&version)
	out["version"] = version

	// 2. max_connections（SHOW 返回文本）
	var maxConnStr string
	if err := db.QueryRowContext(ctx, "SHOW max_connections").Scan(&maxConnStr); err == nil {
		if n, err := strconv.ParseInt(maxConnStr, 10, 64); err == nil {
			out["max_connections"] = n
		}
	}

	// 3. 连接数与状态（按当前库）
	var total, active, idle int64
	_ = db.QueryRowContext(ctx, `
		SELECT count(*)::bigint,
		       count(*) FILTER (WHERE state='active')::bigint,
		       count(*) FILTER (WHERE state='idle')::bigint
		FROM pg_stat_activity WHERE datname=$1`, p.Database).Scan(&total, &active, &idle)
	out["threads_connected"] = total
	out["threads_running"] = active
	out["connections_idle"] = idle

	// 4. 事务/锁（当前库累计，来自 pg_stat_database）
	var xactCommit, xactRollback, deadlocks, blksHit, blksRead int64
	if err := db.QueryRowContext(ctx, `
		SELECT xact_commit, xact_rollback, deadlocks, blks_hit, blks_read
		FROM pg_stat_database WHERE datname=$1`, p.Database).
		Scan(&xactCommit, &xactRollback, &deadlocks, &blksHit, &blksRead); err == nil {
		out["commits"] = xactCommit
		out["rollbacks"] = xactRollback
		out["deadlocks"] = deadlocks
		out["cache_hit_blocks"] = blksHit
		out["cache_read_blocks"] = blksRead
	}

	// 5. 当前锁等待（未授予的锁）
	var lockWaits int64 = -1
	_ = db.QueryRowContext(ctx, "SELECT count(*)::bigint FROM pg_locks WHERE granted=false").Scan(&lockWaits)
	out["lock_waits"] = lockWaits

	// 6. 库大小
	var dbSize int64
	if err := db.QueryRowContext(ctx, "SELECT pg_database_size($1)::bigint", p.Database).Scan(&dbSize); err == nil {
		out["database_size_bytes"] = dbSize
		out["database_size_mb"] = float64(dbSize) / 1024 / 1024
	}

	// 7. 表空间 TOP10（当前 schema=public，用户表 + 物化视图）
	type tbl struct {
		Table  string  `json:"table"`
		Rows   int64   `json:"rows"`
		SizeMB float64 `json:"size_mb"`
	}
	tables := make([]tbl, 0, 10)
	if withSlow {
		if rows, err := db.QueryContext(ctx, `
			SELECT c.relname,
			       CASE WHEN c.reltuples < 0 THEN 0 ELSE c.reltuples::bigint END,
			       ROUND(pg_total_relation_size(c.oid)/1024.0/1024.0, 2)
			FROM pg_class c
			JOIN pg_namespace n ON n.oid = c.relnamespace
			WHERE n.nspname = 'public' AND c.relkind IN ('r','m','p')
			ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 10`); err == nil {
			for rows.Next() {
				var t tbl
				if rows.Scan(&t.Table, &t.Rows, &t.SizeMB) == nil {
					tables = append(tables, t)
				}
			}
			rows.Close()
		}
	}
	out["top_tables"] = tables

	// 8. 慢 SQL TOP5（pg_stat_statements，扩展未装则跳过）
	type slowItem struct {
		SQLText string  `json:"sql_text"`
		ExecCnt int64   `json:"exec_count"`
		AvgMS   float64 `json:"avg_ms"`
		MaxMS   float64 `json:"max_ms"`
	}
	slow := make([]slowItem, 0, 5)
	if withSlow {
		if rows, err := db.QueryContext(ctx, `
			SELECT LEFT(query, 300), calls,
			       ROUND((mean_exec_time)::numeric, 2),
			       ROUND((max_exec_time)::numeric, 2)
			FROM pg_stat_statements
			WHERE dbid = (SELECT oid FROM pg_database WHERE datname=$1)
			ORDER BY total_exec_time DESC LIMIT 5`, p.Database); err == nil {
			for rows.Next() {
				var s slowItem
				if rows.Scan(&s.SQLText, &s.ExecCnt, &s.AvgMS, &s.MaxMS) == nil {
					slow = append(slow, s)
				}
			}
			rows.Close()
		}
	}
	out["top_slow"] = slow

	return json.Marshal(out)
}
