// Package script 脚本执行：自定义任务 script 类型（创建临时 Job 或 kubectl 执行）
package script

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/api-ops-platform/k8s-executor/internal/database"
	"github.com/api-ops-platform/k8s-executor/internal/k8sclient"
	"go.uber.org/zap"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

// Runner 脚本执行器
type Runner struct {
	manager *k8sclient.Manager
	logger  *zap.Logger
}

// NewRunner 创建脚本执行器
func NewRunner(manager *k8sclient.Manager, logger *zap.Logger) *Runner {
	return &Runner{manager: manager, logger: logger}
}

// RunScript 执行脚本（方式：优先创建临时 Job；超时 300 秒）
func (r *Runner) RunScript(ctx context.Context, clusterID uint, scriptContent string, parameters string, taskID uint) (string, error) {
	client, err := r.manager.GetClient(ctx, clusterID)
	if err != nil {
		return "", err
	}
	cs := client.Clientset

	// 参数展开（脚本里的 {param} 占位符替换）
	scriptContent = expandParams(scriptContent, parameters)

	// 创建临时 Job 执行脚本
	jobName := fmt.Sprintf("api-ops-script-%d-%d", taskID, time.Now().Unix())
	job := &batchv1.Job{
		ObjectMeta: metav1.ObjectMeta{Name: jobName, Namespace: "default"},
		Spec: batchv1.JobSpec{
			Template: corev1.PodTemplateSpec{
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					Containers: []corev1.Container{{
						Name:    "runner",
						Image:   "busybox:latest",
						Command: []string{"/bin/sh", "-c", scriptContent},
					}},
				},
			},
		},
	}
	created, err := cs.BatchV1().Jobs("default").Create(ctx, job, metav1.CreateOptions{})
	if err != nil {
		return "", fmt.Errorf("创建脚本 Job 失败: %w", err)
	}

	// 轮询 Job 状态（超时 300 秒）
	deadline := time.Now().Add(300 * time.Second)
	for time.Now().Before(deadline) {
		j, err := cs.BatchV1().Jobs("default").Get(ctx, created.Name, metav1.GetOptions{})
		if err != nil {
			return "", err
		}
		if j.Status.Succeeded > 0 {
			logs := fetchJobLogs(ctx, cs, created.Name)
			cleanupJob(ctx, cs, created.Name)
			return logs, nil
		}
		if j.Status.Failed > 0 {
			cleanupJob(ctx, cs, created.Name)
			return "", fmt.Errorf("脚本 Job 执行失败（%d 次重试失败）", j.Status.Failed)
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(3 * time.Second):
		}
	}
	cleanupJob(ctx, cs, created.Name)
	return "", fmt.Errorf("脚本执行超时（300 秒）")
}

// expandParams 展开脚本中的 {param} 占位符
func expandParams(script, parameters string) string {
	var params map[string]string
	if err := json.Unmarshal([]byte(parameters), &params); err != nil {
		return script
	}
	for k, v := range params {
		script = strings.ReplaceAll(script, "{"+k+"}", v)
	}
	return script
}

// fetchJobLogs 获取 Job Pod 日志
func fetchJobLogs(ctx context.Context, cs *kubernetes.Clientset, jobName string) string {
	pods, err := cs.CoreV1().Pods("default").List(ctx, metav1.ListOptions{
		LabelSelector: fmt.Sprintf("job-name=%s", jobName),
	})
	if err != nil || len(pods.Items) == 0 {
		return ""
	}
	logs, _ := cs.CoreV1().Pods("default").GetLogs(pods.Items[0].Name, &corev1.PodLogOptions{}).DoRaw(ctx)
	return string(logs)
}

// cleanupJob 清理临时 Job
func cleanupJob(ctx context.Context, cs *kubernetes.Clientset, jobName string) {
	_ = cs.BatchV1().Jobs("default").Delete(ctx, jobName, metav1.DeleteOptions{})
}

var _ = database.ExecutionLog{}
