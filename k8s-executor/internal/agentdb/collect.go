// Package agentdb —— 周期采集（配置在 agent 侧，账密不出集群）。
package agentdb

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
)

// CollectOne 采集单个库的指标，返回可直接上报的行：
// {key,name,db_type,host,port,database,collected_at,metrics:{...},slow_sql:[...],error}
func CollectOne(ctx context.Context, cfg DBConfig, withSlow bool) map[string]any {
	row := map[string]any{
		"key":          cfg.Key,
		"name":         cfg.DisplayName(),
		"db_type":      cfg.Type,
		"host":         cfg.Host,
		"port":         cfg.Port,
		"database":     cfg.Database,
		"collected_at": time.Now().Format(time.RFC3339),
	}

	params, err := json.Marshal(connParams{
		DBType:   cfg.Type,
		Host:     cfg.Host,
		Port:     cfg.Port,
		Database: cfg.Database,
		Username: cfg.Username,
		Password: cfg.Password,
	})
	if err != nil {
		row["error"] = "序列化连接参数失败: " + err.Error()
		return row
	}

	raw, err := handleMetrics(ctx, params, withSlow)
	if err != nil {
		row["error"] = err.Error()
		return row
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		row["error"] = "解析采集结果失败: " + err.Error()
		return row
	}
	row["metrics"] = m
	if slow, ok := m["top_slow"]; ok {
		row["slow_sql"] = slow
	}
	return row
}

// CollectOnce 采集全部配置库：单个库失败不影响其它库（失败行只带 error）。
func CollectOnce(ctx context.Context, cfgs []DBConfig, withSlow bool) []map[string]any {
	out := make([]map[string]any, 0, len(cfgs))
	for _, c := range cfgs {
		qctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		out = append(out, CollectOne(qctx, c, withSlow))
		cancel()
	}
	return out
}

// ---------- 按 key 查询（平台不持有凭据，由 agent 用本地配置连库） ----------

var configPath string // -db-config 的值（启动时由 main 注入）

// SetConfigPath 记录 -db-config 路径，供按 key 查询时复用同一份配置。
func SetConfigPath(p string) { configPath = p }

// QueryByKey 按 agent 配置里的 key 执行只读 SQL：
// 平台只发 {key, sql}，账号密码在 agent 本地，不出集群。
func QueryByKey(ctx context.Context, payload []byte) ([]byte, error) {
	var req struct {
		Key string `json:"key"`
		SQL string `json:"sql"`
	}
	if err := json.Unmarshal(payload, &req); err != nil {
		return nil, fmt.Errorf("解析请求失败: %w", err)
	}
	if req.Key == "" || req.SQL == "" {
		return nil, fmt.Errorf("key 和 sql 必填")
	}
	cfgs, err := LoadConfigs(configPath)
	if err != nil {
		return nil, err
	}
	for _, c := range cfgs {
		if c.Key != req.Key {
			continue
		}
		params, err := json.Marshal(connParams{
			DBType: c.Type, Host: c.Host, Port: c.Port, Database: c.Database,
			Username: c.Username, Password: c.Password,
		})
		if err != nil {
			return nil, fmt.Errorf("序列化连接参数失败: %w", err)
		}
		q, err := json.Marshal(map[string]string{"sql": req.SQL})
		if err != nil {
			return nil, err
		}
		merged, err := mergeJSON(params, q)
		if err != nil {
			return nil, err
		}
		return HandleQuery(ctx, merged)
	}
	return nil, fmt.Errorf("agent 配置里没有 key=%s（可能已删除或未下发）", req.Key)
}

func mergeJSON(a, b []byte) ([]byte, error) {
	ma, mb := map[string]any{}, map[string]any{}
	if err := json.Unmarshal(a, &ma); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(b, &mb); err != nil {
		return nil, err
	}
	for k, v := range mb {
		ma[k] = v
	}
	return json.Marshal(ma)
}
