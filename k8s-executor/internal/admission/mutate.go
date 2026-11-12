package admission

import (
	"encoding/json"
	"fmt"

	"go.uber.org/zap"
	admissionv1 "k8s.io/api/admission/v1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// kubedoor-ignore 标签：值为 "true" 的对象直接放行，不施加任何管控。
const ignoreLabel = "kubedoor-ignore"

// patchOp 单个 JSONPatch（RFC 6902）操作。
type patchOp struct {
	Op    string      `json:"op"`
	Path  string      `json:"path"`
	Value interface{} `json:"value,omitempty"`
}

// workload 是 Deployment / StatefulSet 的统一视图，仅取 mutation 需要的字段。
type workload struct {
	Kind       string
	Name       string
	Labels     map[string]string
	Replicas   *int32
	Containers []corev1.Container
}

// allow 放行（无 patch）。
func allow(uid types.UID) *admissionv1.AdmissionResponse {
	return &admissionv1.AdmissionResponse{UID: uid, Allowed: true}
}

// deny fail-closed：拒绝并附带明确 message。
func deny(uid types.UID, msg string) *admissionv1.AdmissionResponse {
	return &admissionv1.AdmissionResponse{
		UID:     uid,
		Allowed: false,
		Result: &metav1.Status{
			Status:  metav1.StatusFailure,
			Message: msg,
			Reason:  metav1.StatusReasonForbidden,
			Code:    403,
		},
	}
}

// mutate 处理单个 AdmissionReview，返回 AdmissionResponse（永不 panic，出错返回 deny）。
func (s *Server) mutate(ar *admissionv1.AdmissionReview) *admissionv1.AdmissionResponse {
	req := ar.Request
	if req == nil {
		return deny("", "AdmissionReview.request 为空")
	}
	uid := req.UID

	// 只处理 Deployment / StatefulSet 的 CREATE / UPDATE，其余放行。
	switch req.Kind.Kind {
	case "Deployment", "StatefulSet":
	default:
		return allow(uid)
	}
	if req.Operation != admissionv1.Create && req.Operation != admissionv1.Update {
		return allow(uid)
	}

	obj, err := parseWorkload(req.Kind.Kind, req.Object.Raw)
	if err != nil {
		return deny(uid, fmt.Sprintf("解析 %s 对象失败: %v", req.Kind.Kind, err))
	}

	// 带 kubedoor-ignore: "true" 标签的对象直接放行。
	if obj.Labels[ignoreLabel] == "true" {
		return allow(uid)
	}

	// 无匹配规则 = 该对象不在管控范围内，放行。
	rule := s.rules.match(req.Namespace, obj.Name)
	if rule == nil {
		return allow(uid)
	}

	patch := s.buildPatch(obj, rule)
	if len(patch) == 0 {
		return allow(uid)
	}

	pt := admissionv1.PatchTypeJSONPatch
	s.logger.Debug("admission mutate",
		zap.String("kind", obj.Kind),
		zap.String("namespace", req.Namespace),
		zap.String("name", obj.Name),
		zap.String("uid", string(uid)),
		zap.String("patch", string(patch)),
	)
	return &admissionv1.AdmissionResponse{
		UID:       uid,
		Allowed:   true,
		Patch:     patch,
		PatchType: &pt,
	}
}

// parseWorkload 解析原始对象为统一视图（Deployment / StatefulSet）。
func parseWorkload(kind string, raw []byte) (*workload, error) {
	switch kind {
	case "Deployment":
		var d appsv1.Deployment
		if err := json.Unmarshal(raw, &d); err != nil {
			return nil, err
		}
		return &workload{
			Kind:       kind,
			Name:       d.Name,
			Labels:     d.Labels,
			Replicas:   d.Spec.Replicas,
			Containers: d.Spec.Template.Spec.Containers,
		}, nil
	case "StatefulSet":
		var sts appsv1.StatefulSet
		if err := json.Unmarshal(raw, &sts); err != nil {
			return nil, err
		}
		return &workload{
			Kind:       kind,
			Name:       sts.Name,
			Labels:     sts.Labels,
			Replicas:   sts.Spec.Replicas,
			Containers: sts.Spec.Template.Spec.Containers,
		}, nil
	default:
		return nil, fmt.Errorf("unsupported kind %q", kind)
	}
}

// buildPatch 生成 JSONPatch 字节：spec/replicas 下限/上限 + 各容器 resources 默认值补齐。
// 无改动返回 nil。
func (s *Server) buildPatch(w *workload, rule *Rule) []byte {
	var ops []patchOp

	// 副本数：clamp 到 [min, max]。
	current := int32(1)
	if w.Replicas != nil {
		current = *w.Replicas
	}
	desired := current
	if rule.MinReplicas != nil && desired < *rule.MinReplicas {
		desired = *rule.MinReplicas
	}
	if rule.MaxReplicas != nil && desired > *rule.MaxReplicas {
		desired = *rule.MaxReplicas
	}
	if desired != current {
		ops = append(ops, patchOp{Op: "add", Path: "/spec/replicas", Value: desired})
	}

	// 容器 requests/limits：仅补齐缺失的默认值（不覆盖已显式设置的值）。
	for i := range w.Containers {
		res, changed := fillDefaults(w.Containers[i].Resources, rule.DefaultRequests, rule.DefaultLimits)
		if changed {
			ops = append(ops, patchOp{
				Op:    "add",
				Path:  fmt.Sprintf("/spec/template/spec/containers/%d/resources", i),
				Value: res,
			})
		}
	}

	if len(ops) == 0 {
		return nil
	}
	b, err := json.Marshal(ops)
	if err != nil {
		// 理论不可达（Value 均为可序列化类型）；fail-closed 由调用方兜底
		s.logger.Warn("序列化 admission patch 失败", zap.Error(err))
		return nil
	}
	return b
}

// fillDefaults 在现有 requests/limits 基础上补齐缺失的默认值，返回 (合并结果, 是否有改动)。
func fillDefaults(res corev1.ResourceRequirements, defReq, defLim corev1.ResourceList) (corev1.ResourceRequirements, bool) {
	changed := false
	out := corev1.ResourceRequirements{
		Requests: map[corev1.ResourceName]resource.Quantity{},
		Limits:   map[corev1.ResourceName]resource.Quantity{},
	}
	for k, v := range res.Requests {
		out.Requests[k] = v
	}
	for k, v := range res.Limits {
		out.Limits[k] = v
	}
	for k, v := range defReq {
		if _, ok := out.Requests[k]; !ok {
			out.Requests[k] = v
			changed = true
		}
	}
	for k, v := range defLim {
		if _, ok := out.Limits[k]; !ok {
			out.Limits[k] = v
			changed = true
		}
	}
	return out, changed
}
