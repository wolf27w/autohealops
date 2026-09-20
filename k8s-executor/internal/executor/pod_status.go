// Package executor Pod 状态查询（恢复判定）
package executor

import (
	"context"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ContainerStateInfo 单个容器状态
type ContainerStateInfo struct {
	Name         string
	State        string // running/waiting/terminated
	Ready        bool
	RestartCount int32
	Reason       string // waiting/terminated 的 reason（空=无）
}

// PodStatus 结构化 Pod 状态
type PodStatus struct {
	Phase           string
	Ready           bool
	RestartCount    int32
	ContainerStates []ContainerStateInfo
}

// GetPodStatus 查询 Pod 结构化状态（恢复判定：所有容器 Running 且 restartCount 不再增长）
func (e *Executor) GetPodStatus(ctx context.Context, namespace, podName string) (*PodStatus, error) {
	pod, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}
	ps := &PodStatus{Phase: string(pod.Status.Phase)}
	allReady := true
	totalRestart := int32(0)
	for _, cs := range pod.Status.ContainerStatuses {
		info := ContainerStateInfo{Name: cs.Name, Ready: cs.Ready, RestartCount: cs.RestartCount}
		totalRestart += cs.RestartCount
		switch {
		case cs.State.Running != nil:
			info.State = "running"
		case cs.State.Waiting != nil:
			info.State = "waiting"
			info.Reason = cs.State.Waiting.Reason
		case cs.State.Terminated != nil:
			info.State = "terminated"
			info.Reason = cs.State.Terminated.Reason
		}
		if !cs.Ready {
			allReady = false
		}
		ps.ContainerStates = append(ps.ContainerStates, info)
	}
	// Pending（无容器）或存在未 Ready 容器 => 未恢复
	if len(pod.Status.ContainerStatuses) == 0 {
		allReady = false
	}
	ps.Ready = allReady
	ps.RestartCount = totalRestart
	return ps, nil
}

var _ = corev1.PodRunning
