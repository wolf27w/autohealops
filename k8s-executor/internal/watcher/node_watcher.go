// Node 事件监听：检测 NotReady 与资源压力
package watcher

import (
	"context"
	"fmt"
	"time"

	"github.com/api-ops-platform/k8s-executor/internal/database"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes"
	"go.uber.org/zap"
)

// NodeWatcher 监听集群 Node 事件
type NodeWatcher struct {
	clusterID uint
	clientset *kubernetes.Clientset
	ctx       context.Context
	cancel    context.CancelFunc
	logger    *zap.Logger
}

// NewNodeWatcher 创建 Node watcher
func NewNodeWatcher(clusterID uint, clientset *kubernetes.Clientset, logger *zap.Logger) *NodeWatcher {
	ctx, cancel := context.WithCancel(context.Background())
	return &NodeWatcher{clusterID: clusterID, clientset: clientset, ctx: ctx, cancel: cancel, logger: logger}
}

// Start 启动监听
func (w *NodeWatcher) Start() {
	go w.run()
}

// Stop 停止监听
func (w *NodeWatcher) Stop() {
	w.cancel()
}

func (w *NodeWatcher) run() {
	for {
		w.watchLoop()
		select {
		case <-w.ctx.Done():
			return
		case <-time.After(5 * time.Second):
		}
	}
}

func (w *NodeWatcher) watchLoop() {
	watcher, err := w.clientset.CoreV1().Nodes().Watch(w.ctx, metav1.ListOptions{})
	if err != nil {
		w.logger.Error("node watch 启动失败", zap.Uint("cluster", w.clusterID), zap.Error(err))
		return
	}
	defer watcher.Stop()
	w.logger.Info("node watch 已启动", zap.Uint("cluster", w.clusterID))
	for {
		select {
		case <-w.ctx.Done():
			return
		case event, ok := <-watcher.ResultChan():
			if !ok {
				return
			}
			w.handleNodeEvent(event)
		}
	}
}

// handleNodeEvent 解析 Node 事件，检测异常
func (w *NodeWatcher) handleNodeEvent(event watch.Event) {
	node, ok := event.Object.(*corev1.Node)
	if !ok {
		return
	}
	// 检测 NotReady
	ready := false
	for _, cond := range node.Status.Conditions {
		if cond.Type == corev1.NodeReady {
			ready = cond.Status == corev1.ConditionTrue
			if !ready {
				t := cond.LastTransitionTime.Time
				w.emitAlert(node.Name, "NodeNotReady", "critical",
					fmt.Sprintf("Node %s is NotReady (condition=%s, reason=%s)", node.Name, cond.Status, cond.Reason), &t)
				return
			}
		}
	}
	// 节点已 Ready → 关闭历史 NodeNotReady 告警（恢复检测：此前只有上报没有关闭，导致审批单无法解除）
	if ready {
		w.resolveNodeFaultAlerts(node.Name, "NodeNotReady")
	}
	// 检测资源压力（条件恢复为 False 时同步关闭对应告警）
	for _, cond := range node.Status.Conditions {
		if cond.Status == corev1.ConditionTrue {
			t := cond.LastTransitionTime.Time
			switch cond.Type {
			case corev1.NodeMemoryPressure:
				w.emitAlert(node.Name, "MemoryPressure", "high",
					fmt.Sprintf("Node %s has MemoryPressure (reason=%s)", node.Name, cond.Reason), &t)
				return
			case corev1.NodeDiskPressure:
				w.emitAlert(node.Name, "DiskPressure", "high",
					fmt.Sprintf("Node %s has DiskPressure (reason=%s)", node.Name, cond.Reason), &t)
				return
			case corev1.NodePIDPressure:
				w.emitAlert(node.Name, "PIDPressure", "high",
					fmt.Sprintf("Node %s has PIDPressure (reason=%s)", node.Name, cond.Reason), &t)
				return
			case corev1.NodeNetworkUnavailable:
				w.emitAlert(node.Name, "NetworkUnavailable", "critical",
					fmt.Sprintf("Node %s has NetworkUnavailable (reason=%s)", node.Name, cond.Reason), &t)
				return
			}
		} else {
			// 压力条件已恢复（False）→ 关闭对应告警
			var ft string
			switch cond.Type {
			case corev1.NodeMemoryPressure:
				ft = "MemoryPressure"
			case corev1.NodeDiskPressure:
				ft = "DiskPressure"
			case corev1.NodePIDPressure:
				ft = "PIDPressure"
			case corev1.NodeNetworkUnavailable:
				ft = "NetworkUnavailable"
			}
			if ft != "" {
				w.resolveNodeFaultAlerts(node.Name, ft)
			}
		}
	}
	// 检测 cordon（不可调度）长期未恢复：SchedulingDisabled 污点
	for _, taint := range node.Spec.Taints {
		if taint.Key == "node.kubernetes.io/unschedulable" && taint.Effect == corev1.TaintEffectNoSchedule {
			t := time.Now()
			w.emitAlert(node.Name, "NodeCordoned", "medium",
				fmt.Sprintf("Node %s is cordoned (unschedulable taint)", node.Name), &t)
			return
		}
	}
}

// resolveNodeFaultAlerts 节点故障恢复 → 关闭该节点的活跃告警（pending/awaiting_approval）。
// 一律 resolved（老板口径：非 AI 处理的都算「已解决」，只有 AI 自动修复验证通过才标 self_healed）。
// 告警关闭后，api-server 的审批检查循环（timeout_handler）会据 resolved/self_healed 状态自动解除关联审批单。
func (w *NodeWatcher) resolveNodeFaultAlerts(nodeName, faultType string) {
	db := database.GetDB()
	now := time.Now()
	var alerts []database.Alert
	err := db.Where("cluster_id = ? AND resource_type = 'node' AND resource_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')",
		w.clusterID, nodeName, faultType).Find(&alerts).Error
	if err != nil || len(alerts) == 0 {
		return
	}
	for _, a := range alerts {
		// 非 AI 处理：节点自行恢复 → 一律「已解决」
		newStatus := "resolved"
		suffix := "（节点已恢复，系统自动关闭）"
		if err := db.Model(&database.Alert{}).Where("id = ?", a.ID).Updates(map[string]interface{}{
			"status":      newStatus,
			"resolved_at": now,
			"message":     a.Message + suffix,
		}).Error; err != nil {
			w.logger.Warn("节点告警关闭失败", zap.Uint("cluster", w.clusterID), zap.String("node", nodeName), zap.Error(err))
			continue
		}
		w.logger.Info("节点故障恢复，关闭告警",
			zap.Uint("cluster", w.clusterID), zap.String("node", nodeName),
			zap.String("fault", faultType), zap.String("status", newStatus), zap.Uint("alert", a.ID))
	}
}

// emitAlert 去重/聚合/限流/维护窗口后写入 Node 告警（occurredAt=故障实际发生时间）
func (w *NodeWatcher) emitAlert(nodeName, faultType, severity, message string, occurredAt *time.Time) {
	if inMaintenanceWindow(w.clusterID) {
		return
	}
	if rateLimited(w.clusterID) {
		return
	}
	db := database.GetDB()
	var existing database.Alert
	err := db.Where("cluster_id = ? AND resource_name = ? AND fault_type = ? AND status = 'pending'",
		w.clusterID, nodeName, faultType).
		Where("detected_at > ?", time.Now().Add(-5*time.Minute)).
		Order("id desc").First(&existing).Error
	if err == nil {
		db.Model(&existing).Updates(map[string]interface{}{
			"event_count": existing.EventCount + 1,
			"detected_at": time.Now(),
			"message":     message,
		})
		return
	}
	alert := database.Alert{
		ClusterID:    w.clusterID,
		ResourceType: "node",
		ResourceName: nodeName,
		Namespace:    "",
		FaultType:    faultType,
		Severity:     severity,
		Status:       "pending",
		Message:      message,
		EventCount:   1,
		OccurredAt:   occurredAt,
		DetectedAt:   time.Now(),
	}
	if err := db.Create(&alert).Error; err != nil {
		if isDuplicateKey(err) {
			mergeDuplicateAlert(w.clusterID, nodeName, faultType, message, false)
			return
		}
		w.logger.Error("写入 Node 告警失败", zap.Error(err))
		return
	}
	w.logger.Info("检测到 Node 故障", zap.Uint("cluster", w.clusterID), zap.String("node", nodeName), zap.String("fault", faultType))
	// 告警写入即触发 api-server 立即处理（知识库优先 → AI 诊断 → 修复）
	triggerAlertProcess(alert.ID)
}
