// Package admission 资源治理·准入控制（P0-R1）：MutatingWebhook 依据管控表改写 Deployment。
//
// 本文件是**纯逻辑**：给定 Deployment 与管控配置，计算 JSONPatch。与部署位置无关，可单测。
// 默认 fail-open（调用方在出错/未命中时放行且不返回 patch）。
package admission

import (
	"encoding/json"
	"fmt"
	"strings"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
)

// IgnoreLabel：打了该标签（Deployment 模板或命名空间）则跳过准入改写。
const IgnoreLabel = "apiops-ignore"

// Gov 一行管控配置（对应 resource_governance 表；空串=该项不管控，Replicas<0=不管控）。
type Gov struct {
	Container       string
	Replicas        int
	CPURequest      string
	CPULimit        string
	MemoryRequest   string
	MemoryLimit     string
	JVMXms          string
	JVMXmx          string
	JVMXss          string
	JVMMaxMetaspace string
}

type patchOp struct {
	Op    string      `json:"op"`
	Path  string      `json:"path"`
	Value interface{} `json:"value,omitempty"`
}

func isIgnored(d *appsv1.Deployment) bool {
	if strings.EqualFold(d.Labels[IgnoreLabel], "true") || strings.EqualFold(d.Annotations[IgnoreLabel], "true") {
		return true
	}
	if strings.EqualFold(d.Spec.Template.Labels[IgnoreLabel], "true") ||
		strings.EqualFold(d.Spec.Template.Annotations[IgnoreLabel], "true") {
		return true
	}
	return false
}

// matchGov 为第 i 个容器选择管控行：container=="" 命中第一个容器，否则按名字匹配。
func matchGov(govs []Gov, container string, i int) *Gov {
	for k := range govs {
		g := &govs[k]
		if g.Container == container {
			return g
		}
		if g.Container == "" && i == 0 {
			return g
		}
	}
	return nil
}

// mergeResources 把管控值合并进现有 resources（仅覆盖非空的 cpu/mem 项）。
func mergeResources(cur corev1.ResourceRequirements, g Gov) (corev1.ResourceRequirements, bool) {
	out := corev1.ResourceRequirements{}
	changed := false
	if cur.Requests != nil {
		out.Requests = cur.Requests.DeepCopy()
	}
	if cur.Limits != nil {
		out.Limits = cur.Limits.DeepCopy()
	}
	set := func(dst *corev1.ResourceList, key corev1.ResourceName, val string) {
		if val == "" {
			return
		}
		q, err := resource.ParseQuantity(val)
		if err != nil {
			return
		}
		if *dst == nil {
			*dst = corev1.ResourceList{}
		}
		if existing, ok := (*dst)[key]; !ok || existing.String() != q.String() {
			(*dst)[key] = q
			changed = true
		}
	}
	out.Requests = ensureList(out.Requests)
	out.Limits = ensureList(out.Limits)
	set(&out.Requests, corev1.ResourceCPU, g.CPURequest)
	set(&out.Limits, corev1.ResourceCPU, g.CPULimit)
	set(&out.Requests, corev1.ResourceMemory, g.MemoryRequest)
	set(&out.Limits, corev1.ResourceMemory, g.MemoryLimit)
	if out.Requests != nil && len(out.Requests) == 0 {
		out.Requests = nil
	}
	if out.Limits != nil && len(out.Limits) == 0 {
		out.Limits = nil
	}
	return out, changed
}

func ensureList(l corev1.ResourceList) corev1.ResourceList {
	if l == nil {
		return corev1.ResourceList{}
	}
	return l
}

func fmtFlag(key, val string) string {
	if strings.HasPrefix(key, "-XX") {
		return key + "=" + val
	}
	return key + val
}

// mergeJVMArgs 调整 JVM 启动参数（-Xms/-Xmx/-Xss/-XX:MaxMetaspaceSize），保留其余 token。
func mergeJVMArgs(tokens []string, g Gov) ([]string, bool) {
	want := []struct{ key, val string }{
		{"-Xms", g.JVMXms},
		{"-Xmx", g.JVMXmx},
		{"-Xss", g.JVMXss},
		{"-XX:MaxMetaspaceSize", g.JVMMaxMetaspace},
	}
	any := false
	for _, w := range want {
		if w.val != "" {
			any = true
		}
	}
	if !any {
		return tokens, false
	}
	out := make([]string, 0, len(tokens)+len(want))
	handled := map[string]bool{}
	for _, t := range tokens {
		replaced := false
		for _, w := range want {
			if w.val == "" {
				continue
			}
			if strings.HasPrefix(t, w.key) {
				out = append(out, fmtFlag(w.key, w.val))
				handled[w.key] = true
				replaced = true
				break
			}
		}
		if !replaced {
			out = append(out, t)
		}
	}
	for _, w := range want {
		if w.val != "" && !handled[w.key] {
			out = append(out, fmtFlag(w.key, w.val))
		}
	}
	if len(out) == len(tokens) {
		same := true
		for i := range out {
			if out[i] != tokens[i] {
				same = false
				break
			}
		}
		if same {
			return tokens, false
		}
	}
	return out, true
}

// BuildPatch 计算 Deployment 的 JSONPatch。无改动返回 (nil, nil)。
func BuildPatch(d *appsv1.Deployment, govs []Gov) ([]byte, error) {
	if d == nil || len(govs) == 0 || isIgnored(d) {
		return nil, nil
	}
	ops := []patchOp{}

	// 副本数
	for _, g := range govs {
		if g.Replicas >= 0 {
			ops = append(ops, patchOp{"add", "/spec/replicas", g.Replicas})
			break
		}
	}

	containers := d.Spec.Template.Spec.Containers
	for i := range containers {
		c := containers[i]
		g := matchGov(govs, c.Name, i)
		if g == nil {
			continue
		}
		if res, changed := mergeResources(c.Resources, *g); changed {
			ops = append(ops, patchOp{"add", fmt.Sprintf("/spec/template/spec/containers/%d/resources", i), res})
		}
		// JVM 参数：优先改 args；若 command 以 java 开头也一并处理
		if args, changed := mergeJVMArgs(c.Args, *g); changed {
			ops = append(ops, patchOp{"add", fmt.Sprintf("/spec/template/spec/containers/%d/args", i), args})
		} else if len(c.Command) > 0 && strings.HasSuffix(c.Command[0], "java") {
			if cmd, changed := mergeJVMArgs(c.Command, *g); changed {
				ops = append(ops, patchOp{"add", fmt.Sprintf("/spec/template/spec/containers/%d/command", i), cmd})
			}
		}
	}

	if len(ops) == 0 {
		return nil, nil
	}
	return json.Marshal(ops)
}
