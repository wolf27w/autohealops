// Package agentdb —— 周期采集（配置在 agent 侧，账密不出集群）。
package agentdb

import (
	"context"
	"encoding/json"
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
