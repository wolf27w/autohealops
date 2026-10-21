// Package prometheus 集群内 Prometheus 查询代理（apiserver Service Proxy，纯 HTTP 无需 port-forward）
package prometheus

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
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

// ---- 性能优化（2026-10-21）----
// 背景：大屏容器监控一次刷新会并发 15~18 个 Prom 查询。旧实现每个查询都会：
//  1. LIST 最多 5 个命名空间的 Service 来发现 Prometheus（受 client-go 默认 5 QPS 限流，
//     15 个查询被串行化成 ~4.4s，实测首屏 6.8s）；
//  2. rest.TransportFor() 新建 transport → 每次重新做 TLS 握手，keep-alive 完全复用不上。
//
// 修法：发现结果缓存 5 分钟（Prometheus Service 基本不变）+ http.Client 按 apiserver 复用。
const discoverTTL = 5 * time.Minute

type discoverEntry struct {
	ns      string
	svc     string
	fetched time.Time
}

var (
	discoverMu    sync.Mutex
	discoverCache = map[string]discoverEntry{}

	clientMu    sync.Mutex
	clientCache = map[string]*http.Client{}
)

func cacheKey(clientset kubernetes.Interface) string { return fmt.Sprintf("%p", clientset) }

// httpClientFor 同一 apiserver 复用同一个 http.Client（transport + 连接池）
func httpClientFor(restConfig *rest.Config) (*http.Client, error) {
	key := restConfig.Host
	clientMu.Lock()
	defer clientMu.Unlock()
	if c, ok := clientCache[key]; ok {
		return c, nil
	}
	transport, err := rest.TransportFor(restConfig)
	if err != nil {
		return nil, fmt.Errorf("transport 构建失败: %w", err)
	}
	c := &http.Client{Transport: transport, Timeout: 60 * time.Second}
	clientCache[key] = c
	return c, nil
}

// discoverService 动态发现集群内 Prometheus Service（ClusterIP，端口 9090），结果缓存 discoverTTL
func discoverService(clientset kubernetes.Interface) (string, string, error) {
	key := cacheKey(clientset)
	discoverMu.Lock()
	if e, ok := discoverCache[key]; ok && time.Since(e.fetched) < discoverTTL {
		discoverMu.Unlock()
		return e.ns, e.svc, nil
	}
	discoverMu.Unlock()

	ns, svc, err := discoverServiceUncached(clientset)
	if err != nil {
		return "", "", err
	}
	discoverMu.Lock()
	discoverCache[key] = discoverEntry{ns: ns, svc: svc, fetched: time.Now()}
	discoverMu.Unlock()
	return ns, svc, nil
}

func invalidateDiscover(clientset kubernetes.Interface) {
	discoverMu.Lock()
	delete(discoverCache, cacheKey(clientset))
	discoverMu.Unlock()
}

func discoverServiceUncached(clientset kubernetes.Interface) (string, string, error) {
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

	path := func(ns, svc string) string {
		return fmt.Sprintf("%s/api/v1/namespaces/%s/services/%s:9090/proxy/api/v1/query?query=%s",
			strings.TrimRight(restConfig.Host, "/"), ns, svc, url.QueryEscape(promql))
	}
	body, status, err := doProxy(restConfig, path(ns, svcName))
	if err == nil && status == http.StatusNotFound {
		// 缓存的 Service 已不存在（Prometheus 重建/迁移）→ 失效重试一次
		invalidateDiscover(clientset)
		if ns2, svc2, err2 := discoverService(clientset); err2 == nil && (ns2 != ns || svc2 != svcName) {
			body, status, err = doProxy(restConfig, path(ns2, svc2))
		}
	}
	if err != nil {
		return nil, fmt.Errorf("Prometheus 查询失败: %w", err)
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("Prometheus 返回 %d: %s", status, string(body)[:min(len(body), 300)])
	}
	return body, nil
}

// doProxy 走 apiserver Service Proxy 发一次 GET，返回 body 与状态码
func doProxy(restConfig *rest.Config, proxyPath string) ([]byte, int, error) {
	req, err := http.NewRequest(http.MethodGet, proxyPath, nil)
	if err != nil {
		return nil, 0, fmt.Errorf("请求构建失败: %w", err)
	}
	client, err := httpClientFor(restConfig)
	if err != nil {
		return nil, 0, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, 0, fmt.Errorf("Prometheus 响应读取失败: %w", err)
	}
	return body, resp.StatusCode, nil
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
	path := func(ns, svc string) string {
		return fmt.Sprintf(
			"%s/api/v1/namespaces/%s/services/%s:9090/proxy/api/v1/query_range?query=%s&start=%d&end=%d&step=%d",
			strings.TrimRight(restConfig.Host, "/"), ns, svc, url.QueryEscape(promql), start, end, step,
		)
	}
	body, status, err := doProxy(restConfig, path(ns, svcName))
	if err == nil && status == http.StatusNotFound {
		invalidateDiscover(clientset)
		if ns2, svc2, err2 := discoverService(clientset); err2 == nil && (ns2 != ns || svc2 != svcName) {
			body, status, err = doProxy(restConfig, path(ns2, svc2))
		}
	}
	if err != nil {
		return nil, fmt.Errorf("Prometheus 范围查询失败: %w", err)
	}
	if status != http.StatusOK {
		return nil, fmt.Errorf("Prometheus 返回 %d: %s", status, string(body)[:min(len(body), 300)])
	}
	return body, nil
}
