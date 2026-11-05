// Package quota 配额采集：Node 汇总 + ResourceQuota + 资源计数，定时快照
package quota

import (
	"context"
	"fmt"
	"time"

	"github.com/autohealops/k8s-executor/internal/database"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"github.com/robfig/cron/v3"
	"go.uber.org/zap"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// Collector 配额采集器
type Collector struct {
	manager *k8sclient.Manager
	logger  *zap.Logger
}

// NewCollector 创建采集器
func NewCollector(manager *k8sclient.Manager, logger *zap.Logger) *Collector {
	return &Collector{manager: manager, logger: logger}
}

// CollectResult 单集群一次采集的结果（纯数据，不落库）。
// 平台侧 executor 落库用；agent 模式在集群内采集后经隧道上报，由 api-server 落库。
type CollectResult struct {
	QuotaSnapshots    []database.QuotaSnapshot
	ResourceSnapshots []database.ResourceSnapshot
}

// CollectClusterQuota 采集单集群配额快照并落库（平台侧 executor 用）
func (c *Collector) CollectClusterQuota(ctx context.Context, clusterID uint) error {
	client, err := c.manager.GetClient(ctx, clusterID)
	if err != nil {
		return err
	}
	res, err := CollectClusterQuotaOnce(ctx, client, clusterID, time.Now())
	if err != nil {
		return err
	}
	db := database.GetDB()
	if len(res.QuotaSnapshots) > 0 {
		if err := db.Create(&res.QuotaSnapshots).Error; err != nil {
			return err
		}
	}
	if len(res.ResourceSnapshots) > 0 {
		if err := db.Create(&res.ResourceSnapshots).Error; err != nil {
			return err
		}
	}
	return nil
}

// CollectClusterQuotaOnce 采集单集群配额 + 资源快照（纯采集，不落库）。
func CollectClusterQuotaOnce(ctx context.Context, client *k8sclient.ClusterClient, clusterID uint, now time.Time) (*CollectResult, error) {
	cs := client.Clientset
	res := &CollectResult{}

	// 1. Node 汇总（总数/就绪数 + CPU/内存容量）
	nodes, err := cs.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	readyCount := 0
	for _, n := range nodes.Items {
		for _, cond := range n.Status.Conditions {
			if cond.Type == corev1.NodeReady && cond.Status == corev1.ConditionTrue {
				readyCount++
			}
		}
	}
	cpuCap, memCap := sumNodeCapacity(nodes)
	// 服务 / 负载均衡计数（ServiceLimit=服务总数，ServiceUsed=type=LoadBalancer 数量）
	svcTotal, lbCount := 0, 0
	if svcs, err := cs.CoreV1().Services("").List(ctx, metav1.ListOptions{}); err == nil {
		svcTotal = len(svcs.Items)
		for _, svc := range svcs.Items {
			if svc.Spec.Type == corev1.ServiceTypeLoadBalancer {
				lbCount++
			}
		}
	}
	res.QuotaSnapshots = append(res.QuotaSnapshots, database.QuotaSnapshot{
		ClusterID:    clusterID,
		Namespace:    "_cluster_",
		CPULimit:     cpuCap,
		MemoryLimit:  memCap,
		PodLimit:     len(nodes.Items),
		PodUsed:      readyCount,
		ServiceLimit: svcTotal,
		ServiceUsed:  lbCount,
		SnapshotTime: now,
	})

	// 2. 所有 namespace 的 ResourceQuota
	if quotas, err := cs.CoreV1().ResourceQuotas("").List(ctx, metav1.ListOptions{}); err == nil {
		for _, q := range quotas.Items {
			snap := parseResourceQuota(q)
			snap.ClusterID = clusterID
			snap.SnapshotTime = now
			res.QuotaSnapshots = append(res.QuotaSnapshots, snap)
		}
	}

	// 3. 无状态 Deployment 资源计数
	if deployments, err := cs.AppsV1().Deployments("").List(ctx, metav1.ListOptions{}); err == nil {
		for _, d := range deployments.Items {
			replicas := 0
			if d.Spec.Replicas != nil {
				replicas = int(*d.Spec.Replicas)
			}
			ut := d.CreationTimestamp.Time
			for _, c := range d.Status.Conditions {
				if c.LastUpdateTime.Time.After(ut) {
					ut = c.LastUpdateTime.Time
				}
			}
			res.ResourceSnapshots = append(res.ResourceSnapshots, database.ResourceSnapshot{
				ClusterID:     clusterID,
				Namespace:     d.Namespace,
				ResourceType:  "deployment",
				ResourceName:  d.Name,
				Status:        "active",
				Replicas:      replicas,
				ReadyReplicas: int(d.Status.ReadyReplicas),
				UpdateTime:    &ut,
				SnapshotTime:  now,
			})
		}
	}

	// 4. 有状态 StatefulSet 资源计数
	if statefulsets, err := cs.AppsV1().StatefulSets("").List(ctx, metav1.ListOptions{}); err == nil {
		for _, s := range statefulsets.Items {
			replicas := 0
			if s.Spec.Replicas != nil {
				replicas = int(*s.Spec.Replicas)
			}
			ut := s.CreationTimestamp.Time
			for _, c := range s.Status.Conditions {
				if c.LastTransitionTime.Time.After(ut) {
					ut = c.LastTransitionTime.Time
				}
			}
			res.ResourceSnapshots = append(res.ResourceSnapshots, database.ResourceSnapshot{
				ClusterID:     clusterID,
				Namespace:     s.Namespace,
				ResourceType:  "statefulset",
				ResourceName:  s.Name,
				Status:        "active",
				Replicas:      replicas,
				ReadyReplicas: int(s.Status.ReadyReplicas),
				UpdateTime:    &ut,
				SnapshotTime:  now,
			})
		}
	}

	// 5. 定时任务 CronJob 资源计数
	if cronjobs, err := cs.BatchV1().CronJobs("").List(ctx, metav1.ListOptions{}); err == nil {
		for _, c := range cronjobs.Items {
			ut := c.CreationTimestamp.Time
			res.ResourceSnapshots = append(res.ResourceSnapshots, database.ResourceSnapshot{
				ClusterID:    clusterID,
				Namespace:    c.Namespace,
				ResourceType: "cronjob",
				ResourceName: c.Name,
				Status:       "active",
				UpdateTime:   &ut,
				SnapshotTime: now,
			})
		}
	}

	// 6. 守护集 DaemonSet 资源计数
	if daemonsets, err := cs.AppsV1().DaemonSets("").List(ctx, metav1.ListOptions{}); err == nil {
		for _, ds := range daemonsets.Items {
			ut := ds.CreationTimestamp.Time
			for _, c := range ds.Status.Conditions {
				if c.LastTransitionTime.Time.After(ut) {
					ut = c.LastTransitionTime.Time
				}
			}
			res.ResourceSnapshots = append(res.ResourceSnapshots, database.ResourceSnapshot{
				ClusterID:     clusterID,
				Namespace:     ds.Namespace,
				ResourceType:  "daemonset",
				ResourceName:  ds.Name,
				Status:        "active",
				Replicas:      int(ds.Status.DesiredNumberScheduled),
				ReadyReplicas: int(ds.Status.NumberReady),
				UpdateTime:    &ut,
				SnapshotTime:  now,
			})
		}
	}

	// 7. Pod 状态 + 资源限制（requests/limits）+ 集群 Pod 总数（kubectl get pods -A 口径）
	if pods, err := cs.CoreV1().Pods("").List(ctx, metav1.ListOptions{}); err == nil {
		podTotal := len(pods.Items)
		podRunning := 0
		for _, p := range pods.Items {
			if p.Status.Phase == corev1.PodRunning {
				podRunning++
			}
			cpuReq, cpuLim, memReq, memLim := sumPodResources(&p)
			res.ResourceSnapshots = append(res.ResourceSnapshots, database.ResourceSnapshot{
				ClusterID:      clusterID,
				Namespace:      p.Namespace,
				ResourceType:   "pod",
				ResourceName:   p.Name,
				Status:         string(p.Status.Phase),
				ContainerCount: len(p.Spec.Containers),
				CPURequest:     cpuReq,
				CPULimit:       cpuLim,
				MemoryRequest:  memReq,
				MemoryLimit:    memLim,
				SnapshotTime:   now,
			})
		}
		// 集群级 Pod 总数快照（namespace=_pods_，PodLimit=总数，PodUsed=Running 数），
		// 供 dashboard 概览用真实口径展示（不依赖分批落库的 resource_snapshots 计数）
		res.QuotaSnapshots = append(res.QuotaSnapshots, database.QuotaSnapshot{
			ClusterID:    clusterID,
			Namespace:    "_pods_",
			PodLimit:     podTotal,
			PodUsed:      podRunning,
			SnapshotTime: now,
		})
	}
	return res, nil
}

// sumPodResources 汇总 Pod 所有容器的资源 requests/limits（空=未设置）
func sumPodResources(pod *corev1.Pod) (cpuReq, cpuLim, memReq, memLim string) {
	var cpuReqMilli, cpuLimMilli, memReqBytes, memLimBytes int64
	for _, c := range pod.Spec.Containers {
		if q, ok := c.Resources.Requests[corev1.ResourceCPU]; ok {
			cpuReqMilli += q.MilliValue()
		}
		if q, ok := c.Resources.Limits[corev1.ResourceCPU]; ok {
			cpuLimMilli += q.MilliValue()
		}
		if q, ok := c.Resources.Requests[corev1.ResourceMemory]; ok {
			memReqBytes += q.Value()
		}
		if q, ok := c.Resources.Limits[corev1.ResourceMemory]; ok {
			memLimBytes += q.Value()
		}
	}
	if cpuReqMilli > 0 {
		cpuReq = fmt.Sprintf("%dm", cpuReqMilli)
	}
	if cpuLimMilli > 0 {
		cpuLim = fmt.Sprintf("%dm", cpuLimMilli)
	}
	if memReqBytes > 0 {
		memReq = formatBytes(memReqBytes)
	}
	if memLimBytes > 0 {
		memLim = formatBytes(memLimBytes)
	}
	return
}

// formatBytes 字节转可读（Mi/Gi）
func formatBytes(bytes int64) string {
	const (
		Mi = 1024 * 1024
		Gi = 1024 * Mi
	)
	if bytes >= Gi {
		return fmt.Sprintf("%dGi", bytes/Gi)
	}
	if bytes >= Mi {
		return fmt.Sprintf("%dMi", bytes/Mi)
	}
	return fmt.Sprintf("%d", bytes)
}

// sumNodeCapacity 汇总节点 CPU/内存容量（返回可读字符串）
func sumNodeCapacity(nodes *corev1.NodeList) (string, string) {
	var cpuMilli int64
	var memBytes int64
	for _, n := range nodes.Items {
		if q, ok := n.Status.Capacity[corev1.ResourceCPU]; ok {
			cpuMilli += q.MilliValue()
		}
		if q, ok := n.Status.Capacity[corev1.ResourceMemory]; ok {
			memBytes += q.Value()
		}
	}
	return fmt.Sprintf("%dm", cpuMilli), fmt.Sprintf("%d", memBytes)
}

// parseResourceQuota 从 ResourceQuota 提取 limit/used
func parseResourceQuota(q corev1.ResourceQuota) database.QuotaSnapshot {
	s := database.QuotaSnapshot{Namespace: q.Namespace}
	getStr := func(rl corev1.ResourceList, name corev1.ResourceName) string {
		if v, ok := rl[name]; ok {
			return v.String()
		}
		return "0"
	}
	getInt := func(rl corev1.ResourceList, name corev1.ResourceName) int {
		if v, ok := rl[name]; ok {
			return int(v.Value())
		}
		return 0
	}
	s.CPULimit = getStr(q.Status.Hard, corev1.ResourceLimitsCPU)
	s.CPUUsed = getStr(q.Status.Used, corev1.ResourceLimitsCPU)
	s.MemoryLimit = getStr(q.Status.Hard, corev1.ResourceLimitsMemory)
	s.MemoryUsed = getStr(q.Status.Used, corev1.ResourceLimitsMemory)
	s.PVCLimit = getInt(q.Status.Hard, corev1.ResourcePersistentVolumeClaims)
	s.PVCUsed = getInt(q.Status.Used, corev1.ResourcePersistentVolumeClaims)
	s.ServiceLimit = getInt(q.Status.Hard, corev1.ResourceServices)
	s.ServiceUsed = getInt(q.Status.Used, corev1.ResourceServices)
	return s
}

// CollectAllClusters 采集所有集群
func (c *Collector) CollectAllClusters(ctx context.Context) {
	db := database.GetDB()
	var clusters []database.Cluster
	// agent 接入的集群没有 kubeconfig，配额采集跳过（agent 侧自行采集上报）
	if err := db.Where("COALESCE(source, '') <> ?", "agent").Find(&clusters).Error; err != nil {
		return
	}
	for _, cl := range clusters {
		if err := c.CollectClusterQuota(ctx, cl.ID); err != nil {
			c.logger.Warn("配额采集失败", zap.Uint("cluster", cl.ID), zap.Error(err))
		}
	}
}

// StartQuotaCollectScheduler 启动采集调度（每 60 秒）
func (c *Collector) StartQuotaCollectScheduler(ctx context.Context) {
	cr := cron.New()
	cr.AddFunc("@every 60s", func() {
		c.CollectAllClusters(ctx)
	})
	cr.Start()
	c.logger.Info("配额采集调度器已启动（每 60s）")
}
