// Package watcher —— 告警落地抽象（sink）
//
// 背景（2026-10-28，P2 agent 告警下沉）：
// 原来 PodWatcher / NodeWatcher 直接在 Go 里用 GORM 写 MySQL。这在「平台侧 executor」没问题，
// 但 agent 模式（agent 跑在被管集群内）根本连不到平台数据库，导致 agent 接入的集群完全没告警。
//
// 现在把「检测」与「落地」拆开：
//   - 检测逻辑（观察期/去重键/故障类型判定）保持不动，仍在本包；
//   - 落地通过 AlertSink 接口：
//       · dbSink     —— 平台侧 executor 用，行为与原来完全一致（直连 MySQL + 回调 api-server）
//       · tunnelSink —— agent 用，事件经反向隧道上报平台，由平台统一写库/去重/触发处理
//
// 维护窗口判断：dbSink 在本地判（读 clusters.maintenance_window）；隧道模式由平台侧统一判，
// 避免 agent 需要读平台库。限流 rateLimited 仍是进程内内存计数，两种模式都保留（多一层保护）。
package watcher

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/autohealops/k8s-executor/internal/database"
	"go.uber.org/zap"
)

// AlertEvent 一条告警落地请求（DB 与隧道两种落地方式共用同一结构，隧道走 JSON）。
type AlertEvent struct {
	ClusterID      uint       `json:"cluster_id"`
	ResourceType   string     `json:"resource_type"` // pod | node
	Namespace      string     `json:"namespace"`
	ResourceName   string     `json:"resource_name"`
	DeploymentName string     `json:"deployment_name"`
	FaultType      string     `json:"fault_type"`
	Severity       string     `json:"severity"`
	Message        string     `json:"message"`
	Status         string     `json:"status"` // pending | resolved
	// DedupBy 去重维度：deployment（默认，同服务聚合）| resource（按资源名，如 TerminatingStuck）
	DedupBy string `json:"dedup_by,omitempty"`
	OccurredAt     *time.Time `json:"occurred_at,omitempty"`
	DetectedAt     time.Time  `json:"detected_at"`
}

// ResolveOpts 关闭活跃告警的选项
type ResolveOpts struct {
	// Suffix 追加到告警 message 末尾（如「（节点已恢复，系统自动关闭）」）
	Suffix string
	// MessageOverride 非空则直接替换 message（如 TerminatingStuck 自动强删）
	MessageOverride string
	// IncludeTicketed 是否一并关闭 ticketed（已转工单）的告警
	IncludeTicketed bool
	// Notify 是否发送「已恢复」通知（节点恢复用）
	Notify bool
}

// AlertSink 告警落地接口
type AlertSink interface {
	// Report 落地一条告警（内部按 resource_type 做去重/聚合）；返回写入或命中的告警 id（隧道模式恒为 0）
	Report(ev AlertEvent) uint
	// Resolve 关闭某资源 + 故障类型的活跃告警
	Resolve(clusterID uint, resourceType, resourceName, faultType string, opts ResolveOpts)
	// LocalFilters 是否在本地做「维护窗口」判断（DB sink=true；隧道 sink=false，由平台统一判）
	LocalFilters() bool
}

var (
	sinkMu     sync.RWMutex
	activeSink AlertSink = &dbSink{}
)

// SetSink 切换告警落地实现（agent 启动时切到隧道 sink；平台侧 executor 保持默认 DB sink）
func SetSink(s AlertSink) {
	sinkMu.Lock()
	defer sinkMu.Unlock()
	if s == nil {
		activeSink = &dbSink{}
		return
	}
	activeSink = s
}

// CurrentSink 取当前落地实现
func CurrentSink() AlertSink {
	sinkMu.RLock()
	defer sinkMu.RUnlock()
	return activeSink
}

// ==================== DB sink（平台侧 executor，原有行为） ====================

type dbSink struct{}

func (d *dbSink) LocalFilters() bool { return true }

// Report 落地告警：Pod（按 Deployment 聚合）与 Node（5 分钟窗口去重）语义不同，分开处理
func (d *dbSink) Report(ev AlertEvent) uint {
	if ev.ResourceType == "node" {
		return reportNodeAlert(ev)
	}
	return reportPodAlert(ev)
}

func reportPodAlert(ev AlertEvent) uint {
	db := database.GetDB()
	if ev.Status == "pending" && ev.DedupBy == "resource" {
		var existing database.Alert
		err := db.Where("cluster_id = ? AND resource_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')",
			ev.ClusterID, ev.ResourceName, ev.FaultType).Order("id desc").First(&existing).Error
		if err == nil {
			return existing.ID // 同 Pod 已报过，不重复（TerminatingStuck 语义）
		}
	}
	if ev.Status == "pending" {
		// 并发竞态防护：watch 事件循环与观察期扫描为不同 goroutine，
		// 同去重键的「查-插」必须互斥，否则会并发首插出重复告警。
		mu := dedupLock(fmt.Sprintf("%d/%s/%s", ev.ClusterID, ev.DeploymentName, ev.FaultType))
		mu.Lock()
		defer mu.Unlock()
		var existing database.Alert
		err := db.Where("cluster_id = ? AND deployment_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')",
			ev.ClusterID, ev.DeploymentName, ev.FaultType).
			Order("id desc").First(&existing).Error
		if err == nil {
			db.Model(&existing).Updates(map[string]interface{}{
				"event_count":   existing.EventCount + 1,
				"detected_at":   time.Now(),
				"message":       ev.Message,
				"resource_name": ev.ResourceName, // 更新为最新出问题的 Pod 名
			})
			return existing.ID
		}
	}
	alert := database.Alert{
		ClusterID:      ev.ClusterID,
		ResourceType:   "pod",
		ResourceName:   ev.ResourceName,
		DeploymentName: ev.DeploymentName,
		Namespace:      ev.Namespace,
		FaultType:      ev.FaultType,
		Severity:       ev.Severity,
		Status:         ev.Status,
		Message:        ev.Message,
		EventCount:     1,
		OccurredAt:     ev.OccurredAt,
		DetectedAt:     time.Now(),
	}
	if ev.Status == "resolved" {
		now := time.Now()
		alert.ResolvedAt = &now
	}
	if err := db.Create(&alert).Error; err != nil {
		if isDuplicateKey(err) {
			mergeDuplicateAlert(ev.ClusterID, ev.DeploymentName, ev.FaultType, ev.Message, true)
			return 0
		}
		return 0
	}
	if ev.Status == "pending" {
		// 告警写入即触发 api-server 立即处理（知识库优先 → AI 诊断 → 修复）
		triggerAlertProcess(alert.ID)
	}
	return alert.ID
}

func reportNodeAlert(ev AlertEvent) uint {
	db := database.GetDB()
	var existing database.Alert
	err := db.Where("cluster_id = ? AND resource_name = ? AND fault_type = ? AND status = 'pending'",
		ev.ClusterID, ev.ResourceName, ev.FaultType).
		Where("detected_at > ?", time.Now().Add(-5*time.Minute)).
		Order("id desc").First(&existing).Error
	if err == nil {
		db.Model(&existing).Updates(map[string]interface{}{
			"event_count": existing.EventCount + 1,
			"detected_at": time.Now(),
			"message":     ev.Message,
		})
		return existing.ID
	}
	alert := database.Alert{
		ClusterID:    ev.ClusterID,
		ResourceType: "node",
		ResourceName: ev.ResourceName,
		Namespace:    "",
		FaultType:    ev.FaultType,
		Severity:     ev.Severity,
		Status:       "pending",
		Message:      ev.Message,
		EventCount:   1,
		OccurredAt:   ev.OccurredAt,
		DetectedAt:   time.Now(),
	}
	if err := db.Create(&alert).Error; err != nil {
		if isDuplicateKey(err) {
			mergeDuplicateAlert(ev.ClusterID, ev.ResourceName, ev.FaultType, ev.Message, false)
			return 0
		}
		return 0
	}
	triggerAlertProcess(alert.ID)
	return alert.ID
}

// Resolve 关闭活跃告警（节点恢复 / 自动强删后留痕）
func (d *dbSink) Resolve(clusterID uint, resourceType, resourceName, faultType string, opts ResolveOpts) {
	db := database.GetDB()
	statuses := "'pending','awaiting_approval'"
	if opts.IncludeTicketed {
		statuses = "'pending','awaiting_approval','ticketed'"
	}
	where := fmt.Sprintf("cluster_id = ? AND resource_type = ? AND resource_name = ? AND fault_type = ? AND status IN (%s)",
		statuses)
	var alerts []database.Alert
	if err := db.Where(where, clusterID, resourceType, resourceName, faultType).Find(&alerts).Error; err != nil || len(alerts) == 0 {
		return
	}
	now := time.Now()
	for _, a := range alerts {
		msg := a.Message + opts.Suffix
		if opts.MessageOverride != "" {
			msg = opts.MessageOverride
		}
		if err := db.Model(&database.Alert{}).Where("id = ?", a.ID).Updates(map[string]interface{}{
			"status":      "resolved",
			"resolved_at": now,
			"message":     msg,
		}).Error; err != nil {
			continue
		}
		if opts.Notify {
			triggerRecoveryNotify(a.ID)
		}
	}
}

// ==================== 通用小工具（原 pod_watcher.go 内实现） ====================

// dedupLocks 串行化同一去重键的「查-插/更新」临界区，消除并发重复告警。
// executor 固定单副本（见 k8s/deployments.yaml：有状态 watcher，多副本会重复 watch），
// 进程内互斥即可保证「查-插」原子；若未来水平扩容，需追加 DB 唯一索引（活跃去重键）+ upsert 兑底。
var dedupLocks sync.Map // key string -> *sync.Mutex

func dedupLock(key string) *sync.Mutex {
	if m, ok := dedupLocks.Load(key); ok {
		return m.(*sync.Mutex)
	}
	m, _ := dedupLocks.LoadOrStore(key, &sync.Mutex{})
	return m.(*sync.Mutex)
}

// isDuplicateKey 判断是否 MySQL 唯一索引冲突（1062）。告警去重 DB 兜底用。
func isDuplicateKey(err error) bool {
	return err != nil && strings.Contains(strings.ToLower(err.Error()), "duplicate")
}

// mergeDuplicateAlert 告警去重 DB 兜底：唯一索引冲突（多副本竞态）时，
// 合并进已有活跃告警（event_count + 1），不新增重复记录。
// byDeployment=true 按 deployment_name 维度合并，否则按 resource_name 维度。
func mergeDuplicateAlert(clusterID uint, name, faultType, message string, byDeployment bool) {
	db := database.GetDB()
	where := "cluster_id = ? AND resource_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')"
	if byDeployment {
		where = "cluster_id = ? AND deployment_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')"
	}
	if err := db.Exec(
		"UPDATE alerts SET event_count = event_count + 1, detected_at = ?, message = ? WHERE "+where,
		time.Now(), message, clusterID, name, faultType,
	).Error; err != nil {
		// 兜底失败不致命（下一轮观察期会再报），静默忽略
		return
	}
}

// ==================== 隧道 sink（agent 模式） ====================

// TunnelSender 事件上行函数（agent 注入：把 AlertEvent 序列化后经隧道发给平台）
type TunnelSender func(payload []byte) error

// tunnelSink agent 侧落地实现：事件经反向隧道上报平台，平台统一写库
type tunnelSink struct {
	clusterID uint
	send      TunnelSender
	logger    *zap.Logger
}

// NewTunnelSink 创建隧道 sink
func NewTunnelSink(clusterID uint, send TunnelSender, logger *zap.Logger) AlertSink {
	return &tunnelSink{clusterID: clusterID, send: send, logger: logger}
}

func (t *tunnelSink) LocalFilters() bool { return false } // 维护窗口由平台统一判

func (t *tunnelSink) Report(ev AlertEvent) uint {
	ev.ClusterID = t.clusterID
	if ev.DetectedAt.IsZero() {
		ev.DetectedAt = time.Now()
	}
	payload, err := json.Marshal(ev)
	if err != nil {
		return 0
	}
	if err := t.send(payload); err != nil && t.logger != nil {
		t.logger.Warn("告警事件上行失败（下一轮观察期会再报）",
			zap.Uint("cluster", t.clusterID), zap.String("fault", ev.FaultType), zap.Error(err))
		return 0
	}
	if t.logger != nil {
		t.logger.Info("告警事件已上报平台",
			zap.Uint("cluster", t.clusterID), zap.String("resource", ev.ResourceName),
			zap.String("fault", ev.FaultType), zap.String("status", ev.Status))
	}
	return 0
}

func (t *tunnelSink) Resolve(clusterID uint, resourceType, resourceName, faultType string, opts ResolveOpts) {
	payload, err := json.Marshal(struct {
		ClusterID      uint   `json:"cluster_id"`
		ResourceType   string `json:"resource_type"`
		ResourceName   string `json:"resource_name"`
		FaultType      string `json:"fault_type"`
		Suffix         string `json:"suffix"`
		MessageOverride string `json:"message_override"`
		IncludeTicketed bool  `json:"include_ticketed"`
		Notify         bool   `json:"notify"`
	}{
		ClusterID: t.clusterID, ResourceType: resourceType, ResourceName: resourceName,
		FaultType: faultType, Suffix: opts.Suffix, MessageOverride: opts.MessageOverride,
		IncludeTicketed: opts.IncludeTicketed, Notify: opts.Notify,
	})
	if err != nil {
		return
	}
	if err := t.send(payload); err != nil && t.logger != nil {
		t.logger.Warn("告警恢复事件上行失败", zap.Uint("cluster", t.clusterID),
			zap.String("resource", resourceName), zap.String("fault", faultType), zap.Error(err))
	}
}
