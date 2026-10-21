// Package executor 回滚：逆序执行 rollback_command
package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/autohealops/k8s-executor/internal/database"
)

// RepairStep 修复步骤（与 ai-service 生成的 plan_json steps 结构一致）
type RepairStep struct {
	Command         string `json:"command"`
	Description     string `json:"description"`
	ExpectedResult  string `json:"expected_result"`
	RollbackCommand string `json:"rollback_command"`
}

// RepairPlan 修复方案
type RepairPlan struct {
	Steps []RepairStep `json:"steps"`
}

// RollbackPlan 逆序执行 rollback_command，返回已执行的回滚命令列表
func (e *Executor) RollbackPlan(ctx context.Context, planID uint, planJSON string) ([]string, error) {
	var plan RepairPlan
	if err := json.Unmarshal([]byte(planJSON), &plan); err != nil {
		return nil, fmt.Errorf("解析 plan_json 失败: %w", err)
	}
	executed := make([]string, 0)
	// 逆序遍历
	for i := len(plan.Steps) - 1; i >= 0; i-- {
		step := plan.Steps[i]
		if step.RollbackCommand == "" {
			continue // 无回滚命令的步骤跳过
		}
		output, err := e.ExecuteCommand(ctx, "", step.RollbackCommand)
		now := time.Now()
		status := "rolled_back"
		if err != nil {
			status = "rollback_failed"
			output = err.Error()
		}
		// 更新 ExecutionLog 状态
		if db := database.GetDB(); db != nil {
			db.Model(&database.ExecutionLog{}).
				Where("plan_id = ? AND step_index = ?", planID, i).
				Updates(map[string]interface{}{"status": status, "output": output, "completed_at": now})
		}
		executed = append(executed, step.RollbackCommand)
		if err != nil {
			return executed, fmt.Errorf("回滚步骤 %d 失败: %w", i, err)
		}
	}
	return executed, nil
}
