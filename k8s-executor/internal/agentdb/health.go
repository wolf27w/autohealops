// Package agentdb —— 数据库健康采集（在集群内执行，经隧道回传）。
package agentdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"strconv"
	"time"
)

type connParams struct {
	DBType   string `json:"db_type"`
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Database string `json:"database"`
	Username string `json:"username"`
	Password string `json:"password"`
}

func (p *connParams) normalize() {
	if p.DBType == "" {
		p.DBType = "mysql"
	}
	if p.Port == 0 {
		p.Port = 3306
	}
}

func openMySQL(p connParams) (*sql.DB, error) {
	dsn := fmt.Sprintf("%s:%s@tcp(%s:%d)/%s?charset=utf8mb4&parseTime=true&timeout=10s&readTimeout=30s",
		p.Username, p.Password, p.Host, p.Port, p.Database)
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, fmt.Errorf("打开连接失败: %w", err)
	}
	db.SetMaxOpenConns(1)
	return db, nil
}

// HandleHealth 采集 MySQL 健康概览（按需查询用，含表空间 TOP10 + 慢 SQL TOP5）。
func HandleHealth(ctx context.Context, payload []byte) ([]byte, error) {
	return handleMetrics(ctx, payload, true)
}

// HandleMetrics 指标采集（周期上报用）：withSlow=false 时跳过表空间 TOP10 / 慢 SQL，
// 供 10s 高频采集使用；慢 SQL 由调用方按 60s 单独触发，避免 performance_schema 被压垮。
func HandleMetrics(ctx context.Context, payload []byte, withSlow bool) ([]byte, error) {
	return handleMetrics(ctx, payload, withSlow)
}

func handleMetrics(ctx context.Context, payload []byte, withSlow bool) ([]byte, error) {
	var p connParams
	if err := json.Unmarshal(payload, &p); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	p.normalize()
	if p.DBType != "mysql" {
		return nil, fmt.Errorf("暂不支持 db_type=%s（健康采集当前仅 mysql）", p.DBType)
	}
	if p.Host == "" {
		return nil, fmt.Errorf("host 不能为空")
	}
	db, err := openMySQL(p)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	qctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	out := map[string]any{"db_type": "mysql", "database": p.Database}

	// 1. 版本
	var version string
	_ = db.QueryRowContext(qctx, "SELECT VERSION()").Scan(&version)
	out["version"] = version

	// 2. GLOBAL STATUS（一次拿全，再挑需要的）
	status := map[string]string{}
	if rows, err := db.QueryContext(qctx, "SHOW GLOBAL STATUS"); err == nil {
		for rows.Next() {
			var k, v string
			if err := rows.Scan(&k, &v); err == nil {
				status[k] = v
			}
		}
		rows.Close()
	}
	num := func(k string) int64 {
		n, _ := strconv.ParseInt(status[k], 10, 64)
		return n
	}
	out["uptime_seconds"] = num("Uptime")
	out["threads_connected"] = num("Threads_connected")
	out["threads_running"] = num("Threads_running")
	out["max_used_connections"] = num("Max_used_connections")
	out["queries"] = num("Queries")
	out["commits"] = num("Com_commit")
	out["rollbacks"] = num("Com_rollback")
	out["slow_queries"] = num("Slow_queries")
	out["row_lock_waits"] = num("Innodb_row_lock_waits")

	// 3. max_connections
	var maxConn int64
	_ = db.QueryRowContext(qctx, "SELECT @@max_connections").Scan(&maxConn)
	out["max_connections"] = maxConn

	// 4. 当前锁等待数
	var lockWaits int64 = -1
	if err := db.QueryRowContext(qctx,
		"SELECT COUNT(*) FROM performance_schema.data_lock_waits").Scan(&lockWaits); err != nil {
		_ = db.QueryRowContext(qctx,
			"SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_state='LOCK WAIT'").Scan(&lockWaits)
	}
	out["lock_waits"] = lockWaits

	// 5. 表空间 TOP10（当前库）
	type tbl struct {
		Table  string  `json:"table"`
		Rows   int64   `json:"rows"`
		SizeMB float64 `json:"size_mb"`
	}
	tables := make([]tbl, 0, 10)
	if withSlow {
		if rows, err := db.QueryContext(qctx, `
		SELECT table_name, TABLE_ROWS, ROUND((data_length+index_length)/1024/1024, 2)
		FROM information_schema.tables
		WHERE table_schema = DATABASE()
		ORDER BY (data_length+index_length) DESC LIMIT 10`); err == nil {
			for rows.Next() {
				var t tbl
				var r sql.NullInt64
				var mb sql.NullFloat64
				if err := rows.Scan(&t.Table, &r, &mb); err == nil {
					t.Rows, t.SizeMB = r.Int64, mb.Float64
					tables = append(tables, t)
				}
			}
			rows.Close()
		}
	}
	out["top_tables"] = tables

	// 6. 慢 SQL TOP5（当前库，performance_schema）
	type slowItem struct {
		SQLText string  `json:"sql_text"`
		ExecCnt int64   `json:"exec_count"`
		AvgMS   float64 `json:"avg_ms"`
		MaxMS   float64 `json:"max_ms"`
	}
	slow := make([]slowItem, 0, 5)
	if withSlow && p.Database != "" {
		if rows, err := db.QueryContext(qctx, `
			SELECT LEFT(DIGEST_TEXT, 300), COUNT_STAR,
			       IFNULL(SUM_TIMER_WAIT/1000000000/NULLIF(COUNT_STAR,0),0),
			       IFNULL(MAX_TIMER_WAIT/1000000000,0)
			FROM performance_schema.events_statements_summary_by_digest
			WHERE SCHEMA_NAME = ?
			ORDER BY SUM_TIMER_WAIT DESC LIMIT 5`, p.Database); err == nil {
			for rows.Next() {
				var s slowItem
				if err := rows.Scan(&s.SQLText, &s.ExecCnt, &s.AvgMS, &s.MaxMS); err == nil {
					slow = append(slow, s)
				}
			}
			rows.Close()
		}
	}
	out["top_slow"] = slow

	return json.Marshal(out)
}
