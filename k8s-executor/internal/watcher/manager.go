// Watch 管理器：多集群 watcher 生命周期管理
package watcher

import (
	"context"
	"sync"

	"github.com/api-ops-platform/k8s-executor/internal/database"
	"github.com/api-ops-platform/k8s-executor/internal/k8sclient"
	"go.uber.org/zap"
)

// ClusterWatchers 单集群的 watcher 集合
type ClusterWatchers struct {
	PodWatcher  *PodWatcher
	NodeWatcher *NodeWatcher
}

// WatchManager 管理所有集群的 watcher
type WatchManager struct {
	mu       sync.RWMutex
	watchers map[uint]*ClusterWatchers
	manager  *k8sclient.Manager
	logger   *zap.Logger
}

// NewWatchManager 创建 WatchManager
func NewWatchManager(manager *k8sclient.Manager, logger *zap.Logger) *WatchManager {
	return &WatchManager{
		watchers: make(map[uint]*ClusterWatchers),
		manager:  manager,
		logger:   logger,
	}
}

// StartClusterWatch 为指定集群启动 Pod + Node watcher
func (wm *WatchManager) StartClusterWatch(ctx context.Context, clusterID uint) error {
	client, err := wm.manager.GetClient(ctx, clusterID)
	if err != nil {
		return err
	}
	wm.mu.Lock()
	defer wm.mu.Unlock()
	if _, exists := wm.watchers[clusterID]; exists {
		return nil // 已在监听
	}
	pw := NewPodWatcher(clusterID, client.Clientset, wm.logger)
	nw := NewNodeWatcher(clusterID, client.Clientset, wm.logger)
	pw.Start()
	nw.Start()
	wm.watchers[clusterID] = &ClusterWatchers{PodWatcher: pw, NodeWatcher: nw}
	wm.logger.Info("集群 watch 已启动", zap.Uint("cluster", clusterID))
	return nil
}

// StopClusterWatch 停止指定集群的 watcher
func (wm *WatchManager) StopClusterWatch(clusterID uint) {
	wm.mu.Lock()
	defer wm.mu.Unlock()
	if cw, exists := wm.watchers[clusterID]; exists {
		cw.PodWatcher.Stop()
		cw.NodeWatcher.Stop()
		delete(wm.watchers, clusterID)
		wm.logger.Info("集群 watch 已停止", zap.Uint("cluster", clusterID))
	}
}

// RestartClusterWatch 重启指定集群 watcher（kubeconfig 更新后）
func (wm *WatchManager) RestartClusterWatch(ctx context.Context, clusterID uint) error {
	wm.StopClusterWatch(clusterID)
	wm.manager.RemoveClient(clusterID)
	return wm.StartClusterWatch(ctx, clusterID)
}

// StartAllWatches 启动所有集群的 watcher（服务启动时调用）
func (wm *WatchManager) StartAllWatches(ctx context.Context) error {
	db := database.GetDB()
	var clusters []database.Cluster
	if err := db.Find(&clusters).Error; err != nil {
		return err
	}
	for _, c := range clusters {
		if err := wm.StartClusterWatch(ctx, c.ID); err != nil {
			wm.logger.Error("启动集群 watch 失败", zap.Uint("cluster", c.ID), zap.Error(err))
		}
	}
	wm.logger.Info("已启动所有集群 watch", zap.Int("count", len(clusters)))
	return nil
}
