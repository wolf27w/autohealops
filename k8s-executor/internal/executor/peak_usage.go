// 高峰期资源采集（R1）：工作负载 P80/P95 CPU/内存用量。
// 数据源：集群内 Prometheus（走 internal/prometheus 的 Query 代理）。
// 口径：先对工作负载所有业务容器按时间戳聚合（sum），再对窗口内该聚合序列做 quantile_over_time。
package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"

	"github.com/autohealops/k8s-executor/internal/prometheus"
)

// PeakUsage 高峰期用量结果（cpu=核数，memory=字节）
type PeakUsage struct {
	P80 float64
	P95 float64
}

// podNameRegexFor 按工作负载类型+名称构造 pod 名匹配正则（PromQL 内用）。
// deployment: <name>-<rs-hash>-<pod-hash>；statefulset: <name>-<ordinal>；daemonset: <name>-<hash>。
func podNameRegexFor(kind, name string) string {
	escaped := regexp.QuoteMeta(name)
	switch strings.ToLower(kind) {
	case "statefulset":
		return fmt.Sprintf("^%s-\\d+$", escaped)
	case "daemonset":
		return fmt.Sprintf("^%s-[0-9a-z]+$", escaped)
	default: // deployment
		return fmt.Sprintf("^%s-[0-9a-z]+-[0-9a-z]+$", escaped)
	}
}

// GetPeakUsage 计算工作负载在窗口内的 P80/P95 用量。
func (e *Executor) GetPeakUsage(ctx context.Context, namespace, kind, name, metric string, windowHours int) (*PeakUsage, error) {
	if namespace == "" || name == "" {
		return nil, fmt.Errorf("namespace 与 workload_name 必填")
	}
	if windowHours <= 0 {
		windowHours = 24
	}

	base, err := peakBaseExpr(namespace, kind, name, metric)
	if err != nil {
		return nil, err
	}

	window := fmt.Sprintf("%dh", windowHours)
	step := peakStep(windowHours)

	p80, err := e.peakQuantile(ctx, base, 0.80, window, step)
	if err != nil {
		return nil, err
	}
	p95, err := e.peakQuantile(ctx, base, 0.95, window, step)
	if err != nil {
		return nil, err
	}
	return &PeakUsage{P80: p80, P95: p95}, nil
}

// peakBaseExpr 构建工作负载聚合后的瞬时用量表达式（sum 到单个序列）。
func peakBaseExpr(namespace, kind, name, metric string) (string, error) {
	podRe := podNameRegexFor(kind, name)
	sel := fmt.Sprintf(`{namespace="%s",pod=~"%s",container!="POD",container!=""}`, namespace, podRe)
	switch strings.ToLower(metric) {
	case "cpu":
		// container_cpu_usage_seconds_total 是累计秒（counter），取 5m 速率得到核数
		return fmt.Sprintf("sum(rate(container_cpu_usage_seconds_total%s[5m]))", sel), nil
	case "memory":
		// working set 是瞬时 gauge，直接聚合
		return fmt.Sprintf("sum(container_memory_working_set_bytes%s)", sel), nil
	default:
		return "", fmt.Errorf("不支持的 metric: %s（仅支持 cpu/memory）", metric)
	}
}

// peakQuantile 对聚合序列做 quantile_over_time，返回单个浮点值。
func (e *Executor) peakQuantile(ctx context.Context, baseExpr string, q float64, window, step string) (float64, error) {
	promql := fmt.Sprintf("quantile_over_time(%s, %s[%s:%s])", formatFloat(q), baseExpr, window, step)
	data, err := prometheus.Query(e.clientset, e.restConfig, promql)
	if err != nil {
		return 0, err
	}
	v, err := parsePromVectorScalar(data)
	if err != nil {
		return 0, fmt.Errorf("解析 Prometheus 结果失败（promql=%s）: %w", promql, err)
	}
	return v, nil
}

// peakStep 按窗口大小给出子查询步长：约 720 个采样点，钳位在 [60s, 600s]。
func peakStep(windowHours int) string {
	sec := windowHours * 3600 / 720
	if sec < 60 {
		sec = 60
	}
	if sec > 600 {
		sec = 600
	}
	return fmt.Sprintf("%ds", sec)
}

func formatFloat(f float64) string {
	return strconv.FormatFloat(f, 'f', -1, 64)
}

// promResponse 解析 Prometheus /api/v1/query 返回的最小结构。
type promResponse struct {
	Status string `json:"status"`
	Data   struct {
		ResultType string `json:"resultType"`
		Result     []struct {
			Metric map[string]string `json:"metric"`
			Value  []interface{}     `json:"value"`
		} `json:"result"`
	} `json:"data"`
}

// parsePromVectorScalar 从瞬时向量结果里取第一个序列的标量值。
func parsePromVectorScalar(data []byte) (float64, error) {
	var resp promResponse
	if err := json.Unmarshal(data, &resp); err != nil {
		return 0, err
	}
	if resp.Status != "success" {
		return 0, fmt.Errorf("prometheus status=%s", resp.Status)
	}
	if len(resp.Data.Result) == 0 {
		return 0, fmt.Errorf("查询无结果（可能是工作负载无运行 Pod 或指标缺失）")
	}
	v := resp.Data.Result[0].Value
	if len(v) < 2 {
		return 0, fmt.Errorf("结果 value 格式异常")
	}
	s, ok := v[1].(string)
	if !ok {
		return 0, fmt.Errorf("结果 value 不是字符串: %v", v[1])
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0, fmt.Errorf("value 解析失败: %s", s)
	}
	return f, nil
}
