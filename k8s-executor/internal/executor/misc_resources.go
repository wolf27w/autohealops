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

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
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
}

// StorageClassInfo 存储类简况（cluster-scoped）
type StorageClassInfo struct {
	Name        string
	Provisioner string
	IsDefault   bool
	CreatedAt   string
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

// 杂项资源短缓存：这些对象（服务/路由/配置/密钥/PVC）变动不频繁，
// 而一次全命名空间 list 在远端集群上要 3~9s，来回切页会反复挨这一刀。
const miscCacheTTL = 15 * time.Second

type miscCacheEntry struct {
	at   time.Time
	data *MiscResources
}

var (
	miscCacheMu sync.Mutex
	miscCache   = map[string]miscCacheEntry{}
	_           = fmt.Sprintf
)

// ListMiscResources 列出 namespace（空=全部）下的 服务/路由/ConfigMap/Secret/PVC/StorageClass
//
// 性能：6 类资源改为**并行**拉取。原先串行时每类单次 list 在真实集群上 0.3~1.2s，
// 累计 5~8s（集群 API server 单次 list 延迟高时更久），是「点进集群特别慢」的主因。
func (e *Executor) ListMiscResources(ctx context.Context, namespace string) (*MiscResources, error) {
	if namespace == "" {
		namespace = metav1.NamespaceAll
	}

	cacheKey := fmt.Sprintf("%d|%s", e.clusterID, namespace)
	miscCacheMu.Lock()
	if hit, ok := miscCache[cacheKey]; ok && time.Since(hit.at) < miscCacheTTL {
		miscCacheMu.Unlock()
		return hit.data, nil
	}
	miscCacheMu.Unlock()

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
		svcs, err := e.clientset.CoreV1().Services(namespace).List(ctx, metav1.ListOptions{})
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
		ings, err := e.clientset.NetworkingV1().Ingresses(namespace).List(ctx, metav1.ListOptions{})
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
		cms, err := e.clientset.CoreV1().ConfigMaps(namespace).List(ctx, metav1.ListOptions{})
		if err != nil {
			fail(fmt.Errorf("list configmaps: %w", err))
			return
		}
		items := make([]ConfigMapInfo, 0, len(cms.Items))
		for i := range cms.Items {
			cm := &cms.Items[i]
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
		secs, err := e.clientset.CoreV1().Secrets(namespace).List(ctx, metav1.ListOptions{})
		if err != nil {
			fail(fmt.Errorf("list secrets: %w", err))
			return
		}
		items := make([]SecretInfo, 0, len(secs.Items))
		for i := range secs.Items {
			sc := &secs.Items[i]
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
		pvcList, err := e.clientset.CoreV1().PersistentVolumeClaims(namespace).List(ctx, metav1.ListOptions{})
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
		scs, err := e.clientset.StorageV1().StorageClasses().List(ctx, metav1.ListOptions{})
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
			items = append(items, StorageClassInfo{Name: sc.Name, Provisioner: sc.Provisioner, IsDefault: isDefault, CreatedAt: sc.CreationTimestamp.Format(time.RFC3339)})
		}
		sort.Slice(items, func(i, j int) bool { return items[i].Name < items[j].Name })
		storageClasses = items
	}()

	wg.Wait()
	// 慢调用留痕（>2s 才打，避免刷日志）：用于定位「点进集群慢」是哪一类 list 拖的
	if el := time.Since(t0); el > 2*time.Second {
		log.Printf("[misc] cluster=%v ns=%q total=%v services=%v ingresses=%v configmaps=%v secrets=%v pvcs=%v storageclasses=%v",
			e.clusterID, namespace, el, cost["services"], cost["ingresses"], cost["configmaps"], cost["secrets"], cost["pvcs"], cost["storageclasses"])
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

	res := &MiscResources{
		Services:       services,
		Ingresses:      ingresses,
		ConfigMaps:     configMaps,
		Secrets:        secrets,
		PVCs:           pvcs,
		StorageClasses: storageClasses,
	}
	miscCacheMu.Lock()
	miscCache[cacheKey] = miscCacheEntry{at: time.Now(), data: res}
	miscCacheMu.Unlock()
	return res, nil
}
