// Package executor 发布历史与版本对比（R60）：列出 Deployment 各 revision 的 ReplicaSet + 取指定版本的 Pod 模板
package executor

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/yaml"
)

const (
	revisionAnnotation   = "deployment.kubernetes.io/revision"
	changeCauseAnno      = "deployment.kubernetes.io/change-cause"
	podTemplateHashLabel = "pod-template-hash"
)

// revisionFailureReasons 容器 waiting 状态中「典型起不来」的 reason
var revisionFailureReasons = map[string]bool{
	"CrashLoopBackOff":           true,
	"ImagePullBackOff":           true,
	"ErrImagePull":               true,
	"CreateContainerConfigError": true,
}

// RevisionInfo 单个 revision 的发布信息（字段与 proto RevisionInfo 对齐）
type RevisionInfo struct {
	Revision        int64
	Replicaset      string
	Images          []string
	DesiredReplicas int32
	ReadyReplicas   int32
	CreatedAt       string // RFC3339
	IsCurrent       bool
	Failed          bool
	ChangeCause     string
	PodTemplateHash string
}

// ownedByDeployment 判断 ReplicaSet 是否属于指定 Deployment（OwnerReferences）
func ownedByDeployment(rs *appsv1.ReplicaSet, name string) bool {
	for _, o := range rs.OwnerReferences {
		if o.Kind == "Deployment" && o.Name == name {
			return true
		}
	}
	return false
}

// podTemplateHash 取 RS 的 pod-template-hash（RS 自身 label，兜底取模板 label）
func podTemplateHash(rs *appsv1.ReplicaSet) string {
	if h := rs.Labels[podTemplateHashLabel]; h != "" {
		return h
	}
	return rs.Spec.Template.Labels[podTemplateHashLabel]
}

// rsImages 收集 RS 模板内所有容器镜像（containers + initContainers）
func rsImages(rs *appsv1.ReplicaSet) []string {
	var out []string
	for _, c := range rs.Spec.Template.Spec.Containers {
		out = append(out, c.Image)
	}
	for _, c := range rs.Spec.Template.Spec.InitContainers {
		out = append(out, c.Image)
	}
	return out
}

// revisionFailed 判定该 revision 是否「起不来」：
// 1) 有期望副本但 0 就绪（desired > 0 && ready == 0）
// 2) 就绪为 0 时，进一步查该 RS 关联 Pod：任一容器 waiting 且 reason ∈ 失败集，或 Pod phase == Failed
func (e *Executor) revisionFailed(ctx context.Context, namespace string, rs *appsv1.ReplicaSet, desired, ready int32) bool {
	if desired > 0 && ready == 0 {
		return true
	}
	if ready != 0 {
		return false
	}
	hash := podTemplateHash(rs)
	if hash == "" {
		return false
	}
	pods, err := e.clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{
		LabelSelector: podTemplateHashLabel + "=" + hash,
	})
	if err != nil {
		return false
	}
	for i := range pods.Items {
		p := &pods.Items[i]
		if p.Status.Phase == corev1.PodFailed {
			return true
		}
		for _, cs := range p.Status.ContainerStatuses {
			if cs.State.Waiting != nil && revisionFailureReasons[cs.State.Waiting.Reason] {
				return true
			}
		}
		for _, cs := range p.Status.InitContainerStatuses {
			if cs.State.Waiting != nil && revisionFailureReasons[cs.State.Waiting.Reason] {
				return true
			}
		}
	}
	return false
}

// findRevisionRS 找到属于该 Deployment、且 revision 匹配的 ReplicaSet
func (e *Executor) findRevisionRS(ctx context.Context, namespace, name string, revision int64) (*appsv1.ReplicaSet, error) {
	rsList, err := e.clientset.AppsV1().ReplicaSets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	for i := range rsList.Items {
		rs := &rsList.Items[i]
		if !ownedByDeployment(rs, name) {
			continue
		}
		var rv int64
		fmt.Sscanf(rs.Annotations[revisionAnnotation], "%d", &rv)
		if rv == revision {
			return rs, nil
		}
	}
	return nil, fmt.Errorf("未找到 deployment %s 的 revision %d", name, revision)
}

// ListRevisions 列出 Deployment 全部发布历史（按 revision 降序），并返回当前 revision
func (e *Executor) ListRevisions(ctx context.Context, namespace, name string) ([]RevisionInfo, int64, error) {
	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, 0, err
	}
	var curRev int64
	fmt.Sscanf(dep.Annotations[revisionAnnotation], "%d", &curRev)

	rsList, err := e.clientset.AppsV1().ReplicaSets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, 0, err
	}

	items := make([]RevisionInfo, 0, len(rsList.Items))
	for i := range rsList.Items {
		rs := &rsList.Items[i]
		if !ownedByDeployment(rs, name) {
			continue
		}
		var rev int64
		fmt.Sscanf(rs.Annotations[revisionAnnotation], "%d", &rev)
		if rev == 0 {
			continue // 跳过无 revision 注解的 RS（如 orphan）
		}
		desired := int32(0)
		if rs.Spec.Replicas != nil {
			desired = *rs.Spec.Replicas
		}
		ready := rs.Status.ReadyReplicas
		info := RevisionInfo{
			Revision:        rev,
			Replicaset:      rs.Name,
			Images:          rsImages(rs),
			DesiredReplicas: desired,
			ReadyReplicas:   ready,
			CreatedAt:       rs.CreationTimestamp.Time.Format(time.RFC3339),
			IsCurrent:       rev == curRev,
			ChangeCause:     rs.Annotations[changeCauseAnno],
			PodTemplateHash: podTemplateHash(rs),
		}
		info.Failed = e.revisionFailed(ctx, namespace, rs, desired, ready)
		items = append(items, info)
	}

	sort.Slice(items, func(i, j int) bool { return items[i].Revision > items[j].Revision })
	return items, curRev, nil
}

// RollbackResult 回滚执行结果（对应 proto RollbackWorkloadResponse）
type RollbackResult struct {
	FromRevision int64
	ToRevision   int64
	Image        string
}

// RollbackWorkload 工作负载回滚执行通道（等价 kubectl rollout undo）
//
// 仅支持 deployment（statefulset/daemonset 明确返回不支持）。
// toRevision=0 → 回滚到「当前 revision 之前最近的一个 revision」；
// toRevision>0 → 回滚到指定 revision。
//
// 实现：列出该 Deployment 的 ReplicaSet（按 deployment.kubernetes.io/revision 注解排序）→
// 把目标 RS 的 spec.template 复制到 Deployment 的 spec.template，清空 template 注解里的
// deployment.kubernetes.io/revision（让控制器重新分配），selector 保持不动。
func (e *Executor) RollbackWorkload(ctx context.Context, kind, namespace, name string, toRevision int64) (*RollbackResult, error) {
	if WorkloadKind(kind) != KindDeployment {
		return nil, fmt.Errorf("回滚仅支持 deployment，%s 暂不支持（请使用 restart）", kind)
	}

	// 超时 20s：整个回滚链路（读 dep / 列 RS / 写回）共享一个 deadline
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("deployment %s/%s 不存在或获取失败: %w", namespace, name, err)
	}

	curRev := parseRevision(dep.Annotations[revisionAnnotation])
	if curRev == 0 {
		return nil, fmt.Errorf("deployment %s/%s 无 revision 注解，无法回滚", namespace, name)
	}

	rsList, err := e.clientset.AppsV1().ReplicaSets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("列出 ReplicaSet 失败: %w", err)
	}

	// 收集属于该 deployment、且有 revision 注解的 RS，按 revision 升序
	type rsRev struct {
		rev int64
		rs  *appsv1.ReplicaSet
	}
	var owned []rsRev
	for i := range rsList.Items {
		rs := &rsList.Items[i]
		if !ownedByDeployment(rs, name) {
			continue
		}
		rev := parseRevision(rs.Annotations[revisionAnnotation])
		if rev == 0 {
			continue
		}
		owned = append(owned, rsRev{rev: rev, rs: rs})
	}
	if len(owned) == 0 {
		return nil, fmt.Errorf("deployment %s/%s 未找到任何历史 ReplicaSet，无法回滚", namespace, name)
	}
	sort.Slice(owned, func(i, j int) bool { return owned[i].rev < owned[j].rev })

	// 确定目标 revision
	var target *appsv1.ReplicaSet
	var targetRev int64
	if toRevision == 0 {
		// 取「当前 revision 之前最近的一个 revision」
		for i := len(owned) - 1; i >= 0; i-- {
			if owned[i].rev < curRev {
				target, targetRev = owned[i].rs, owned[i].rev
				break
			}
		}
		if target == nil {
			return nil, fmt.Errorf("deployment %s/%s 当前仅有一个 revision（%d），无可回滚的历史版本", namespace, name, curRev)
		}
	} else {
		for i := range owned {
			if owned[i].rev == toRevision {
				target, targetRev = owned[i].rs, owned[i].rev
				break
			}
		}
		if target == nil {
			return nil, fmt.Errorf("deployment %s/%s 未找到 revision %d", namespace, name, toRevision)
		}
		if targetRev == curRev {
			return nil, fmt.Errorf("目标 revision %d 即当前 revision，无需回滚", targetRev)
		}
	}

	// 把目标 RS 的 spec.template 复制到 deployment，并清空 template 上的 revision 注解
	dep.Spec.Template = *target.Spec.Template.DeepCopy()
	if dep.Spec.Template.Annotations != nil {
		delete(dep.Spec.Template.Annotations, revisionAnnotation)
		if len(dep.Spec.Template.Annotations) == 0 {
			dep.Spec.Template.Annotations = nil
		}
	}
	// selector 保持不动（dep.Spec.Selector 未触碰）

	if _, err := e.clientset.AppsV1().Deployments(namespace).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
		return nil, fmt.Errorf("回写 deployment 失败: %w", err)
	}

	res := &RollbackResult{FromRevision: curRev, ToRevision: targetRev}
	if imgs := rsImages(target); len(imgs) > 0 {
		res.Image = imgs[0]
	}
	return res, nil
}

// parseRevision 解析 revision 注解为 int64（解析失败返回 0）
func parseRevision(s string) int64 {
	if s == "" {
		return 0
	}
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0
	}
	return n
}

// GetRevisionTemplate 返回指定 revision 的 PodTemplateSpec YAML + 对应 ReplicaSet 名
func (e *Executor) GetRevisionTemplate(ctx context.Context, namespace, name string, revision int64) (string, string, error) {
	rs, err := e.findRevisionRS(ctx, namespace, name, revision)
	if err != nil {
		return "", "", err
	}
	tmpl := rs.Spec.Template.DeepCopy()
	// 去掉无意义字段（managedFields/资源版本/uid/创建时间等）
	tmpl.ManagedFields = nil
	tmpl.ResourceVersion = ""
	tmpl.UID = ""
	tmpl.SelfLink = ""
	tmpl.Generation = 0
	tmpl.CreationTimestamp = metav1.Time{}
	data, err := yaml.Marshal(tmpl)
	if err != nil {
		return "", "", fmt.Errorf("YAML 序列化失败: %w", err)
	}
	return string(data), rs.Name, nil
}
