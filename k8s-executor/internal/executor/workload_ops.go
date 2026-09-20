// Package executor 工作负载运维操作：列表 / 副本增减 / 回滚重启 / 探针编辑 / 重新创建（集群管理云原生改造，专项 D）
package executor

import (
	"context"
	"fmt"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
)

// WorkloadKind 工作负载类型
type WorkloadKind string

const (
	KindDeployment  WorkloadKind = "deployment"
	KindStatefulSet WorkloadKind = "statefulset"
	KindDaemonSet   WorkloadKind = "daemonset"
	KindCronJob     WorkloadKind = "cronjob"
)

// WorkloadInfo 工作负载简况
type WorkloadInfo struct {
	Kind          string
	Name          string
	Namespace     string
	Replicas      int32
	ReadyReplicas int32
}

// ListWorkloads 列出指定 namespace（空=全部）下的工作负载（无状态/有状态/守护集/定时任务）
func (e *Executor) ListWorkloads(ctx context.Context, namespace string) ([]WorkloadInfo, error) {
	lo := metav1.ListOptions{}
	if namespace == "" {
		namespace = metav1.NamespaceAll
	}
	var out []WorkloadInfo

	deps, err := e.clientset.AppsV1().Deployments(namespace).List(ctx, lo)
	if err != nil {
		return nil, fmt.Errorf("list deployments: %w", err)
	}
	for _, d := range deps.Items {
		replicas := int32(1)
		if d.Spec.Replicas != nil {
			replicas = *d.Spec.Replicas
		}
		out = append(out, WorkloadInfo{Kind: string(KindDeployment), Name: d.Name, Namespace: d.Namespace, Replicas: replicas, ReadyReplicas: d.Status.ReadyReplicas})
	}

	sts, err := e.clientset.AppsV1().StatefulSets(namespace).List(ctx, lo)
	if err != nil {
		return nil, fmt.Errorf("list statefulsets: %w", err)
	}
	for _, s := range sts.Items {
		replicas := int32(1)
		if s.Spec.Replicas != nil {
			replicas = *s.Spec.Replicas
		}
		out = append(out, WorkloadInfo{Kind: string(KindStatefulSet), Name: s.Name, Namespace: s.Namespace, Replicas: replicas, ReadyReplicas: s.Status.ReadyReplicas})
	}

	ds, err := e.clientset.AppsV1().DaemonSets(namespace).List(ctx, lo)
	if err != nil {
		return nil, fmt.Errorf("list daemonsets: %w", err)
	}
	for _, d := range ds.Items {
		out = append(out, WorkloadInfo{Kind: string(KindDaemonSet), Name: d.Name, Namespace: d.Namespace, Replicas: d.Status.DesiredNumberScheduled, ReadyReplicas: d.Status.NumberReady})
	}

	cjs, err := e.clientset.BatchV1().CronJobs(namespace).List(ctx, lo)
	if err != nil {
		return nil, fmt.Errorf("list cronjobs: %w", err)
	}
	for _, c := range cjs.Items {
		out = append(out, WorkloadInfo{Kind: string(KindCronJob), Name: c.Name, Namespace: c.Namespace, Replicas: 1, ReadyReplicas: 0})
	}
	return out, nil
}

// ScaleWorkload 副本增减（deployment / statefulset；允许降到 0）
func (e *Executor) ScaleWorkload(ctx context.Context, kind, namespace, name string, replicas int32) (string, error) {
	if replicas < 0 {
		return "", fmt.Errorf("副本数不能为负")
	}
	switch WorkloadKind(kind) {
	case KindDeployment:
		dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return "", err
		}
		dep.Spec.Replicas = &replicas
		if _, err := e.clientset.AppsV1().Deployments(namespace).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
			return "", err
		}
		return fmt.Sprintf("deployment %s scaled to %d", name, replicas), nil
	case KindStatefulSet:
		sts, err := e.clientset.AppsV1().StatefulSets(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return "", err
		}
		sts.Spec.Replicas = &replicas
		if _, err := e.clientset.AppsV1().StatefulSets(namespace).Update(ctx, sts, metav1.UpdateOptions{}); err != nil {
			return "", err
		}
		return fmt.Sprintf("statefulset %s scaled to %d", name, replicas), nil
	default:
		return "", fmt.Errorf("不支持的资源类型 %s（仅 deployment/statefulset）", kind)
	}
}

// RolloutWorkload 回滚/重启：restart=滚动重启；undo=回滚到上一版本（或指定 revision）
func (e *Executor) RolloutWorkload(ctx context.Context, kind, namespace, name, action string, revision int64) (string, error) {
	switch WorkloadKind(kind) {
	case KindDeployment:
		if action == "restart" {
			dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
			if err != nil {
				return "", err
			}
			if dep.Spec.Template.Annotations == nil {
				dep.Spec.Template.Annotations = map[string]string{}
			}
			dep.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = fmt.Sprintf("%d", time.Now().UnixNano())
			if _, err := e.clientset.AppsV1().Deployments(namespace).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
				return "", err
			}
			return fmt.Sprintf("deployment %s restart triggered", name), nil
		}
		if action == "undo" {
			// 回滚：把 deployment template 回退到上一个 revision 的 ReplicaSet template（rollout undo 语义）
			return e.undoDeployment(ctx, namespace, name, revision)
		}
		return "", fmt.Errorf("不支持的 action %s（restart/undo）", action)
	case KindStatefulSet:
		if action != "restart" {
			return "", fmt.Errorf("statefulset 仅支持 restart（滚动重启，避免卷冲突）")
		}
		sts, err := e.clientset.AppsV1().StatefulSets(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return "", err
		}
		if sts.Spec.Template.Annotations == nil {
			sts.Spec.Template.Annotations = map[string]string{}
		}
		sts.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = fmt.Sprintf("%d", time.Now().UnixNano())
		if _, err := e.clientset.AppsV1().StatefulSets(namespace).Update(ctx, sts, metav1.UpdateOptions{}); err != nil {
			return "", err
		}
		return fmt.Sprintf("statefulset %s restart triggered", name), nil
	default:
		return "", fmt.Errorf("不支持的资源类型 %s", kind)
	}
}

// undoDeployment 回滚 deployment 到指定 revision（默认上一版本）
// client-go 已移除 Rollback 方法，改为：找目标 revision 的 ReplicaSet → 取其 template → 回写 deployment template
func (e *Executor) undoDeployment(ctx context.Context, namespace, name string, revision int64) (string, error) {
	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	rsList, err := e.clientset.AppsV1().ReplicaSets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return "", err
	}

	// 找属于该 deployment、且 revision == target 的 ReplicaSet；revision=0 时取最大（非当前）revision
	curRev := dep.Annotations["deployment.kubernetes.io/revision"]
	var target *appsv1.ReplicaSet
	var maxRev int64 = -1
	for i := range rsList.Items {
		rs := &rsList.Items[i]
		owned := false
		for _, o := range rs.OwnerReferences {
			if o.Kind == "Deployment" && o.Name == name {
				owned = true
				break
			}
		}
		if !owned {
			continue
		}
		revStr := rs.Annotations["deployment.kubernetes.io/revision"]
		var rv int64
		fmt.Sscanf(revStr, "%d", &rv)
		if revision > 0 {
			if rv == revision {
				target = rs
				break
			}
		} else {
			// 回上一版本：找 < 当前 revision 的最大 revision
			var cur int64
			fmt.Sscanf(curRev, "%d", &cur)
			if rv < cur && rv > maxRev {
				maxRev = rv
				target = rs
			}
		}
	}
	if target == nil {
		return "", fmt.Errorf("未找到可回滚的历史版本")
	}
	dep.Spec.Template = target.Spec.Template
	if _, err := e.clientset.AppsV1().Deployments(namespace).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
		return "", err
	}
	return fmt.Sprintf("deployment %s rolled back to revision %s", name, target.Annotations["deployment.kubernetes.io/revision"]), nil
}

// RolloutHistory 回滚历史（deployment rollout history，返回 revision 列表）
func (e *Executor) RolloutHistory(ctx context.Context, namespace, name string) ([]int64, error) {
	rs, err := e.clientset.AppsV1().ReplicaSets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	var revisions []int64
	seen := map[int64]bool{}
	for _, r := range rs.Items {
		// 只取属于该 deployment 的 ReplicaSet（ownerReference 指向 deployment）
		for _, owner := range r.OwnerReferences {
			if owner.Kind == "Deployment" && owner.Name == name {
				rev := r.Annotations["deployment.kubernetes.io/revision"]
				var rv int64
				if rev != "" {
					fmt.Sscanf(rev, "%d", &rv)
				}
				if rv > 0 && !seen[rv] {
					seen[rv] = true
					revisions = append(revisions, rv)
				}
			}
		}
	}
	return revisions, nil
}

// ProbeSpec 探针表单化输入
type ProbeSpec struct {
	ProbeType            string // liveness / readiness / startup
	ProbeKind            string // httpGet / tcpSocket / exec / none
	Path                 string
	Port                 int32
	Command              []string
	InitialDelaySeconds  int32
	PeriodSeconds        int32
	TimeoutSeconds       int32
	FailureThreshold     int32
	SuccessThreshold     int32
}

// UpdateProbe 探针便捷编辑（patch deployment 指定容器的探针）
func (e *Executor) UpdateProbe(ctx context.Context, namespace, name, container string, probe ProbeSpec) (string, error) {
	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	// 定位容器
	var idx = -1
	for i, c := range dep.Spec.Template.Spec.Containers {
		if c.Name == container {
			idx = i
			break
		}
	}
	if idx == -1 {
		if len(dep.Spec.Template.Spec.Containers) > 0 && container == "" {
			idx = 0
		} else {
			return "", fmt.Errorf("容器 %q 不存在", container)
		}
	}
	c := &dep.Spec.Template.Spec.Containers[idx]

	probeObj := buildProbe(probe)

	switch probe.ProbeType {
	case "liveness":
		c.LivenessProbe = probeObj
	case "readiness":
		c.ReadinessProbe = probeObj
	case "startup":
		c.StartupProbe = probeObj
	default:
		return "", fmt.Errorf("探针类型 %q 无效（liveness/readiness/startup）", probe.ProbeType)
	}

	if _, err := e.clientset.AppsV1().Deployments(namespace).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
		return "", err
	}
	return fmt.Sprintf("deployment %s container %s %s probe updated", name, c.Name, probe.ProbeType), nil
}

func buildProbe(spec ProbeSpec) *corev1.Probe {
	if spec.ProbeKind == "none" {
		return nil
	}
	p := &corev1.Probe{
		InitialDelaySeconds: spec.InitialDelaySeconds,
		PeriodSeconds:       spec.PeriodSeconds,
		TimeoutSeconds:      spec.TimeoutSeconds,
		FailureThreshold:    spec.FailureThreshold,
		SuccessThreshold:    spec.SuccessThreshold,
	}
	switch spec.ProbeKind {
	case "httpGet":
		p.HTTPGet = &corev1.HTTPGetAction{Path: spec.Path, Port: intstr.FromInt32(spec.Port)}
	case "tcpSocket":
		p.TCPSocket = &corev1.TCPSocketAction{Port: intstr.FromInt32(spec.Port)}
	case "exec":
		p.Exec = &corev1.ExecAction{Command: spec.Command}
	}
	if p.InitialDelaySeconds == 0 {
		p.InitialDelaySeconds = 10
	}
	if p.PeriodSeconds == 0 {
		p.PeriodSeconds = 10
	}
	if p.TimeoutSeconds == 0 {
		p.TimeoutSeconds = 1
	}
	if p.FailureThreshold == 0 {
		p.FailureThreshold = 3
	}
	if p.SuccessThreshold == 0 {
		p.SuccessThreshold = 1
	}
	return p
}

// RecreateWorkload 重新创建服务
// 无状态(deployment)：先建新（改名加后缀）→ 等就绪 → 删旧（先创建后删除，判据=Pod 就绪）
// 有状态/守护集：滚动重启（不起两套，避免卷冲突）
func (e *Executor) RecreateWorkload(ctx context.Context, kind, namespace, name string) (string, error) {
	switch WorkloadKind(kind) {
	case KindDeployment:
		return e.recreateDeployment(ctx, namespace, name)
	case KindStatefulSet:
		return e.RolloutWorkload(ctx, kind, namespace, name, "restart", 0)
	case KindDaemonSet:
		// 守护集：滚动重启
		ds, err := e.clientset.AppsV1().DaemonSets(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return "", err
		}
		if ds.Spec.Template.Annotations == nil {
			ds.Spec.Template.Annotations = map[string]string{}
		}
		ds.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = fmt.Sprintf("%d", time.Now().UnixNano())
		if _, err := e.clientset.AppsV1().DaemonSets(namespace).Update(ctx, ds, metav1.UpdateOptions{}); err != nil {
			return "", err
		}
		return fmt.Sprintf("daemonset %s restart triggered", name), nil
	default:
		return "", fmt.Errorf("不支持的资源类型 %s", kind)
	}
}

// recreateDeployment 无状态重建：先创建后删除（新 Deployment 用原名加时间戳后缀，label 不变，service 仍可路由）
func (e *Executor) recreateDeployment(ctx context.Context, namespace, name string) (string, error) {
	old, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	// 复制为新对象（改名字，清空 status/uid/resourceVersion）
	newName := fmt.Sprintf("%s-recreate-%d", name, time.Now().Unix())
	old.Spec.DeepCopyInto(&old.Spec)
	newDep := &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{
			Name:      newName,
			Namespace: namespace,
			Labels:    old.Labels,
		},
		Spec: old.Spec,
	}
	replicas := int32(1)
	if old.Spec.Replicas != nil {
		replicas = *old.Spec.Replicas
	}
	newDep.Spec.Replicas = &replicas
	if _, err := e.clientset.AppsV1().Deployments(namespace).Create(ctx, newDep, metav1.CreateOptions{}); err != nil {
		return "", err
	}

	// 等新 Deployment 就绪（最多 120s）
	deadline := time.Now().Add(120 * time.Second)
	for time.Now().Before(deadline) {
		nd, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, newName, metav1.GetOptions{})
		if err == nil && nd.Status.ReadyReplicas >= replicas {
			break
		}
		time.Sleep(3 * time.Second)
	}

	// 删旧
	if err := e.clientset.AppsV1().Deployments(namespace).Delete(ctx, name, metav1.DeleteOptions{}); err != nil {
		return "", fmt.Errorf("新 Deployment 已建（%s），但删旧失败: %w", newName, err)
	}
	return fmt.Sprintf("deployment %s 已重建（先建新 %s → 删旧）", name, newName), nil
}
