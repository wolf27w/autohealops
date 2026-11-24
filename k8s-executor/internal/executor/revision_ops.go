// Package executor 发布历史与版本对比（R60）：列出 Deployment 各 revision 的 ReplicaSet + 取指定版本的 Pod 模板
package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"
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

// ListRevisions 列出工作负载全部发布历史（按 revision 降序），并返回当前 revision。
//
// 支持 deployment / statefulset / daemonset。proto 未携带 kind 字段，此处按
// Deployment → StatefulSet → DaemonSet 顺序探测，命中即返回：
//   - deployment：走 ReplicaSet + pod-template-hash + deployment.kubernetes.io/revision；
//   - statefulset / daemonset：走 ControllerRevision（ownerRef UID 归属，按 .revision 排序）。
func (e *Executor) ListRevisions(ctx context.Context, namespace, name string) ([]RevisionInfo, int64, error) {
	// Deployment
	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
	switch {
	case err == nil:
		return e.listDeploymentRevisions(ctx, namespace, name, dep)
	case !k8serrors.IsNotFound(err):
		return nil, 0, err
	}
	// StatefulSet
	sts, err := e.clientset.AppsV1().StatefulSets(namespace).Get(ctx, name, metav1.GetOptions{})
	switch {
	case err == nil:
		return listControllerRevisionRevisions(ctx, e.clientset, namespace, name, sts.UID,
			parseRevision(sts.Annotations[revisionAnnotation]))
	case !k8serrors.IsNotFound(err):
		return nil, 0, err
	}
	// DaemonSet
	ds, err := e.clientset.AppsV1().DaemonSets(namespace).Get(ctx, name, metav1.GetOptions{})
	switch {
	case err == nil:
		return listControllerRevisionRevisions(ctx, e.clientset, namespace, name, ds.UID,
			parseRevision(ds.Annotations[revisionAnnotation]))
	case !k8serrors.IsNotFound(err):
		return nil, 0, err
	}
	return nil, 0, fmt.Errorf("未找到工作负载 %s/%s（deployment/statefulset/daemonset）", namespace, name)
}

// listDeploymentRevisions 列出 Deployment 全部发布历史（原 ListRevisions 逻辑，语义不变）
func (e *Executor) listDeploymentRevisions(ctx context.Context, namespace, name string, dep *appsv1.Deployment) ([]RevisionInfo, int64, error) {
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

// listControllerRevisionRevisions 列出 STS/DS 的发布历史（ControllerRevision，按 revision 降序）。
// 当前 revision 由调用方从工作负载注解解析传入（缺失/为 0 时取最大 revision）。
// clientset 抽成参数（kubernetes.Interface），便于用 fake clientset 单测。
func listControllerRevisionRevisions(ctx context.Context, clientset kubernetes.Interface, namespace, name string, uid types.UID, curRev int64) ([]RevisionInfo, int64, error) {
	crs, err := ownedControllerRevisions(ctx, clientset, namespace, uid)
	if err != nil {
		return nil, 0, err
	}
	if curRev == 0 && len(crs) > 0 {
		curRev = crs[len(crs)-1].Revision // 注解缺失 → 取最大 revision
	}
	items := make([]RevisionInfo, 0, len(crs))
	for _, cr := range crs {
		pt, err := templateFromControllerRevision(cr)
		if err != nil {
			continue // 跳过无法解析的 ControllerRevision（与 kubectl 行为一致，不中断整表）
		}
		items = append(items, RevisionInfo{
			Revision:   cr.Revision,
			Replicaset: cr.Name, // 该 revision 对应的 ControllerRevision 名
			Images:     podTemplateImages(pt),
			CreatedAt:  cr.CreationTimestamp.Time.Format(time.RFC3339),
			IsCurrent:  cr.Revision == curRev,
		})
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
// 支持 deployment / statefulset / daemonset。
// toRevision=0 → 回滚到「当前 revision 之前最近的一个 revision」；
// toRevision>0 → 回滚到指定 revision。
//
// deployment：列出 ReplicaSet（按 deployment.kubernetes.io/revision 注解排序）→
// 把目标 RS 的 spec.template 复制到 Deployment 的 spec.template。
// statefulset/daemonset：列出 ControllerRevision（按 ownerRef UID 过滤、cr.Revision 排序）→
// 从目标 ControllerRevision.Data 还原 Pod 模板写回 spec.template，PVC/数据卷不动。
func (e *Executor) RollbackWorkload(ctx context.Context, kind, namespace, name string, toRevision int64) (*RollbackResult, error) {
	// 超时 20s：整个回滚链路（读对象 / 列历史 / 写回）共享一个 deadline
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()

	switch WorkloadKind(kind) {
	case KindDeployment:
		return e.rollbackDeployment(ctx, namespace, name, toRevision)
	case KindStatefulSet:
		return e.rollbackStatefulSet(ctx, namespace, name, toRevision)
	case KindDaemonSet:
		return e.rollbackDaemonSet(ctx, namespace, name, toRevision)
	default:
		return nil, fmt.Errorf("回滚仅支持 deployment/statefulset/daemonset，%s 暂不支持（请使用 restart）", kind)
	}
}

// rollbackDeployment 回滚 Deployment（原有实现，语义不变）
func (e *Executor) rollbackDeployment(ctx context.Context, namespace, name string, toRevision int64) (*RollbackResult, error) {
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

// rollbackStatefulSet 回滚 StatefulSet（用 ControllerRevision，不动 volumeClaimTemplates）
func (e *Executor) rollbackStatefulSet(ctx context.Context, namespace, name string, toRevision int64) (*RollbackResult, error) {
	sts, err := e.clientset.AppsV1().StatefulSets(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("statefulset %s/%s 不存在或获取失败: %w", namespace, name, err)
	}
	return e.rollbackViaControllerRevision(ctx, string(KindStatefulSet), namespace, name, sts.UID, toRevision,
		func(pt *corev1.PodTemplateSpec) error {
			// 只回写 Pod 模板；spec.volumeClaimTemplates（PVC 模板）保持不动
			sts.Spec.Template = *pt
			if _, err := e.clientset.AppsV1().StatefulSets(namespace).Update(ctx, sts, metav1.UpdateOptions{}); err != nil {
				return fmt.Errorf("回写 statefulset %s/%s 失败: %w", namespace, name, err)
			}
			return nil
		})
}

// rollbackDaemonSet 回滚 DaemonSet（用 ControllerRevision）
func (e *Executor) rollbackDaemonSet(ctx context.Context, namespace, name string, toRevision int64) (*RollbackResult, error) {
	ds, err := e.clientset.AppsV1().DaemonSets(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("daemonset %s/%s 不存在或获取失败: %w", namespace, name, err)
	}
	return e.rollbackViaControllerRevision(ctx, string(KindDaemonSet), namespace, name, ds.UID, toRevision,
		func(pt *corev1.PodTemplateSpec) error {
			ds.Spec.Template = *pt
			if _, err := e.clientset.AppsV1().DaemonSets(namespace).Update(ctx, ds, metav1.UpdateOptions{}); err != nil {
				return fmt.Errorf("回写 daemonset %s/%s 失败: %w", namespace, name, err)
			}
			return nil
		})
}

// rollbackViaControllerRevision 用 ControllerRevision 回滚 STS/DS 的 Pod 模板（kubectl rollout undo 语义）
func (e *Executor) rollbackViaControllerRevision(ctx context.Context, kind, namespace, name string, uid types.UID, toRevision int64, apply func(pt *corev1.PodTemplateSpec) error) (*RollbackResult, error) {
	crs, err := ownedControllerRevisions(ctx, e.clientset, namespace, uid)
	if err != nil {
		return nil, fmt.Errorf("列出 %s %s/%s 的 ControllerRevision 失败: %w", kind, namespace, name, err)
	}
	if len(crs) == 0 {
		return nil, fmt.Errorf("%s %s/%s 未找到任何 ControllerRevision，无法回滚", kind, namespace, name)
	}
	// current = 最新 revision（kubectl rollout undo 对 STS/DS 的「上一个版本」= 次新版本）
	current := crs[len(crs)-1].Revision
	target, targetRev, err := pickControllerRevision(crs, toRevision, current)
	if err != nil {
		return nil, fmt.Errorf("%s %s/%s 回滚失败: %w", kind, namespace, name, err)
	}
	pt, err := templateFromControllerRevision(target)
	if err != nil {
		return nil, err
	}
	if err := apply(pt); err != nil {
		return nil, err
	}
	res := &RollbackResult{FromRevision: current, ToRevision: targetRev}
	if imgs := podTemplateImages(pt); len(imgs) > 0 {
		res.Image = imgs[0]
	}
	return res, nil
}

// ownedControllerRevisions 列出属于指定工作负载（按 ownerRef UID 过滤）的 ControllerRevision，按 revision 升序
func ownedControllerRevisions(ctx context.Context, clientset kubernetes.Interface, namespace string, uid types.UID) ([]*appsv1.ControllerRevision, error) {
	crList, err := clientset.AppsV1().ControllerRevisions(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	var owned []*appsv1.ControllerRevision
	for i := range crList.Items {
		cr := &crList.Items[i]
		for _, o := range cr.OwnerReferences {
			if o.UID == uid {
				owned = append(owned, cr)
				break
			}
		}
	}
	sort.Slice(owned, func(i, j int) bool { return owned[i].Revision < owned[j].Revision })
	return owned, nil
}

// pickControllerRevision 从 ControllerRevision 列表里选出回滚目标（内部按 revision 升序排序）。
// toRevision==0 → 取「小于 current 的最大版本」（上一个版本）；
// toRevision>0  → 精确命中该版本；命中 current 视为「无需回滚」报错。
func pickControllerRevision(revisions []*appsv1.ControllerRevision, toRevision, current int64) (*appsv1.ControllerRevision, int64, error) {
	if len(revisions) == 0 {
		return nil, 0, fmt.Errorf("未找到任何历史版本")
	}
	sort.Slice(revisions, func(i, j int) bool { return revisions[i].Revision < revisions[j].Revision })
	if current == 0 {
		current = revisions[len(revisions)-1].Revision
	}
	if toRevision == 0 {
		for i := len(revisions) - 1; i >= 0; i-- {
			if revisions[i].Revision < current {
				return revisions[i], revisions[i].Revision, nil
			}
		}
		return nil, 0, fmt.Errorf("当前仅有一个版本（%d），无可回滚的历史版本", current)
	}
	for i := range revisions {
		if revisions[i].Revision == toRevision {
			if toRevision == current {
				return nil, 0, fmt.Errorf("目标 revision %d 即当前 revision，无需回滚", toRevision)
			}
			return revisions[i], toRevision, nil
		}
	}
	return nil, 0, fmt.Errorf("未找到 revision %d", toRevision)
}

// templateFromControllerRevision 从 ControllerRevision.Data 还原 Pod 模板。
// STS/DS 的 Data.Raw 是「替换 spec.template」的 strategic merge patch：
// {"spec":{"template":{...完整 PodTemplateSpec..., "$patch":"replace"}}}
// spec.template 即历史模板，$patch 标记字段在 json.Unmarshal 时被忽略。
func templateFromControllerRevision(cr *appsv1.ControllerRevision) (*corev1.PodTemplateSpec, error) {
	var patch struct {
		Spec struct {
			Template corev1.PodTemplateSpec `json:"template"`
		} `json:"spec"`
	}
	if err := json.Unmarshal(cr.Data.Raw, &patch); err != nil {
		return nil, fmt.Errorf("解析 ControllerRevision %s 的模板失败: %w", cr.Name, err)
	}
	return &patch.Spec.Template, nil
}

// podTemplateImages 收集 Pod 模板内所有容器镜像（containers + initContainers）
func podTemplateImages(pt *corev1.PodTemplateSpec) []string {
	var out []string
	for _, c := range pt.Spec.Containers {
		out = append(out, c.Image)
	}
	for _, c := range pt.Spec.InitContainers {
		out = append(out, c.Image)
	}
	return out
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

// GetRevisionTemplate 返回指定 revision 的 PodTemplateSpec YAML + 对应 revision 对象名。
// 与 ListRevisions 同序探测 deployment → statefulset → daemonset。
func (e *Executor) GetRevisionTemplate(ctx context.Context, namespace, name string, revision int64) (string, string, error) {
	// Deployment
	if _, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{}); err == nil {
		return e.getDeploymentRevisionTemplate(ctx, namespace, name, revision)
	} else if !k8serrors.IsNotFound(err) {
		return "", "", err
	}
	// StatefulSet
	if sts, err := e.clientset.AppsV1().StatefulSets(namespace).Get(ctx, name, metav1.GetOptions{}); err == nil {
		return getControllerRevisionTemplate(ctx, e.clientset, namespace, sts.UID, revision)
	} else if !k8serrors.IsNotFound(err) {
		return "", "", err
	}
	// DaemonSet
	if ds, err := e.clientset.AppsV1().DaemonSets(namespace).Get(ctx, name, metav1.GetOptions{}); err == nil {
		return getControllerRevisionTemplate(ctx, e.clientset, namespace, ds.UID, revision)
	} else if !k8serrors.IsNotFound(err) {
		return "", "", err
	}
	return "", "", fmt.Errorf("未找到工作负载 %s/%s（deployment/statefulset/daemonset）", namespace, name)
}

// getDeploymentRevisionTemplate 返回 Deployment 指定 revision 的 PodTemplateSpec YAML（原逻辑）
func (e *Executor) getDeploymentRevisionTemplate(ctx context.Context, namespace, name string, revision int64) (string, string, error) {
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

// getControllerRevisionTemplate 返回 STS/DS 指定 revision 的 PodTemplateSpec YAML（ControllerRevision）。
// clientset 抽成参数（kubernetes.Interface），便于用 fake clientset 单测。
func getControllerRevisionTemplate(ctx context.Context, clientset kubernetes.Interface, namespace string, uid types.UID, revision int64) (string, string, error) {
	crs, err := ownedControllerRevisions(ctx, clientset, namespace, uid)
	if err != nil {
		return "", "", err
	}
	for _, cr := range crs {
		if cr.Revision != revision {
			continue
		}
		pt, err := templateFromControllerRevision(cr)
		if err != nil {
			return "", "", err
		}
		// 去掉无意义字段，与 Deployment 路径口径一致
		pt.ManagedFields = nil
		pt.ResourceVersion = ""
		pt.UID = ""
		pt.SelfLink = ""
		pt.Generation = 0
		pt.CreationTimestamp = metav1.Time{}
		data, err := yaml.Marshal(pt)
		if err != nil {
			return "", "", fmt.Errorf("YAML 序列化失败: %w", err)
		}
		return string(data), cr.Name, nil
	}
	return "", "", fmt.Errorf("未找到工作负载 revision %d", revision)
}
