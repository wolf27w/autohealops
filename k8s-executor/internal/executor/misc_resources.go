// Package executor 集群管理其它资源：服务/路由(Ingress)/ConfigMap/Secret/存储(PVC)
package executor

import (
	"context"
	"fmt"
	"sort"
	"strings"

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
}

// IngressInfo 路由简况
type IngressInfo struct {
	Namespace string
	Name      string
	Hosts     string
	Class     string
	Address   string
}

// ConfigMapInfo ConfigMap 简况（不取内容）
type ConfigMapInfo struct {
	Namespace string
	Name      string
	DataCount int32
}

// SecretInfo Secret 简况（不取内容）
type SecretInfo struct {
	Namespace string
	Name      string
	Type      string
	DataCount int32
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
}

// StorageClassInfo 存储类简况（cluster-scoped）
type StorageClassInfo struct {
	Name        string
	Provisioner string
	IsDefault   bool
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

// ListMiscResources 列出 namespace（空=全部）下的 服务/路由/ConfigMap/Secret/PVC
func (e *Executor) ListMiscResources(ctx context.Context, namespace string) (*MiscResources, error) {
	if namespace == "" {
		namespace = metav1.NamespaceAll
	}
	out := &MiscResources{
		Services:       []ServiceInfo{},
		Ingresses:      []IngressInfo{},
		ConfigMaps:     []ConfigMapInfo{},
		Secrets:        []SecretInfo{},
		PVCs:           []PvcInfo{},
		StorageClasses: []StorageClassInfo{},
	}

	// 服务
	svcs, err := e.clientset.CoreV1().Services(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list services: %w", err)
	}
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
		out.Services = append(out.Services, ServiceInfo{
			Namespace: s.Namespace, Name: s.Name, Type: string(s.Spec.Type),
			ClusterIP: s.Spec.ClusterIP, Ports: strings.Join(ports, ", "), ExternalIP: extIP,
		})
	}
	sort.Slice(out.Services, func(i, j int) bool {
		if out.Services[i].Namespace != out.Services[j].Namespace {
			return out.Services[i].Namespace < out.Services[j].Namespace
		}
		return out.Services[i].Name < out.Services[j].Name
	})

	// 路由 Ingress（networking.k8s.io/v1）
	ings, err := e.clientset.NetworkingV1().Ingresses(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list ingresses: %w", err)
	}
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
		out.Ingresses = append(out.Ingresses, IngressInfo{
			Namespace: ing.Namespace, Name: ing.Name, Hosts: strings.Join(hosts, ", "),
			Class: class, Address: addr,
		})
	}
	sort.Slice(out.Ingresses, func(i, j int) bool {
		if out.Ingresses[i].Namespace != out.Ingresses[j].Namespace {
			return out.Ingresses[i].Namespace < out.Ingresses[j].Namespace
		}
		return out.Ingresses[i].Name < out.Ingresses[j].Name
	})

	// ConfigMap
	cms, err := e.clientset.CoreV1().ConfigMaps(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list configmaps: %w", err)
	}
	for i := range cms.Items {
		cm := &cms.Items[i]
		out.ConfigMaps = append(out.ConfigMaps, ConfigMapInfo{Namespace: cm.Namespace, Name: cm.Name, DataCount: int32(len(cm.Data) + len(cm.BinaryData))})
	}
	sort.Slice(out.ConfigMaps, func(i, j int) bool {
		if out.ConfigMaps[i].Namespace != out.ConfigMaps[j].Namespace {
			return out.ConfigMaps[i].Namespace < out.ConfigMaps[j].Namespace
		}
		return out.ConfigMaps[i].Name < out.ConfigMaps[j].Name
	})

	// Secret（不取内容，只列名字/类型/键数）
	secs, err := e.clientset.CoreV1().Secrets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list secrets: %w", err)
	}
	for i := range secs.Items {
		sc := &secs.Items[i]
		out.Secrets = append(out.Secrets, SecretInfo{Namespace: sc.Namespace, Name: sc.Name, Type: string(sc.Type), DataCount: int32(len(sc.Data))})
	}
	sort.Slice(out.Secrets, func(i, j int) bool {
		if out.Secrets[i].Namespace != out.Secrets[j].Namespace {
			return out.Secrets[i].Namespace < out.Secrets[j].Namespace
		}
		return out.Secrets[i].Name < out.Secrets[j].Name
	})

	// 存储 PVC
	pvcs, err := e.clientset.CoreV1().PersistentVolumeClaims(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list pvcs: %w", err)
	}
	for i := range pvcs.Items {
		p := &pvcs.Items[i]
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
		out.PVCs = append(out.PVCs, PvcInfo{
			Namespace: p.Namespace, Name: p.Name, Status: string(p.Status.Phase),
			Capacity: capacity, StorageClass: sc, VolumeName: p.Spec.VolumeName,
			AccessModes: strings.Join(modes, ","),
		})
	}
	sort.Slice(out.PVCs, func(i, j int) bool {
		if out.PVCs[i].Namespace != out.PVCs[j].Namespace {
			return out.PVCs[i].Namespace < out.PVCs[j].Namespace
		}
		return out.PVCs[i].Name < out.PVCs[j].Name
	})

	// 存储类 StorageClass（cluster-scoped）
	scs, err := e.clientset.StorageV1().StorageClasses().List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list storageclasses: %w", err)
	}
	for i := range scs.Items {
		sc := &scs.Items[i]
		isDefault := false
		for _, k := range []string{"storageclass.kubernetes.io/is-default-class", "storageclass.beta.kubernetes.io/is-default-class"} {
			if v, ok := sc.Annotations[k]; ok && v == "true" {
				isDefault = true
			}
		}
		out.StorageClasses = append(out.StorageClasses, StorageClassInfo{Name: sc.Name, Provisioner: sc.Provisioner, IsDefault: isDefault})
	}
	sort.Slice(out.StorageClasses, func(i, j int) bool { return out.StorageClasses[i].Name < out.StorageClasses[j].Name })

	// 数量上限保护：超大集群避免一次拉爆
	const capN = 5000
	trim := func(n int) int {
		if n > capN {
			return capN
		}
		return n
	}
	out.Services = out.Services[:trim(len(out.Services))]
	out.Ingresses = out.Ingresses[:trim(len(out.Ingresses))]
	out.ConfigMaps = out.ConfigMaps[:trim(len(out.ConfigMaps))]
	out.Secrets = out.Secrets[:trim(len(out.Secrets))]
	out.PVCs = out.PVCs[:trim(len(out.PVCs))]
	out.StorageClasses = out.StorageClasses[:trim(len(out.StorageClasses))]
	return out, nil
}
