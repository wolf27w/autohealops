// Package admission 实现 R1 准入控制 MutatingWebhook（fail-closed）。
//
// 最小实现：HTTP 服务处理 POST /mutate（AdmissionReview v1 JSON），对 Deployment/StatefulSet
// 的 CREATE/UPDATE 按管控值改写 spec.replicas（副本数下限/上限）与容器 requests/limits
// （CPU/内存，作为「默认值」补齐，不覆盖已显式设置的值）。
//
// 管控值来源：本地配置（环境变量 ADMISSION_RULES，JSON；或 ADMISSION_RULES_FILE 文件），
// 后续再接 api-server 管控表。未配置规则 = 放行（准入未启用）。
package admission

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	corev1 "k8s.io/api/core/v1"
)

// Rule 单条准入规则（按顺序匹配，命中第一条生效）。
type Rule struct {
	// Namespaces 匹配的 namespace（空 = 匹配所有；支持 * 通配符，如 "prod-*"）。
	Namespaces []string `json:"namespaces,omitempty"`
	// Workloads 匹配的工作负载名（空 = 匹配所有；支持 * 通配符）。
	Workloads []string `json:"workloads,omitempty"`
	// MinReplicas / MaxReplicas 副本数下限/上限（nil = 不限）。
	MinReplicas *int32 `json:"minReplicas,omitempty"`
	MaxReplicas *int32 `json:"maxReplicas,omitempty"`
	// DefaultRequests / DefaultLimits 容器资源默认值（仅当容器未显式设置该资源时补齐）。
	// 例：{"cpu":"100m","memory":"128Mi"}。
	DefaultRequests corev1.ResourceList `json:"defaultRequests,omitempty"`
	DefaultLimits   corev1.ResourceList `json:"defaultLimits,omitempty"`
}

// Rules 规则集。
type Rules struct {
	Rules []Rule `json:"rules"`
}

// ParseRules 解析规则 JSON；非法 JSON 或非法资源量（quantity）会报错。
func ParseRules(data []byte) (*Rules, error) {
	var r Rules
	if err := json.Unmarshal(data, &r); err != nil {
		return nil, fmt.Errorf("解析准入规则失败: %w", err)
	}
	return &r, nil
}

// LoadRules 从环境/配置文件加载规则。优先级：ADMISSION_RULES > ADMISSION_RULES_FILE。
// 两者都为空时返回空规则（准入放行）。
func LoadRules() (*Rules, error) {
	if raw := strings.TrimSpace(os.Getenv("ADMISSION_RULES")); raw != "" {
		return ParseRules([]byte(raw))
	}
	if file := strings.TrimSpace(os.Getenv("ADMISSION_RULES_FILE")); file != "" {
		b, err := os.ReadFile(file)
		if err != nil {
			return nil, fmt.Errorf("读取准入规则文件 %s 失败: %w", file, err)
		}
		return ParseRules(b)
	}
	return &Rules{}, nil
}

// match 返回第一条命中 namespace+workload 的规则；无命中返回 nil（放行）。
func (r *Rules) match(namespace, name string) *Rule {
	if r == nil {
		return nil
	}
	for i := range r.Rules {
		rule := &r.Rules[i]
		if !matchAny(rule.Namespaces, namespace) {
			continue
		}
		if !matchAny(rule.Workloads, name) {
			continue
		}
		return rule
	}
	return nil
}

// matchAny 空模式列表 = 匹配所有；否则任一模式命中即匹配。
func matchAny(patterns []string, value string) bool {
	if len(patterns) == 0 {
		return true
	}
	for _, p := range patterns {
		if matchPattern(p, value) {
			return true
		}
	}
	return false
}

// matchPattern 支持精确匹配、`*` 通配、以及后缀通配（如 "prod-*"）。
func matchPattern(pattern, value string) bool {
	p := strings.TrimSpace(pattern)
	if p == "" || p == "*" {
		return true
	}
	if strings.HasSuffix(p, "*") {
		return strings.HasPrefix(value, strings.TrimSuffix(p, "*"))
	}
	return p == value
}
