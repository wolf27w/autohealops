// Package agentdb —— 架构元数据（供平台 SQL 编辑器做补全）。
// 只返回当前账号有权限看到的内容：MySQL 的 information_schema 会自动按权限过滤。
package agentdb

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"
)

type schemaTable struct {
	DB   string `json:"db"`
	Name string `json:"name"`
	Type string `json:"type"`
}

// SchemaByKey 按 key 返回 databases + tables（最多 3000 张表，避免隧道过大）。
func SchemaByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key string `json:"key"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Key == "" {
		return nil, fmt.Errorf("key 必填")
	}
	cfgs, err := LoadConfigs(configPath)
	if err != nil {
		return nil, err
	}
	var cfg *DBConfig
	for i := range cfgs {
		if cfgs[i].Key == req.Key {
			cfg = &cfgs[i]
			break
		}
	}
	if cfg == nil {
		return nil, fmt.Errorf("agent 配置里没有 key=%s", req.Key)
	}
	if cfg.Type != "mysql" {
		return nil, fmt.Errorf("暂不支持 db_type=%s 的元数据（当前仅 mysql）", cfg.Type)
	}

	db, err := openMySQL(connParams{
		DBType: cfg.Type, Host: cfg.Host, Port: cfg.Port, Database: cfg.Database,
		Username: cfg.Username, Password: cfg.Password,
	})
	if err != nil {
		return nil, err
	}
	defer db.Close()
	db.SetMaxOpenConns(1)

	qctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	dbs := make([]string, 0, 16)
	if rows, err := db.QueryContext(qctx, "SHOW DATABASES"); err == nil {
		for rows.Next() {
			var s string
			if rows.Scan(&s) == nil {
				dbs = append(dbs, s)
			}
		}
		rows.Close()
	}

	tables := make([]schemaTable, 0, 128)
	if rows, err := db.QueryContext(qctx, `
		SELECT table_schema, table_name, table_type
		FROM information_schema.tables
		ORDER BY table_schema, table_name
		LIMIT 3000`); err == nil {
		for rows.Next() {
			var t schemaTable
			var typ sql.NullString
			if rows.Scan(&t.DB, &t.Name, &typ) == nil {
				t.Type = typ.String
				tables = append(tables, t)
			}
		}
		rows.Close()
	}

	out, _ := json.Marshal(map[string]any{
		"key": cfg.Key, "db_type": cfg.Type, "database": cfg.Database,
		"databases": dbs, "tables": tables,
	})
	return out, nil
}
