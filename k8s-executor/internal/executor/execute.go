// Package executor 命令执行：白名单 + 安全检查 + dry-run 预演 + 幂等 + 影响面评估
package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
)

// Executor 命令执行器
type Executor struct {
	clientset  *kubernetes.Clientset
	restConfig *rest.Config
	clusterID  uint
	whitelist  []string
}

// NewExecutor 创建执行器
func NewExecutor(clusterID uint, clientset *kubernetes.Clientset, restConfig *rest.Config, whitelist []string) *Executor {
	return &Executor{clientset: clientset, restConfig: restConfig, clusterID: clusterID, whitelist: whitelist}
}

// ParsedCommand 解析后的命令结构
type ParsedCommand struct {
	Verb      string   // 首个子命令
	Resource  string   // deployment/pod/service...
	Name      string   // 资源名
	Namespace string   // -n 指定的 namespace
	Args      []string // 其余参数
	Raw       string   // 原始命令
}

// ParseCommand 解析 kubectl 命令（首词为 kubectl 则剥离）
func ParseCommand(command string) (*ParsedCommand, error) {
	fields := strings.Fields(command)
	if len(fields) == 0 {
		return nil, fmt.Errorf("空命令")
	}
	if fields[0] == "kubectl" {
		fields = fields[1:]
	}
	if len(fields) == 0 {
		return nil, fmt.Errorf("无效命令")
	}
	pc := &ParsedCommand{Verb: fields[0], Raw: command, Args: fields[1:]}
	// 提取 namespace
	for i := 0; i < len(fields); i++ {
		if fields[i] == "-n" || fields[i] == "--namespace" {
			if i+1 < len(fields) {
				pc.Namespace = fields[i+1]
			}
		}
	}
	// 提取资源类型和名称
	switch pc.Verb {
	case "cordon", "uncordon":
		pc.Resource = "node"
		if len(fields) >= 2 && !strings.HasPrefix(fields[1], "-") {
			pc.Name = fields[1]
		}
	case "logs":
		// logs <pod> [-c container] [--previous] [--tail=N] [--all-containers=true]
		if len(fields) >= 2 && !strings.HasPrefix(fields[1], "-") {
			pc.Resource, pc.Name = "pod", fields[1]
		}
	case "scale", "rollout", "delete", "describe", "get", "edit", "set":
		if len(fields) >= 2 {
			// scale deployment/name / rollout restart deployment/name / delete deployment name
			if strings.Contains(fields[1], "/") {
				parts := strings.SplitN(fields[1], "/", 2)
				pc.Resource, pc.Name = parts[0], parts[1]
			} else {
				pc.Resource = fields[1]
				if len(fields) >= 3 && !strings.HasPrefix(fields[2], "-") {
					pc.Name = fields[2]
				}
			}
		}
	}
	return pc, nil
}

// WhitelistCheck 白名单校验：命令首词必须在允许列表
func (e *Executor) WhitelistCheck(pc *ParsedCommand) error {
	for _, w := range e.whitelist {
		if strings.TrimSpace(w) == pc.Verb {
			return nil
		}
	}
	return fmt.Errorf("命令 %q 不在白名单（允许：%s）", pc.Verb, strings.Join(e.whitelist, ","))
}

// SafetyCheck 安全检查：拦截危险模式
var dangerousPatterns = []string{
	"rm -rf", "rm -fr", "drop", "format", "purge", "mkfs",
	":(){:|:&};:", "chmod 777 /", "dd if=/dev/zero",
	"fdisk", "shutdown", "reboot", "poweroff",
}

func SafetyCheck(command string) error {
	lower := strings.ToLower(command)
	for _, p := range dangerousPatterns {
		if strings.Contains(lower, p) {
			return fmt.Errorf("命令包含危险操作 %q，已拦截", p)
		}
	}
	return nil
}

// IdempotencyCheck 幂等检查：资源已处于目标状态则跳过
func (e *Executor) IdempotencyCheck(ctx context.Context, pc *ParsedCommand) (bool, error) {
	ns := pc.Namespace
	if ns == "" {
		ns = "default"
	}
	switch pc.Verb {
	case "scale":
		// 解析 --replicas=N，与当前比较
		target := 0
		for i, a := range pc.Args {
			if a == "--replicas" && i+1 < len(pc.Args) {
				fmt.Sscanf(pc.Args[i+1], "%d", &target)
			}
		}
		dep, err := e.clientset.AppsV1().Deployments(ns).Get(ctx, pc.Name, metav1.GetOptions{})
		if err != nil {
			return false, nil // 取不到则不判定幂等
		}
		if dep.Spec.Replicas != nil && int(*dep.Spec.Replicas) == target {
			return true, nil
		}
	}
	return false, nil
}

// DryRunCheck 写操作预演（client-go DryRun）
func (e *Executor) DryRunCheck(ctx context.Context, pc *ParsedCommand) error {
	ns := pc.Namespace
	if ns == "" {
		ns = "default"
	}
	switch pc.Verb {
	case "scale":
		target := int32(1)
		for i, a := range pc.Args {
			if a == "--replicas" && i+1 < len(pc.Args) {
				var n int32
				fmt.Sscanf(pc.Args[i+1], "%d", &n)
				target = n
			}
		}
		// 以 dry-run 方式验证 scale 请求（先取当前 scale 对象，改副本后 dry-run 提交）
		scale, err := e.clientset.AppsV1().Deployments(ns).GetScale(ctx, pc.Name, metav1.GetOptions{})
		if err != nil {
			return err
		}
		scale.Spec.Replicas = target
		_, err = e.clientset.AppsV1().Deployments(ns).UpdateScale(ctx, pc.Name, scale, metav1.UpdateOptions{DryRun: []string{"All"}})
		return err
	case "delete":
		return nil // delete 由白名单+决策层控制，dry-run 意义有限
	}
	return nil
}

// BlastRadiusEvaluate 评估修复方案影响面（统计受影响的 Pod 数量）
func (e *Executor) BlastRadiusEvaluate(ctx context.Context, planJSON string) (int, error) {
	var plan struct {
		Steps []struct {
			Command string `json:"command"`
		} `json:"steps"`
	}
	if err := json.Unmarshal([]byte(planJSON), &plan); err != nil {
		return 0, err
	}
	// 简单实现：统计涉及 deployment 的副本总数
	total := 0
	for _, step := range plan.Steps {
		pc, err := ParseCommand(step.Command)
		if err != nil {
			continue
		}
		if pc.Resource == "deployment" && pc.Name != "" {
			ns := pc.Namespace
			if ns == "" {
				ns = "default"
			}
			dep, err := e.clientset.AppsV1().Deployments(ns).Get(ctx, pc.Name, metav1.GetOptions{})
			if err == nil && dep.Spec.Replicas != nil {
				total += int(*dep.Spec.Replicas)
			}
		}
	}
	return total, nil
}

// ExecuteCommand 执行单条命令（白名单→安全→幂等→dry-run→执行）
func (e *Executor) ExecuteCommand(ctx context.Context, namespace, command string) (string, error) {
	pc, err := ParseCommand(command)
	if err != nil {
		return "", err
	}
	if namespace != "" {
		pc.Namespace = namespace
	}
	if err := e.WhitelistCheck(pc); err != nil {
		return "", err
	}
	if err := SafetyCheck(command); err != nil {
		return "", err
	}
	// 幂等：已处于目标状态则跳过
	if skip, _ := e.IdempotencyCheck(ctx, pc); skip {
		return "资源已处于目标状态，跳过执行（幂等）", nil
	}
	if err := e.DryRunCheck(ctx, pc); err != nil {
		return "", fmt.Errorf("dry-run 预演失败: %w", err)
	}
	return e.dispatch(ctx, pc)
}

// dispatch 实际命令分发（client-go 翻译常见 kubectl 子命令）
func (e *Executor) dispatch(ctx context.Context, pc *ParsedCommand) (string, error) {
	ns := pc.Namespace
	if ns == "" {
		ns = "default"
	}
	switch pc.Verb {
	case "get":
		return e.execGet(ctx, pc, ns)
	case "describe":
		return e.execDescribe(ctx, pc, ns)
	case "scale":
		return e.execScale(ctx, pc, ns)
	case "rollout":
		return e.execRollout(ctx, pc, ns)
	case "delete":
		return e.execDelete(ctx, pc, ns)
	case "cordon", "uncordon":
		return e.execCordon(ctx, pc)
	case "logs":
		return e.execLogs(ctx, pc, ns)
	default:
		return "", fmt.Errorf("子命令 %q 暂不支持（当前支持 get/describe/scale/rollout/delete/cordon/uncordon/logs）", pc.Verb)
	}
}

func (e *Executor) execGet(ctx context.Context, pc *ParsedCommand, ns string) (string, error) {
	switch pc.Resource {
	case "deployment", "deployments":
		list, err := e.clientset.AppsV1().Deployments(ns).List(ctx, metav1.ListOptions{})
		if err != nil {
			return "", err
		}
		if len(list.Items) == 0 {
			return "(该命名空间无 Deployment)", nil
		}
		var b strings.Builder
		for _, d := range list.Items {
			replicas := int32(0)
			if d.Spec.Replicas != nil {
				replicas = *d.Spec.Replicas
			}
			b.WriteString(fmt.Sprintf("deployment %s: replicas=%d ready=%d\n", d.Name, replicas, d.Status.ReadyReplicas))
			for _, c := range d.Spec.Template.Spec.Containers {
				b.WriteString(fmt.Sprintf("  container %s: image=%s\n", c.Name, c.Image))
				if len(c.Resources.Requests) > 0 {
					b.WriteString("    requests:")
					for k, v := range c.Resources.Requests {
						b.WriteString(fmt.Sprintf(" %s=%s", k, v.String()))
					}
					b.WriteString("\n")
				}
				if len(c.Resources.Limits) > 0 {
					b.WriteString("    limits:")
					for k, v := range c.Resources.Limits {
						b.WriteString(fmt.Sprintf(" %s=%s", k, v.String()))
					}
					b.WriteString("\n")
				}
			}
		}
		return b.String(), nil
	case "hpa", "horizontalpodautoscaler", "horizontalpodautoscalers":
		list, err := e.clientset.AutoscalingV2().HorizontalPodAutoscalers(ns).List(ctx, metav1.ListOptions{})
		if err != nil {
			return "", err
		}
		if len(list.Items) == 0 {
			return "(该命名空间无 HPA)", nil
		}
		var b strings.Builder
		for _, h := range list.Items {
			min := int32(0)
			if h.Spec.MinReplicas != nil {
				min = *h.Spec.MinReplicas
			}
			b.WriteString(fmt.Sprintf("HPA %s: min=%d max=%d current=%d\n", h.Name, min, h.Spec.MaxReplicas, h.Status.CurrentReplicas))
			for _, m := range h.Spec.Metrics {
				if m.Resource != nil {
					tgt := ""
					if m.Resource.Target.AverageUtilization != nil {
						tgt = fmt.Sprintf("%d%%", *m.Resource.Target.AverageUtilization)
					} else if m.Resource.Target.AverageValue != nil {
						tgt = m.Resource.Target.AverageValue.String()
					}
					b.WriteString(fmt.Sprintf("  metric resource %s target=%s\n", m.Resource.Name, tgt))
				} else {
					b.WriteString(fmt.Sprintf("  metric %s\n", m.Type))
				}
			}
			for _, cm := range h.Status.CurrentMetrics {
				if cm.Resource != nil {
					cur := ""
					if cm.Resource.Current.AverageUtilization != nil {
						cur = fmt.Sprintf("%d%%", *cm.Resource.Current.AverageUtilization)
					} else if cm.Resource.Current.AverageValue != nil {
						cur = cm.Resource.Current.AverageValue.String()
					}
					b.WriteString(fmt.Sprintf("  current resource %s=%s\n", cm.Resource.Name, cur))
				}
			}
		}
		return b.String(), nil
	case "pod", "pods":
		if wantsJSON(pc.Args) {
			return e.podsJSON(ctx, ns)
		}
		list, err := e.clientset.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{})
		if err != nil {
			return "", err
		}
		var b strings.Builder
		for _, p := range list.Items {
			b.WriteString(fmt.Sprintf("%s\t%s\n", p.Name, p.Status.Phase))
		}
		return b.String(), nil
	case "node", "nodes":
		if wantsJSON(pc.Args) {
			return e.nodesJSON(ctx)
		}
		list, err := e.clientset.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
		if err != nil {
			return "", err
		}
		if len(list.Items) == 0 {
			return "(无节点)", nil
		}
		var b strings.Builder
		for _, n := range list.Items {
			var roles []string
			for k := range n.Labels {
				if strings.HasPrefix(k, "node-role.kubernetes.io/") {
					roles = append(roles, strings.TrimPrefix(k, "node-role.kubernetes.io/"))
				}
			}
			sched := "Schedulable"
			if n.Spec.Unschedulable {
				sched = "Unschedulable"
			}
			ready := "Unknown"
			for _, c := range n.Status.Conditions {
				if string(c.Type) == "Ready" {
					if c.Status == "True" {
						ready = "Ready"
					} else {
						ready = "NotReady"
					}
				}
			}
			b.WriteString(fmt.Sprintf("node %s: %s %s roles=%v\n", n.Name, ready, sched, roles))
		}
		return b.String(), nil
	case "limitrange", "limitranges":
		list, err := e.clientset.CoreV1().LimitRanges(ns).List(ctx, metav1.ListOptions{})
		if err != nil {
			return "", err
		}
		if len(list.Items) == 0 {
			return "(该命名空间无 LimitRange)", nil
		}
		var b strings.Builder
		for _, lr := range list.Items {
			b.WriteString(fmt.Sprintf("LimitRange %s\n", lr.Name))
			for _, it := range lr.Spec.Limits {
				b.WriteString(fmt.Sprintf("  type=%s default=%v defaultRequest=%v\n", it.Type, it.Default, it.DefaultRequest))
			}
		}
		return b.String(), nil
	case "resourcequota", "resourcequotas", "quota", "quotas":
		list, err := e.clientset.CoreV1().ResourceQuotas(ns).List(ctx, metav1.ListOptions{})
		if err != nil {
			return "", err
		}
		if len(list.Items) == 0 {
			return "(该命名空间无 ResourceQuota)", nil
		}
		var b strings.Builder
		for _, q := range list.Items {
			b.WriteString(fmt.Sprintf("ResourceQuota %s\n", q.Name))
			for k, v := range q.Spec.Hard {
				b.WriteString(fmt.Sprintf("  hard %s=%s\n", k, v.String()))
			}
		}
		return b.String(), nil
	default:
		return "", fmt.Errorf("get %s 暂不支持", pc.Resource)
	}
}

// execCordon 节点 cordon/uncordon（禁止/恢复调度）
func (e *Executor) execCordon(ctx context.Context, pc *ParsedCommand) (string, error) {
	if pc.Name == "" {
		return "", fmt.Errorf("cordon/uncordon 需要节点名")
	}
	unschedulable := pc.Verb == "cordon"
	node, err := e.clientset.CoreV1().Nodes().Get(ctx, pc.Name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	if node.Spec.Unschedulable == unschedulable {
		state := "可调度"
		if unschedulable {
			state = "不可调度"
		}
		return fmt.Sprintf("节点 %s 已处于「%s」状态（幂等跳过）", pc.Name, state), nil
	}
	node.Spec.Unschedulable = unschedulable
	if _, err := e.clientset.CoreV1().Nodes().Update(ctx, node, metav1.UpdateOptions{}); err != nil {
		return "", err
	}
	if unschedulable {
		return fmt.Sprintf("节点 %s 已 cordon（禁止调度）", pc.Name), nil
	}
	return fmt.Sprintf("节点 %s 已 uncordon（恢复调度）", pc.Name), nil
}

func (e *Executor) execDescribe(ctx context.Context, pc *ParsedCommand, ns string) (string, error) {
	if pc.Resource == "pod" && pc.Name != "" {
		pod, err := e.clientset.CoreV1().Pods(ns).Get(ctx, pc.Name, metav1.GetOptions{})
		if err != nil {
			return "", err
		}
		var b strings.Builder
		b.WriteString(fmt.Sprintf("Pod %s: phase=%s, node=%s\n", pod.Name, pod.Status.Phase, pod.Spec.NodeName))
		// 容器状态
		for _, cs := range pod.Status.ContainerStatuses {
			state := "running"
			reason := ""
			if cs.State.Waiting != nil {
				state, reason = "waiting", cs.State.Waiting.Reason
			} else if cs.State.Terminated != nil {
				state, reason = "terminated", cs.State.Terminated.Reason
			}
			b.WriteString(fmt.Sprintf("  container %s: %s (ready=%v, restarts=%d)", cs.Name, state, cs.Ready, cs.RestartCount))
			if reason != "" {
				b.WriteString(fmt.Sprintf(" reason=%s", reason))
			}
			b.WriteString("\n")
		}
		// Pod 事件（诊断依据：describe 的 events）
		events, err := e.clientset.CoreV1().Events(ns).List(ctx, metav1.ListOptions{
			FieldSelector: fmt.Sprintf("involvedObject.name=%s,involvedObject.namespace=%s", pc.Name, ns),
		})
		if err == nil && len(events.Items) > 0 {
			b.WriteString("Events:\n")
			for i := len(events.Items) - 1; i >= 0; i-- {
				ev := events.Items[i]
				b.WriteString(fmt.Sprintf("  [%s] %s %s: %s\n", ev.LastTimestamp.Format("15:04:05"), ev.Type, ev.Reason, ev.Message))
			}
		}
		return b.String(), nil
	}
	return "", fmt.Errorf("describe 仅支持 pod，且需指定名称")
}

func (e *Executor) execScale(ctx context.Context, pc *ParsedCommand, ns string) (string, error) {
	if pc.Resource != "deployment" || pc.Name == "" {
		return "", fmt.Errorf("scale 仅支持 deployment/名称")
	}
	var replicas int32 = 1
	for i, a := range pc.Args {
		if a == "--replicas" && i+1 < len(pc.Args) {
			var n int32
			fmt.Sscanf(pc.Args[i+1], "%d", &n)
			replicas = n
		}
	}
	dep, err := e.clientset.AppsV1().Deployments(ns).Get(ctx, pc.Name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	dep.Spec.Replicas = &replicas
	if _, err := e.clientset.AppsV1().Deployments(ns).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
		return "", err
	}
	return fmt.Sprintf("deployment %s scaled to %d", pc.Name, replicas), nil
}

func (e *Executor) execRollout(ctx context.Context, pc *ParsedCommand, ns string) (string, error) {
	// rollout restart deployment/name：给 Pod 模板打 annotation 触发滚动重启
	if len(pc.Args) >= 1 && pc.Args[0] == "restart" {
		name := pc.Name
		if name == "" && len(pc.Args) >= 2 {
			name = pc.Args[1]
		}
		if name == "" {
			return "", fmt.Errorf("rollout restart 需指定资源名")
		}
		dep, err := e.clientset.AppsV1().Deployments(ns).Get(ctx, name, metav1.GetOptions{})
		if err != nil {
			return "", err
		}
		if dep.Spec.Template.Annotations == nil {
			dep.Spec.Template.Annotations = map[string]string{}
		}
		dep.Spec.Template.Annotations["kubectl.kubernetes.io/restartedAt"] = timeNowString()
		if _, err := e.clientset.AppsV1().Deployments(ns).Update(ctx, dep, metav1.UpdateOptions{}); err != nil {
			return "", err
		}
		return fmt.Sprintf("deployment %s restart triggered", name), nil
	}
	return "", fmt.Errorf("rollout 仅支持 restart")
}

func (e *Executor) execDelete(ctx context.Context, pc *ParsedCommand, ns string) (string, error) {
	if pc.Resource == "pod" && pc.Name != "" {
		opts := metav1.DeleteOptions{}
		// 识别 --force / --grace-period=0：强删（立即 SIGKILL，不等优雅期）。
		// 否则沿用 Pod 的 terminationGracePeriodSeconds（可能卡住数分钟，无法用于 TerminatingStuck 强删）。
		force := false
		for _, a := range pc.Args {
			if a == "--force" || a == "--grace-period=0" || a == "--grace-period=0s" {
				force = true
			}
		}
		if force {
			grace := int64(0)
			opts.GracePeriodSeconds = &grace
		}
		if err := e.clientset.CoreV1().Pods(ns).Delete(ctx, pc.Name, opts); err != nil {
			return "", err
		}
		return fmt.Sprintf("pod %s deleted", pc.Name), nil
	}
	return "", fmt.Errorf("delete 仅支持 pod（高危操作走审批）")
}

// execLogs 拉取容器日志（client-go GetLogs）：支持 -c/--container、--previous、--tail=N、--all-containers=true
func (e *Executor) execLogs(ctx context.Context, pc *ParsedCommand, ns string) (string, error) {
	if pc.Name == "" {
		return "", fmt.Errorf("logs 需指定 pod 名称")
	}
	previous := false
	allContainers := false
	container := ""
	tail := int64(200)
	for i, a := range pc.Args {
		switch {
		case a == "--previous" || a == "-p":
			previous = true
		case a == "--all-containers=true" || a == "--all-containers":
			allContainers = true
		case a == "-c" || a == "--container":
			if i+1 < len(pc.Args) {
				container = pc.Args[i+1]
			}
		case strings.HasPrefix(a, "--container="):
			container = strings.TrimPrefix(a, "--container=")
		case strings.HasPrefix(a, "--tail="):
			fmt.Sscanf(strings.TrimPrefix(a, "--tail="), "%d", &tail)
		case a == "--tail":
			if i+1 < len(pc.Args) {
				fmt.Sscanf(pc.Args[i+1], "%d", &tail)
			}
		}
	}
	pod, err := e.clientset.CoreV1().Pods(ns).Get(ctx, pc.Name, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	var names []string
	switch {
	case allContainers:
		for _, c := range pod.Spec.Containers {
			names = append(names, c.Name)
		}
	case container != "":
		names = []string{container}
	case len(pod.Spec.Containers) > 0:
		names = []string{pod.Spec.Containers[0].Name}
	}
	if len(names) == 0 {
		return "", fmt.Errorf("pod %s 无容器", pc.Name)
	}
	t := tail
	var b strings.Builder
	for _, cn := range names {
		opts := &corev1.PodLogOptions{Container: cn, TailLines: &t, Previous: previous}
		bs, err := e.clientset.CoreV1().Pods(ns).GetLogs(pc.Name, opts).DoRaw(ctx)
		if err != nil {
			b.WriteString(fmt.Sprintf("--- container %s (previous=%v): <无日志: %v>\n", cn, previous, err))
			continue
		}
		if len(names) > 1 {
			b.WriteString(fmt.Sprintf("--- container %s (previous=%v) ---\n", cn, previous))
		}
		b.Write(bs)
		b.WriteString("\n")
	}
	return b.String(), nil
}

func timeNowString() string {
	return fmt.Sprintf("%d", time.Now().UnixNano())
}

