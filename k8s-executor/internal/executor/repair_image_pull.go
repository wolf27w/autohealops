// Package executor 镜像拉取修复：定位 Deployment → 参考集群内其他服务 imagePullSecrets → 补上
package executor

import (
	"context"
	"fmt"
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

// RepairImagePull 修复镜像拉取失败：
// 1. Pod → ReplicaSet → Deployment 定位所属 Deployment；Pod 已不存在则从名称推断
// 2. 收集集群内正常服务（非故障 Deployment）的所有 imagePullSecrets
// 3. 对比故障 Deployment 的 secrets，缺失的补上（参考正常服务）
func (e *Executor) RepairImagePull(ctx context.Context, namespace, podName string) *RepairImagePullResult {
	// 1. 定位 Pod 所属 Deployment（Pod 可能已被重建，从名称推断兜底）
	deploymentName, err := e.findDeployment(ctx, namespace, podName)
	if err != nil {
		deploymentName = inferDeploymentName(podName)
		if deploymentName == "" {
			return &RepairImagePullResult{Success: false, Message: err.Error(), Fixed: false}
		}
	}

	// 2. 查故障 Deployment
	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, deploymentName, metav1.GetOptions{})
	if err != nil {
		return &RepairImagePullResult{Success: false, Message: fmt.Sprintf("获取 Deployment 失败: %v", err), Fixed: false}
	}

	// 3. 收集集群内正常服务的 imagePullSecrets（去重）
	normalSecrets, err := e.collectNormalSecrets(ctx, namespace, deploymentName)
	if err != nil {
		return &RepairImagePullResult{Success: false, Message: err.Error(), Fixed: false}
	}
	if len(normalSecrets) == 0 {
		return &RepairImagePullResult{Success: false, Message: "集群内没有其他服务配置 imagePullSecrets，无法自动补（需人工确认镜像仓库认证配置）", Fixed: false}
	}

	// 4. 故障 Deployment 当前 secrets → 找缺失
	currentSecrets := make(map[string]bool)
	for _, s := range dep.Spec.Template.Spec.ImagePullSecrets {
		currentSecrets[s.Name] = true
	}
	var missing []string
	for s := range normalSecrets {
		if !currentSecrets[s] {
			missing = append(missing, s)
		}
	}

	if len(missing) > 0 {
		// 5. 补上缺失的 secrets
		for _, s := range missing {
			dep.Spec.Template.Spec.ImagePullSecrets = append(dep.Spec.Template.Spec.ImagePullSecrets, corev1.LocalObjectReference{Name: s})
		}
		if _, err := e.clientset.AppsV1().Deployments(namespace).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
			return &RepairImagePullResult{Success: false, Message: fmt.Sprintf("patch Deployment 失败: %v", err), Fixed: false}
		}
		return &RepairImagePullResult{
			Success: true,
			Message: fmt.Sprintf("已为 Deployment %s 补 imagePullSecrets %v（参考集群内正常服务）", deploymentName, missing),
			Fixed:   true,
		}
	}

	// 6. secrets 已齐全但仍拉取失败 → 非 secrets 问题
	return &RepairImagePullResult{
		Success: true,
		Message: fmt.Sprintf("Deployment %s 的 imagePullSecrets 已与集群内正常服务一致（%v），但仍拉取失败，可能是镜像名错/凭证失效/网络问题，转人工", deploymentName, keysOf(normalSecrets)),
		Fixed:   false,
	}
}

func keysOf(m map[string]bool) []string {
	var ks []string
	for k := range m {
		ks = append(ks, k)
	}
	return ks
}

// collectNormalSecrets 收集集群内正常服务（Running 且有 Ready 副本的 Deployment）的 imagePullSecrets（去重）
func (e *Executor) collectNormalSecrets(ctx context.Context, namespace, excludeDeployment string) (map[string]bool, error) {
	deps, err := e.clientset.AppsV1().Deployments("").List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("查询集群 Deployment 失败: %v", err)
	}
	secrets := make(map[string]bool)
	for _, d := range deps.Items {
		if d.Namespace == namespace && d.Name == excludeDeployment {
			continue // 跳过故障 Deployment 自己
		}
		// 只参考"正常"的服务：有 Ready 副本（镜像能拉取成功）
		if d.Status.ReadyReplicas <= 0 {
			continue
		}
		for _, s := range d.Spec.Template.Spec.ImagePullSecrets {
			secrets[s.Name] = true
		}
	}
	return secrets, nil
}

// inferDeploymentName 从 Pod 名推断 Deployment 名（Pod 名格式：{deployment}-{rs-hash}-{pod-hash}）
func inferDeploymentName(podName string) string {
	parts := strings.Split(podName, "-")
	if len(parts) >= 3 {
		return strings.Join(parts[:len(parts)-2], "-")
	}
	return podName
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

// findDeployment 通过 Pod → ReplicaSet → Deployment 定位所属 Deployment
func (e *Executor) findDeployment(ctx context.Context, namespace, podName string) (string, error) {
	pod, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{})
	if err != nil {
		return "", fmt.Errorf("获取 Pod 失败: %v", err)
	}
	// Pod → ReplicaSet
	rsName := ""
	for _, owner := range pod.OwnerReferences {
		if owner.Kind == "ReplicaSet" {
			rsName = owner.Name
		}
	}
	if rsName != "" {
		rs, err := e.clientset.AppsV1().ReplicaSets(namespace).Get(ctx, rsName, metav1.GetOptions{})
		if err == nil {
			for _, owner := range rs.OwnerReferences {
				if owner.Kind == "Deployment" {
					return owner.Name, nil
				}
			}
		}
	}
	// 兜底：直接按 Pod owner 的 Deployment
	for _, owner := range pod.OwnerReferences {
		if owner.Kind == "Deployment" {
			return owner.Name, nil
		}
	}
	return "", fmt.Errorf("无法定位 Pod %s 所属的 Deployment（可能由 StatefulSet/DaemonSet 管理，暂不支持自动补 secrets）", podName)
}
