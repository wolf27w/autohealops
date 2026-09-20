// Package executor 灰度执行：按影响面（blast radius）分批 + 熔断 + 流式进度推送
package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/api-ops-platform/k8s-executor/internal/database"
)

// NotifyFunc 进度推送回调（gRPC 流式推送用）
type NotifyFunc func(stepIndex int, command string, success bool, output, message string, finished, circuitTriggered bool, rollbackCommand string)

// GrayExecutor 灰度执行器
type GrayExecutor struct {
	executor *Executor
	breaker  *CircuitBreaker
}

// NewGrayExecutor 创建灰度执行器
func NewGrayExecutor(executor *Executor) *GrayExecutor {
	return &GrayExecutor{executor: executor, breaker: NewCircuitBreaker(30000)} // 预期每步 30s
}

// ExecuteWithGrayStrategy 按灰度策略执行修复计划
func (g *GrayExecutor) ExecuteWithGrayStrategy(ctx context.Context, planID uint, planJSON string, notify NotifyFunc) error {
	var plan RepairPlan
	if err := json.Unmarshal([]byte(planJSON), &plan); err != nil {
		return fmt.Errorf("解析 plan_json 失败: %w", err)
	}
	steps := plan.Steps
	if len(steps) == 0 {
		return fmt.Errorf("修复计划无步骤")
	}

	// 影响面评估
	blastRadius, _ := g.executor.BlastRadiusEvaluate(ctx, planJSON)

	// 按影响面确定执行批次策略
	type batch struct {
		indices []int
		wait    time.Duration
	}
	var batches []batch
	switch {
	case blastRadius <= 1:
		// 直接执行所有步骤
		for i := range steps {
			batches = append(batches, batch{indices: []int{i}, wait: 0})
		}
	case blastRadius <= 10:
		// 第一步单独执行 + 等待 30s 验证，再执行剩余
		batches = append(batches, batch{indices: []int{0}, wait: 30 * time.Second})
		for i := 1; i < len(steps); i++ {
			batches = append(batches, batch{indices: []int{i}, wait: 0})
		}
	default:
		// >10 个 Pod：分 10 批，每批后等待 60s
		batchSize := (len(steps) + 9) / 10
		if batchSize < 1 {
			batchSize = 1
		}
		for start := 0; start < len(steps); start += batchSize {
			end := start + batchSize
			if end > len(steps) {
				end = len(steps)
			}
			indices := make([]int, 0, end-start)
			for i := start; i < end; i++ {
				indices = append(indices, i)
			}
			batches = append(batches, batch{indices: indices, wait: 60 * time.Second})
		}
	}

	// 逐批执行
	for bi, b := range batches {
		for _, si := range b.indices {
			step := steps[si]
			start := time.Now()
			output, err := g.executor.ExecuteCommand(ctx, "", step.Command)
			latencyMs := time.Since(start).Milliseconds()
			success := err == nil
			if err != nil {
				output = err.Error()
			}

			// 记录执行日志（实时落库，供前端轮询）
			g.recordLog(planID, si, step.Command, output, success)

			// 熔断器记录
			g.breaker.RecordStep(success, latencyMs)
			triggered := g.breaker.CheckTriggered()

			// 流式推送进度
			if notify != nil {
				notify(si, step.Command, success, output, "", false, triggered, step.RollbackCommand)
			}

			if triggered {
				if notify != nil {
					notify(si, step.Command, false, "熔断触发", "连续失败或错误率超标，已中止执行", true, true, step.RollbackCommand)
				}
				return fmt.Errorf("熔断触发，已中止执行（step %d）", si)
			}
			if !success {
				return fmt.Errorf("步骤 %d 执行失败: %s", si, output)
			}
		}
		// 批次间等待（灰度验证窗口）
		if bi < len(batches)-1 && b.wait > 0 {
			if notify != nil {
				notify(-1, "", true, "", fmt.Sprintf("灰度验证窗口：等待 %v", b.wait), false, false, "")
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(b.wait):
			}
		}
	}

	if notify != nil {
		notify(-1, "", true, "", "全部步骤执行完成", true, false, "")
	}
	return nil
}

// recordLog 记录执行日志到数据库
func (g *GrayExecutor) recordLog(planID uint, stepIndex int, command, output string, success bool) {
	db := database.GetDB()
	if db == nil {
		return
	}
	status := "success"
	if !success {
		status = "failed"
	}
	now := time.Now()
	logEntry := database.ExecutionLog{
		PlanID:      planID,
		StepIndex:   stepIndex,
		Command:     command,
		Status:      status,
		Output:      output,
		StartedAt:   now,
		CompletedAt: &now,
	}
	db.Create(&logEntry)
}
