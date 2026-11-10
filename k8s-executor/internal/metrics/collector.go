// Package metrics Pod 指标采集：metrics-server（CPU/内存）+ kubelet stats summary（网络）
package metrics

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/autohealops/k8s-executor/internal/database"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"go.uber.org/zap"
	"gorm.io/gorm/clause"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// 采样与保留配置（每分钟采样；保留期见 Config.MetricsRetentionHours，R27-1 起与 api-server 同口径）
const (
	collectInterval = 1 * time.Minute
	cleanupInterval = 1 * time.Hour

	// 脏数据防护：metrics-server 偶发返回异常 CPU（累计值/单位错乱，实测单 Pod 报数万核）。
	// 单 Pod CPU 超过该阈值视为无效采样，置 0 并告警日志（真实节点单机不会超过数百核）。
	maxSanePodCPU      = 256.0
	maxSanePodMemBytes = 2 << 40 // 2 TiB，与 api-server 侧过滤口径一致
)

// Summary kubelet /stats/summary 响应（最小结构）
type Summary struct {
	Pods []PodStats `json:"pods"`
}

type PodStats struct {
	PodRef  PodRef        `json:"podRef"`
	Network *NetworkStats `json:"network"`
}

type PodRef struct {
	Name      string `json:"name"`
	Namespace string `json:"namespace"`
}

type NetworkStats struct {
	RxBytes *uint64 `json:"rxBytes"`
	TxBytes *uint64 `json:"txBytes"`
}

// Collector 指标采集器
type Collector struct {
	manager   *k8sclient.Manager
	logger    *zap.Logger
	retention time.Duration
}

// NewCollector 创建采集器
// retention：pod_metrics 原始采样保留时长（<=0 时回退 24h；与 api-server 保持一致
// 才能保证「平台直连」与「agent 接入」两种方式可查时长相同，见 R27-1）
func NewCollector(manager *k8sclient.Manager, logger *zap.Logger, retention time.Duration) *Collector {
	if retention <= 0 {
		retention = 24 * time.Hour
	}
	return &Collector{manager: manager, logger: logger, retention: retention}
}

// StartCollectScheduler 启动采集调度器（启动即采一次，之后每分钟）+ 定期清理
func (c *Collector) StartCollectScheduler(ctx context.Context) {
	go c.runCollectLoop(ctx)
	go c.runCleanupLoop(ctx)
	c.logger.Info("指标采集调度器已启动（每 60s）",
		zap.Duration("retention", c.retention))
}

func (c *Collector) runCollectLoop(ctx context.Context) {
	c.collectAll(ctx) // 启动即采
	ticker := time.NewTicker(collectInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.collectAll(ctx)
		}
	}
}

func (c *Collector) runCleanupLoop(ctx context.Context) {
	ticker := time.NewTicker(cleanupInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.cleanup()
		}
	}
}

func (c *Collector) collectAll(ctx context.Context) {
	for _, clusterID := range c.manager.ListClients() {
		c.collectCluster(ctx, clusterID)
	}
}

type podUsage struct {
	cpu float64
	mem int64
}

type podNet struct {
	rx int64
	tx int64
}

// collectCluster 采集单集群全部 Pod 指标并落库
func (c *Collector) collectCluster(ctx context.Context, clusterID uint) {
	client, err := c.manager.GetClient(ctx, clusterID)
	if err != nil {
		c.logger.Warn("指标采集：获取集群客户端失败", zap.Uint("cluster", clusterID), zap.Error(err))
		return
	}
	metrics := CollectClusterOnce(ctx, client, clusterID, c.logger)
	if len(metrics) > 0 {
		// R27-2：幂等写入（uk_pod_metric_sample），重试/补采不会产生重复行
		err := database.GetDB().Clauses(clause.OnConflict{
			Columns: []clause.Column{
				{Name: "cluster_id"}, {Name: "namespace"},
				{Name: "pod_name"}, {Name: "sampled_at"},
			},
			DoUpdates: clause.AssignmentColumns([]string{
				"cpu_usage", "memory_usage", "network_rx", "network_tx",
			}),
		}).Create(&metrics).Error
		if err != nil {
			c.logger.Error("指标写入失败", zap.Uint("cluster", clusterID), zap.Error(err))
		}
	}
}

// CollectClusterOnce 采集单集群全部 Pod 指标（纯采集，不落库）。
// 平台侧 executor 落库用；agent 模式在集群内采集后经隧道上报，由 api-server 落库。
func CollectClusterOnce(ctx context.Context, client *k8sclient.ClusterClient, clusterID uint, logger *zap.Logger) []database.PodMetric {
	now := time.Now()

	// 1. CPU / 内存（metrics.k8s.io）
	usageMap := make(map[string]*podUsage)
	if client.MetricsClient != nil {
		pmList, err := client.MetricsClient.MetricsV1beta1().PodMetricses("").List(ctx, metav1.ListOptions{})
		if err != nil {
			logger.Debug("指标采集：PodMetrics 获取失败（可能未装 metrics-server）",
				zap.Uint("cluster", clusterID), zap.Error(err))
		} else {
			for i := range pmList.Items {
				pm := &pmList.Items[i]
				u := &podUsage{}
				for _, cont := range pm.Containers {
					if cpu := cont.Usage.Cpu(); cpu != nil {
						u.cpu += cpu.AsApproximateFloat64()
					}
					if mem := cont.Usage.Memory(); mem != nil {
						u.mem += mem.Value()
					}
				}
				usageMap[pm.Namespace+"/"+pm.Name] = u
			}
		}
	}

	// 2. 网络（kubelet /stats/summary，按节点拉取）
	netMap := make(map[string]*podNet)
	if nodes, err := client.Clientset.CoreV1().Nodes().List(ctx, metav1.ListOptions{}); err == nil {
		for _, node := range nodes.Items {
			sum, err := fetchSummary(ctx, client, node.Name)
			if err != nil {
				logger.Debug("指标采集：kubelet summary 获取失败",
					zap.Uint("cluster", clusterID), zap.String("node", node.Name), zap.Error(err))
				continue
			}
			for _, p := range sum.Pods {
				if p.Network == nil {
					continue
				}
				n := &podNet{}
				if p.Network.RxBytes != nil {
					n.rx = int64(*p.Network.RxBytes)
				}
				if p.Network.TxBytes != nil {
					n.tx = int64(*p.Network.TxBytes)
				}
				netMap[p.PodRef.Namespace+"/"+p.PodRef.Name] = n
			}
		}
	}

	// 3. 合并
	keys := make(map[string]bool, len(usageMap)+len(netMap))
	for k := range usageMap {
		keys[k] = true
	}
	for k := range netMap {
		keys[k] = true
	}
	metrics := make([]database.PodMetric, 0, len(keys))
	for k := range keys {
		ns, name := splitKey(k)
		m := database.PodMetric{ClusterID: clusterID, Namespace: ns, PodName: name, SampledAt: now}
		if u, ok := usageMap[k]; ok {
			if u.cpu > maxSanePodCPU {
				logger.Warn("指标采集：Pod CPU 采样异常已丢弃",
					zap.Uint("cluster", clusterID), zap.String("pod", k), zap.Float64("cpu", u.cpu))
				u.cpu = 0
			}
			if u.mem > maxSanePodMemBytes {
				logger.Warn("指标采集：Pod 内存采样异常已丢弃",
					zap.Uint("cluster", clusterID), zap.String("pod", k), zap.Int64("mem", u.mem))
				u.mem = 0
			}
			m.CPUUsage = u.cpu
			m.MemoryUsage = u.mem
		}
		if n, ok := netMap[k]; ok {
			m.NetworkRx = n.rx
			m.NetworkTx = n.tx
		}
		metrics = append(metrics, m)
	}
	return metrics
}

// fetchSummary 通过 API server 代理访问 kubelet /stats/summary
func fetchSummary(ctx context.Context, client *k8sclient.ClusterClient, nodeName string) (*Summary, error) {
	reqCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	data, err := client.Clientset.CoreV1().RESTClient().Get().
		AbsPath(fmt.Sprintf("/api/v1/nodes/%s/proxy/stats/summary", nodeName)).
		Do(reqCtx).Raw()
	if err != nil {
		return nil, err
	}
	var sum Summary
	if err := json.Unmarshal(data, &sum); err != nil {
		return nil, err
	}
	return &sum, nil
}

// cleanup 删除保留期前的指标
func (c *Collector) cleanup() {
	cutoff := time.Now().Add(-c.retention)
	res := database.GetDB().Where("sampled_at < ?", cutoff).Delete(&database.PodMetric{})
	if res.Error != nil {
		c.logger.Error("指标清理失败", zap.Error(res.Error))
		return
	}
	if res.RowsAffected > 0 {
		c.logger.Info("指标清理完成",
			zap.Int64("deleted", res.RowsAffected), zap.Duration("retention", c.retention))
	}
}

// splitKey 拆分 "namespace/podName"
func splitKey(k string) (string, string) {
	for i := 0; i < len(k); i++ {
		if k[i] == '/' {
			return k[:i], k[i+1:]
		}
	}
	return "", k
}
