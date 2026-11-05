// Package eventstore k8s 事件历史采集：k8s 默认只保留 1 小时（kube-apiserver --event-ttl），
// 容器重启/重建后旧事件就没了。这里每 60s 采集一次事件并 upsert 到平台库，保留 7 天，
// 用于「以前的容器事件」回溯。
package eventstore

import (
	"context"
	"time"

	"github.com/autohealops/k8s-executor/internal/database"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"go.uber.org/zap"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	collectInterval = 60 * time.Second
	// 只采集最近活跃的事件（新事件/计数有更新），避免每分钟把历史事件重复搬运
	activeWindow = 15 * time.Minute
	retention    = 7 * 24 * time.Hour
	cleanupEvery = time.Hour
)

// CollectEventsOnce 采集最近活跃的 k8s 事件（纯采集，不落库）。
func CollectEventsOnce(ctx context.Context, client *k8sclient.ClusterClient, clusterID uint, now time.Time) ([]database.K8sEvent, error) {
	evs, err := client.Clientset.CoreV1().Events("").List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	since := now.Add(-activeWindow)
	out := make([]database.K8sEvent, 0, len(evs.Items))
	for i := range evs.Items {
		e := &evs.Items[i]
		first := e.FirstTimestamp.Time
		if first.IsZero() {
			first = e.CreationTimestamp.Time
		}
		last := e.LastTimestamp.Time
		if last.IsZero() {
			last = first
		}
		// k8s 用 EventTime/Series 的新版本事件：兜底用 CreationTimestamp
		if last.IsZero() {
			last = e.CreationTimestamp.Time
		}
		if last.Before(since) {
			continue
		}
		out = append(out, database.K8sEvent{
			ClusterID:    clusterID,
			UID:          string(e.UID),
			Namespace:    e.InvolvedObject.Namespace,
			ResourceKind: e.InvolvedObject.Kind,
			ResourceName: e.InvolvedObject.Name,
			Reason:       e.Reason,
			Type:         e.Type,
			Message:      e.Message,
			Count:        e.Count,
			FirstSeen:    &first,
			LastSeen:     &last,
		})
	}
	return out, nil
}

// Collector 平台侧事件采集器（kubeconfig 集群直接落库）
type Collector struct {
	manager *k8sclient.Manager
	logger  *zap.Logger
}

func NewCollector(manager *k8sclient.Manager, logger *zap.Logger) *Collector {
	return &Collector{manager: manager, logger: logger}
}

// StartScheduler 启动采集（启动即采一次，之后每 60s）+ 每小时清理 7 天前的事件
func (c *Collector) StartScheduler(ctx context.Context) {
	go func() {
		c.collectAll(ctx)
		t := time.NewTicker(collectInterval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				c.collectAll(ctx)
			}
		}
	}()
	go func() {
		t := time.NewTicker(cleanupEvery)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				c.cleanup()
			}
		}
	}()
	c.logger.Info("k8s 事件采集调度器已启动（每 60s，保留 7 天）")
}

func (c *Collector) collectAll(ctx context.Context) {
	for _, id := range c.manager.ListClients() {
		client, err := c.manager.GetClient(ctx, id)
		if err != nil {
			continue
		}
		rows, err := CollectEventsOnce(ctx, client, id, time.Now())
		if err != nil {
			c.logger.Debug("事件采集失败", zap.Uint("cluster", id), zap.Error(err))
			continue
		}
		if len(rows) == 0 {
			continue
		}
		Upsert(database.GetDB(), rows)
	}
}

func (c *Collector) cleanup() {
	cutoff := time.Now().Add(-retention)
	res := database.GetDB().Where("last_seen < ?", cutoff).Delete(&database.K8sEvent{})
	if res.Error != nil {
		c.logger.Warn("事件清理失败", zap.Error(res.Error))
	} else if res.RowsAffected > 0 {
		c.logger.Info("事件清理完成", zap.Int64("deleted", res.RowsAffected))
	}
}

// Upsert 按 (cluster_id, uid) 幂等写入；count/last_seen/message 取最新
func Upsert(db *gorm.DB, rows []database.K8sEvent) {
	if len(rows) == 0 {
		return
	}
	db.Clauses(clause.OnConflict{
		Columns: []clause.Column{{Name: "cluster_id"}, {Name: "uid"}},
		DoUpdates: clause.Assignments(map[string]any{
			"namespace":     gorm.Expr("VALUES(namespace)"),
			"resource_kind": gorm.Expr("VALUES(resource_kind)"),
			"resource_name": gorm.Expr("VALUES(resource_name)"),
			"reason":        gorm.Expr("VALUES(reason)"),
			"type":          gorm.Expr("VALUES(type)"),
			"message":       gorm.Expr("VALUES(message)"),
			"count":         gorm.Expr("VALUES(count)"),
			"first_seen":    gorm.Expr("VALUES(first_seen)"),
			"last_seen":     gorm.Expr("VALUES(last_seen)"),
		}),
	}).CreateInBatches(rows, 500)
}
