// Package agentdb —— 方言抽象：把「数据库能力」和「具体数据库类型」解耦。
//
// 现在的实现：
//
//	mysql  → mysqlDialect（复用 query/schema/objects 里已有的 MySQL 逻辑）
//
// 以后加 PG / Oracle：
//  1. 新增 pg.go / oracle.go 实现 Dialect
//  2. 在 DialectFor 里注册
//
// 隧道方法、平台接口、前端都不用动。
package agentdb

import (
	"context"
	"encoding/json"
	"fmt"
)

// Dialect 一个数据库类型需要提供的能力（只读查询 + 元数据 + 导入导出）
type Dialect interface {
	Kind() string
	Query(ctx context.Context, sql string, limit int, allowWrite bool) (map[string]any, error)
	Schema(ctx context.Context) (map[string]any, error)
	DDL(ctx context.Context, kind, db, name string) (map[string]any, error)
	Export(ctx context.Context, db, table string, rows int) (map[string]any, error)
	Import(ctx context.Context, db, table, format, content string) (map[string]any, error)
}

// DialectFor 按 agent 配置里的 key 解析出对应方言实现。
func DialectFor(key string) (Dialect, *DBConfig, error) {
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
	switch cfg.Type {
	case "mysql":
		return &mysqlDialect{key: key}, cfg, nil
	case "pg", "postgres":
		return &pgDialect{key: key}, cfg, nil
	default:
		return nil, cfg, fmt.Errorf("暂不支持 db_type=%s（当前仅 mysql/pg）", cfg.Type)
	}
}

// ---------- MySQL 适配（薄封装，复用现有实现） ----------

type mysqlDialect struct{ key string }

func (d *mysqlDialect) Kind() string { return "mysql" }

func (d *mysqlDialect) Query(ctx context.Context, sqlText string, limit int, allowWrite bool) (map[string]any, error) {
	payload, _ := json.Marshal(map[string]any{"key": d.key, "sql": sqlText, "limit": limit, "allow_write": allowWrite})
	raw, err := QueryByKey(ctx, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (d *mysqlDialect) Schema(ctx context.Context) (map[string]any, error) {
	payload, _ := json.Marshal(map[string]any{"key": d.key})
	raw, err := SchemaByKey(ctx, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (d *mysqlDialect) DDL(ctx context.Context, kind, db, name string) (map[string]any, error) {
	payload, _ := json.Marshal(map[string]any{"key": d.key, "kind": kind, "db": db, "name": name})
	raw, err := ObjectDDLByKey(ctx, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (d *mysqlDialect) Export(ctx context.Context, db, table string, rows int) (map[string]any, error) {
	payload, _ := json.Marshal(map[string]any{"key": d.key, "db": db, "table": table, "rows": rows})
	raw, err := ExportByKey(ctx, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (d *mysqlDialect) Import(ctx context.Context, db, table, format, content string) (map[string]any, error) {
	payload, _ := json.Marshal(map[string]any{"key": d.key, "db": db, "table": table, "format": format, "content": content})
	raw, err := ImportByKey(ctx, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// ---------- PostgreSQL 适配（R1：只读查询 + 元数据；DDL/导出/导入待方言实现） ----------

type pgDialect struct{ key string }

func (d *pgDialect) Kind() string { return "pg" }

func (d *pgDialect) Query(ctx context.Context, sqlText string, limit int, allowWrite bool) (map[string]any, error) {
	payload, _ := json.Marshal(map[string]any{"key": d.key, "sql": sqlText, "limit": limit, "allow_write": allowWrite})
	raw, err := QueryByKey(ctx, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (d *pgDialect) Schema(ctx context.Context) (map[string]any, error) {
	payload, _ := json.Marshal(map[string]any{"key": d.key})
	raw, err := SchemaByKey(ctx, payload)
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func (d *pgDialect) DDL(ctx context.Context, kind, db, name string) (map[string]any, error) {
	return nil, fmt.Errorf("pg 暂不支持导出对象 DDL（R1 仅查询+健康采集）")
}

func (d *pgDialect) Export(ctx context.Context, db, table string, rows int) (map[string]any, error) {
	return nil, fmt.Errorf("pg 暂不支持导出（R1 仅查询+健康采集）")
}

func (d *pgDialect) Import(ctx context.Context, db, table, format, content string) (map[string]any, error) {
	return nil, fmt.Errorf("pg 暂不支持导入（R1 仅查询+健康采集）")
}
