// Package config 提供 k8s-executor 配置管理（统一从项目根 .env 读取，代码不留硬编码）
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/joho/godotenv"
	"github.com/spf13/viper"
)

// Config 全局配置
type Config struct {
	DatabaseURL      string   // MySQL DSN: user:pass@tcp(host:3306)/db?charset=utf8mb4&parseTime=True&loc=Local
	GRPCPort         string   // gRPC 监听端口，默认 9090
	LogLevel         string   // debug/info/warn/error
	EncryptionKey    string   // 32 字节，kubeconfig 解密（与 Python 端一致）
	CommandWhitelist []string // 命令白名单
	APIURL           string   // api-server 基地址（告警写入后回调触发立即处理）
	InternalToken    string   // 内部服务令牌（X-Internal-Token，与 api-server JWT_SECRET_KEY 一致）

	// 实时日志跟随（P1-R5）：executor 本地 HTTP 端口，供 api-server 代理流式日志（空=关闭）
	LogStreamAddr string // 监听地址，如 :9099

	// Pod 指标原始采样保留小时数（R27-1）。与 api-server 的
	// POD_METRICS_RAW_RETENTION_HOURS 共用同一个环境变量，保证两种接入方式口径一致。
	MetricsRetentionHours int
}

// findEnvFile 向上查找项目根目录下的 .env 文件
func findEnvFile() string {
	dir, _ := os.Getwd()
	for i := 0; i < 5; i++ {
		candidate := filepath.Join(dir, ".env")
		if _, err := os.Stat(candidate); err == nil {
			return candidate
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return ""
}

// Load 从项目根 .env 加载配置（godotenv 注入环境变量 + Viper 读取）
func Load() (*Config, error) {
	envFile := findEnvFile()
	if envFile != "" {
		if err := godotenv.Load(envFile); err != nil {
			return nil, fmt.Errorf("加载 .env 失败: %w", err)
		}
	}

	v := viper.New()
	v.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))
	v.AutomaticEnv()

	cfg := &Config{
		DatabaseURL:   v.GetString("DATABASE_URL"),
		GRPCPort:      v.GetString("GRPC_PORT"),
		LogLevel:      v.GetString("LOG_LEVEL"),
		EncryptionKey: v.GetString("ENCRYPTION_KEY"),
		APIURL:        v.GetString("API_SERVER_URL"),
		InternalToken: v.GetString("JWT_SECRET_KEY"),

		LogStreamAddr: v.GetString("LOGSTREAM_ADDR"),

		// 指标保留期（R27-1）：与 api-server 保持一致，默认 24 小时
		MetricsRetentionHours: toInt(v.GetString("POD_METRICS_RAW_RETENTION_HOURS"), 24),
	}
	if cfg.LogStreamAddr == "" {
		cfg.LogStreamAddr = ":9099" // 默认开启（api-server 经 EXECUTOR_LOGSTREAM_ADDR 访问）
	}
	if cfg.GRPCPort == "" {
		cfg.GRPCPort = "9090"
	}
	if cfg.LogLevel == "" {
		cfg.LogLevel = "info"
	}
	if cfg.APIURL == "" {
		cfg.APIURL = "http://localhost:8080"
	}
	if cfg.DatabaseURL == "" {
		return nil, fmt.Errorf("DATABASE_URL 未配置（请在项目根 .env 中设置）")
	}
	// .env 中 DATABASE_URL 为 SQLAlchemy 格式（mysql+aiomysql://user:pass@host:3306/db），
	// 转换为 GORM DSN（user:pass@tcp(host:3306)/db?charset=utf8mb4&parseTime=True&loc=Local）
	cfg.DatabaseURL = toGormDSN(cfg.DatabaseURL)
	// 命令白名单（逗号分隔）
	wl := v.GetString("COMMAND_WHITELIST")
	if wl == "" {
		wl = "get,list,describe,logs,top,explain,rollout,scale,set,apply,patch,delete,exec,create,edit,cordon,uncordon,label,taint"
	}
	cfg.CommandWhitelist = strings.Split(wl, ",")
	return cfg, nil
}

// toGormDSN 将 SQLAlchemy 格式的 DATABASE_URL 转换为 GORM MySQL DSN
// 输入：mysql+aiomysql://user:pass@host:3306/db
// 输出：user:pass@tcp(host:3306)/db?charset=utf8mb4&parseTime=True&loc=Local
func toGormDSN(url string) string {
	s := url
	// 去除 scheme 前缀
	if i := strings.Index(s, "://"); i >= 0 {
		s = s[i+3:]
	}
	// 拆分 user:pass@host:port/db
	at := strings.LastIndex(s, "@")
	if at < 0 {
		return url // 非标准格式，原样返回
	}
	userpass := s[:at]
	hostDB := s[at+1:]
	slash := strings.LastIndex(hostDB, "/")
	if slash < 0 {
		return url
	}
	hostPort := hostDB[:slash]
	db := hostDB[slash+1:]
	return fmt.Sprintf("%s@tcp(%s)/%s?charset=utf8mb4&parseTime=True&loc=Local", userpass, hostPort, db)
}

// toInt 把字符串配置转整数（R27-1），非法/<=0 时回退默认值。
func toInt(s string, def int) int {
	s = strings.TrimSpace(s)
	if s == "" {
		return def
	}
	n, err := strconv.Atoi(s)
	if err != nil || n <= 0 {
		return def
	}
	return n
}
