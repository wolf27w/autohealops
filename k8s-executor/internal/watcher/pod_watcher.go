// Package watcher 提供 K8s Pod/Node 事件监听与故障检测
package watcher

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/api-ops-platform/k8s-executor/internal/database"
	"go.uber.org/zap"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes"
)

// 观察期机制配置（先常量，后续迁移到 alert_policy 表）
const (
	observeWindow        = 30 * time.Second // A 类故障观察期时长
	oomTimeWindow        = 5 * time.Minute  // OOM 时间窗口：FinishedAt 距今超过该值忽略（历史 OOM 不翻旧账）
	restartGrowThreshold = int32(1)         // 观察期内重启增长阈值：增长 >=1（即重启 >=2 次）直接报
	observeTickInterval  = 10 * time.Second // 观察状态扫描间隔
	pendingTimeout       = 5 * time.Minute  // Pending 超时阈值：启动慢的窗口内不报（老板 8-27 定：超过 5 分钟还持续才报）
	terminatingTimeout   = 60 * time.Second // TerminatingStuck 阈值：卡 Terminating 满 60s → 告警
	terminatingForceDel  = 60 * time.Second // 告警后 60s 仍未删除 → 自动强删（force + grace 0）
	terminatingScanTick  = 20 * time.Second // Terminating 扫描间隔

	terminatingPageSize   = int64(500) // Terminating 分页拉取：每页 Pod 数（单次响应体小，降低超时/connection reset）
	terminatingMaxRetries = 3          // 单页 List 瞬时错误最大重试次数
)

// observedFault A 类故障（OOM/CrashLoop）观察状态
type observedFault struct {
	namespace     string
	podName       string
	faultType     string
	severity      string
	message       string
	firstSeen     time.Time
	baseRestarts  int32
	lastRestarts  int32
	occurredAt    *time.Time
	observeWindow time.Duration
}

// policyConfig 单个 fault_type 的观察期策略（从 alert_policies 表读，读不到用默认）
type policyConfig struct {
	observeWindow    time.Duration
	oomWindow        time.Duration
	restartThreshold int32
}

// PodWatcher 监听集群所有 namespace 的 Pod 事件
type PodWatcher struct {
	clusterID uint
	clientset *kubernetes.Clientset
	ctx       context.Context
	cancel    context.CancelFunc
	logger    *zap.Logger
	// 观察期状态（A 类故障）
	obsMu    sync.Mutex
	observed map[string]*observedFault
	// 告警策略（fault_type → 配置）
	policies map[string]*policyConfig
	// Terminating 扫描进行中标志（防重叠：上一轮未结束时跳过本轮）
	terminatingScanning int32
}

// NewPodWatcher 创建 Pod watcher
func NewPodWatcher(clusterID uint, clientset *kubernetes.Clientset, logger *zap.Logger) *PodWatcher {
	ctx, cancel := context.WithCancel(context.Background())
	w := &PodWatcher{
		clusterID: clusterID, clientset: clientset, ctx: ctx, cancel: cancel, logger: logger,
		observed: make(map[string]*observedFault),
		policies: make(map[string]*policyConfig),
	}
	w.loadPolicies()
	return w
}

// loadPolicies 从数据库加载告警策略（alert_policies 表），读不到用默认常量
func (w *PodWatcher) loadPolicies() {
	db := database.GetDB()
	var policies []database.AlertPolicy
	if err := db.Where("enabled = ?", true).Find(&policies).Error; err != nil {
		return
	}
	for _, p := range policies {
		cfg := &policyConfig{
			observeWindow:    time.Duration(p.ObserveSeconds) * time.Second,
			oomWindow:        time.Duration(p.TimeWindowSeconds) * time.Second,
			restartThreshold: int32(p.RestartThreshold),
		}
		if cfg.observeWindow <= 0 {
			cfg.observeWindow = observeWindow
		}
		if cfg.oomWindow <= 0 {
			cfg.oomWindow = oomTimeWindow
		}
		if cfg.restartThreshold <= 0 {
			cfg.restartThreshold = restartGrowThreshold
		}
		w.policies[p.FaultType] = cfg
	}
}

// policyFor 取某故障类型的策略（无则默认）
func (w *PodWatcher) policyFor(faultType string) *policyConfig {
	if cfg, ok := w.policies[faultType]; ok {
		return cfg
	}
	return &policyConfig{
		observeWindow:    observeWindow,
		oomWindow:        oomTimeWindow,
		restartThreshold: restartGrowThreshold,
	}
}

// Start 启动监听（goroutine，带断线重连）+ 观察期扫描 + Terminating 扫描
func (w *PodWatcher) Start() {
	go w.run()
	go w.observeLoop()
	go w.terminatingLoop()
}

// Stop 停止监听
func (w *PodWatcher) Stop() {
	w.cancel()
}

func (w *PodWatcher) run() {
	for {
		w.watchLoop()
		select {
		case <-w.ctx.Done():
			return
		case <-time.After(5 * time.Second): // Watch 断线后重连
		}
	}
}

func (w *PodWatcher) watchLoop() {
	watcher, err := w.clientset.CoreV1().Pods("").Watch(w.ctx, metav1.ListOptions{})
	if err != nil {
		w.logger.Error("pod watch 启动失败", zap.Uint("cluster", w.clusterID), zap.Error(err))
		return
	}
	defer watcher.Stop()
	w.logger.Info("pod watch 已启动", zap.Uint("cluster", w.clusterID))
	for {
		select {
		case <-w.ctx.Done():
			return
		case event, ok := <-watcher.ResultChan():
			if !ok {
				return // channel 关闭，触发重连
			}
			w.handlePodEvent(event)
		}
	}
}

// handlePodEvent 解析 Pod 事件，检测故障
func (w *PodWatcher) handlePodEvent(event watch.Event) {
	pod, ok := event.Object.(*corev1.Pod)
	if !ok {
		return
	}
	// 删除事件忽略（缩容/滚动更新/主动删除，非故障）
	if event.Type == watch.Deleted {
		return
	}
	// 维护窗口 + 限流（观察与上报统一入口检查）
	if inMaintenanceWindow(w.clusterID) || rateLimited(w.clusterID) {
		return
	}

	// 遍历容器检测故障
	for _, cs := range pod.Status.ContainerStatuses {
		// 容器 Waiting 状态
		if cs.State.Waiting != nil {
			reason := cs.State.Waiting.Reason
			// 提取故障实际发生时间：优先用上次终止时间，否则用 Pod 创建时间
			var occurredAt *time.Time
			if cs.LastTerminationState.Terminated != nil {
				t := cs.LastTerminationState.Terminated.FinishedAt.Time
				occurredAt = &t
			}
			switch reason {
			case "CrashLoopBackOff":
				// A 类：进观察期
				w.handleClassAFault(pod, "CrashLoopBackOff", "high",
					fmt.Sprintf("Pod %s in namespace %s is in CrashLoopBackOff state. Container %s: reason=%s",
						pod.Name, pod.Namespace, cs.Name, reason), occurredAt, cs.RestartCount)
				return
			case "ImagePullBackOff", "ErrImagePull":
				// C 类：直接上报（AI 诊断补 imagePullSecrets）；归一化：ErrImagePull/ImagePullBackOff 同根因
				w.emitAlert(pod, "ImagePullBackOff", "medium",
					fmt.Sprintf("Pod %s in namespace %s failed to pull image. Container %s: reason=%s",
						pod.Name, pod.Namespace, cs.Name, reason), occurredAt)
				return
			case "CreateContainerConfigError", "CreateContainerError", "RunContainerError", "ContainerCannotRun":
				// 🟡 强删重建→观察→转人工：容器创建/运行失败，多为配置错误，删 Pod 重建可尝试自愈
				w.emitAlert(pod, reason, "high",
					fmt.Sprintf("Pod %s in namespace %s: container %s %s. %s",
						pod.Name, pod.Namespace, cs.Name, reason, cs.State.Waiting.Message), occurredAt)
				return
			case "InvalidImageName", "ErrImageNeverPull", "ImageInspectError", "RegistryUnavailable":
				// 🟠 转人工：镜像名/镜像策略/仓库问题，平台不自动处理
				w.emitAlert(pod, reason, "medium",
					fmt.Sprintf("Pod %s in namespace %s: container %s %s. %s",
						pod.Name, pod.Namespace, cs.Name, reason, cs.State.Waiting.Message), occurredAt)
				return
			}
		}
		// 检测上次终止原因（OOMKilled）：加时间窗口，历史 OOM 不报
		if cs.LastTerminationState.Terminated != nil &&
			cs.LastTerminationState.Terminated.Reason == "OOMKilled" {
			t := cs.LastTerminationState.Terminated.FinishedAt.Time
			if time.Since(t) > w.policyFor("OOMKilled").oomWindow {
				continue // 历史 OOM，忽略
			}
			// A 类：进观察期
			w.handleClassAFault(pod, "OOMKilled", "high",
				fmt.Sprintf("Pod %s in namespace %s was OOMKilled. Container %s",
					pod.Name, pod.Namespace, cs.Name), &t, cs.RestartCount)
			return
		}
	}
	// 检测 Pending 超过阈值（B 类直接报；启动慢的窗口内不打扰）
	if pod.Status.Phase == corev1.PodPending && time.Since(pod.CreationTimestamp.Time) > pendingTimeout {
		t := pod.CreationTimestamp.Time
		w.emitAlert(pod, "PendingTimeout", "medium",
			fmt.Sprintf("Pod %s in namespace %s has been Pending for over 5 minutes", pod.Name, pod.Namespace), &t)
	}
}

// handleClassAFault A 类故障进入观察期：观察期内恢复→自愈留痕；重启>=阈值→直接报；超时未恢复→报
func (w *PodWatcher) handleClassAFault(pod *corev1.Pod, faultType, severity, message string, occurredAt *time.Time, restartCount int32) {
	cfg := w.policyFor(faultType)
	k := fmt.Sprintf("%s/%s/%s", pod.Namespace, pod.Name, faultType)
	w.obsMu.Lock()
	if item, exists := w.observed[k]; exists {
		// 已在观察：更新重启次数
		item.lastRestarts = restartCount
		item.message = message
		// 重启增长 >= 阈值（重启 >=3 次）→ 直接报
		if restartCount-item.baseRestarts >= cfg.restartThreshold {
			delete(w.observed, k)
			w.obsMu.Unlock()
			w.emitPendingAlert(item)
			return
		}
		w.obsMu.Unlock()
		return
	}
	// 首次观察：不报，记录基线
	w.observed[k] = &observedFault{
		namespace:     pod.Namespace,
		podName:       pod.Name,
		faultType:     faultType,
		severity:      severity,
		message:       message,
		firstSeen:     time.Now(),
		baseRestarts:  restartCount,
		lastRestarts:  restartCount,
		occurredAt:    occurredAt,
		observeWindow: cfg.observeWindow,
	}
	w.obsMu.Unlock()
	w.logger.Debug("A 类故障进入观察期",
		zap.Uint("cluster", w.clusterID), zap.String("pod", pod.Name), zap.String("fault", faultType))
}

// observeLoop 观察期扫描：处理超时（恢复→自愈留痕 / 未恢复→上报）
func (w *PodWatcher) observeLoop() {
	ticker := time.NewTicker(observeTickInterval)
	defer ticker.Stop()
	for {
		select {
		case <-w.ctx.Done():
			return
		case <-ticker.C:
			w.tickObservations()
		}
	}
}

// tickObservations 收集到期观察项并处理
func (w *PodWatcher) tickObservations() {
	var due []*observedFault
	w.obsMu.Lock()
	now := time.Now()
	for k, item := range w.observed {
		if now.Sub(item.firstSeen) > item.observeWindow {
			delete(w.observed, k)
			due = append(due, item)
		}
	}
	w.obsMu.Unlock()

	for _, item := range due {
		recovered, _ := w.checkRecovered(item)
		if recovered {
			w.emitAutoResolved(item)
		} else {
			w.emitPendingAlert(item)
		}
	}
}

// checkRecovered 查询 Pod 当前状态，判断是否已恢复（所有容器 Running 且 restartCount 不再增长）
func (w *PodWatcher) checkRecovered(item *observedFault) (bool, int32) {
	pod, err := w.clientset.CoreV1().Pods(item.namespace).Get(w.ctx, item.podName, metav1.GetOptions{})
	if err != nil {
		return false, item.lastRestarts
	}
	totalRestart := int32(0)
	allRunning := len(pod.Status.ContainerStatuses) > 0
	for _, cs := range pod.Status.ContainerStatuses {
		totalRestart += cs.RestartCount
		if cs.State.Running == nil || !cs.Ready {
			allRunning = false
		}
	}
	// 恢复 = 所有容器 Running 且 restartCount 不再增长
	return allRunning && totalRestart <= item.lastRestarts, totalRestart
}

// emitAlert 上报 B/C 类告警（去重聚合）
func (w *PodWatcher) emitAlert(pod *corev1.Pod, faultType, severity, message string, occurredAt *time.Time) {
	w.createAlert(pod.Namespace, pod.Name, faultType, severity, message, occurredAt, "pending")
}

// emitPendingAlert 观察超时/重启超阈值 → 上报 pending 告警
func (w *PodWatcher) emitPendingAlert(item *observedFault) {
	w.createAlert(item.namespace, item.podName, item.faultType, item.severity, item.message, item.occurredAt, "pending")
}

// emitAutoResolved 自动恢复留痕（status=resolved；非 AI 处理，只是观察窗内已自行恢复）
func (w *PodWatcher) emitAutoResolved(item *observedFault) {
	w.createAlert(item.namespace, item.podName, item.faultType, item.severity,
		item.message+"（已自行恢复，无需处理）", item.occurredAt, "resolved")
}

// inferDeploymentName 从 Pod 名推断 Deployment 名（Pod 名格式：{deployment}-{rs-hash}-{pod-hash}，去最后两段）
func inferDeploymentName(podName string) string {
	parts := strings.Split(podName, "-")
	if len(parts) >= 3 {
		return strings.Join(parts[:len(parts)-2], "-")
	}
	return podName
}

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

// createAlert 写入告警/自愈记录
func (w *PodWatcher) createAlert(namespace, name, faultType, severity, message string, occurredAt *time.Time, status string) {
	db := database.GetDB()
	depName := inferDeploymentName(name)
	// pending 去重聚合：按 Deployment 名聚合（同服务持续故障合并为一条告警，不按 Pod 实例分散）；
	// 跨状态聚合（pending/awaiting_approval 都算活跃，同服务同故障只保留一条活跃告警）
	if status == "pending" {
		// 并发竞态防护：watch 事件循环与观察期扫描为不同 goroutine，
		// 同去重键的「查-插」必须互斥，否则会并发首插出重复告警。
		mu := dedupLock(fmt.Sprintf("%d/%s/%s", w.clusterID, depName, faultType))
		mu.Lock()
		defer mu.Unlock()
		var existing database.Alert
		err := db.Where("cluster_id = ? AND deployment_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')",
			w.clusterID, depName, faultType).
			Order("id desc").First(&existing).Error
		if err == nil {
			db.Model(&existing).Updates(map[string]interface{}{
				"event_count":   existing.EventCount + 1,
				"detected_at":   time.Now(),
				"message":       message,
				"resource_name": name, // 更新为最新出问题的 Pod 名
			})
			return
		}
	}
	alert := database.Alert{
		ClusterID:      w.clusterID,
		ResourceType:   "pod",
		ResourceName:   name,
		DeploymentName: depName,
		Namespace:      namespace,
		FaultType:      faultType,
		Severity:       severity,
		Status:         status,
		Message:        message,
		EventCount:     1,
		OccurredAt:     occurredAt,
		DetectedAt:     time.Now(),
	}
	if status == "resolved" {
		now := time.Now()
		alert.ResolvedAt = &now
	}
	if err := db.Create(&alert).Error; err != nil {
		if isDuplicateKey(err) {
			mergeDuplicateAlert(w.clusterID, depName, faultType, message, true)
			w.logger.Info("Pod 告警命中唯一索引，已合并计数",
				zap.Uint("cluster", w.clusterID), zap.String("fault", faultType))
			return
		}
		w.logger.Error("写入告警失败", zap.Error(err))
		return
	}
	w.logger.Info("Pod 告警写入",
		zap.Uint("cluster", w.clusterID), zap.String("pod", name),
		zap.String("fault", faultType), zap.String("status", status))
	if status == "pending" {
		// 告警写入即触发 api-server 立即处理（知识库优先 → AI 诊断 → 修复）
		triggerAlertProcess(alert.ID)
	}
}

// inMaintenanceWindow 判断集群是否处于维护窗口
func inMaintenanceWindow(clusterID uint) bool {
	db := database.GetDB()
	var cluster database.Cluster
	if err := db.First(&cluster, clusterID).Error; err != nil {
		return false
	}
	var mw struct {
		Enabled bool   `json:"enabled"`
		Start   string `json:"start"`
		End     string `json:"end"`
	}
	if err := json.Unmarshal([]byte(cluster.MaintenanceWindow), &mw); err != nil || !mw.Enabled {
		return false
	}
	now := time.Now()
	nowHM := fmt.Sprintf("%02d:%02d", now.Hour(), now.Minute())
	if mw.Start <= mw.End {
		return nowHM >= mw.Start && nowHM <= mw.End
	}
	// 跨天窗口（如 23:00-02:00）
	return nowHM >= mw.Start || nowHM <= mw.End
}

// rateLimited 入库限流：每集群每分钟最多 20 条（用内存计数器，粗粒度防风暴）
var rateLimitState = struct {
	mu       sync.Mutex
	counters map[uint]int
	window   map[uint]time.Time
}{counters: make(map[uint]int), window: make(map[uint]time.Time)}

const maxAlertsPerMinute = 20

func rateLimited(clusterID uint) bool {
	rateLimitState.mu.Lock()
	defer rateLimitState.mu.Unlock()
	now := time.Now()
	if w, ok := rateLimitState.window[clusterID]; !ok || now.Sub(w) > time.Minute {
		rateLimitState.window[clusterID] = now
		rateLimitState.counters[clusterID] = 0
	}
	rateLimitState.counters[clusterID]++
	return rateLimitState.counters[clusterID] > maxAlertsPerMinute
}

// ============ TerminatingStuck 检测（批三：卡 Terminating 满 60s → 告警；告警后 60s → 自动强删，免审批） ============

// terminatingLoop 周期扫描卡在 Terminating 的 Pod（DeletionTimestamp 已设置但迟迟未删除）
func (w *PodWatcher) terminatingLoop() {
	ticker := time.NewTicker(terminatingScanTick)
	defer ticker.Stop()
	for {
		select {
		case <-w.ctx.Done():
			return
		case <-ticker.C:
			w.scanTerminating()
		}
	}
}

// scanTerminating 扫描卡在 Terminating 的 Pod。
// 全集群 Pod 一次性 List 在大集群下偶发 connection reset（API 读超时），
// 改为分页（Limit+Continue）+ 单页瞬时错误有限重试，降低超时概率。
func (w *PodWatcher) scanTerminating() {
	if inMaintenanceWindow(w.clusterID) {
		return
	}
	// 防重叠：上一轮扫描尚未结束时跳过本轮（避免大集群下扫描慢导致重叠）
	if !atomic.CompareAndSwapInt32(&w.terminatingScanning, 0, 1) {
		return
	}
	defer atomic.StoreInt32(&w.terminatingScanning, 0)

	continueToken := ""
	for {
		pods, err := w.listPodsPage(continueToken)
		if err != nil {
			// 整轮失败放弃：跳过本轮，不影响主循环
			w.logger.Error("Terminating 扫描失败", zap.Uint("cluster", w.clusterID), zap.Error(err))
			return
		}
		w.processTerminatingPage(pods)
		continueToken = pods.Continue
		if continueToken == "" {
			return
		}
	}
}

// listPodsPage 分页拉取一页 Pod，单页瞬时错误做有限重试（指数退避，尊重 ctx 取消）。
func (w *PodWatcher) listPodsPage(continueToken string) (*corev1.PodList, error) {
	var lastErr error
	for attempt := 0; attempt <= terminatingMaxRetries; attempt++ {
		pods, err := w.clientset.CoreV1().Pods("").List(w.ctx, metav1.ListOptions{
			Limit:    terminatingPageSize,
			Continue: continueToken,
		})
		if err == nil {
			return pods, nil
		}
		lastErr = err
		if attempt == terminatingMaxRetries {
			break
		}
		select {
		case <-w.ctx.Done():
			return nil, w.ctx.Err()
		case <-time.After(terminatingBackoff(attempt)):
		}
	}
	return nil, lastErr
}

// terminatingBackoff 第 attempt 次（从 0 起）重试前的退避时长：200ms → 500ms → 1s（封顶 1s）。
func terminatingBackoff(attempt int) time.Duration {
	switch attempt {
	case 0:
		return 200 * time.Millisecond
	case 1:
		return 500 * time.Millisecond
	default:
		return time.Second
	}
}

// processTerminatingPage 就地处理一页 Pod：DeletionTimestamp 已设置且超阈值的报/强删，逻辑保持原样。
func (w *PodWatcher) processTerminatingPage(pods *corev1.PodList) {
	for i := range pods.Items {
		pod := &pods.Items[i]
		if pod.DeletionTimestamp == nil {
			continue // 未在删除中
		}
		stuckFor := time.Since(pod.DeletionTimestamp.Time)
		if stuckFor < terminatingTimeout {
			continue // 未满 60s，正常删除窗口内
		}
		// 满 60s → 报 TerminatingStuck（告警去重聚合）
		w.emitTerminatingStuck(pod, stuckFor)
		// 满 120s（告警后 60s）→ 自动强删（force + grace 0，免审批）
		if stuckFor >= terminatingTimeout+terminatingForceDel {
			w.forceDeletePod(pod)
		}
	}
}

// emitTerminatingStuck 报 TerminatingStuck 告警（去重：同 Pod 已存在 pending 则不重复报）
func (w *PodWatcher) emitTerminatingStuck(pod *corev1.Pod, stuckFor time.Duration) {
	db := database.GetDB()
	var existing database.Alert
	err := db.Where("cluster_id = ? AND resource_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')",
		w.clusterID, pod.Name, "TerminatingStuck").Order("id desc").First(&existing).Error
	if err == nil {
		return // 已报过
	}
	t := pod.DeletionTimestamp.Time
	alert := database.Alert{
		ClusterID:      w.clusterID,
		ResourceType:   "pod",
		ResourceName:   pod.Name,
		DeploymentName: inferDeploymentName(pod.Name),
		Namespace:      pod.Namespace,
		FaultType:      "TerminatingStuck",
		Severity:       "high",
		Status:         "pending",
		Message:        fmt.Sprintf("Pod %s in namespace %s 卡 Terminating 已 %.0f 秒（预计 60s 后自动强删）", pod.Name, pod.Namespace, stuckFor.Seconds()),
		EventCount:     1,
		OccurredAt:     &t,
		DetectedAt:     time.Now(),
	}
	if err := db.Create(&alert).Error; err != nil {
		if isDuplicateKey(err) {
			mergeDuplicateAlert(w.clusterID, pod.Name, "TerminatingStuck", alert.Message, false)
			return
		}
		w.logger.Error("写入 TerminatingStuck 告警失败", zap.Error(err))
		return
	}
	w.logger.Info("检测到 TerminatingStuck", zap.Uint("cluster", w.clusterID), zap.String("pod", pod.Name), zap.Duration("stuck", stuckFor))
	triggerAlertProcess(alert.ID)
}

// forceDeletePod 自动强删卡死的 Pod（force + grace 0，免审批；批三 TerminatingStuck 兜底）
func (w *PodWatcher) forceDeletePod(pod *corev1.Pod) {
	grace := int64(0)
	err := w.clientset.CoreV1().Pods(pod.Namespace).Delete(w.ctx, pod.Name, metav1.DeleteOptions{GracePeriodSeconds: &grace})
	if err != nil {
		w.logger.Warn("TerminatingStuck 强删失败", zap.Uint("cluster", w.clusterID), zap.String("pod", pod.Name), zap.Error(err))
		return
	}
	// 告警标记已解决（自愈：自动强删）
	db := database.GetDB()
	now := time.Now()
	db.Model(&database.Alert{}).Where("cluster_id = ? AND resource_name = ? AND fault_type = ? AND status IN ('pending','awaiting_approval')",
		w.clusterID, pod.Name, "TerminatingStuck").Updates(map[string]interface{}{
		"status":      "resolved",
		"resolved_at": &now,
		"message":     fmt.Sprintf("Pod %s 卡 Terminating 已自动强删（force + grace 0）", pod.Name),
	})
	w.logger.Info("TerminatingStuck 已自动强删", zap.Uint("cluster", w.clusterID), zap.String("pod", pod.Name))
}
