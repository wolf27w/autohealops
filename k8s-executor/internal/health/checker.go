// Package health 集群连接检查（独立定时）：连接成功=healthy，连续失败达阈值=unreachable
//
// 设计要点（老板 2026-09-12 要求）：
//   - 连接检查是**独立定时任务**，其结论才决定集群状态；
//   - **不要盲目直写库**：连续失败达到阈值（默认 2 次）才置 unreachable，避免网络抖动产生误判；
//   - 状态只在**发生变化**时才写 status；每次检查都记录 last_check_at / last_check_error 供界面展示；
//   - 成功即恢复 healthy 并清空错误。
package health

import (
	"context"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/autohealops/k8s-executor/internal/database"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"github.com/robfig/cron/v3"
	"go.uber.org/zap"
)

// 连续失败阈值（达到才判定为不可达，防抖动）
const defaultUnreachableThreshold = 2

// Checker 连接检查器
type Checker struct {
	manager   *k8sclient.Manager
	logger    *zap.Logger
	mu        sync.Mutex
	failCount map[uint]int
	threshold int
}

// NewChecker 创建检查器
func NewChecker(manager *k8sclient.Manager, logger *zap.Logger) *Checker {
	n := defaultUnreachableThreshold
	if v := os.Getenv("HEALTH_CHECK_FAIL_THRESHOLD"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			n = parsed
		}
	}
	return &Checker{manager: manager, logger: logger, failCount: map[uint]int{}, threshold: n}
}

// HealthCheck 检查单集群连通性并（按规则）更新状态
func (c *Checker) HealthCheck(ctx context.Context, clusterID uint) (status, version string, err error) {
	client, cerr := c.manager.GetClient(ctx, clusterID)
	if cerr != nil {
		c.onFailure(clusterID, cerr)
		return "unreachable", "", cerr
	}
	info, verr := client.Clientset.Discovery().ServerVersion()
	if verr != nil {
		c.onFailure(clusterID, verr)
		return "unreachable", "", verr
	}
	c.onSuccess(clusterID, info.GitVersion)
	return "healthy", info.GitVersion, nil
}

// onSuccess 连接成功：清空失败计数；状态变化才写 status
func (c *Checker) onSuccess(clusterID uint, version string) {
	c.mu.Lock()
	c.failCount[clusterID] = 0
	c.mu.Unlock()

	db := database.GetDB()
	var cur database.Cluster
	if err := db.Select("status", "version").First(&cur, clusterID).Error; err != nil {
		c.logger.Warn("读取集群状态失败", zap.Uint("cluster", clusterID), zap.Error(err))
		return
	}
	updates := map[string]interface{}{"last_check_at": time.Now(), "last_check_error": ""}
	if cur.Status != "healthy" {
		updates["status"] = "healthy" // 仅状态变更才写
	}
	if version != "" && version != cur.Version {
		updates["version"] = version
	}
	if err := db.Model(&database.Cluster{}).Where("id = ?", clusterID).Updates(updates).Error; err != nil {
		c.logger.Warn("集群状态写回失败", zap.Uint("cluster", clusterID), zap.Error(err))
	}
}

// onFailure 连接失败：计入失败次数；达到阈值且状态变化才写 unreachable
func (c *Checker) onFailure(clusterID uint, err error) {
	c.mu.Lock()
	c.failCount[clusterID]++
	n := c.failCount[clusterID]
	c.mu.Unlock()

	msg := ""
	if err != nil {
		msg = err.Error()
		if len(msg) > 250 {
			msg = msg[:250]
		}
	}
	updates := map[string]interface{}{"last_check_at": time.Now(), "last_check_error": msg}
	if n >= c.threshold {
		db := database.GetDB()
		var cur database.Cluster
		if e := db.Select("status").First(&cur, clusterID).Error; e == nil && cur.Status != "unreachable" {
			updates["status"] = "unreachable" // 仅状态变更才写
			c.logger.Warn("集群连接检查连续失败，标记为不可达",
				zap.Uint("cluster", clusterID), zap.Int("consecutive", n), zap.String("error", msg))
		}
	}
	db := database.GetDB()
	if e := db.Model(&database.Cluster{}).Where("id = ?", clusterID).Updates(updates).Error; e != nil {
		c.logger.Warn("集群检查结果写回失败", zap.Uint("cluster", clusterID), zap.Error(e))
	}
}

// CheckAllClusters 遍历所有集群
func (c *Checker) CheckAllClusters(ctx context.Context) {
	db := database.GetDB()
	var clusters []database.Cluster
	// agent 接入的集群平台侧没有 kubeconfig，健康检查由 api-server 经隧道做（这里跳过）
	if err := db.Where("COALESCE(source, '') <> ?", "agent").Find(&clusters).Error; err != nil {
		c.logger.Error("查询集群失败", zap.Error(err))
		return
	}
	for _, cl := range clusters {
		status, version, err := c.HealthCheck(ctx, cl.ID)
		if err != nil {
			c.logger.Warn("集群健康检查失败", zap.Uint("cluster", cl.ID), zap.Error(err))
		} else {
			c.logger.Debug("集群健康", zap.Uint("cluster", cl.ID), zap.String("status", status), zap.String("version", version))
		}
	}
}

// StartHealthCheckScheduler 启动连接检查调度（间隔可配，默认 60s）
func (c *Checker) StartHealthCheckScheduler(ctx context.Context) {
	interval := 60 * time.Second
	if v := os.Getenv("HEALTH_CHECK_INTERVAL_SECONDS"); v != "" {
		if parsed, err := strconv.Atoi(v); err == nil && parsed > 0 {
			interval = time.Duration(parsed) * time.Second
		}
	}
	cr := cron.New()
	_, err := cr.AddFunc("@every "+interval.String(), func() {
		c.CheckAllClusters(ctx)
	})
	if err != nil {
		c.logger.Error("连接检查调度注册失败", zap.Error(err))
		return
	}
	cr.Start()
	c.logger.Info("集群连接检查调度器已启动",
		zap.Duration("interval", interval), zap.Int("fail_threshold", c.threshold))
}
