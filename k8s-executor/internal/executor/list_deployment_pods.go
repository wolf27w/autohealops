// Package executor 列出 Deployment 所有 Pod 状态（验证修复是否真正恢复，区分新旧版本副本）
package executor

import (
	"context"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// DeploymentPodInfo 单个 Pod 状态
type DeploymentPodInfo struct {
	Name         string
	Phase        string
	Ready        bool
	RestartCount int32
}

// ListDeploymentPods 列出 Deployment selector 匹配的所有 Pod（含滚动更新中的新旧版本 ReplicaSet）
// 返回 Pod 列表 + 是否所有 Pod 都 Running+Ready
func (e *Executor) ListDeploymentPods(ctx context.Context, namespace, deploymentName string) ([]DeploymentPodInfo, bool, error) {
	dep, err := e.clientset.AppsV1().Deployments(namespace).Get(ctx, deploymentName, metav1.GetOptions{})
	if err != nil {
		return nil, false, err
	}
	selector, err := metav1.LabelSelectorAsSelector(dep.Spec.Selector)
	if err != nil {
		return nil, false, err
	}
	pods, err := e.clientset.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{LabelSelector: selector.String()})
	if err != nil {
		return nil, false, err
	}

	allReady := len(pods.Items) > 0
	var infos []DeploymentPodInfo
	for _, p := range pods.Items {
		ready := len(p.Status.ContainerStatuses) > 0
		restart := int32(0)
		for _, cs := range p.Status.ContainerStatuses {
			restart += cs.RestartCount
			if !cs.Ready {
				ready = false
			}
		}
		if p.Status.Phase != "Running" {
			ready = false
		}
		if !ready {
			allReady = false
		}
		infos = append(infos, DeploymentPodInfo{
			Name:         p.Name,
			Phase:        string(p.Status.Phase),
			Ready:        ready,
			RestartCount: restart,
		})
	}
	return infos, allReady, nil
}
