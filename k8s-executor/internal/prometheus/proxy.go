// Package prometheus 集群内 Prometheus 查询代理（apiserver Service Proxy，纯 HTTP 无需 port-forward）
package prometheus

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

// Prometheus 服务常见部署命名空间（按优先级探测）
var probeNamespaces = []string{
	"kubesphere-monitoring-system", // KubeSphere 监控栈
	"monitoring",
	"prometheus",
	"kube-system",
	"default",
}

// discoverService 动态发现集群内 Prometheus Service（ClusterIP，端口 9090）
func discoverService(clientset kubernetes.Interface) (string, string, error) {
	for _, ns := range probeNamespaces {
		svcs, err := clientset.CoreV1().Services(ns).List(context.Background(), metav1.ListOptions{})
		if err != nil {
			continue
		}
		for _, svc := range svcs.Items {
			if !strings.Contains(svc.Name, "prometheus") {
				continue
			}
			for _, p := range svc.Spec.Ports {
				if p.Port == 9090 {
					return ns, svc.Name, nil
				}
			}
		}
	}
	return "", "", fmt.Errorf("未发现 Prometheus Service（探测命名空间: %v）", probeNamespaces)
}

// Query 执行 PromQL 查询，返回 Prometheus /api/v1/query 原始 JSON
//
// 走 apiserver Service Proxy（GET /api/v1/namespaces/{ns}/services/{svc}:9090/proxy/api/v1/query），
// 纯 HTTP 无 SPDY upgrade 要求，避免 port-forward 在 LB 场景下无法升级连接的问题。
func Query(clientset kubernetes.Interface, restConfig *rest.Config, promql string) ([]byte, error) {
	ns, svcName, err := discoverService(clientset)
	if err != nil {
		return nil, err
	}

	proxyPath := fmt.Sprintf(
		"%s/api/v1/namespaces/%s/services/%s:9090/proxy/api/v1/query?query=%s",
		strings.TrimRight(restConfig.Host, "/"), ns, svcName, url.QueryEscape(promql),
	)
	req, err := http.NewRequest(http.MethodGet, proxyPath, nil)
	if err != nil {
		return nil, fmt.Errorf("请求构建失败: %w", err)
	}
	transport, err := rest.TransportFor(restConfig)
	if err != nil {
		return nil, fmt.Errorf("transport 构建失败: %w", err)
	}
	client := &http.Client{Transport: transport, Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Prometheus 查询失败: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("Prometheus 响应读取失败: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Prometheus 返回 %d: %s", resp.StatusCode, string(body)[:min(len(body), 300)])
	}
	return body, nil
}

// ContainerUsagePromQL 容器资源使用率 PromQL（usage ÷ limit × 100）//
// 无 limit 的容器 join 不到 kube_pod_container_resource_limits → 自然不返回（跳过）
const (
	CPUPromQL = `sum by (namespace, pod, container) (rate(container_cpu_usage_seconds_total{container!="POD",container!=""}[2m])) ` +
		`/ on(namespace, pod, container) group_left kube_pod_container_resource_limits{resource="cpu"} * 100`
	MemoryPromQL = `container_memory_working_set_bytes{container!="POD",container!=""} ` +
		`/ on(namespace, pod, container) group_left kube_pod_container_resource_limits{resource="memory"} * 100`
)

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// QueryRange 执行 PromQL 范围查询（query_range），返回 Prometheus 原始 JSON（趋势曲线用）。
// start/end 为 Unix 秒，step 为秒。
func QueryRange(clientset kubernetes.Interface, restConfig *rest.Config, promql string, start, end int64, step int) ([]byte, error) {
	ns, svcName, err := discoverService(clientset)
	if err != nil {
		return nil, err
	}
	if step <= 0 {
		step = 30
	}
	proxyPath := fmt.Sprintf(
		"%s/api/v1/namespaces/%s/services/%s:9090/proxy/api/v1/query_range?query=%s&start=%d&end=%d&step=%d",
		strings.TrimRight(restConfig.Host, "/"), ns, svcName, url.QueryEscape(promql), start, end, step,
	)
	req, err := http.NewRequest(http.MethodGet, proxyPath, nil)
	if err != nil {
		return nil, fmt.Errorf("请求构建失败: %w", err)
	}
	transport, err := rest.TransportFor(restConfig)
	if err != nil {
		return nil, fmt.Errorf("transport 构建失败: %w", err)
	}
	client := &http.Client{Transport: transport, Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("Prometheus 范围查询失败: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("Prometheus 响应读取失败: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("Prometheus 返回 %d: %s", resp.StatusCode, string(body)[:min(len(body), 300)])
	}
	return body, nil
}
