// Package executor 清理已驱逐（Evicted）的 Pod：终态对象，删除不影响业务。
package executor

import (
	"context"
	"fmt"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// CleanupEvicted 删除全集群 phase=Failed 且 reason=Evicted 的 Pod，返回删除数量。
func (e *Executor) CleanupEvicted(ctx context.Context) (int, error) {
	pods, err := e.clientset.CoreV1().Pods("").List(ctx, metav1.ListOptions{
		FieldSelector: "status.phase=Failed",
	})
	if err != nil {
		return 0, fmt.Errorf("查询 Failed Pod 失败: %w", err)
	}
	deleted := 0
	var firstErr error
	for i := range pods.Items {
		p := &pods.Items[i]
		if p.Status.Reason != "Evicted" {
			continue
		}
		if err := e.clientset.CoreV1().Pods(p.Namespace).Delete(ctx, p.Name, metav1.DeleteOptions{}); err != nil {
			if firstErr == nil {
				firstErr = fmt.Errorf("%s/%s 删除失败: %w", p.Namespace, p.Name, err)
			}
			continue
		}
		deleted++
	}
	if deleted == 0 && firstErr != nil {
		return 0, firstErr
	}
	return deleted, nil
}
