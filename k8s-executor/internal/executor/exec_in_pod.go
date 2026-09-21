// Package executor 容器内只读命令执行（应用诊断）：ExecInPod
package executor

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"bytes"
	"context"
	"fmt"
	"strings"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/remotecommand"
)

// readOnlyCommands 容器内诊断命令白名单（只读，拒绝任何写操作）
// 已移除可写/可执行外部命令的 awk/sed/echo：
//   - awk/sed 支持 -i 原地写文件、可调 system() 执行任意命令 → 命令注入风险
//   - echo 无诊断价值且可配合其他途径写文件
var readOnlyCommands = []string{
	"ps", "env", "cat", "ls", "ss", "netstat", "df", "free", "top",
	"pwd", "wc", "head", "tail", "grep", "du", "uptime", "uname", "id",
	"which", "lsof", "pidof", "stat", "hostname", "date", "printenv",
}

// checkReadOnlyCommand 校验诊断命令：首词在白名单 + 拒绝管道/重定向/命令替换
func checkReadOnlyCommand(command string) error {
	fields := strings.Fields(command)
	if len(fields) == 0 {
		return fmt.Errorf("空命令")
	}
	verb := fields[0]
	allowed := false
	for _, w := range readOnlyCommands {
		if w == verb {
			allowed = true
			break
		}
	}
	if !allowed {
		return fmt.Errorf("诊断命令 %q 不在只读白名单", verb)
	}
	// 拒绝 shell 特性（管道/重定向/命令替换/分号），防注入
	for _, ch := range []string{"|", ">", "<", ";", "$(", "`", "&&", "||"} {
		if strings.Contains(command, ch) {
			return fmt.Errorf("诊断命令包含非法字符 %q", ch)
		}
	}
	return nil
}

// ExecInPod 在指定 Pod 容器内执行只读诊断命令，返回输出
func (e *Executor) ExecInPod(ctx context.Context, namespace, podName, container, command string) (string, error) {
	if err := checkReadOnlyCommand(command); err != nil {
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
	err = exec.StreamWithContext(ctx, remotecommand.StreamOptions{
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
