// Package hostagent —— agent 侧「外部内网 VM」SSH 代采（R7）。
//
// 背景：外部集群内网的虚拟机，平台 SSH 不可达；agent 在集群内，用对方集群挂载的
// SSH 私钥（Secret 挂载）连同网段 VM，执行 ps 采集进程状态，经隧道 event 帧
// （method=HostMetrics）上报平台，由平台自动建主机 + 更新状态 + 进程翻转通知。
//
// 凭据（SSH 私钥）只留在对方集群，平台不保存。
package hostagent

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"go.uber.org/zap"
	"golang.org/x/crypto/ssh"
)

// HostProcess hosts.json 里的一条进程匹配规则
type HostProcess struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

// HostConfig hosts.json 单条主机配置
type HostConfig struct {
	Key       string        `json:"key"`
	Name      string        `json:"name"`
	Address   string        `json:"address"`
	Port      int           `json:"port"`
	User      string        `json:"user"`
	Processes []HostProcess `json:"processes"`
}

// ProcessResult 单进程采集结果
type ProcessResult struct {
	Name     string  `json:"name"`
	Path     string  `json:"path"`
	Running  bool    `json:"running"`
	PidCount int     `json:"pid_count"`
	CPU      float64 `json:"cpu_pct"`
	MemMB    float64 `json:"mem_mb"`
}

// HostMetricRow 上报平台的一条主机指标
type HostMetricRow struct {
	Key       string          `json:"key"`
	Name      string          `json:"name"`
	Address   string          `json:"address"`
	Port      int             `json:"port"`
	User      string          `json:"user"`
	TS        string          `json:"ts"`
	Online    bool            `json:"online"`
	Error     string          `json:"error"`
	Processes []ProcessResult `json:"processes"`
}

// LoadHosts 读取 hosts.json；path 为空返回空切片（不启用主机代采）。
func LoadHosts(path string) ([]HostConfig, error) {
	if path == "" {
		return nil, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var hosts []HostConfig
	if err := json.Unmarshal(b, &hosts); err != nil {
		return nil, err
	}
	return hosts, nil
}

// CollectOnce 对全部主机做一次采集。返回可直接 JSON 序列化的结果。
func CollectOnce(ctx context.Context, hosts []HostConfig, sshKeyPath string, logger *zap.Logger) []HostMetricRow {
	signer := loadSigner(sshKeyPath, logger)
	rows := make([]HostMetricRow, 0, len(hosts))
	for _, h := range hosts {
		rows = append(rows, collectOne(ctx, h, signer, logger))
	}
	return rows
}

func loadSigner(path string, logger *zap.Logger) ssh.Signer {
	if path == "" {
		return nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		logger.Warn("主机代采：读取 SSH 私钥失败", zap.String("path", path), zap.Error(err))
		return nil
	}
	signer, err := ssh.ParsePrivateKey(b)
	if err != nil {
		logger.Warn("主机代采：解析 SSH 私钥失败", zap.String("path", path), zap.Error(err))
		return nil
	}
	return signer
}

func collectOne(_ context.Context, h HostConfig, signer ssh.Signer, logger *zap.Logger) HostMetricRow {
	row := HostMetricRow{
		Key: h.Key, Name: h.Name, Address: h.Address,
		Port: h.Port, User: h.User, TS: time.Now().UTC().Format(time.RFC3339),
		Online: false, Processes: []ProcessResult{},
	}
	if row.Name == "" {
		row.Name = h.Key
	}
	if row.Port == 0 {
		row.Port = 22
	}
	if row.User == "" {
		row.User = "root"
	}
	if signer == nil {
		row.Error = "SSH 私钥未配置或无效"
		return row
	}
	cfg := &ssh.ClientConfig{
		User:            row.User,
		Auth:            []ssh.AuthMethod{ssh.PublicKeys(signer)},
		HostKeyCallback: ssh.InsecureIgnoreHostKey(),
		Timeout:         8 * time.Second,
	}
	addr := fmt.Sprintf("%s:%d", h.Address, row.Port)
	client, err := ssh.Dial("tcp", addr, cfg)
	if err != nil {
		row.Error = "SSH 连接失败: " + err.Error()
		return row
	}
	defer client.Close()

	session, err := client.NewSession()
	if err != nil {
		row.Error = "创建会话失败: " + err.Error()
		return row
	}
	defer session.Close()

	out, err := session.CombinedOutput("ps -eo pid,pcpu,pmem,rss,args")
	if err != nil {
		row.Error = "ps 执行失败: " + err.Error()
		return row
	}
	row.Online = true
	row.Processes = matchProcesses(string(out), h.Processes)
	return row
}

// matchProcesses 解析 ps 输出，按 path 匹配进程（与平台 host_monitor 同口径）。
func matchProcesses(psOut string, specs []HostProcess) []ProcessResult {
	lines := strings.Split(psOut, "\n")
	data := lines
	if len(data) > 0 {
		data = data[1:] // 去掉表头
	}
	res := make([]ProcessResult, 0, len(specs))
	for _, sp := range specs {
		if strings.TrimSpace(sp.Path) == "" {
			continue
		}
		r := ProcessResult{Name: sp.Name, Path: sp.Path}
		if r.Name == "" {
			r.Name = sp.Path
		}
		var pids int
		var cpuMax, memKB float64
		for _, ln := range data {
			words := strings.Fields(ln)
			if len(words) < 5 {
				continue
			}
			args := strings.Join(words[4:], " ")
			if !strings.Contains(args, sp.Path) {
				continue
			}
			if _, err := strconv.Atoi(words[0]); err == nil {
				pids++
			}
			if cpu, err := strconv.ParseFloat(words[1], 64); err == nil && cpu > cpuMax {
				cpuMax = cpu
			}
			if mem, err := strconv.ParseFloat(words[3], 64); err == nil {
				memKB += mem
			}
		}
		r.Running = pids > 0
		r.PidCount = pids
		r.CPU = round1(cpuMax)
		r.MemMB = round1(memKB / 1024)
		res = append(res, r)
	}
	return res
}

func round1(v float64) float64 {
	return float64(int(v*10+0.5)) / 10
}
