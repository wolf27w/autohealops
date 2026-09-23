// Package executor 容器内命令执行（应用诊断）：ExecInPod
package executor

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"bytes"
	"context"
	"fmt"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/remotecommand"
)

// blockedShellVerbs 拦截 shell / 脚本解释器：防止用 `sh -c "..."` / `python -c "..."` 绕过单命令限制执行任意代码
var blockedShellVerbs = map[string]bool{
	"sh": true, "bash": true, "ash": true, "dash": true, "zsh": true, "fish": true,
	"python": true, "python2": true, "python3": true, "perl": true, "ruby": true,
	"php": true, "node": true, "lua": true, "pwsh": true, "powershell": true,
}

// checkCommand 校验诊断命令：非空 + 拒绝 shell 特性 + 拦截解释器。
// 老板口径（2026-09-23）：应用诊断应在容器内「操作所有东西」（安装缺失工具、调整配置），
// 故不再限制为只读白名单，允许任意命令。但仍以单命令（binary+args）执行、不经 shell，
// 并拦截 sh/python 等解释器，从根上阻断命令注入/串联。
func checkCommand(command string) error {
	fields := strings.Fields(command)
	if len(fields) == 0 {
		return fmt.Errorf("空命令")
	}
	verb := fields[0]
	if blockedShellVerbs[verb] {
		return fmt.Errorf("命令 %q 被拦截（不允许 shell/脚本解释器）", verb)
	}
	// 拒绝 shell 特性（管道/重定向/命令替换/分号/连接符），防注入
	for _, ch := range []string{"|", ">", "<", ";", "$(", "`", "&&", "||"} {
		if strings.Contains(command, ch) {
			return fmt.Errorf("诊断命令包含非法字符 %q（不允许管道/重定向/命令连接）", ch)
		}
	}
	return nil
}

// ExecInPod 在指定 Pod 容器内执行诊断命令，返回输出
func (e *Executor) ExecInPod(ctx context.Context, namespace, podName, container, command string) (string, error) {
	if err := checkCommand(command); err != nil {
		return "", err
	}
	if e.restConfig == nil {
		return "", fmt.Errorf("集群 rest.Config 不可用")
	}
	// 容器名为空时自动选主容器（多容器 Pod 不指定容器会被 API Server 拒绝）
	if container == "" {
		if pod, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{}); err == nil {
			container = pickDefaultContainer(pod)
		}
	}

	req := e.clientset.CoreV1().RESTClient().Post().
		Resource("pods").
		Name(podName).
		Namespace(namespace).
		SubResource("exec").
		VersionedParams(&corev1.PodExecOptions{
			Container: container,
			Command:   strings.Fields(command),
			Stdout:    true,
			Stderr:    true,
		}, scheme.ParameterCodec)

	exec, err := remotecommand.NewSPDYExecutor(e.restConfig, "POST", req.URL())
	if err != nil {
		return "", fmt.Errorf("创建 exec 执行器失败: %w", err)
	}

	var stdout, stderr bytes.Buffer
	// 15s 显式超时：慢集群/挂起的 exec 快速失败，不拖满 30s gRPC deadline（否则拖慢整轮诊断）
	execCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	err = exec.StreamWithContext(execCtx, remotecommand.StreamOptions{
		Stdout: &stdout,
		Stderr: &stderr,
	})
	if err != nil {
		return stdout.String(), fmt.Errorf("exec 失败: %v %s", err, stderr.String())
	}
	out := stdout.String()
	if stderr.Len() > 0 {
		out += stderr.String()
	}
	return out, nil
}
