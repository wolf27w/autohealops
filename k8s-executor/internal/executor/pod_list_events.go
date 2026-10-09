// Package executor 集群管理增强：Pod 列表（含创建时间）/ Pod 事件 / 工作负载（整服务）YAML 查看与应用
package executor

import (
	"context"
	"encoding/base64"
	"fmt"
	"sort"
	"time"
	"unicode/utf8"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"sigs.k8s.io/yaml"
)

// PodBriefInfo Pod 简况（含创建时间，集群管理「容器组」列表用）
type PodBriefInfo struct {
	Namespace      string
	Name           string
	Phase          string
	Ready          bool
	RestartCount   int32
	CreatedAt      string // RFC3339
	NodeName       string
	PodIP          string
	Containers     []string // 业务容器（含 sidecar）
	InitContainers []string
}

// ListPods 列出 namespace（空=全部）下的 Pod，带创建时间/就绪/重启次数
func (e *Executor) ListPods(ctx context.Context, namespace string) ([]PodBriefInfo, error) {
	if namespace == "" {
		namespace = metav1.NamespaceAll
	}
	list, err := e.clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list pods: %w", err)
	}
	out := make([]PodBriefInfo, 0, len(list.Items))
	for i := range list.Items {
		p := &list.Items[i]
		// Ready = phase Running 且所有容器 Ready
		ready := p.Status.Phase == corev1.PodRunning
		var restarts int32
		for _, cs := range p.Status.ContainerStatuses {
			restarts += cs.RestartCount
			if !cs.Ready {
				ready = false
			}
		}
		created := ""
		if !p.CreationTimestamp.IsZero() {
			created = p.CreationTimestamp.Time.Format(time.RFC3339)
		}
		containers := make([]string, 0, len(p.Spec.Containers))
		for _, c := range p.Spec.Containers {
			containers = append(containers, c.Name)
		}
		inits := make([]string, 0, len(p.Spec.InitContainers))
		for _, c := range p.Spec.InitContainers {
			inits = append(inits, c.Name)
		}
		out = append(out, PodBriefInfo{
			Namespace:      p.Namespace,
			Name:           p.Name,
			Phase:          string(p.Status.Phase),
			Ready:          ready,
			RestartCount:   restarts,
			CreatedAt:      created,
			NodeName:       p.Spec.NodeName,
			PodIP:          p.Status.PodIP,
			Containers:     containers,
			InitContainers: inits,
		})
	}
	return out, nil
}

// ContainerDetail 容器详情（含镜像/状态）
type ContainerDetail struct {
	Name         string
	Image        string
	State        string // running/waiting/terminated
	Ready        bool
	RestartCount int32
	Reason       string
	StartedAt    string // RFC3339
	FinishedAt   string // RFC3339
	IsInit       bool
}

// PodDetail Pod 详情（含容器组内容）
type PodDetail struct {
	Name           string
	Namespace      string
	Phase          string
	Ready          bool
	RestartCount   int32
	CreatedAt      string
	NodeName       string
	PodIP          string
	HostIP         string
	Containers     []ContainerDetail
	InitContainers []ContainerDetail
}

// GetPodDetail 获取 Pod 详情：基本信息 + 容器组内各容器（镜像/状态/就绪/重启）
func (e *Executor) GetPodDetail(ctx context.Context, namespace, podName string) (*PodDetail, error) {
	p, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}
	d := &PodDetail{
		Name: p.Name, Namespace: p.Namespace, Phase: string(p.Status.Phase),
		NodeName: p.Spec.NodeName, PodIP: p.Status.PodIP, HostIP: p.Status.HostIP,
	}
	if !p.CreationTimestamp.IsZero() {
		d.CreatedAt = p.CreationTimestamp.Time.Format(time.RFC3339)
	}
	// 镜像：spec 里按名字建索引（status 只给状态，不给镜像）
	imgMap := map[string]string{}
	for _, c := range p.Spec.Containers {
		imgMap[c.Name] = c.Image
	}
	for _, c := range p.Spec.InitContainers {
		imgMap[c.Name] = c.Image
	}

	totalRestart := int32(0)
	allReady := len(p.Status.ContainerStatuses) > 0
	for _, cs := range p.Status.ContainerStatuses {
		totalRestart += cs.RestartCount
		if !cs.Ready {
			allReady = false
		}
		d.Containers = append(d.Containers, buildContainerDetail(cs, imgMap[cs.Name], false))
	}
	for _, cs := range p.Status.InitContainerStatuses {
		totalRestart += cs.RestartCount
		d.InitContainers = append(d.InitContainers, buildContainerDetail(cs, imgMap[cs.Name], true))
	}
	d.RestartCount = totalRestart
	d.Ready = allReady && p.Status.Phase == corev1.PodRunning
	return d, nil
}

func buildContainerDetail(cs corev1.ContainerStatus, image string, isInit bool) ContainerDetail {
	cd := ContainerDetail{Name: cs.Name, Image: image, Ready: cs.Ready, RestartCount: cs.RestartCount, IsInit: isInit}
	switch {
	case cs.State.Running != nil:
		cd.State = "running"
		cd.StartedAt = cs.State.Running.StartedAt.Time.Format(time.RFC3339)
	case cs.State.Waiting != nil:
		cd.State = "waiting"
		cd.Reason = cs.State.Waiting.Reason
	case cs.State.Terminated != nil:
		cd.State = "terminated"
		cd.Reason = cs.State.Terminated.Reason
		if !cs.State.Terminated.StartedAt.IsZero() {
			cd.StartedAt = cs.State.Terminated.StartedAt.Time.Format(time.RFC3339)
		}
		if !cs.State.Terminated.FinishedAt.IsZero() {
			cd.FinishedAt = cs.State.Terminated.FinishedAt.Time.Format(time.RFC3339)
		}
	}
	return cd
}

// PodEventInfo 单个 k8s Event（Pod 相关）
type PodEventInfo struct {
	Type           string
	Reason         string
	Message        string
	Count          int32
	FirstTimestamp string // RFC3339
	LastTimestamp  string // RFC3339
	Source         string
}

// GetPodEvents 查询某个 Pod 的 k8s Event（按最后发生时间倒序，最多 100 条）
func (e *Executor) GetPodEvents(ctx context.Context, namespace, podName string) ([]PodEventInfo, error) {
	fs := fields.OneTermEqualSelector("involvedObject.name", podName).String()
	evs, err := e.clientset.CoreV1().Events(namespace).List(ctx, metav1.ListOptions{FieldSelector: fs})
	if err != nil {
		return nil, fmt.Errorf("list events: %w", err)
	}
	out := make([]PodEventInfo, 0, len(evs.Items))
	for i := range evs.Items {
		ev := &evs.Items[i]
		if ev.InvolvedObject.Kind != "" && ev.InvolvedObject.Kind != "Pod" {
			continue // 同名但非 Pod 对象的事件排除
		}
		first, last := "", ""
		if !ev.FirstTimestamp.IsZero() {
			first = ev.FirstTimestamp.Time.Format(time.RFC3339)
		}
		if !ev.LastTimestamp.IsZero() {
			last = ev.LastTimestamp.Time.Format(time.RFC3339)
		}
		if last == "" && !ev.EventTime.IsZero() {
			last = ev.EventTime.Time.Format(time.RFC3339)
		}
		out = append(out, PodEventInfo{
			Type:           ev.Type,
			Reason:         ev.Reason,
			Message:        ev.Message,
			Count:          ev.Count,
			FirstTimestamp: first,
			LastTimestamp:  last,
			Source:         ev.Source.Component,
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].LastTimestamp > out[j].LastTimestamp })
	if len(out) > 100 {
		out = out[:100]
	}
	return out, nil
}

// workloadGVR 工作负载/配置类资源类型 → GVR
func workloadGVR(kind string) (schema.GroupVersionResource, error) {
	switch kind {
	case "deployment":
		return schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "deployments"}, nil
	case "statefulset":
		return schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "statefulsets"}, nil
	case "daemonset":
		return schema.GroupVersionResource{Group: "apps", Version: "v1", Resource: "daemonsets"}, nil
	case "cronjob":
		return schema.GroupVersionResource{Group: "batch", Version: "v1", Resource: "cronjobs"}, nil
	case "configmap":
		return schema.GroupVersionResource{Group: "", Version: "v1", Resource: "configmaps"}, nil
	case "secret":
		return schema.GroupVersionResource{Group: "", Version: "v1", Resource: "secrets"}, nil
	case "service":
		return schema.GroupVersionResource{Group: "", Version: "v1", Resource: "services"}, nil
	case "ingress":
		return schema.GroupVersionResource{Group: "networking.k8s.io", Version: "v1", Resource: "ingresses"}, nil
	case "pvc":
		return schema.GroupVersionResource{Group: "", Version: "v1", Resource: "persistentvolumeclaims"}, nil
	case "pod":
		return schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}, nil
	default:
		return schema.GroupVersionResource{}, fmt.Errorf("不支持的类型 %q（仅 deployment/statefulset/daemonset/cronjob/pod/configmap/secret/service/ingress/pvc）", kind)
	}
}

// GetWorkloadYaml 获取工作负载（整个服务）YAML（只读）
func (e *Executor) GetWorkloadYaml(ctx context.Context, kind, namespace, name string) (string, error) {
	gvr, err := workloadGVR(kind)
	if err != nil {
		return "", err
	}
	dc, err := dynamic.NewForConfig(e.restConfig)
	if err != nil {
		return "", fmt.Errorf("创建 dynamic client 失败: %w", err)
	}
	u, err := dc.Resource(gvr).Namespace(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	u.SetManagedFields(nil)
	u.SetResourceVersion("")
	u.SetUID("")
	u.SetCreationTimestamp(metav1.Time{})
	if kind == "secret" {
		revealSecretData(u)
	}
	data, err := yaml.Marshal(u.Object)
	if err != nil {
		return "", fmt.Errorf("YAML 序列化失败: %w", err)
	}
	return string(data), nil
}

// ApplyWorkloadYaml 应用工作负载（整个服务）YAML（server-side apply，需操作员权限）
func (e *Executor) ApplyWorkloadYaml(ctx context.Context, kind, namespace, name, yamlContent string) error {
	gvr, err := workloadGVR(kind)
	if err != nil {
		return err
	}
	obj := &unstructured.Unstructured{}
	if err := yaml.Unmarshal([]byte(yamlContent), obj); err != nil {
		return fmt.Errorf("YAML 解析失败: %w", err)
	}
	if obj.GetName() == "" {
		return fmt.Errorf("YAML 缺少 metadata.name")
	}
	// kind 一致性校验（防误 apply 到别的类型）
	if k := obj.GetKind(); k != "" && !kindMatches(k, kind) {
		return fmt.Errorf("YAML Kind=%q 与目标类型 %q 不一致", k, kind)
	}
	if ns := obj.GetNamespace(); ns != "" {
		namespace = ns
	}
	if namespace == "" {
		namespace = "default"
	}
	if name != "" && obj.GetName() != name {
		return fmt.Errorf("YAML 中的 name(%s) 与目标(%s) 不一致", obj.GetName(), name)
	}
	dc, err := dynamic.NewForConfig(e.restConfig)
	if err != nil {
		return fmt.Errorf("创建 dynamic client 失败: %w", err)
	}
	if _, err := dc.Resource(gvr).Namespace(namespace).Apply(
		ctx, obj.GetName(), obj,
		metav1.ApplyOptions{FieldManager: "api-ops", Force: true},
	); err != nil {
		return fmt.Errorf("apply 失败: %w", err)
	}
	return nil
}

// DeleteResource 删除配置类对象（ConfigMap/Secret）
func (e *Executor) DeleteResource(ctx context.Context, kind, namespace, name string) error {
	switch kind {
	case "configmap", "secret", "service", "ingress", "pvc",
		"deployment", "statefulset", "daemonset", "cronjob", "pod":
	default:
		return fmt.Errorf("不支持删除的类型 %q（仅 configmap/secret/service/ingress/pvc/deployment/statefulset/daemonset/cronjob/pod）", kind)
	}
	if namespace == "" || name == "" {
		return fmt.Errorf("namespace/name 不能为空")
	}
	gvr, err := workloadGVR(kind)
	if err != nil {
		return err
	}
	dc, err := dynamic.NewForConfig(e.restConfig)
	if err != nil {
		return fmt.Errorf("创建 dynamic client 失败: %w", err)
	}
	if err := dc.Resource(gvr).Namespace(namespace).Delete(ctx, name, metav1.DeleteOptions{}); err != nil {
		return fmt.Errorf("删除失败: %w", err)
	}
	return nil
}

// revealSecretData 把 Secret 的 data（base64）解码为 stringData（明文），便于查看/编辑
// （如 imagePullSecret 的 .dockerconfigjson 解码后可直接看到用户名/密码）。
// 仅对合法 UTF-8 值解码；二进制值保留在 data（避免 stringData 二次编码破坏内容）。
func revealSecretData(u *unstructured.Unstructured) {
	dataMap, found, _ := unstructured.NestedStringMap(u.Object, "data")
	if !found || len(dataMap) == 0 {
		return
	}
	stringData := map[string]interface{}{}
	remaining := map[string]interface{}{}
	for k, v := range dataMap {
		b, err := base64.StdEncoding.DecodeString(v)
		if err == nil && utf8.Valid(b) {
			stringData[k] = string(b)
		} else {
			remaining[k] = v
		}
	}
	if len(stringData) > 0 {
		_ = unstructured.SetNestedMap(u.Object, stringData, "stringData")
	}
	if len(remaining) > 0 {
		_ = unstructured.SetNestedMap(u.Object, remaining, "data")
	} else {
		delete(u.Object, "data")
	}
}

// kindMatches kind 名与内部小写类型匹配
func kindMatches(kind, internal string) bool {
	switch internal {
	case "deployment":
		return kind == "Deployment"
	case "statefulset":
		return kind == "StatefulSet"
	case "daemonset":
		return kind == "DaemonSet"
	case "cronjob":
		return kind == "CronJob"
	case "configmap":
		return kind == "ConfigMap"
	case "secret":
		return kind == "Secret"
	case "service":
		return kind == "Service"
	case "ingress":
		return kind == "Ingress"
	case "pvc":
		return kind == "PersistentVolumeClaim"
	case "pod":
		return kind == "Pod"
	}
	return false
}
