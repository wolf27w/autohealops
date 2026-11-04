// 节点元数据编辑：label / taint（2026-11-04）
// 走 kubectl 语义但由 client-go 直接执行（不经 shell），供节点管理页编辑标签/污点用。
//
//	label node <name> k=v [k2=v2] [k3-]
//	taint node <name> k[=v]:NoSchedule [k2:NoExecute-]
//
// ⚠️ 节点对象常被 kubelet / controller-manager 并发修改（cordone 时会自动加 unschedulable 污点、
// 压力条件变化会加/删污点），直接 Update 会 resourceVersion 冲突 → 这里统一走 RetryOnConflict 重试。
package executor

import (
	"context"
	"fmt"
	"strings"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/validation"
	"k8s.io/client-go/util/retry"
)

// nodeMetaArgs 过滤开关参数（--overwrite 等），只保留 key=value / key- 规格
func nodeMetaArgs(args []string) []string {
	out := make([]string, 0, len(args))
	for _, a := range args {
		if strings.HasPrefix(a, "-") {
			continue
		}
		out = append(out, a)
	}
	return out
}

// patchNodeSpec 读-改-写节点，冲突自动重试；apply 返回 (说明, 是否有变更, error)
func (e *Executor) patchNodeSpec(ctx context.Context, name string,
	apply func(*corev1.Node) (string, bool, error)) (string, error) {
	var msg string
	var changed bool
	err := retry.RetryOnConflict(retry.DefaultBackoff, func() error {
		node, err := e.clientset.CoreV1().Nodes().Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return err
		}
		m, ch, aerr := apply(node)
		if aerr != nil {
			return aerr
		}
		msg, changed = m, ch
		if !ch {
			return nil
		}
		_, uerr := e.clientset.CoreV1().Nodes().Update(ctx, node, metav1.UpdateOptions{})
		return uerr
	})
	if err != nil {
		return "", err
	}
	if !changed {
		msg += "无变化（幂等）"
	}
	return msg, nil
}

// execLabel 节点标签增删（同名 key 直接覆盖，等价 kubectl label --overwrite）
func (e *Executor) execLabel(ctx context.Context, pc *ParsedCommand) (string, error) {
	if pc.Name == "" {
		return "", fmt.Errorf("label 需要节点名")
	}
	specs := nodeMetaArgs(pc.Args)
	if len(specs) == 0 {
		return "", fmt.Errorf("label 需要至少一个 key=value（新增/修改）或 key-（删除）")
	}
	// 先做纯语法校验（不碰集群）
	for _, s := range specs {
		if strings.HasSuffix(s, "-") && !strings.Contains(s, "=") {
			k := strings.TrimSuffix(s, "-")
			if errs := validation.IsQualifiedName(k); len(errs) > 0 {
				return "", fmt.Errorf("非法标签 key %q：%s", k, errs[0])
			}
			continue
		}
		k, v, ok := strings.Cut(s, "=")
		if !ok {
			return "", fmt.Errorf("label 参数 %q 格式应为 key=value 或 key-", s)
		}
		if errs := validation.IsQualifiedName(k); len(errs) > 0 {
			return "", fmt.Errorf("非法标签 key %q：%s", k, errs[0])
		}
		if errs := validation.IsValidLabelValue(v); len(errs) > 0 {
			return "", fmt.Errorf("非法标签值 %q：%s", v, errs[0])
		}
	}
	msg, err := e.patchNodeSpec(ctx, pc.Name, func(node *corev1.Node) (string, bool, error) {
		if node.Labels == nil {
			node.Labels = map[string]string{}
		}
		var b strings.Builder
		changed := false
		for _, s := range specs {
			if strings.HasSuffix(s, "-") && !strings.Contains(s, "=") {
				k := strings.TrimSuffix(s, "-")
				if _, ok := node.Labels[k]; !ok {
					b.WriteString(fmt.Sprintf("标签 %s 不存在（跳过）\n", k))
					continue
				}
				delete(node.Labels, k)
				changed = true
				b.WriteString(fmt.Sprintf("已删除标签 %s\n", k))
				continue
			}
			k, v, _ := strings.Cut(s, "=")
			if old, ok := node.Labels[k]; ok && old == v {
				b.WriteString(fmt.Sprintf("标签 %s=%s 未变化（跳过）\n", k, v))
				continue
			}
			node.Labels[k] = v
			changed = true
			b.WriteString(fmt.Sprintf("已设置标签 %s=%s\n", k, v))
		}
		return b.String(), changed, nil
	})
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("节点 %s 标签：\n%s", pc.Name, msg), nil
}

// taintSpec 解析后的污点规格
type taintSpec struct {
	Key    string
	Value  string
	Effect corev1.TaintEffect
	Remove bool
}

func parseTaintSpec(s string) (*taintSpec, error) {
	remove := strings.HasSuffix(s, "-")
	if remove {
		s = strings.TrimSuffix(s, "-")
	}
	keyPart, eff, ok := strings.Cut(s, ":")
	if !ok || eff == "" {
		return nil, fmt.Errorf("污点格式应为 key[=value]:Effect，Effect ∈ NoSchedule/PreferNoSchedule/NoExecute")
	}
	effect := corev1.TaintEffect(eff)
	switch effect {
	case corev1.TaintEffectNoSchedule, corev1.TaintEffectPreferNoSchedule, corev1.TaintEffectNoExecute:
	default:
		return nil, fmt.Errorf("非法污点 effect %q（只能是 NoSchedule/PreferNoSchedule/NoExecute）", eff)
	}
	key, value, _ := strings.Cut(keyPart, "=")
	if errs := validation.IsQualifiedName(key); len(errs) > 0 {
		return nil, fmt.Errorf("非法污点 key %q：%s", key, errs[0])
	}
	if value != "" {
		if errs := validation.IsValidLabelValue(value); len(errs) > 0 {
			return nil, fmt.Errorf("非法污点值 %q：%s", value, errs[0])
		}
	}
	return &taintSpec{Key: key, Value: value, Effect: effect, Remove: remove}, nil
}

// execTaint 节点污点增删（同名 key+effect 覆盖，等价 kubectl taint --overwrite）
func (e *Executor) execTaint(ctx context.Context, pc *ParsedCommand) (string, error) {
	if pc.Name == "" {
		return "", fmt.Errorf("taint 需要节点名")
	}
	specsRaw := nodeMetaArgs(pc.Args)
	if len(specsRaw) == 0 {
		return "", fmt.Errorf("taint 需要至少一个 key[=value]:Effect（加）或 key[=value]:Effect-（删）")
	}
	specs := make([]*taintSpec, 0, len(specsRaw))
	for _, s := range specsRaw {
		ts, err := parseTaintSpec(s)
		if err != nil {
			return "", err
		}
		specs = append(specs, ts)
	}
	msg, err := e.patchNodeSpec(ctx, pc.Name, func(node *corev1.Node) (string, bool, error) {
		var b strings.Builder
		changed := false
		for _, ts := range specs {
			if ts.Remove {
				kept := node.Spec.Taints[:0]
				removed := false
				for _, t := range node.Spec.Taints {
					if t.Key == ts.Key && t.Effect == ts.Effect && (ts.Value == "" || t.Value == ts.Value) {
						removed = true
						continue
					}
					kept = append(kept, t)
				}
				node.Spec.Taints = kept
				if !removed {
					b.WriteString(fmt.Sprintf("污点 %s:%s 不存在（跳过）\n", ts.Key, ts.Effect))
					continue
				}
				changed = true
				b.WriteString(fmt.Sprintf("已删除污点 %s:%s\n", ts.Key, ts.Effect))
				continue
			}
			idx := -1
			for i := range node.Spec.Taints {
				if node.Spec.Taints[i].Key == ts.Key && node.Spec.Taints[i].Effect == ts.Effect {
					idx = i
					break
				}
			}
			if idx >= 0 {
				if node.Spec.Taints[idx].Value == ts.Value {
					b.WriteString(fmt.Sprintf("污点 %s=%s:%s 未变化（跳过）\n", ts.Key, ts.Value, ts.Effect))
					continue
				}
				node.Spec.Taints[idx].Value = ts.Value
				changed = true
				b.WriteString(fmt.Sprintf("已更新污点 %s=%s:%s\n", ts.Key, ts.Value, ts.Effect))
				continue
			}
			node.Spec.Taints = append(node.Spec.Taints, corev1.Taint{Key: ts.Key, Value: ts.Value, Effect: ts.Effect})
			changed = true
			b.WriteString(fmt.Sprintf("已添加污点 %s=%s:%s\n", ts.Key, ts.Value, ts.Effect))
		}
		return b.String(), changed, nil
	})
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("节点 %s 污点：\n%s", pc.Name, msg), nil
}
