// Package executor 列出集群全部 Pod 的重启信息（N5：重启原因 AI 诊断的检测数据源）
package executor

import (
	"context"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// 手动重启注解：kubectl rollout restart 会给新 Pod 打上该注解，用于排除「手动重启」场景
const restartedAtAnnotation = "kubectl.kubernetes.io/restartedAt"

// ContainerRestartInfo 单容器重启信息（含上次终止原因）
type ContainerRestartInfo struct {
	Name                    string
	RestartCount            int32
	LastTerminatedReason    string // OOMKilled / Error / ...
	LastTerminatedExitCode  int32
	LastTerminatedMessage   string
}

// PodRestartInfo 单个 Pod 重启信息
type PodRestartInfo struct {
	Namespace    string
	Name         string
	RestartCount int32 // 全部容器重启次数之和
	RestartedAt  string // restartedAt 注解值（空=无，手动重启标记）
	Phase        string
	Ready        bool
	Containers   []ContainerRestartInfo
}

// ListPodRestartInfo 列出集群全部 namespace 的 Pod 重启信息（含每容器上次终止原因 + 手动重启注解）
func (e *Executor) ListPodRestartInfo(ctx context.Context) ([]PodRestartInfo, error) {
	pods, err := e.clientset.CoreV1().Pods("").List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	out := make([]PodRestartInfo, 0, len(pods.Items))
	for i := range pods.Items {
		p := &pods.Items[i]
		info := PodRestartInfo{
			Namespace:   p.Namespace,
			Name:        p.Name,
			Phase:       string(p.Status.Phase),
			RestartedAt: p.Annotations[restartedAtAnnotation],
		}
		ready := len(p.Status.ContainerStatuses) > 0
		total := int32(0)
		for _, cs := range p.Status.ContainerStatuses {
			c := ContainerRestartInfo{Name: cs.Name, RestartCount: cs.RestartCount}
			total += cs.RestartCount
			if lt := cs.LastTerminationState.Terminated; lt != nil {
				c.LastTerminatedReason = lt.Reason
				c.LastTerminatedExitCode = lt.ExitCode
				c.LastTerminatedMessage = lt.Message
			}
			if !cs.Ready {
				ready = false
			}
			info.Containers = append(info.Containers, c)
		}
		info.RestartCount = total
		info.Ready = ready
		out = append(out, info)
	}
	return out, nil
}
