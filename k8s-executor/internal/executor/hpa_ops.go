// Package executor HPA（水平自动扩缩容）管理：列表 / 详情 / 创建更新 / 删除
package executor

import (
	"context"
	"fmt"
	"strings"
	"time"

	autoscalingv2 "k8s.io/api/autoscaling/v2"
	corev1 "k8s.io/api/core/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/yaml"
)

// HPAMetricSpec HPA 单个指标（列表/详情共用，字段与 proto HPAMetricSpec 对齐）
type HPAMetricSpec struct {
	Type               string // Resource / Pods / Object / External
	Name               string // cpu / memory / 自定义指标名
	TargetType         string // Utilization / AverageValue / Value
	TargetUtilization  int32  // 目标利用率百分比（target_type=Utilization）
	TargetValue        string // 目标绝对值（如 500m / 100）
	CurrentValue       string // 当前值（只读回填）
	CurrentUtilization int32  // 当前利用率百分比（只读回填，-1=无）
	CurrentAvailable   bool   // 当前指标是否可用（false = unknown）
}

// HPAInfo HPA 完整信息（列表/详情共用，字段与 proto HPAInfo 对齐）
type HPAInfo struct {
	Namespace                     string
	Name                          string
	TargetKind                    string // Deployment / StatefulSet
	TargetName                    string
	TargetApiVersion              string // apps/v1
	MinReplicas                   int32
	MaxReplicas                   int32
	CurrentReplicas               int32
	DesiredReplicas               int32
	Metrics                       []HPAMetricSpec
	Conditions                    []string // "ScalingActive=True (ValidMetricFound)"
	LastScaleTime                 string   // RFC3339，空=从未
	ScaleUpStabilizationSeconds   int32    // behavior.scaleUp.stabilizationWindowSeconds
	ScaleDownStabilizationSeconds int32    // behavior.scaleDown.stabilizationWindowSeconds
}

// ApplyHPASpec 创建/更新 HPA 的规格（字段与 proto ApplyHPARequest 对齐）
type ApplyHPASpec struct {
	Namespace                     string
	Name                          string
	TargetKind                    string // deployment/statefulset（大小写不敏感）
	TargetName                    string
	MinReplicas                   int32
	MaxReplicas                   int32
	Metrics                       []HPAMetricSpec
	ScaleUpStabilizationSeconds   int32
	ScaleDownStabilizationSeconds int32
}

// ListHPA 列出命名空间下的 HPA（必带 namespace，不跨 ns；targetKind/targetName 非空时按 scaleTargetRef 过滤）
func (e *Executor) ListHPA(ctx context.Context, namespace, targetKind, targetName string) ([]HPAInfo, error) {
	if namespace == "" {
		return nil, fmt.Errorf("namespace 必填")
	}
	list, err := e.clientset.AutoscalingV2().HorizontalPodAutoscalers(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list hpa: %w", err)
	}
	out := make([]HPAInfo, 0, len(list.Items))
	for i := range list.Items {
		h := &list.Items[i]
		if targetKind != "" && !strings.EqualFold(h.Spec.ScaleTargetRef.Kind, targetKind) {
			continue
		}
		if targetName != "" && h.Spec.ScaleTargetRef.Name != targetName {
			continue
		}
		out = append(out, hpaToInfo(h))
	}
	return out, nil
}

// GetHPA 获取单个 HPA：详情 + YAML（只读）
func (e *Executor) GetHPA(ctx context.Context, namespace, name string) (HPAInfo, string, error) {
	h, err := e.clientset.AutoscalingV2().HorizontalPodAutoscalers(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return HPAInfo{}, "", err
	}
	info := hpaToInfo(h)
	y, err := hpaToYAML(h)
	if err != nil {
		return info, "", err
	}
	return info, y, nil
}

// ApplyHPA 创建或更新 HPA：Get → 不存在则 Create，存在则改 Spec 后 Update；dryRun=true 只返回 YAML 不下发
func (e *Executor) ApplyHPA(ctx context.Context, spec ApplyHPASpec, dryRun bool) (string, error) {
	hpa := buildHPA(spec)
	if dryRun {
		return hpaToYAML(hpa)
	}
	client := e.clientset.AutoscalingV2().HorizontalPodAutoscalers(spec.Namespace)
	existing, err := client.Get(ctx, spec.Name, metav1.GetOptions{})
	if err != nil {
		if !k8serrors.IsNotFound(err) {
			return "", fmt.Errorf("查询 HPA 失败: %w", err)
		}
		created, cerr := client.Create(ctx, hpa, metav1.CreateOptions{})
		if cerr != nil {
			return "", fmt.Errorf("创建 HPA 失败: %w", cerr)
		}
		return hpaToYAML(created)
	}
	// 已存在 → 改 Spec 后 Update（保留 resourceVersion）
	hpa.ResourceVersion = existing.ResourceVersion
	updated, uerr := client.Update(ctx, hpa, metav1.UpdateOptions{})
	if uerr != nil {
		return "", fmt.Errorf("更新 HPA 失败: %w", uerr)
	}
	return hpaToYAML(updated)
}

// DeleteHPA 删除 HPA
func (e *Executor) DeleteHPA(ctx context.Context, namespace, name string) error {
	if err := e.clientset.AutoscalingV2().HorizontalPodAutoscalers(namespace).Delete(ctx, name, metav1.DeleteOptions{}); err != nil {
		return fmt.Errorf("删除 HPA 失败: %w", err)
	}
	return nil
}

// hpaToInfo 将 typed HPA 转成结构化信息（读侧）
func hpaToInfo(h *autoscalingv2.HorizontalPodAutoscaler) HPAInfo {
	info := HPAInfo{
		Namespace:        h.Namespace,
		Name:             h.Name,
		TargetKind:       h.Spec.ScaleTargetRef.Kind,
		TargetName:       h.Spec.ScaleTargetRef.Name,
		TargetApiVersion: h.Spec.ScaleTargetRef.APIVersion,
		MaxReplicas:      h.Spec.MaxReplicas,
		CurrentReplicas:  h.Status.CurrentReplicas,
		DesiredReplicas:  h.Status.DesiredReplicas,
	}
	if h.Spec.MinReplicas != nil {
		info.MinReplicas = *h.Spec.MinReplicas
	}
	if h.Status.LastScaleTime != nil {
		info.LastScaleTime = h.Status.LastScaleTime.Time.Format(time.RFC3339)
	}
	for i := range h.Spec.Metrics {
		info.Metrics = append(info.Metrics, metricSpecToInfo(h.Spec.Metrics[i]))
	}
	// 用 Status.CurrentMetrics 回填 current_*（按 type+name 匹配，取不到则 current_available=false）
	for i := range info.Metrics {
		spec := &info.Metrics[i]
		for j := range h.Status.CurrentMetrics {
			cm := &h.Status.CurrentMetrics[j]
			if string(cm.Type) == spec.Type && metricStatusName(*cm) == spec.Name {
				v, u, avail := metricCurrent(*cm)
				spec.CurrentValue = v
				spec.CurrentUtilization = u
				spec.CurrentAvailable = avail
				break
			}
		}
		if !spec.CurrentAvailable {
			spec.CurrentUtilization = -1
		}
	}
	for _, c := range h.Status.Conditions {
		info.Conditions = append(info.Conditions, fmt.Sprintf("%s=%s (%s)", c.Type, c.Status, c.Reason))
	}
	if h.Spec.Behavior != nil {
		if h.Spec.Behavior.ScaleUp != nil && h.Spec.Behavior.ScaleUp.StabilizationWindowSeconds != nil {
			info.ScaleUpStabilizationSeconds = *h.Spec.Behavior.ScaleUp.StabilizationWindowSeconds
		}
		if h.Spec.Behavior.ScaleDown != nil && h.Spec.Behavior.ScaleDown.StabilizationWindowSeconds != nil {
			info.ScaleDownStabilizationSeconds = *h.Spec.Behavior.ScaleDown.StabilizationWindowSeconds
		}
	}
	return info
}

// metricSpecToInfo 转出指标的 spec 侧（type/name/target）
func metricSpecToInfo(m autoscalingv2.MetricSpec) HPAMetricSpec {
	out := HPAMetricSpec{Type: string(m.Type), Name: metricName(m), CurrentUtilization: -1}
	var tgt *autoscalingv2.MetricTarget
	switch m.Type {
	case autoscalingv2.ResourceMetricSourceType:
		if m.Resource != nil {
			tgt = &m.Resource.Target
		}
	case autoscalingv2.PodsMetricSourceType:
		if m.Pods != nil {
			tgt = &m.Pods.Target
		}
	case autoscalingv2.ObjectMetricSourceType:
		if m.Object != nil {
			tgt = &m.Object.Target
		}
	case autoscalingv2.ExternalMetricSourceType:
		if m.External != nil {
			tgt = &m.External.Target
		}
	}
	if tgt != nil {
		switch {
		case tgt.AverageUtilization != nil:
			out.TargetType = "Utilization"
			out.TargetUtilization = *tgt.AverageUtilization
		case tgt.AverageValue != nil:
			out.TargetType = "AverageValue"
			out.TargetValue = tgt.AverageValue.String()
		case tgt.Value != nil:
			out.TargetType = "Value"
			out.TargetValue = tgt.Value.String()
		}
	}
	return out
}

// metricCurrent 提取某条 current 指标的值（utilization 优先，其次 averageValue/value）
func metricCurrent(m autoscalingv2.MetricStatus) (value string, utilization int32, available bool) {
	var mv *autoscalingv2.MetricValueStatus
	switch m.Type {
	case autoscalingv2.ResourceMetricSourceType:
		if m.Resource != nil {
			mv = &m.Resource.Current
		}
	case autoscalingv2.PodsMetricSourceType:
		if m.Pods != nil {
			mv = &m.Pods.Current
		}
	case autoscalingv2.ObjectMetricSourceType:
		if m.Object != nil {
			mv = &m.Object.Current
		}
	case autoscalingv2.ExternalMetricSourceType:
		if m.External != nil {
			mv = &m.External.Current
		}
	}
	if mv == nil {
		return "", -1, false
	}
	switch {
	case mv.AverageUtilization != nil:
		return "", *mv.AverageUtilization, true
	case mv.AverageValue != nil:
		return mv.AverageValue.String(), -1, true
	case mv.Value != nil:
		return mv.Value.String(), -1, true
	}
	return "", -1, true // 指标存在但无值
}

// metricName 提取指标名（spec 侧）
func metricName(m autoscalingv2.MetricSpec) string {
	switch m.Type {
	case autoscalingv2.ResourceMetricSourceType:
		if m.Resource != nil {
			return string(m.Resource.Name)
		}
	case autoscalingv2.PodsMetricSourceType:
		if m.Pods != nil {
			return m.Pods.Metric.Name
		}
	case autoscalingv2.ObjectMetricSourceType:
		if m.Object != nil {
			return m.Object.Metric.Name
		}
	case autoscalingv2.ExternalMetricSourceType:
		if m.External != nil {
			return m.External.Metric.Name
		}
	}
	return ""
}

// metricStatusName 提取指标名（status 侧）
func metricStatusName(m autoscalingv2.MetricStatus) string {
	switch m.Type {
	case autoscalingv2.ResourceMetricSourceType:
		if m.Resource != nil {
			return string(m.Resource.Name)
		}
	case autoscalingv2.PodsMetricSourceType:
		if m.Pods != nil {
			return m.Pods.Metric.Name
		}
	case autoscalingv2.ObjectMetricSourceType:
		if m.Object != nil {
			return m.Object.Metric.Name
		}
	case autoscalingv2.ExternalMetricSourceType:
		if m.External != nil {
			return m.External.Metric.Name
		}
	}
	return ""
}

// buildHPA 由规格构造 HPA 对象（写侧）
func buildHPA(spec ApplyHPASpec) *autoscalingv2.HorizontalPodAutoscaler {
	min := spec.MinReplicas
	kind := normalizeWorkloadKind(spec.TargetKind)
	hpa := &autoscalingv2.HorizontalPodAutoscaler{
		ObjectMeta: metav1.ObjectMeta{
			Name:      spec.Name,
			Namespace: spec.Namespace,
		},
		Spec: autoscalingv2.HorizontalPodAutoscalerSpec{
			ScaleTargetRef: autoscalingv2.CrossVersionObjectReference{
				Kind:       kind,
				Name:       spec.TargetName,
				APIVersion: "apps/v1",
			},
			MinReplicas: &min,
			MaxReplicas: spec.MaxReplicas,
			Metrics:     buildMetrics(spec.Metrics),
		},
	}
	var up, down *autoscalingv2.HPAScalingRules
	if spec.ScaleUpStabilizationSeconds > 0 {
		up = &autoscalingv2.HPAScalingRules{StabilizationWindowSeconds: int32Ptr(spec.ScaleUpStabilizationSeconds)}
	}
	if spec.ScaleDownStabilizationSeconds > 0 {
		down = &autoscalingv2.HPAScalingRules{StabilizationWindowSeconds: int32Ptr(spec.ScaleDownStabilizationSeconds)}
	}
	if up != nil || down != nil {
		hpa.Spec.Behavior = &autoscalingv2.HorizontalPodAutoscalerBehavior{ScaleUp: up, ScaleDown: down}
	}
	return hpa
}

// buildMetrics 将内部指标规格转成 autoscalingv2.MetricSpec（当前支持 Resource / Pods）
func buildMetrics(in []HPAMetricSpec) []autoscalingv2.MetricSpec {
	out := make([]autoscalingv2.MetricSpec, 0, len(in))
	for _, m := range in {
		if ms, ok := buildMetric(m); ok {
			out = append(out, ms)
		}
	}
	return out
}

// buildMetric 单条指标转换；不支持的 type 返回 ok=false（跳过）
func buildMetric(m HPAMetricSpec) (autoscalingv2.MetricSpec, bool) {
	tgt := buildMetricTarget(m)
	switch m.Type {
	case "Resource":
		return autoscalingv2.MetricSpec{
			Type:     autoscalingv2.ResourceMetricSourceType,
			Resource: &autoscalingv2.ResourceMetricSource{Name: corev1.ResourceName(m.Name), Target: tgt},
		}, true
	case "Pods":
		return autoscalingv2.MetricSpec{
			Type: autoscalingv2.PodsMetricSourceType,
			Pods: &autoscalingv2.PodsMetricSource{
				Metric: autoscalingv2.MetricIdentifier{Name: m.Name},
				Target: tgt,
			},
		}, true
	}
	return autoscalingv2.MetricSpec{}, false
}

// buildMetricTarget 构造指标目标值：Utilization 用 AverageUtilization，AverageValue/Value 用绝对量
func buildMetricTarget(m HPAMetricSpec) autoscalingv2.MetricTarget {
	switch m.TargetType {
	case "AverageValue", "Value":
		q, err := resource.ParseQuantity(m.TargetValue)
		if err != nil {
			q = resource.MustParse("0")
		}
		if m.TargetType == "Value" {
			return autoscalingv2.MetricTarget{Type: autoscalingv2.ValueMetricType, Value: &q}
		}
		return autoscalingv2.MetricTarget{Type: autoscalingv2.AverageValueMetricType, AverageValue: &q}
	default:
		return autoscalingv2.MetricTarget{
			Type:               autoscalingv2.UtilizationMetricType,
			AverageUtilization: int32Ptr(m.TargetUtilization),
		}
	}
}

// hpaToYAML 序列化 HPA 为 YAML（清理 managedFields/resourceVersion/uid/creationTimestamp，与 GetWorkloadYaml 一致）
func hpaToYAML(h *autoscalingv2.HorizontalPodAutoscaler) (string, error) {
	m, err := runtime.DefaultUnstructuredConverter.ToUnstructured(h)
	if err != nil {
		return "", fmt.Errorf("转换 HPA 失败: %w", err)
	}
	u := &unstructured.Unstructured{Object: m}
	u.SetManagedFields(nil)
	u.SetResourceVersion("")
	u.SetUID("")
	u.SetCreationTimestamp(metav1.Time{})
	data, err := yaml.Marshal(u.Object)
	if err != nil {
		return "", fmt.Errorf("YAML 序列化失败: %w", err)
	}
	return string(data), nil
}

// normalizeWorkloadKind 大小写不敏感归一化目标 Kind（deployment→Deployment，statefulset→StatefulSet）
func normalizeWorkloadKind(kind string) string {
	switch {
	case strings.EqualFold(kind, "deployment"):
		return "Deployment"
	case strings.EqualFold(kind, "statefulset"):
		return "StatefulSet"
	default:
		return kind
	}
}

func int32Ptr(v int32) *int32 { return &v }
