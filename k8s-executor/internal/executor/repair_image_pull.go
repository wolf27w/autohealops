// Package executor 镜像拉取修复：定位工作负载（Deployment / StatefulSet / DaemonSet）
// → 参考同命名空间"正常"服务的 imagePullSecrets → 补上；补不了时识别 401（凭证失效）。
package executor

import (
	"context"
	"fmt"
	"regexp"
	"strings"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// RepairImagePullResult 镜像拉取修复结果
type RepairImagePullResult struct {
	Success bool
	Message string
	Fixed   bool
}

var (
	// Deployment Pod：{deploy}-{rsHash}-{podHash}
	rsPodRe = regexp.MustCompile(`^(.+)-[a-z0-9]{5,10}-[a-z0-9]{5}$`)
	// StatefulSet Pod：{sts}-{ordinal}
	stsPodRe = regexp.MustCompile(`^(.+)-\d+$`)
)

// RepairImagePull 修复镜像拉取失败：
// 1. 定位 Pod 的属主（ReplicaSet→Deployment；StatefulSet/DaemonSet 直接取）
// 2. 收集同命名空间里"正常"工作负载（有 Ready 副本）的 imagePullSecrets
// 3. 对比故障工作负载缺失的 secrets → 补上（四种工作负载都支持）
// 4. secrets 已齐全仍拉取失败 → 读 Pod 事件识别 401，明确告知"凭证失效，补 secrets 修不了"
func (e *Executor) RepairImagePull(ctx context.Context, namespace, podName string) *RepairImagePullResult {
	kind, name, err := e.findWorkloadOwner(ctx, namespace, podName)
	if err != nil {
		return &RepairImagePullResult{Success: false, Message: err.Error()}
	}

	normalSecrets, err := e.collectNormalSecrets(ctx, namespace, kind, name)
	if err != nil {
		return &RepairImagePullResult{Success: false, Message: err.Error()}
	}
	if len(normalSecrets) == 0 {
		return &RepairImagePullResult{
			Success: false,
			Message: "集群内没有其他服务配置 imagePullSecrets，无法自动补（需人工确认镜像仓库认证配置）",
		}
	}

	missing, err := e.patchMissingSecrets(ctx, namespace, kind, name, normalSecrets)
	if err != nil {
		return &RepairImagePullResult{Success: false, Message: err.Error()}
	}
	if len(missing) > 0 {
		return &RepairImagePullResult{
			Success: true,
			Fixed:   true,
			Message: fmt.Sprintf("已为 %s %s 补 imagePullSecrets %v（参考集群内正常服务）", kind, name, missing),
		}
	}

	// secrets 与正常服务一致但仍拉不到：先看是不是 401（凭证失效/无权限）
	if authMsg := e.detectPullAuthError(ctx, namespace, podName); authMsg != "" {
		return &RepairImagePullResult{
			Success: true,
			Fixed:   false,
			Message: fmt.Sprintf("%s %s 的 imagePullSecrets 已与集群内正常服务一致，但镜像仓库返回 401：%s。"+
				"属于凭证失效/账号无该镜像权限，补 secrets 修不了，需在集群内更新 imagePullSecrets 的账号密码", kind, name, authMsg),
		}
	}

	return &RepairImagePullResult{
		Success: true,
		Fixed:   false,
		Message: fmt.Sprintf("%s %s 的 imagePullSecrets 已与集群内正常服务一致（%v），但仍拉取失败，可能是镜像名错/凭证失效/网络问题，转人工", kind, name, keysOf(normalSecrets)),
	}
}

// findWorkloadOwner 定位 Pod 的属主工作负载（Deployment / StatefulSet / DaemonSet）
func (e *Executor) findWorkloadOwner(ctx context.Context, namespace, podName string) (string, string, error) {
	pod, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{})
	if err != nil {
		// Pod 已删除：按名字推断（Deployment / StatefulSet）
		if kind, name := inferWorkloadFromPodName(podName); name != "" {
			return kind, name, nil
		}
		return "", "", fmt.Errorf("无法定位 Pod %s 的属主：%v", podName, err)
	}

	for _, owner := range pod.OwnerReferences {
		switch owner.Kind {
		case "StatefulSet", "DaemonSet":
			return owner.Kind, owner.Name, nil
		case "ReplicaSet":
			rs, e2 := e.clientset.AppsV1().ReplicaSets(namespace).Get(ctx, owner.Name, metav1.GetOptions{})
			if e2 == nil {
				for _, ro := range rs.OwnerReferences {
					if ro.Kind == "Deployment" {
						return "Deployment", ro.Name, nil
					}
				}
			}
			return "", "", fmt.Errorf("ReplicaSet %s 未关联到 Deployment（可能由自定义控制器管理），暂不支持自动补 secrets", owner.Name)
		}
	}
	return "", "", fmt.Errorf("无法定位 Pod %s 的属主（Job/CronJob 等暂不支持自动补 secrets）", podName)
}

// inferWorkloadFromPodName Pod 已被删除时按命名规则推断工作负载
func inferWorkloadFromPodName(podName string) (string, string) {
	if m := rsPodRe.FindStringSubmatch(podName); m != nil {
		return "Deployment", m[1]
	}
	if m := stsPodRe.FindStringSubmatch(podName); m != nil {
		return "StatefulSet", m[1]
	}
	return "", ""
}

// patchMissingSecrets 给故障工作负载补缺失的 imagePullSecrets，返回补了哪些
func (e *Executor) patchMissingSecrets(ctx context.Context, namespace, kind, name string, normal map[string]bool) ([]string, error) {
	switch kind {
	case "Deployment":
		dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return nil, fmt.Errorf("获取 Deployment 失败: %v", err)
		}
		missing := diffSecrets(dep.Spec.Template.Spec.ImagePullSecrets, normal)
		if len(missing) == 0 {
			return nil, nil
		}
		for _, s := range missing {
			dep.Spec.Template.Spec.ImagePullSecrets = append(dep.Spec.Template.Spec.ImagePullSecrets, corev1.LocalObjectReference{Name: s})
		}
		if _, err := e.clientset.AppsV1().Deployments(namespace).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
			return nil, fmt.Errorf("patch Deployment 失败: %v", err)
		}
		return missing, nil

	case "StatefulSet":
		sts, err := e.clientset.AppsV1().StatefulSets(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return nil, fmt.Errorf("获取 StatefulSet 失败: %v", err)
		}
		missing := diffSecrets(sts.Spec.Template.Spec.ImagePullSecrets, normal)
		if len(missing) == 0 {
			return nil, nil
		}
		for _, s := range missing {
			sts.Spec.Template.Spec.ImagePullSecrets = append(sts.Spec.Template.Spec.ImagePullSecrets, corev1.LocalObjectReference{Name: s})
		}
		if _, err := e.clientset.AppsV1().StatefulSets(namespace).Update(ctx, sts, metav1.UpdateOptions{}); err != nil {
			return nil, fmt.Errorf("patch StatefulSet 失败: %v", err)
		}
		return missing, nil

	case "DaemonSet":
		ds, err := e.clientset.AppsV1().DaemonSets(namespace).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return nil, fmt.Errorf("获取 DaemonSet 失败: %v", err)
		}
		missing := diffSecrets(ds.Spec.Template.Spec.ImagePullSecrets, normal)
		if len(missing) == 0 {
			return nil, nil
		}
		for _, s := range missing {
			ds.Spec.Template.Spec.ImagePullSecrets = append(ds.Spec.Template.Spec.ImagePullSecrets, corev1.LocalObjectReference{Name: s})
		}
		if _, err := e.clientset.AppsV1().DaemonSets(namespace).Update(ctx, ds, metav1.UpdateOptions{}); err != nil {
			return nil, fmt.Errorf("patch DaemonSet 失败: %v", err)
		}
		return missing, nil
	}
	return nil, fmt.Errorf("不支持的工作负载类型: %s", kind)
}

// diffSecrets 找出 normal 里有、existing 里没有的 secret 名
func diffSecrets(existing []corev1.LocalObjectReference, normal map[string]bool) []string {
	cur := make(map[string]bool, len(existing))
	for _, s := range existing {
		cur[s.Name] = true
	}
	missing := make([]string, 0, len(normal))
	for s := range normal {
		if s != "" && !cur[s] {
			missing = append(missing, s)
		}
	}
	return missing
}

func keysOf(m map[string]bool) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	return ks
}

// collectNormalSecrets 收集同命名空间正常服务（有 Ready 副本）的 imagePullSecrets（去重）
// 覆盖 Deployment / StatefulSet / DaemonSet；STS/DS 查询失败不影响主流程。
func (e *Executor) collectNormalSecrets(ctx context.Context, namespace, excludeKind, excludeName string) (map[string]bool, error) {
	secrets := make(map[string]bool)
	add := func(refs []corev1.LocalObjectReference) {
		for _, s := range refs {
			if s.Name != "" {
				secrets[s.Name] = true
			}
		}
	}

	deps, err := e.clientset.AppsV1().Deployments(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("查询 Deployment 失败: %v", err)
	}
	for i := range deps.Items {
		d := &deps.Items[i]
		if excludeKind == "Deployment" && d.Name == excludeName {
			continue // 跳过故障工作负载自己
		}
		if d.Status.ReadyReplicas > 0 { // 只参考"正常"的服务：有 Ready 副本（镜像能拉取成功）
			add(d.Spec.Template.Spec.ImagePullSecrets)
		}
	}

	if stss, err := e.clientset.AppsV1().StatefulSets(namespace).List(ctx, metav1.ListOptions{}); err == nil {
		for i := range stss.Items {
			s := &stss.Items[i]
			if excludeKind == "StatefulSet" && s.Name == excludeName {
				continue
			}
			if s.Status.ReadyReplicas > 0 {
				add(s.Spec.Template.Spec.ImagePullSecrets)
			}
		}
	}

	if dss, err := e.clientset.AppsV1().DaemonSets(namespace).List(ctx, metav1.ListOptions{}); err == nil {
		for i := range dss.Items {
			d := &dss.Items[i]
			if excludeKind == "DaemonSet" && d.Name == excludeName {
				continue
			}
			if d.Status.NumberReady > 0 {
				add(d.Spec.Template.Spec.ImagePullSecrets)
			}
		}
	}

	return secrets, nil
}

// detectPullAuthError 从 Pod 事件里识别镜像仓库认证失败（401），返回命中的事件消息
func (e *Executor) detectPullAuthError(ctx context.Context, namespace, podName string) string {
	evs, err := e.clientset.CoreV1().Events(namespace).List(ctx, metav1.ListOptions{
		FieldSelector: fmt.Sprintf("involvedObject.name=%s,involvedObject.kind=Pod", podName),
	})
	if err != nil || len(evs.Items) == 0 {
		return ""
	}
	for i := len(evs.Items) - 1; i >= 0; i-- { // 从新到旧
		msg := evs.Items[i].Message
		low := strings.ToLower(msg)
		if strings.Contains(low, "401") || strings.Contains(low, "unauthorized") || strings.Contains(low, "authentication required") {
			if len(msg) > 200 {
				msg = msg[:200]
			}
			return msg
		}
	}
	return ""
}

// DeploymentStatus Deployment 副本状态
type DeploymentStatus struct {
	Replicas      int32
	ReadyReplicas int32
	Ready         bool
}

// GetDeploymentStatus 查询 Deployment 副本状态（Pod 重建后判断是否恢复）
func (e *Executor) GetDeploymentStatus(ctx context.Context, namespace, deploymentName string) (*DeploymentStatus, error) {
	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, deploymentName, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}
	replicas := int32(0)
	if dep.Spec.Replicas != nil {
		replicas = *dep.Spec.Replicas
	}
	return &DeploymentStatus{
		Replicas:      replicas,
		ReadyReplicas: dep.Status.ReadyReplicas,
		Ready:         replicas > 0 && dep.Status.ReadyReplicas >= replicas,
	}, nil
}
