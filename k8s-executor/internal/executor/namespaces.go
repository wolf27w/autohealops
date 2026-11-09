// Package executor 命名空间列表（R19：发布入口 namespace 标签门控用）。
package executor

import (
	"context"
	"fmt"
	"sort"
	"strings"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// NamespaceInfo 命名空间简况（name + labels）
type NamespaceInfo struct {
	Name   string
	Labels map[string]string
}

// ListNamespaces 列出集群所有命名空间（含 labels）。labels 供平台侧做发布标签开关判定。
func (e *Executor) ListNamespaces(ctx context.Context) ([]NamespaceInfo, error) {
	nsList, err := e.clientset.CoreV1().Namespaces().List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list namespaces: %w", err)
	}
	out := make([]NamespaceInfo, 0, len(nsList.Items))
	for i := range nsList.Items {
		ns := &nsList.Items[i]
		labels := map[string]string{}
		for k, v := range ns.Labels {
			labels[k] = v
		}
		out = append(out, NamespaceInfo{Name: ns.Name, Labels: labels})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out, nil
}

// LabelsToCSV 把 labels 编码成 "k=v,k2=v2"（顺序稳定，便于跨隧道传输/展示）。
func LabelsToCSV(labels map[string]string) string {
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+labels[k])
	}
	return strings.Join(parts, ",")
}
