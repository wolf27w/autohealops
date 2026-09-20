// Package executor 交互式终端（TTY）：双向流 exec（stdin/stdout/stderr/resize），专项 D 终端链路
package executor

import (
	"context"
	"fmt"
	"io"
	"strings"
	"sync"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/remotecommand"
)

// ExecStreamSession 交互式终端会话（双向流桥接）
// 提供 stdin writer、stdout/stderr reader、resize 通道，供上层 gRPC 流收发。
type ExecStreamSession struct {
	ctx    context.Context
	cancel context.CancelFunc
	stdin  io.WriteCloser
	stdout io.Reader
	stderr io.Reader
	resize chan remotecommand.TerminalSize
	done   chan error
	idle   *idleTimer
}

// terminalSizeQueue 适配 resize channel 到 remotecommand.TerminalSizeQueue
type terminalSizeQueue chan remotecommand.TerminalSize

func (q terminalSizeQueue) Next() *remotecommand.TerminalSize {
	size, ok := <-q
	if !ok {
		return nil
	}
	return &size
}

// idleTimeout 静默超时：无 stdin 输入自动断开会话（老板定：静默 5 分钟自动断开）
const IdleTimeout = 5 * time.Minute

// idleTimer 记录会话最后活动时间，供上层判断静默超时
type idleTimer struct {
	mu      sync.Mutex
	lastIn  time.Time
}

func (t *idleTimer) touch() {
	t.mu.Lock()
	t.lastIn = time.Now()
	t.mu.Unlock()
}

func (t *idleTimer) idle() time.Duration {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.lastIn.IsZero() {
		return 0
	}
	return time.Since(t.lastIn)
}

// StartExecStream 建立交互式 exec 会话（TTY 或非 TTY），返回会话句柄。
// 返回后即可通过 stdin 写、stdout/stderr 读、Resize 调整窗口。
func (e *Executor) StartExecStream(ctx context.Context, namespace, podName, container string, tty bool) (*ExecStreamSession, error) {
	if e.restConfig == nil {
		return nil, fmt.Errorf("集群 rest.Config 不可用")
	}
	ctx, cancel := context.WithCancel(ctx)

	// container 为空：单容器 pod 自动用唯一容器；多容器 pod 默认选第一个（非 sidecar 优先）
	if container == "" {
		c, err := e.resolveContainer(ctx, namespace, podName)
		if err != nil {
			cancel()
			return nil, err
		}
		container = c
	}

	req := e.clientset.CoreV1().RESTClient().Post().
		Resource("pods").
		Name(podName).
		Namespace(namespace).
		SubResource("exec").
		VersionedParams(&corev1.PodExecOptions{
			Container: container,
			Command:   []string{"/bin/sh"},
			Stdin:     true,
			Stdout:    true,
			Stderr:    true,
			TTY:       tty,
		}, scheme.ParameterCodec)

	exec, err := remotecommand.NewSPDYExecutor(e.restConfig, "POST", req.URL())
	if err != nil {
		cancel()
		return nil, fmt.Errorf("创建 exec 执行器失败: %w", err)
	}

	// stdin / stdout / stderr 管道
	stdinReader, stdinWriter := io.Pipe()
	stdoutReader, stdoutWriter := io.Pipe()
	stderrReader, stderrWriter := io.Pipe()
	resizeCh := make(chan remotecommand.TerminalSize, 8)
	done := make(chan error, 1)

	// TTY 模式：预置一个初始终端尺寸，避免 remotecommand 等待首次 resize 阻塞
	if tty {
		resizeCh <- remotecommand.TerminalSize{Width: 120, Height: 30}
	}

	sess := &ExecStreamSession{
		ctx: ctx, cancel: cancel, stdin: stdinWriter, stdout: stdoutReader, stderr: stderrReader,
		resize: resizeCh, done: done, idle: &idleTimer{lastIn: time.Now()},
	}

	go func() {
		var opts remotecommand.StreamOptions
		opts.Stdin = stdinReader
		opts.Stdout = stdoutWriter
		opts.Stderr = stderrWriter
		opts.Tty = tty
		if tty {
			opts.TerminalSizeQueue = terminalSizeQueue(resizeCh)
		}
		err := exec.StreamWithContext(ctx, opts)
		// 关闭写端，通知读端 EOF
		_ = stdinWriter.Close()
		_ = stdoutWriter.Close()
		_ = stderrWriter.Close()
		done <- err
	}()

	return sess, nil
}

// Write 写 stdin（键盘输入）
func (s *ExecStreamSession) Write(data []byte) (int, error) {
	s.idle.touch()
	return s.stdin.Write(data)
}

// Idle 返回自上次输入以来的静默时长
func (s *ExecStreamSession) Idle() time.Duration {
	return s.idle.idle()
}

// Done 返回会话结束 channel（可用于 select 等待）
func (s *ExecStreamSession) Done() <-chan error {
	return s.done
}

// ReadStdout 读 stdout
func (s *ExecStreamSession) ReadStdout(p []byte) (int, error) {
	return s.stdout.Read(p)
}

// ReadStderr 读 stderr
func (s *ExecStreamSession) ReadStderr(p []byte) (int, error) {
	return s.stderr.Read(p)
}

// CloseStdin 关闭 stdin（客户端断开输入）
func (s *ExecStreamSession) CloseStdin() {
	_ = s.stdin.Close()
}

// Resize 调整终端窗口大小
func (s *ExecStreamSession) Resize(rows, cols uint32) {
	select {
	case s.resize <- remotecommand.TerminalSize{Width: uint16(cols), Height: uint16(rows)}:
	default:
	}
}

// Wait 等待会话结束，返回退出错误（nil=正常退出）
func (s *ExecStreamSession) Wait() error {
	return <-s.done
}

// Close 关闭会话
func (s *ExecStreamSession) Close() {
	s.cancel()
}

// ExitCode 从 remotecommand 错误提取退出码（无退出码返回 -1）
func ExitCode(err error) int {
	if err == nil {
		return 0
	}
	msg := err.Error()
	// remotecommand 的 CodeExitError 格式含 "command terminated with exit code N"
	if idx := strings.Index(msg, "exit code"); idx >= 0 {
		var code int
		if _, e := fmt.Sscanf(msg[idx:], "exit code %d", &code); e == nil {
			return code
		}
	}
	return -1
}

// sidecarPatterns 常见 sidecar/代理容器名前缀，多容器时降级为非首选
var sidecarPatterns = []string{
	"filebeat", "istio-proxy", "istio-init", "log", "exporter", "sidecar", "prometheus",
	"envoy", "fluent", "metrics", "monitor", "agent", "kube-proxy",
}

// resolveContainer 当 container 为空时自动选容器：单容器用唯一；多容器跳过 sidecar 选第一个业务容器
func (e *Executor) resolveContainer(ctx context.Context, namespace, podName string) (string, error) {
	pod, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{})
	if err != nil {
		return "", fmt.Errorf("查询 Pod %s 失败: %w", podName, err)
	}
	names := make([]string, 0, len(pod.Spec.Containers))
	for _, c := range pod.Spec.Containers {
		names = append(names, c.Name)
	}
	if len(names) == 0 {
		return "", fmt.Errorf("Pod %s 无容器", podName)
	}
	if len(names) == 1 {
		return names[0], nil
	}
	// 多容器：先找非 sidecar 的业务容器，找不到就用第一个
	for _, n := range names {
		isSidecar := false
		lower := strings.ToLower(n)
		for _, p := range sidecarPatterns {
			if strings.Contains(lower, p) {
				isSidecar = true
				break
			}
		}
		if !isSidecar {
			return n, nil
		}
	}
	return names[0], nil
}
