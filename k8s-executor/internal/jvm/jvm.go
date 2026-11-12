// Package jvm JVM 深度诊断（R2）：jstack/jfr/heapdump/jcmd + 启动参数采集。
//
// 复用 executor.Executor.ExecInPod（容器内单命令执行，不经 shell、拦截 shell 解释器），
// 所有诊断动作拆成 jcmd/jstack/jmap/jps 等单命令（binary+args），天然通过命令校验。
// jstack/jcmd 输出为文本，直接返回；jfr/heapdump 产物先在容器内落 /tmp，再用 base64
// 拉回 executor 本地 ArtifactDir 落盘，返回 file_path 供 api-server 下载。
package jvm

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/autohealops/k8s-executor/internal/executor"
)

// ArtifactDir jfr/heapdump 产物落盘目录（executor 本地）。api-server 经 logstream HTTP
// 端点（/files/，待接）下载；目录可按部署环境覆盖。
var ArtifactDir = func() string {
	d := filepath.Join(os.TempDir(), "autohealops-jvm")
	_ = os.MkdirAll(d, 0o755)
	return d
}()

// Diagnostic JVM 诊断执行器（绑定单个集群的 executor）。
type Diagnostic struct {
	ex *executor.Executor
}

// New 构建 JVM 诊断执行器。
func New(ex *executor.Executor) *Diagnostic {
	return &Diagnostic{ex: ex}
}

// Diagnose 执行一次 JVM 诊断动作。
//
// action: jstack / jfr / heapdump / jcmd（不区分大小写）。
// args: pid，或 "pid <subcommand> [args]"（jcmd 透传）；jfr 支持 "pid [duration]"。
// 返回 output（jstack/jcmd 文本输出）和 filePath（jfr/heapdump 产物落盘路径）。
func (d *Diagnostic) Diagnose(ctx context.Context, namespace, pod, container, action, args string) (output, filePath string, err error) {
	switch strings.ToLower(strings.TrimSpace(action)) {
	case "jstack":
		return d.jstack(ctx, namespace, pod, container, args)
	case "jcmd":
		return d.jcmd(ctx, namespace, pod, container, args)
	case "jfr":
		return d.jfr(ctx, namespace, pod, container, args)
	case "heapdump":
		return d.heapdump(ctx, namespace, pod, container, args)
	default:
		return "", "", fmt.Errorf("不支持的诊断动作 %q（可选 jstack/jfr/heapdump/jcmd）", action)
	}
}

// Params 采集 JVM 启动参数：-Xms/-Xmx/-Xss/-XX:MaxMetaspaceSize + java_version。
// args 传 pid；为空时自动探测容器内的 Java 进程。
func (d *Diagnostic) Params(ctx context.Context, namespace, pod, container, args string) (string, error) {
	pid := firstArg(args)
	if pid == "" {
		pid = d.findJavaPid(ctx, namespace, pod, container)
	}
	if pid == "" {
		return "", fmt.Errorf("未指定 PID 且无法自动探测 Java 进程（请在 args 传入 pid）")
	}

	var sb strings.Builder

	// java_version（VM.version 首行非空行）
	if out, e := d.ex.ExecInPod(ctx, namespace, pod, container, fmt.Sprintf("jcmd %s VM.version", pid)); e == nil {
		if v := firstNonEmptyLine(out); v != "" {
			sb.WriteString("java_version: " + v + "\n")
		}
	}

	// command_line（用户显式 -Xms/-Xmx/-Xss/-XX:MaxMetaspaceSize）
	explicit := map[string]string{}
	if out, e := d.ex.ExecInPod(ctx, namespace, pod, container, fmt.Sprintf("jcmd %s VM.command_line", pid)); e == nil {
		explicit = extractExplicitFlags(out)
	}

	// VM.flags（有效值，InitialHeapSize/MaxHeapSize/ThreadStackSize/MaxMetaspaceSize）
	eff := map[string]uint64{}
	if out, e := d.ex.ExecInPod(ctx, namespace, pod, container, fmt.Sprintf("jcmd %s VM.flags", pid)); e == nil {
		eff = extractEffectiveFlags(out)
	}

	sb.WriteString("-Xms: " + pickHeap(explicit["xms"], eff["InitialHeapSize"], 1) + "\n")
	sb.WriteString("-Xmx: " + pickHeap(explicit["xmx"], eff["MaxHeapSize"], 1) + "\n")
	sb.WriteString("-Xss: " + pickHeap(explicit["xss"], eff["ThreadStackSize"], 1024) + "\n")
	sb.WriteString("-XX:MaxMetaspaceSize: " + pickMeta(explicit["maxmetaspacesize"], eff["MaxMetaspaceSize"]) + "\n")

	return sb.String(), nil
}

// ---------- 各诊断动作 ----------

func (d *Diagnostic) jstack(ctx context.Context, ns, pod, container, args string) (string, string, error) {
	pid := firstArg(args)
	if pid == "" {
		return "", "", fmt.Errorf("jstack 需要 pid（args=pid）")
	}
	// 优先 jcmd Thread.print -l（JDK 通用），失败回退 jstack -l
	out, err := d.ex.ExecInPod(ctx, ns, pod, container, fmt.Sprintf("jcmd %s Thread.print -l", pid))
	if err != nil {
		if out2, err2 := d.ex.ExecInPod(ctx, ns, pod, container, fmt.Sprintf("jstack %s -l", pid)); err2 == nil {
			return out2, "", nil
		} else {
			return out, "", fmt.Errorf("jstack 失败: %v（jcmd: %v）", err2, err)
		}
	}
	return out, "", nil
}

func (d *Diagnostic) jcmd(ctx context.Context, ns, pod, container, args string) (string, string, error) {
	fields := strings.Fields(args)
	if len(fields) < 2 {
		return "", "", fmt.Errorf("jcmd 需要 pid + 子命令（args=\"pid <subcommand> [args]\"）")
	}
	out, err := d.ex.ExecInPod(ctx, ns, pod, container, "jcmd "+strings.Join(fields, " "))
	if err != nil {
		return out, "", err
	}
	return out, "", nil
}

// jfr 采集飞行记录：JFR.start(duration+filename) → 等 duration 结束落盘 → base64 拉回。
func (d *Diagnostic) jfr(ctx context.Context, ns, pod, container, args string) (string, string, error) {
	fields := strings.Fields(args)
	pid := ""
	if len(fields) > 0 {
		pid = fields[0]
	}
	if pid == "" {
		return "", "", fmt.Errorf("jfr 需要 pid（args=pid）")
	}
	duration := 15 * time.Second // 默认采集 15s
	if len(fields) > 1 {
		if du, e := time.ParseDuration(fields[1]); e == nil && du > 0 {
			duration = du
		}
	}

	name := fmt.Sprintf("jvm-%s", time.Now().Format("20060102-150405"))
	podPath := "/tmp/" + name + ".jfr"

	startCmd := fmt.Sprintf("jcmd %s JFR.start name=%s settings=profile filename=%s duration=%s", pid, name, podPath, duration.String())
	out, err := d.ex.ExecInPod(ctx, ns, pod, container, startCmd)
	if err != nil {
		return out, "", fmt.Errorf("JFR.start 失败: %w", err)
	}

	// duration 结束后文件才会写全，多等 2s
	select {
	case <-ctx.Done():
		return out, "", ctx.Err()
	case <-time.After(duration + 2*time.Second):
	}

	localPath, err := d.pullFile(ctx, ns, pod, container, podPath, name+".jfr")
	if err != nil {
		return out, "", err
	}
	// 清理容器内临时文件（忽略失败）
	_, _ = d.ex.ExecInPod(ctx, ns, pod, container, "rm -f "+podPath)
	return out, localPath, nil
}

// heapdump 堆转储：jmap -dump → base64 拉回；jmap 缺失时回退 jcmd GC.heap_dump。
func (d *Diagnostic) heapdump(ctx context.Context, ns, pod, container, args string) (string, string, error) {
	pid := firstArg(args)
	if pid == "" {
		return "", "", fmt.Errorf("heapdump 需要 pid（args=pid）")
	}
	name := fmt.Sprintf("jvm-%s", time.Now().Format("20060102-150405"))
	podPath := "/tmp/" + name + ".hprof"

	cmd := fmt.Sprintf("jmap -dump:live,format=b,file=%s %s", podPath, pid)
	out, err := d.ex.ExecInPod(ctx, ns, pod, container, cmd)
	if err != nil {
		// 回退 jcmd GC.heap_dump（现代 JDK 推荐）
		cmd2 := fmt.Sprintf("jcmd %s GC.heap_dump %s", pid, podPath)
		if out2, err2 := d.ex.ExecInPod(ctx, ns, pod, container, cmd2); err2 == nil {
			out = out2
		} else {
			return out, "", fmt.Errorf("heapdump 失败: %v（jcmd 回退: %v）", err, err2)
		}
	}

	localPath, err := d.pullFile(ctx, ns, pod, container, podPath, name+".hprof")
	if err != nil {
		return out, "", err
	}
	_, _ = d.ex.ExecInPod(ctx, ns, pod, container, "rm -f "+podPath)
	return out, localPath, nil
}

// pullFile 用 base64 把容器内二进制文件拉回 executor 本地落盘，返回本地路径。
func (d *Diagnostic) pullFile(ctx context.Context, ns, pod, container, podPath, localName string) (string, error) {
	b64, err := d.ex.ExecInPod(ctx, ns, pod, container, "base64 "+podPath)
	if err != nil {
		return "", fmt.Errorf("拉回产物失败（base64 %s）: %w", podPath, err)
	}
	raw, err := decodeBase64(b64)
	if err != nil {
		return "", fmt.Errorf("产物 base64 解码失败: %w", err)
	}
	if len(raw) == 0 {
		return "", fmt.Errorf("产物为空（%s）", podPath)
	}
	localPath := filepath.Join(ArtifactDir, localName)
	if err := os.WriteFile(localPath, raw, 0o644); err != nil {
		return "", fmt.Errorf("产物落盘失败: %w", err)
	}
	return localPath, nil
}

// findJavaPid 自动探测容器内 Java 进程 PID：优先 jps -l，回退 ps -ef 抓 java。
func (d *Diagnostic) findJavaPid(ctx context.Context, ns, pod, container string) string {
	if out, err := d.ex.ExecInPod(ctx, ns, pod, container, "jps -l"); err == nil {
		for _, line := range strings.Split(out, "\n") {
			fields := strings.Fields(line)
			if len(fields) == 0 || !isNumeric(fields[0]) {
				continue
			}
			// 跳过 jps 自身
			if len(fields) > 1 && strings.Contains(strings.ToLower(fields[1]), "jps") {
				continue
			}
			return fields[0]
		}
	}
	if out, err := d.ex.ExecInPod(ctx, ns, pod, container, "ps -ef"); err == nil {
		for _, line := range strings.Split(out, "\n") {
			if !strings.Contains(line, "java") {
				continue
			}
			fields := strings.Fields(line)
			if len(fields) == 0 {
				continue
			}
			// coreutils ps -ef：PID 在字段 2；busybox ps：PID 在字段 1
			if isNumeric(fields[0]) {
				return fields[0]
			}
		}
	}
	return ""
}

// ---------- 参数解析辅助 ----------

var (
	reXms        = regexp.MustCompile(`-Xms(\S+)`)
	reXmx        = regexp.MustCompile(`-Xmx(\S+)`)
	reXss        = regexp.MustCompile(`-Xss(\S+)`)
	reMaxMetaArg = regexp.MustCompile(`-XX:MaxMetaspaceSize=(\S+)`)

	reInitialHeap = regexp.MustCompile(`InitialHeapSize\s*=\s*(\d+)`)
	reMaxHeap     = regexp.MustCompile(`MaxHeapSize\s*=\s*(\d+)`)
	reThreadStack = regexp.MustCompile(`ThreadStackSize\s*=\s*(\d+)`)
	reMaxMeta     = regexp.MustCompile(`MaxMetaspaceSize\s*=\s*(\d+)`)
)

// extractExplicitFlags 从 VM.command_line 输出解析显式 -Xms/-Xmx/-Xss/-XX:MaxMetaspaceSize。
func extractExplicitFlags(s string) map[string]string {
	m := map[string]string{}
	if v := reXms.FindStringSubmatch(s); len(v) == 2 {
		m["xms"] = v[1]
	}
	if v := reXmx.FindStringSubmatch(s); len(v) == 2 {
		m["xmx"] = v[1]
	}
	if v := reXss.FindStringSubmatch(s); len(v) == 2 {
		m["xss"] = v[1]
	}
	if v := reMaxMetaArg.FindStringSubmatch(s); len(v) == 2 {
		m["maxmetaspacesize"] = v[1]
	}
	return m
}

// extractEffectiveFlags 从 VM.flags 输出解析有效值（字节/或 ThreadStackSize 的 KB）。
func extractEffectiveFlags(s string) map[string]uint64 {
	m := map[string]uint64{}
	pairs := []struct {
		key string
		re  *regexp.Regexp
	}{
		{"InitialHeapSize", reInitialHeap},
		{"MaxHeapSize", reMaxHeap},
		{"ThreadStackSize", reThreadStack},
		{"MaxMetaspaceSize", reMaxMeta},
	}
	for _, p := range pairs {
		if v := p.re.FindStringSubmatch(s); len(v) == 2 {
			if n, e := strconv.ParseUint(v[1], 10, 64); e == nil {
				m[p.key] = n
			}
		}
	}
	return m
}

// pickHeap 优先显式值，缺失时用有效值（字节）转 human；mult 用于 KB 值（ThreadStackSize）。
func pickHeap(explicit string, effective uint64, mult uint64) string {
	if explicit != "" {
		return explicit
	}
	if effective == 0 {
		return "unknown"
	}
	return humanSize(effective * mult)
}

// pickMeta 优先显式值，缺失时用有效值；"无上限" 哨兵值报告 unlimited。
func pickMeta(explicit string, effective uint64) string {
	if explicit != "" {
		return explicit
	}
	if effective == 0 {
		return "unknown"
	}
	if effective > 1<<60 { // MaxMetaspaceSize 默认无上限哨兵
		return "unlimited"
	}
	return humanSize(effective)
}

func humanSize(n uint64) string {
	const (
		KB = uint64(1) << 10
		MB = uint64(1) << 20
		GB = uint64(1) << 30
	)
	switch {
	case n >= GB:
		return trimFloat(n, GB) + "g"
	case n >= MB:
		return trimFloat(n, MB) + "m"
	case n >= KB:
		return trimFloat(n, KB) + "k"
	default:
		return strconv.FormatUint(n, 10)
	}
}

func trimFloat(n, unit uint64) string {
	return strings.TrimSuffix(strings.TrimSuffix(fmt.Sprintf("%.1f", float64(n)/float64(unit)), "0"), ".")
}

func decodeBase64(s string) ([]byte, error) {
	// 去掉换行/空白（busybox base64 默认折行）
	clean := strings.Map(func(r rune) rune {
		switch r {
		case '\n', '\r', ' ', '\t':
			return -1
		}
		return r
	}, s)
	return base64.StdEncoding.DecodeString(clean)
}

func firstArg(args string) string {
	f := strings.Fields(args)
	if len(f) == 0 {
		return ""
	}
	return f[0]
}

func firstNonEmptyLine(s string) string {
	for _, line := range strings.Split(s, "\n") {
		if t := strings.TrimSpace(line); t != "" {
			return t
		}
	}
	return ""
}

func isNumeric(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
