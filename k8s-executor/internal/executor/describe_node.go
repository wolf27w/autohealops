// Package executor：节点诊断（describe node）
//
// 2026-10-21（老板提出"节点没有诊断"）：新增 describe node，输出可直接给 AI/规则分析的结构化文本。
// 覆盖：Ready/压力条件（含 reason/message/持续时间）、容量与可分配量、节点上 Pod 的 requests/limits 汇总、
// 污点、kubelet/OS/内核/容器运行时版本、Node 事件、节点上 Pod 清单。全部只读。
package executor

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// describeNode 生成节点诊断文本（只读）
func (e *Executor) describeNode(ctx context.Context, name string) (string, error) {
	if name == "" {
		return "", fmt.Errorf("describe node 需要节点名")
	}
	n, err := e.clientset.CoreV1().Nodes().Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}

	var b strings.Builder
	var roles []string
	for k := range n.Labels {
		if strings.HasPrefix(k, "node-role.kubernetes.io/") {
			roles = append(roles, strings.TrimPrefix(k, "node-role.kubernetes.io/"))
		}
	}
	sort.Strings(roles)
	sched := "Schedulable"
	if n.Spec.Unschedulable {
		sched = "Unschedulable（已 cordon，不再调度新 Pod）"
	}
	ready := "Unknown"
	for _, c := range n.Status.Conditions {
		if c.Type == corev1.NodeReady {
			if c.Status == corev1.ConditionTrue {
				ready = "Ready"
			} else {
				ready = "NotReady"
			}
		}
	}
	b.WriteString(fmt.Sprintf("Node %s: %s, %s, roles=%v\n", n.Name, ready, sched, roles))
	b.WriteString(fmt.Sprintf("  kubelet=%s os=%s kernel=%s runtime=%s\n",
		n.Status.NodeInfo.KubeletVersion, n.Status.NodeInfo.OSImage,
		n.Status.NodeInfo.KernelVersion, n.Status.NodeInfo.ContainerRuntimeVersion))
	if t := n.CreationTimestamp.Time; !t.IsZero() {
		b.WriteString(fmt.Sprintf("  joined=%s (age=%s)\n", t.Format("2006-01-02"), shortDuration(time.Since(t))))
	}

	// ---- 条件（NodeReady / MemoryPressure / DiskPressure / PIDPressure / NetworkUnavailable）----
	b.WriteString("Conditions:\n")
	for _, c := range n.Status.Conditions {
		line := fmt.Sprintf("  %s=%s", c.Type, c.Status)
		if c.Reason != "" {
			line += fmt.Sprintf(" reason=%s", c.Reason)
		}
		if !c.LastTransitionTime.IsZero() {
			line += fmt.Sprintf(" since=%s(%s)", c.LastTransitionTime.Format("01-02 15:04:05"),
				shortDuration(time.Since(c.LastTransitionTime.Time)))
		}
		if c.Message != "" {
			line += fmt.Sprintf(" message=%s", strings.ReplaceAll(c.Message, "\n", " "))
		}
		b.WriteString(line + "\n")
	}

	// ---- 容量 / 可分配 ----
	fmtRes := func(m corev1.ResourceList) string {
		parts := []string{}
		for _, k := range []corev1.ResourceName{corev1.ResourceCPU, corev1.ResourceMemory,
			corev1.ResourceEphemeralStorage, corev1.ResourcePods} {
			if v, ok := m[k]; ok {
				parts = append(parts, fmt.Sprintf("%s=%s", k, v.String()))
			}
		}
		return strings.Join(parts, " ")
	}
	b.WriteString("Capacity:    " + fmtRes(n.Status.Capacity) + "\n")
	b.WriteString("Allocatable: " + fmtRes(n.Status.Allocatable) + "\n")

	// ---- 节点上 Pod 的 requests/limits 汇总 ----
	pods, err := e.clientset.CoreV1().Pods("").List(ctx, metav1.ListOptions{
		FieldSelector: "spec.nodeName=" + name,
	})
	if err == nil {
		var cpuReq, memReq, cpuLim, memLim int64
		running, notRunning := 0, 0
		var podLines []string
		for i := range pods.Items {
			p := &pods.Items[i]
			var prCPU, prMem, plCPU, plMem int64
			for _, c := range p.Spec.Containers {
				if v, ok := c.Resources.Requests[corev1.ResourceCPU]; ok {
					prCPU += v.MilliValue()
				}
				if v, ok := c.Resources.Requests[corev1.ResourceMemory]; ok {
					prMem += v.Value()
				}
				if v, ok := c.Resources.Limits[corev1.ResourceCPU]; ok {
					plCPU += v.MilliValue()
				}
				if v, ok := c.Resources.Limits[corev1.ResourceMemory]; ok {
					plMem += v.Value()
				}
			}
			cpuReq += prCPU
			memReq += prMem
			cpuLim += plCPU
			memLim += plMem
			if p.Status.Phase == corev1.PodRunning {
				running++
			} else {
				notRunning++
			}
			restarts := int32(0)
			for _, cs := range p.Status.ContainerStatuses {
				if cs.RestartCount > restarts {
					restarts = cs.RestartCount
				}
			}
			podLines = append(podLines, fmt.Sprintf("    %s/%s phase=%s restarts=%d requests(cpu=%dm,mem=%s)",
				p.Namespace, p.Name, p.Status.Phase, restarts, prCPU, humanBytes(prMem)))
		}
		sort.Strings(podLines)
		b.WriteString(fmt.Sprintf("Pods on node: %d (running=%d, other=%d)\n", len(pods.Items), running, notRunning))
		b.WriteString(fmt.Sprintf("  requests: cpu=%dm mem=%s | limits: cpu=%dm mem=%s\n",
			cpuReq, humanBytes(memReq), cpuLim, humanBytes(memLim)))
		if len(podLines) > 0 {
			b.WriteString("Pod list:\n")
			for _, l := range podLines {
				b.WriteString(l + "\n")
			}
		}
	}

	// ---- 污点 ----
	if len(n.Spec.Taints) == 0 {
		b.WriteString("Taints: (none)\n")
	} else {
		b.WriteString("Taints:\n")
		for _, t := range n.Spec.Taints {
			eff := string(t.Effect)
			line := fmt.Sprintf("  %s=%s:%s", t.Key, t.Value, eff)
			if !t.TimeAdded.IsZero() {
				line += fmt.Sprintf(" added=%s", t.TimeAdded.Format("01-02 15:04:05"))
			}
			b.WriteString(line + "\n")
		}
	}

	// ---- Node 事件 ----
	events, err := e.clientset.CoreV1().Events("").List(ctx, metav1.ListOptions{
		FieldSelector: fmt.Sprintf("involvedObject.kind=Node,involvedObject.name=%s", name),
	})
	if err == nil && len(events.Items) > 0 {
		sort.Slice(events.Items, func(i, j int) bool {
			return events.Items[i].LastTimestamp.After(events.Items[j].LastTimestamp.Time)
		})
		b.WriteString("Events (latest 15):\n")
		for i, ev := range events.Items {
			if i >= 15 {
				break
			}
			ts := ev.LastTimestamp.Time
			if ts.IsZero() {
				ts = ev.EventTime.Time
			}
			b.WriteString(fmt.Sprintf("  [%s] %s %s: %s\n", ts.Format("01-02 15:04:05"), ev.Type, ev.Reason,
				strings.ReplaceAll(ev.Message, "\n", " ")))
		}
	}
	return b.String(), nil
}

// shortDuration 人类可读的短时长（用于 condition 持续时长）
func shortDuration(d time.Duration) string {
	if d < 0 {
		d = -d
	}
	switch {
	case d < time.Minute:
		return fmt.Sprintf("%ds", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh%dm", int(d.Hours()), int(d.Minutes())%60)
	default:
		return fmt.Sprintf("%dd%dh", int(d.Hours())/24, int(d.Hours())%24)
	}
}

func humanBytes(v int64) string {
	const (
		Ki = 1024
		Mi = 1024 * Ki
		Gi = 1024 * Mi
	)
	switch {
	case v >= Gi:
		return fmt.Sprintf("%.1fGi", float64(v)/float64(Gi))
	case v >= Mi:
		return fmt.Sprintf("%.0fMi", float64(v)/float64(Mi))
	case v >= Ki:
		return fmt.Sprintf("%.0fKi", float64(v)/float64(Ki))
	default:
		return fmt.Sprintf("%dB", v)
	}
}
