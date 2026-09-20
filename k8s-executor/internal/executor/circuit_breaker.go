// Package executor 熔断控制：连续失败 / 错误率 / 延迟异常 触发熔断
package executor

import (
	"sync"
	"time"
)

// latencySample 延迟样本
type latencySample struct {
	success   bool
	latencyMs int64
}

// CircuitBreaker 熔断器
type CircuitBreaker struct {
	mu                 sync.Mutex
	failedSteps        int
	consecutiveFailures int
	samples            []latencySample // 最近 20 个样本
	expectedLatencyMs  int64
}

// NewCircuitBreaker 创建熔断器
func NewCircuitBreaker(expectedLatencyMs int64) *CircuitBreaker {
	return &CircuitBreaker{expectedLatencyMs: expectedLatencyMs}
}

// RecordStep 记录步骤执行结果
func (cb *CircuitBreaker) RecordStep(success bool, latencyMs int64) {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	if !success {
		cb.failedSteps++
		cb.consecutiveFailures++
	} else {
		cb.consecutiveFailures = 0
	}
	cb.samples = append(cb.samples, latencySample{success: success, latencyMs: latencyMs})
	if len(cb.samples) > 20 {
		cb.samples = cb.samples[len(cb.samples)-20:] // 保留最近 20 个
	}
}

// CheckTriggered 判断是否触发熔断：
//   连续 2 步失败 / 错误率 > 5%（最近 20 样本）/ 平均延迟 > 预期 200%
func (cb *CircuitBreaker) CheckTriggered() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	// 连续 2 步失败
	if cb.consecutiveFailures >= 2 {
		return true
	}
	// 错误率 > 5%（至少 10 个样本才评估）
	if len(cb.samples) >= 10 {
		failures := 0
		for _, s := range cb.samples {
			if !s.success {
				failures++
			}
		}
		if float64(failures)/float64(len(cb.samples)) > 0.05 {
			return true
		}
	}
	// 平均延迟 > 预期 200%
	if len(cb.samples) >= 5 && cb.expectedLatencyMs > 0 {
		var sum int64
		for _, s := range cb.samples {
			sum += s.latencyMs
		}
		avg := sum / int64(len(cb.samples))
		if avg > cb.expectedLatencyMs*2 {
			return true
		}
	}
	return false
}

// Reset 重置熔断器
func (cb *CircuitBreaker) Reset() {
	cb.mu.Lock()
	defer cb.mu.Unlock()
	cb.failedSteps = 0
	cb.consecutiveFailures = 0
	cb.samples = nil
}

var _ = time.Second // 保留 time 引用
