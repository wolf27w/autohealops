// 节点 / Pod 列表的 JSON 输出（R4）：供 api-server 读取可分配量、用量、所属节点等。
// 触发：`get nodes -o json` / `get pods -o json`（executor 原生处理，非 kubectl）。
package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	metricsclientset "k8s.io/metrics/pkg/client/clientset/versioned"
)

// wantsJSON 判断命令参数是否要求 JSON 输出（含 -o json / --output=json / json）
func wantsJSON(args []string) bool {
	for i, a := range args {
		if a == "json" {
			return true
		}
		if (a == "-o" || a == "--output") && i+1 < len(args) && args[i+1] == "json" {
			return true
		}
		if strings.HasPrefix(a, "--output=json") || a == "-ojson" {
			return true
		}
	}
	return false
}

type nodeConditionJSON struct {
	Type    string `json:"type"`
	Status  string `json:"status"`
	Reason  string `json:"reason"`
	Message string `json:"message"`
	Since   string `json:"since"` // lastTransitionTime RFC3339
}

type nodeJSON struct {
	Name          string              `json:"name"`
	Ready         bool                `json:"ready"`
	Unschedulable bool                `json:"unschedulable"`
	Roles         []string            `json:"roles"`
	Allocatable   map[string]string   `json:"allocatable"`
	Capacity      map[string]string   `json:"capacity"`
	PodCount      int                 `json:"pod_count"`
	CPUUsageMilli int64               `json:"cpu_usage_milli"`
	MemUsageBytes int64               `json:"mem_usage_bytes"`
	HasMetrics    bool                `json:"has_metrics"`
	KubeletVer    string              `json:"kubelet_version"`
	InternalIP    string              `json:"internal_ip"` // 诊断用：Prometheus instance 常是节点 IP
	Conditions    []nodeConditionJSON `json:"conditions"`  // 节点诊断用（2026-10-21）
	Taints        []string            `json:"taints"`      // key=value:effect
}

func nodeReady(n *corev1.Node) bool {
	for _, c := range n.Status.Conditions {
		if c.Type == corev1.NodeReady {
			return c.Status == corev1.ConditionTrue
		}
	}
	return false
}

func nodeRoles(n *corev1.Node) []string {
	var roles []string
	for k := range n.Labels {
		if strings.HasPrefix(k, "node-role.kubernetes.io/") {
			r := strings.TrimPrefix(k, "node-role.kubernetes.io/")
			if r != "" {
				roles = append(roles, r)
			}
		}
	}
	sort.Strings(roles)
	return roles
}

func qtyMap(rl corev1.ResourceList) map[string]string {
	out := map[string]string{}
	for _, k := range []corev1.ResourceName{corev1.ResourceCPU, corev1.ResourceMemory, corev1.ResourcePods, corev1.ResourceEphemeralStorage} {
		if q, ok := rl[k]; ok {
			out[string(k)] = q.String()
		}
	}
	return out
}

func (e *Executor) nodesJSON(ctx context.Context) (string, error) {
	nodes, err := e.clientset.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	if err != nil {
		return "", err
	}
	podCount := map[string]int{}
	if pods, e2 := e.clientset.CoreV1().Pods("").List(ctx, metav1.ListOptions{}); e2 == nil {
		for i := range pods.Items {
			if nn := pods.Items[i].Spec.NodeName; nn != "" {
				podCount[nn]++
			}
		}
	}
	// 节点用量（best-effort，metrics-server 不可用则 has_metrics=false）
	usage := map[string][2]int64{}
	hasMetrics := false
	if mc, e2 := metricsclientset.NewForConfig(e.restConfig); e2 == nil {
		if ml, e3 := mc.MetricsV1beta1().NodeMetricses().List(ctx, metav1.ListOptions{}); e3 == nil {
			hasMetrics = true
			for i := range ml.Items {
				m := ml.Items[i]
				usage[m.Name] = [2]int64{m.Usage.Cpu().MilliValue(), m.Usage.Memory().Value()}
			}
		}
	}
	out := make([]nodeJSON, 0, len(nodes.Items))
	for i := range nodes.Items {
		n := &nodes.Items[i]
		u := usage[n.Name]
		conds := make([]nodeConditionJSON, 0, len(n.Status.Conditions))
		for _, c := range n.Status.Conditions {
			conds = append(conds, nodeConditionJSON{
				Type: string(c.Type), Status: string(c.Status), Reason: c.Reason,
				Message: strings.ReplaceAll(c.Message, "\n", " "),
				Since:   c.LastTransitionTime.Format(time.RFC3339),
			})
		}
		taints := make([]string, 0, len(n.Spec.Taints))
		for _, t := range n.Spec.Taints {
			taints = append(taints, fmt.Sprintf("%s=%s:%s", t.Key, t.Value, t.Effect))
		}
		ip := ""
		for _, a := range n.Status.Addresses {
			if a.Type == corev1.NodeInternalIP {
				ip = a.Address
				break
			}
		}
		out = append(out, nodeJSON{
			Name: n.Name, Ready: nodeReady(n), Unschedulable: n.Spec.Unschedulable, Roles: nodeRoles(n),
			Allocatable: qtyMap(n.Status.Allocatable), Capacity: qtyMap(n.Status.Capacity),
			PodCount: podCount[n.Name], CPUUsageMilli: u[0], MemUsageBytes: u[1], HasMetrics: hasMetrics,
			KubeletVer: n.Status.NodeInfo.KubeletVersion, InternalIP: ip, Conditions: conds, Taints: taints,
		})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	b, _ := json.Marshal(out)
	return string(b), nil
}

type podJSON struct {
	Name        string   `json:"name"`
	Namespace   string   `json:"namespace"`
	Phase       string   `json:"phase"`
	NodeName    string   `json:"node_name"`
	OwnerKind   string   `json:"owner_kind"`
	OwnerName   string   `json:"owner_name"`
	HostNetwork bool     `json:"host_network"`
	CPUMilli    int64    `json:"cpu_request_milli"`
	MemBytes    int64    `json:"mem_request_bytes"`
	Containers  []string `json:"containers"`
	Ready       bool     `json:"ready"`
}

func (e *Executor) podsJSON(ctx context.Context, ns string) (string, error) {
	pods, err := e.clientset.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{})
	if err != nil {
		return "", err
	}
	out := make([]podJSON, 0, len(pods.Items))
	for i := range pods.Items {
		p := &pods.Items[i]
		var cpu, mem int64
		names := make([]string, 0, len(p.Spec.Containers))
		for j := range p.Spec.Containers {
			c := &p.Spec.Containers[j]
			names = append(names, c.Name)
			if q, ok := c.Resources.Requests[corev1.ResourceCPU]; ok {
				cpu += q.MilliValue()
			}
			if q, ok := c.Resources.Requests[corev1.ResourceMemory]; ok {
				mem += q.Value()
			}
		}
		ownerKind, ownerName := "", ""
		if len(p.OwnerReferences) > 0 {
			// 取最上层：优先 ReplicaSet/StatefulSet/DaemonSet 的 owner
			for _, o := range p.OwnerReferences {
				ownerKind, ownerName = o.Kind, o.Name
			}
		}
		ready := false
		for _, c := range p.Status.Conditions {
			if c.Type == corev1.PodReady && c.Status == corev1.ConditionTrue {
				ready = true
			}
		}
		out = append(out, podJSON{
			Name: p.Name, Namespace: p.Namespace, Phase: string(p.Status.Phase), NodeName: p.Spec.NodeName,
			OwnerKind: ownerKind, OwnerName: ownerName, HostNetwork: p.Spec.HostNetwork,
			CPUMilli: cpu, MemBytes: mem, Containers: names, Ready: ready,
		})
	}
	b, _ := json.Marshal(out)
	return string(b), nil
}
