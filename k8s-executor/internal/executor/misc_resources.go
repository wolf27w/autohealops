// Package executor 集群管理其它资源：服务/路由(Ingress)/ConfigMap/Secret/存储(PVC)
package executor

import (
	"context"
	"fmt"
	"log"
	"sort"
	"strings"
	"sync"
	"time"

	"golang.org/x/sync/singleflight"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	storagev1 "k8s.io/api/storage/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	"github.com/autohealops/k8s-executor/internal/k8sclient"
)

// ServiceInfo 服务简况
type ServiceInfo struct {
	Namespace  string
	Name       string
	Type       string
	ClusterIP  string
	Ports      string
	ExternalIP string
	CreatedAt  string
}

// IngressInfo 路由简况
type IngressInfo struct {
	Namespace string
	Name      string
	Hosts     string
	Class     string
	Address   string
	CreatedAt string
}

// ConfigMapInfo ConfigMap 简况（不取内容）
type ConfigMapInfo struct {
	Namespace string
	Name      string
	DataCount int32
	CreatedAt string
}

// SecretInfo Secret 简况（不取内容）
type SecretInfo struct {
	Namespace string
	Name      string
	Type      string
	DataCount int32
	CreatedAt string
}

// PvcInfo 存储(PVC)简况
type PvcInfo struct {
	Namespace    string
	Name         string
	Status       string
	Capacity     string
	StorageClass string
	VolumeName   string
	AccessModes  string
	CreatedAt    string
	Mounted      bool
	MountedBy    []string
}

// StorageClassInfo 存储类简况（cluster-scoped）
type StorageClassInfo struct {
	Name             string
	Provisioner      string
	IsDefault        bool
	CreatedAt        string
	PvcCount         int32
	AllowExpansion   bool
	SupportsClone    bool
	SupportsSnapshot bool
}

// MiscResources 集群管理其它资源聚合
type MiscResources struct {
	Services       []ServiceInfo
	Ingresses      []IngressInfo
	ConfigMaps     []ConfigMapInfo
	Secrets        []SecretInfo
	PVCs           []PvcInfo
	StorageClasses []StorageClassInfo
}

// cloneSnapshotCapability 按 provisioner 常见能力推断 StorageClass 是否支持克隆/快照
func cloneSnapshotCapability(provisioner string) (clone, snapshot bool) {
	p := strings.ToLower(provisioner)
	if p == "" || p == "kubernetes.io/no-provisioner" {
		return false, false
	}
	// CSI/云厂商/Longhorn/Rook-Ceph 等 provisioner 大多支持克隆与快照
	if strings.Contains(p, "csi") || strings.Contains(p, "longhorn") || strings.Contains(p, "rook") || strings.Contains(p, "ceph") {
		return true, true
	}
	return false, false
}

// 杂项资源短缓存：这些对象（服务/路由/配置/密钥/PVC）变动不频繁，
// 而一次全命名空间 list 在远端集群上要 3~9s，来回切页会反复挨这一刀。
// 冷路径用 stale-while-revalidate：过期后立即返回旧值，后台单飞刷新，用户不再等。
const (
	miscCacheTTL              = 15 * time.Second // 缓存新鲜窗口
	miscKeepaliveInterval     = 60 * time.Second // 后台保活刷新间隔
	miscKeepaliveRecentWindow = 10 * time.Minute // 仅刷新最近访问过的 key
)

type miscCacheEntry struct {
	at         time.Time // 数据抓取时间
	lastAccess time.Time // 最近一次返回给用户的时间
	namespace  string    // 抓取时的 namespace（后台刷新用）
	ex         *Executor // 抓取所用的 executor（clientset 共享，后台刷新用）
	data       *MiscResources
	refreshing bool // 后台刷新是否进行中（单飞标记，避免并发重复刷）
}

var (
	miscCacheMu sync.Mutex
	miscCache   = map[string]*miscCacheEntry{}
	miscSF      singleflight.Group
)

// ListMiscResources 列出 namespace（空=全部）下的 服务/路由/ConfigMap/Secret/PVC/StorageClass
//
// 性能（stale-while-revalidate）：命中新鲜缓存直接返回；命中过期缓存立即返回旧值并后台单飞刷新；
// 完全冷启动（无缓存）才同步拉取一次。用户侧不再吃到 5~6s 的冷查询。
func (e *Executor) ListMiscResources(ctx context.Context, namespace string) (*MiscResources, error) {
	if namespace == "" {
		namespace = metav1.NamespaceAll
	}

	key := fmt.Sprintf("%d|%s", e.clusterID, namespace)
	now := time.Now()

	miscCacheMu.Lock()
	ent, ok := miscCache[key]
	if ok && now.Sub(ent.at) < miscCacheTTL {
		// 新鲜命中
		ent.lastAccess = now
		miscCacheMu.Unlock()
		return ent.data, nil
	}
	if ok {
		// 过期命中：立即返回旧值，后台单飞刷新，用户不再等冷查询
		ent.lastAccess = now
		data := ent.data
		ent.ex = e
		ent.namespace = namespace
		needRefresh := !ent.refreshing
		ent.refreshing = true
		miscCacheMu.Unlock()

		if needRefresh {
			go e.refreshMiscBackground(context.Background(), key, namespace)
		}
		return data, nil
	}
	miscCacheMu.Unlock()

	// 完全冷启动：没有旧值可返回，只能同步拉取（单飞去重并发冷启动）
	v, err, _ := miscSF.Do(key, func() (interface{}, error) {
		data, err := e.fetchMiscResources(ctx, namespace)
		if err != nil {
			return nil, err
		}
		miscCacheMu.Lock()
		miscCache[key] = &miscCacheEntry{at: time.Now(), lastAccess: time.Now(), namespace: namespace, ex: e, data: data}
		miscCacheMu.Unlock()
		return data, nil
	})
	if err != nil {
		return nil, err
	}
	return v.(*MiscResources), nil
}

// fetchMiscResources 实际拉取（冷路径）：6 类资源并行 list + Pod 挂载反查。
// 只负责返回数据，不写缓存（缓存由 ListMiscResources / 后台刷新统一管理）。
func (e *Executor) fetchMiscResources(ctx context.Context, namespace string) (*MiscResources, error) {
	t0 := time.Now()
	cost := map[string]time.Duration{}
	var costMu sync.Mutex
	mark := func(kind string, start time.Time) {
		costMu.Lock()
		cost[kind] = time.Since(start)
		costMu.Unlock()
	}

	var (
		services       []ServiceInfo
		ingresses      []IngressInfo
		configMaps     []ConfigMapInfo
		secrets        []SecretInfo
		pvcs           []PvcInfo
		storageClasses []StorageClassInfo
		podMounts      map[string][]string // namespace/claimName → 挂载它的 Pod 名

		wg       sync.WaitGroup
		errMu    sync.Mutex
		firstErr error
	)
	fail := func(err error) {
		errMu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		errMu.Unlock()
	}

	// 服务
	wg.Add(1)
	go func() {
		defer wg.Done()
		st := time.Now()
		defer func() { mark("services", st) }()
		var svcs *corev1.ServiceList
		err := k8sclient.RetryOnTransient(ctx, 3, func() error {
			var e2 error
			svcs, e2 = e.clientset.CoreV1().Services(namespace).List(ctx, metav1.ListOptions{})
			return e2
		})
		if err != nil {
			fail(fmt.Errorf("list services: %w", err))
			return
		}
		items := make([]ServiceInfo, 0, len(svcs.Items))
		for i := range svcs.Items {
			s := &svcs.Items[i]
			var ports []string
			for _, p := range s.Spec.Ports {
				ports = append(ports, fmt.Sprintf("%d:%d/%s", p.Port, p.NodePort, p.Protocol))
			}
			extIP := ""
			if len(s.Status.LoadBalancer.Ingress) > 0 {
				var ips []string
				for _, ing := range s.Status.LoadBalancer.Ingress {
					if ing.IP != "" {
						ips = append(ips, ing.IP)
					} else if ing.Hostname != "" {
						ips = append(ips, ing.Hostname)
					}
				}
				extIP = strings.Join(ips, ",")
			}
			items = append(items, ServiceInfo{
				Namespace: s.Namespace, Name: s.Name, Type: string(s.Spec.Type),
				ClusterIP: s.Spec.ClusterIP, Ports: strings.Join(ports, ", "), ExternalIP: extIP,
				CreatedAt: s.CreationTimestamp.Format(time.RFC3339),
			})
		}
		sort.Slice(items, func(i, j int) bool {
			if items[i].Namespace != items[j].Namespace {
				return items[i].Namespace < items[j].Namespace
			}
			return items[i].Name < items[j].Name
		})
		services = items
	}()

	// 路由 Ingress（networking.k8s.io/v1）
	wg.Add(1)
	go func() {
		defer wg.Done()
		st := time.Now()
		defer func() { mark("ingresses", st) }()
		var ings *networkingv1.IngressList
		err := k8sclient.RetryOnTransient(ctx, 3, func() error {
			var e2 error
			ings, e2 = e.clientset.NetworkingV1().Ingresses(namespace).List(ctx, metav1.ListOptions{})
			return e2
		})
		if err != nil {
			fail(fmt.Errorf("list ingresses: %w", err))
			return
		}
		items := make([]IngressInfo, 0, len(ings.Items))
		for i := range ings.Items {
			ing := &ings.Items[i]
			var hosts []string
			for _, r := range ing.Spec.Rules {
				if r.Host != "" {
					hosts = append(hosts, r.Host)
				}
			}
			class := ""
			if ing.Spec.IngressClassName != nil {
				class = *ing.Spec.IngressClassName
			}
			addr := ""
			if len(ing.Status.LoadBalancer.Ingress) > 0 {
				var as []string
				for _, a := range ing.Status.LoadBalancer.Ingress {
					if a.IP != "" {
						as = append(as, a.IP)
					} else if a.Hostname != "" {
						as = append(as, a.Hostname)
					}
				}
				addr = strings.Join(as, ",")
			}
			items = append(items, IngressInfo{
				Namespace: ing.Namespace, Name: ing.Name, Hosts: strings.Join(hosts, ", "),
				Class: class, Address: addr,
				CreatedAt: ing.CreationTimestamp.Format(time.RFC3339),
			})
		}
		sort.Slice(items, func(i, j int) bool {
			if items[i].Namespace != items[j].Namespace {
				return items[i].Namespace < items[j].Namespace
			}
			return items[i].Name < items[j].Name
		})
		ingresses = items
	}()

	// ConfigMap
	wg.Add(1)
	go func() {
		defer wg.Done()
		st := time.Now()
		defer func() { mark("configmaps", st) }()
		cmItems, err := e.listConfigMapsPaged(ctx, namespace)
		if err != nil {
			fail(fmt.Errorf("list configmaps: %w", err))
			return
		}
		items := make([]ConfigMapInfo, 0, len(cmItems))
		for i := range cmItems {
			cm := &cmItems[i]
			items = append(items, ConfigMapInfo{Namespace: cm.Namespace, Name: cm.Name, DataCount: int32(len(cm.Data) + len(cm.BinaryData)), CreatedAt: cm.CreationTimestamp.Format(time.RFC3339)})
		}
		sort.Slice(items, func(i, j int) bool {
			if items[i].Namespace != items[j].Namespace {
				return items[i].Namespace < items[j].Namespace
			}
			return items[i].Name < items[j].Name
		})
		configMaps = items
	}()

	// Secret（不取内容，只列名字/类型/键数）
	wg.Add(1)
	go func() {
		defer wg.Done()
		st := time.Now()
		defer func() { mark("secrets", st) }()
		secretItems, err := e.listSecretsPaged(ctx, namespace)
		if err != nil {
			fail(fmt.Errorf("list secrets: %w", err))
			return
		}
		items := make([]SecretInfo, 0, len(secretItems))
		for i := range secretItems {
			sc := &secretItems[i]
			items = append(items, SecretInfo{Namespace: sc.Namespace, Name: sc.Name, Type: string(sc.Type), DataCount: int32(len(sc.Data)), CreatedAt: sc.CreationTimestamp.Format(time.RFC3339)})
		}
		sort.Slice(items, func(i, j int) bool {
			if items[i].Namespace != items[j].Namespace {
				return items[i].Namespace < items[j].Namespace
			}
			return items[i].Name < items[j].Name
		})
		secrets = items
	}()

	// 存储 PVC
	wg.Add(1)
	go func() {
		defer wg.Done()
		st := time.Now()
		defer func() { mark("pvcs", st) }()
		var pvcList *corev1.PersistentVolumeClaimList
		err := k8sclient.RetryOnTransient(ctx, 3, func() error {
			var e2 error
			pvcList, e2 = e.clientset.CoreV1().PersistentVolumeClaims(namespace).List(ctx, metav1.ListOptions{})
			return e2
		})
		if err != nil {
			fail(fmt.Errorf("list pvcs: %w", err))
			return
		}
		items := make([]PvcInfo, 0, len(pvcList.Items))
		for i := range pvcList.Items {
			p := &pvcList.Items[i]
			capacity := ""
			if q, ok := p.Status.Capacity["storage"]; ok {
				capacity = q.String()
			}
			sc := ""
			if p.Spec.StorageClassName != nil {
				sc = *p.Spec.StorageClassName
			}
			modes := make([]string, 0, len(p.Spec.AccessModes))
			for _, m := range p.Spec.AccessModes {
				modes = append(modes, string(m))
			}
			items = append(items, PvcInfo{
				Namespace: p.Namespace, Name: p.Name, Status: string(p.Status.Phase),
				Capacity: capacity, StorageClass: sc, VolumeName: p.Spec.VolumeName,
				AccessModes: strings.Join(modes, ","),
				CreatedAt:   p.CreationTimestamp.Format(time.RFC3339),
			})
		}
		sort.Slice(items, func(i, j int) bool {
			if items[i].Namespace != items[j].Namespace {
				return items[i].Namespace < items[j].Namespace
			}
			return items[i].Name < items[j].Name
		})
		pvcs = items
	}()

	// 存储类 StorageClass（cluster-scoped）
	wg.Add(1)
	go func() {
		defer wg.Done()
		st := time.Now()
		defer func() { mark("storageclasses", st) }()
		var scs *storagev1.StorageClassList
		err := k8sclient.RetryOnTransient(ctx, 3, func() error {
			var e2 error
			scs, e2 = e.clientset.StorageV1().StorageClasses().List(ctx, metav1.ListOptions{})
			return e2
		})
		if err != nil {
			fail(fmt.Errorf("list storageclasses: %w", err))
			return
		}
		items := make([]StorageClassInfo, 0, len(scs.Items))
		for i := range scs.Items {
			sc := &scs.Items[i]
			isDefault := false
			for _, k := range []string{"storageclass.kubernetes.io/is-default-class", "storageclass.beta.kubernetes.io/is-default-class"} {
				if v, ok := sc.Annotations[k]; ok && v == "true" {
					isDefault = true
				}
			}
			allowExp := sc.AllowVolumeExpansion != nil && *sc.AllowVolumeExpansion
			clone, snap := cloneSnapshotCapability(sc.Provisioner)
			items = append(items, StorageClassInfo{Name: sc.Name, Provisioner: sc.Provisioner, IsDefault: isDefault, CreatedAt: sc.CreationTimestamp.Format(time.RFC3339), AllowExpansion: allowExp, SupportsClone: clone, SupportsSnapshot: snap})
		}
		sort.Slice(items, func(i, j int) bool { return items[i].Name < items[j].Name })
		storageClasses = items
	}()

	// Pod 挂载 PVC 反查（用于 PVC「挂载状态」列）：只 list 一次 Pod，按 volume.claimName 反查
	wg.Add(1)
	go func() {
		defer wg.Done()
		st := time.Now()
		defer func() { mark("pods", st) }()
		var pods *corev1.PodList
		err := k8sclient.RetryOnTransient(ctx, 3, func() error {
			var e2 error
			pods, e2 = e.clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
			return e2
		})
		if err != nil {
			// 挂载状态仅是附加列，list pods 失败不阻断整页（留空 = 全部未挂载）
			log.Printf("[misc] list pods 失败（挂载状态留空）: %v", err)
			return
		}
		m := make(map[string][]string)
		for i := range pods.Items {
			pod := &pods.Items[i]
			for _, vol := range pod.Spec.Volumes {
				if vol.PersistentVolumeClaim != nil && vol.PersistentVolumeClaim.ClaimName != "" {
					key := pod.Namespace + "/" + vol.PersistentVolumeClaim.ClaimName
					m[key] = append(m[key], pod.Name)
				}
			}
		}
		podMounts = m
	}()

	wg.Wait()
	// 慢调用留痕（>2s 才打，避免刷日志）：用于定位「点进集群慢」是哪一类 list 拖的
	if el := time.Since(t0); el > 2*time.Second {
		log.Printf("[misc] cluster=%v ns=%q total=%v services=%v ingresses=%v configmaps=%v secrets=%v pvcs=%v storageclasses=%v pods=%v",
			e.clusterID, namespace, el, cost["services"], cost["ingresses"], cost["configmaps"], cost["secrets"], cost["pvcs"], cost["storageclasses"], cost["pods"])
	}
	if firstErr != nil {
		return nil, firstErr
	}

	// 数量上限保护：超大集群避免一次拉爆
	const capN = 5000
	trim := func(n int) int {
		if n > capN {
			return capN
		}
		return n
	}
	services = services[:trim(len(services))]
	ingresses = ingresses[:trim(len(ingresses))]
	configMaps = configMaps[:trim(len(configMaps))]
	secrets = secrets[:trim(len(secrets))]
	pvcs = pvcs[:trim(len(pvcs))]
	storageClasses = storageClasses[:trim(len(storageClasses))]

	// 回填 PVC 挂载状态 + StorageClass 的 PVC 数量（依赖上面 list 到的 PVC 与 Pod）
	const maxMountedBy = 10
	scPvcCount := make(map[string]int32)
	for i := range pvcs {
		p := &pvcs[i]
		if p.StorageClass != "" {
			scPvcCount[p.StorageClass]++
		}
		if mounts, ok := podMounts[p.Namespace+"/"+p.Name]; ok && len(mounts) > 0 {
			p.Mounted = true
			if len(mounts) > maxMountedBy {
				mounts = mounts[:maxMountedBy]
			}
			p.MountedBy = mounts
		}
	}
	for i := range storageClasses {
		storageClasses[i].PvcCount = scPvcCount[storageClasses[i].Name]
	}

	res := &MiscResources{
		Services:       services,
		Ingresses:      ingresses,
		ConfigMaps:     configMaps,
		Secrets:        secrets,
		PVCs:           pvcs,
		StorageClasses: storageClasses,
	}
	return res, nil
}

// refreshMiscBackground 后台刷新指定 key（singleflight 去重，避免并发重复刷）。
// 成功则更新缓存；失败保留旧值并打日志，绝不清空缓存把用户打回冷路径。
func (e *Executor) refreshMiscBackground(parent context.Context, key, namespace string) {
	ctx, cancel := context.WithTimeout(parent, 30*time.Second)
	defer cancel()

	_, err, _ := miscSF.Do(key, func() (interface{}, error) {
		data, err := e.fetchMiscResources(ctx, namespace)
		if err != nil {
			return nil, err
		}
		miscCacheMu.Lock()
		if ent, ok := miscCache[key]; ok {
			ent.at = time.Now()
			ent.data = data
			ent.refreshing = false
		}
		miscCacheMu.Unlock()
		return data, nil
	})
	if err != nil {
		// 刷新失败：保留旧值，清 refreshing 标记以便下次重试
		miscCacheMu.Lock()
		if ent, ok := miscCache[key]; ok {
			ent.refreshing = false
		}
		miscCacheMu.Unlock()
		log.Printf("[misc] 后台刷新失败（保留旧值）cluster=%v ns=%q: %v", e.clusterID, namespace, err)
	}
}

// StartMiscCacheKeepalive 后台保活刷新：定期刷新「最近访问过」且已过期的 key，
// 让用户点进来时大概率命中新鲜缓存，同时避免对全集群无脑轮询打爆 apiserver。
// 随 ctx 取消优雅退出，与 main 生命周期一致。
func StartMiscCacheKeepalive(ctx context.Context) {
	ticker := time.NewTicker(miscKeepaliveInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			refreshRecentMiscKeys(ctx)
		}
	}
}

// refreshRecentMiscKeys 挑出「10 分钟内访问过」且「已过期」的 key 逐个后台刷新。
func refreshRecentMiscKeys(ctx context.Context) {
	now := time.Now()
	type job struct {
		ex  *Executor
		key string
		ns  string
	}
	var jobs []job
	miscCacheMu.Lock()
	for key, ent := range miscCache {
		if now.Sub(ent.lastAccess) <= miscKeepaliveRecentWindow && now.Sub(ent.at) >= miscCacheTTL {
			jobs = append(jobs, job{ex: ent.ex, key: key, ns: ent.namespace})
		}
	}
	miscCacheMu.Unlock()

	for _, j := range jobs {
		if j.ex == nil {
			continue
		}
		// 每个 key 独立 goroutine + 独立超时，慢 key 不拖累整轮；singleflight 去重并发重复
		go func(j job) {
			j.ex.refreshMiscBackground(ctx, j.key, j.ns)
		}(j)
	}
}

// ---------- 2026-11-26：分页 + 传输层重试 ----------
// 背景：进生产集群报 `list secrets: http2: server sent GOAWAY ... LastStreamID=379`。
// 除连接层关 HTTP/2（见 k8sclient.tuneTransport）外，这里把大对象列表改成分页拉取，
// 并对瞬时传输错误做有限重试，避免一次失败就把「点进集群」打成报错。

const listPageSize = 500

func (e *Executor) listSecretsPaged(ctx context.Context, ns string) ([]corev1.Secret, error) {
	var out []corev1.Secret
	opts := metav1.ListOptions{Limit: listPageSize}
	for {
		var page *corev1.SecretList
		err := k8sclient.RetryOnTransient(ctx, 3, func() error {
			var e2 error
			page, e2 = e.clientset.CoreV1().Secrets(ns).List(ctx, opts)
			return e2
		})
		if err != nil {
			return nil, err
		}
		out = append(out, page.Items...)
		if page.Continue == "" || len(page.Items) == 0 {
			break
		}
		opts.Continue = page.Continue
	}
	return out, nil
}

func (e *Executor) listConfigMapsPaged(ctx context.Context, ns string) ([]corev1.ConfigMap, error) {
	var out []corev1.ConfigMap
	opts := metav1.ListOptions{Limit: listPageSize}
	for {
		var page *corev1.ConfigMapList
		err := k8sclient.RetryOnTransient(ctx, 3, func() error {
			var e2 error
			page, e2 = e.clientset.CoreV1().ConfigMaps(ns).List(ctx, opts)
			return e2
		})
		if err != nil {
			return nil, err
		}
		out = append(out, page.Items...)
		if page.Continue == "" || len(page.Items) == 0 {
			break
		}
		opts.Continue = page.Continue
	}
	return out, nil
}
