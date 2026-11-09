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

type schemaObject struct {
	DB    string `json:"db"`
	Name  string `json:"name"`
	Type  string `json:"type"`  // FUNCTION / PROCEDURE / EVENT
	Extra string `json:"extra"` // 事件状态等
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
	if cfg.Type == "postgres" {
		cfg.Type = "pg"
	}
	if cfg.Type != "mysql" && cfg.Type != "pg" {
		return nil, fmt.Errorf("暂不支持 db_type=%s 的元数据（当前 mysql/pg）", cfg.Type)
	}

	db, err := openDB(connParams{
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
	if cfg.Type == "pg" {
		if rows, err := db.QueryContext(qctx,
			"SELECT datname FROM pg_database WHERE datistemplate=false AND datallowconn ORDER BY datname"); err == nil {
			for rows.Next() {
				var s string
				if rows.Scan(&s) == nil {
					dbs = append(dbs, s)
				}
			}
			rows.Close()
		}
	} else if rows, err := db.QueryContext(qctx, "SHOW DATABASES"); err == nil {
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

	// 视图
	views := make([]schemaTable, 0, 32)
	if rows, err := db.QueryContext(qctx, `
		SELECT table_schema, table_name FROM information_schema.views
		ORDER BY table_schema, table_name LIMIT 500`); err == nil {
		for rows.Next() {
			var t schemaTable
			if rows.Scan(&t.DB, &t.Name) == nil {
				t.Type = "VIEW"
				views = append(views, t)
			}
		}
		rows.Close()
	}

	// 函数 / 存储过程
	routines := make([]schemaObject, 0, 32)
	if rows, err := db.QueryContext(qctx, `
		SELECT routine_schema, routine_name, routine_type FROM information_schema.routines
		ORDER BY routine_schema, routine_type, routine_name LIMIT 500`); err == nil {
		for rows.Next() {
			var o schemaObject
			if rows.Scan(&o.DB, &o.Name, &o.Type) == nil {
				routines = append(routines, o)
			}
		}
		rows.Close()
	}

	// 事件
	events := make([]schemaObject, 0, 16)
	if rows, err := db.QueryContext(qctx, `
		SELECT event_schema, event_name, IFNULL(status,'') FROM information_schema.events
		ORDER BY event_schema, event_name LIMIT 500`); err == nil {
		for rows.Next() {
			var o schemaObject
			if rows.Scan(&o.DB, &o.Name, &o.Extra) == nil {
				o.Type = "EVENT"
				events = append(events, o)
			}
		}
		rows.Close()
	}

	out, _ := json.Marshal(map[string]any{
		"key": cfg.Key, "db_type": cfg.Type, "database": cfg.Database,
		"databases": dbs, "tables": tables,
		"views": views, "routines": routines, "events": events,
	})
	return out, nil
}
