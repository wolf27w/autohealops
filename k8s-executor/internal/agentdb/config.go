// Package agentdb —— agent 侧数据库配置加载（账密只存在目标集群，不上传平台）。
package agentdb

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// DBConfig agent 侧的数据库配置项。
// key 是稳定标识（同集群内唯一），平台据此把多次上报归到同一个数据库实例；
// name 是展示名（缺省 = key），平台侧不可编辑，一律以这里为准。
type DBConfig struct {
	Key      string `json:"key"`
	Name     string `json:"name"`
	Type     string `json:"type"` // mysql / postgres（postgres 采集待 R1 支持）
	Host     string `json:"host"`
	Port     int    `json:"port"`
	Database string `json:"database"`
	Username string `json:"username"`
	Password string `json:"password"`
}

func (c *DBConfig) normalize() {
	c.Key = strings.TrimSpace(c.Key)
	c.Name = strings.TrimSpace(c.Name)
	c.Type = strings.ToLower(strings.TrimSpace(c.Type))
	c.Host = strings.TrimSpace(c.Host)
	c.Database = strings.TrimSpace(c.Database)
	c.Username = strings.TrimSpace(c.Username)
	if c.Type == "" {
		c.Type = "mysql"
	}
	if c.Port == 0 {
		if c.Type == "postgres" {
			c.Port = 5432
		} else {
			c.Port = 3306
		}
	}
	if c.Name == "" {
		c.Name = c.Key
	}
}

// DisplayName 平台展示名（以 agent 配置为准）
func (c *DBConfig) DisplayName() string {
	if c.Name != "" {
		return c.Name
	}
	return c.Key
}

// LoadConfigs 加载数据库配置，优先级：
//  1. -db-config 指定的 JSON 文件
//  2. 环境变量 AGENT_DB_CONFIG_FILE（文件路径）
//  3. 环境变量 AGENT_DB_CONFIG（直接内联 JSON）
//
// 都为空时返回 (nil, nil) —— 不做数据库采集。
func LoadConfigs(path string) ([]DBConfig, error) {
	var raw []byte
	switch {
	case strings.TrimSpace(path) != "":
		b, err := os.ReadFile(strings.TrimSpace(path))
		if err != nil {
			return nil, fmt.Errorf("读取数据库配置文件失败: %w", err)
		}
		raw = b
	case strings.TrimSpace(os.Getenv("AGENT_DB_CONFIG_FILE")) != "":
		p := strings.TrimSpace(os.Getenv("AGENT_DB_CONFIG_FILE"))
		b, err := os.ReadFile(p)
		if err != nil {
			return nil, fmt.Errorf("读取 AGENT_DB_CONFIG_FILE(%s) 失败: %w", p, err)
		}
		raw = b
	case strings.TrimSpace(os.Getenv("AGENT_DB_CONFIG")) != "":
		raw = []byte(os.Getenv("AGENT_DB_CONFIG"))
	default:
		return nil, nil
	}
	return parseConfigs(raw)
}

// parseConfigs 支持两种形态：顶层数组，或 {"dbs":[...]}
func parseConfigs(raw []byte) ([]DBConfig, error) {
	var arr []DBConfig
	if err := json.Unmarshal(raw, &arr); err != nil {
		var wrap struct {
			DBs []DBConfig `json:"dbs"`
		}
		if err2 := json.Unmarshal(raw, &wrap); err2 != nil {
			return nil, fmt.Errorf("数据库配置不是合法 JSON（需数组或 {\"dbs\":[...]}）: %v", err)
		}
		arr = wrap.DBs
	}

	seen := make(map[string]bool, len(arr))
	out := make([]DBConfig, 0, len(arr))
	for i := range arr {
		c := arr[i]
		c.normalize()
		if c.Key == "" {
			return nil, fmt.Errorf("第 %d 项缺少 key", i+1)
		}
		if seen[c.Key] {
			return nil, fmt.Errorf("key 重复: %s", c.Key)
		}
		if c.Type != "mysql" && c.Type != "postgres" {
			return nil, fmt.Errorf("%s: 不支持的 type=%s（仅 mysql/postgres）", c.Key, c.Type)
		}
		if c.Host == "" {
			return nil, fmt.Errorf("%s: host 不能为空（集群内 Service 名或 Pod IP）", c.Key)
		}
		seen[c.Key] = true
		out = append(out, c)
	}
	return out, nil
}
