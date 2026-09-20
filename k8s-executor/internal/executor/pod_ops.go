// Package executor Pod 运维操作：日志 / YAML 查看 / YAML 应用（集群管理页用）
package executor

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"sigs.k8s.io/yaml"
)

// GetPodLogs 获取 Pod 容器日志（tail 最近 N 行；previous=取上一次崩溃日志）
func (e *Executor) GetPodLogs(ctx context.Context, namespace, podName, container string, tailLines int64, previous bool) (string, error) {
	if tailLines <= 0 {
		tailLines = 200
	}
	// 容器名为空时自动选容器：多容器 Pod 不指定容器会被 API Server 拒绝
	// （报错形如 "the server rejected our request for an unknown reason"）
	// 选择顺序：kubectl.kubernetes.io/default-container 注解 → 第一个非 sidecar 容器 → 第一个容器
	if container == "" {
		if pod, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{}); err == nil {
			container = pickDefaultContainer(pod)
		}
	}
	req := e.clientset.CoreV1().Pods(namespace).GetLogs(podName, &corev1.PodLogOptions{
		Container: container,
		TailLines: &tailLines,
		Previous:  previous,
	})
	data, err := req.Do(ctx).Raw()
	if err != nil {
		return "", err
	}
	return string(data), nil
}

// pickDefaultContainer 选择默认日志容器
func pickDefaultContainer(pod *corev1.Pod) string {
	if pod == nil || len(pod.Spec.Containers) == 0 {
		return ""
	}
	if v := pod.Annotations["kubectl.kubernetes.io/default-container"]; v != "" {
		return v
	}
	// 优先非 sidecar（按常见命名跳过），否则退回第一个容器
	sidecars := map[string]bool{"filebeat": true, "istio-proxy": true, "envoy": true, "linkerd-proxy": true, "sidecar": true}
	for _, c := range pod.Spec.Containers {
		if !sidecars[c.Name] {
			return c.Name
		}
	}
	return pod.Spec.Containers[0].Name
}

// GetPodYaml 获取 Pod YAML（只读；去掉 managedFields/资源版本等噪音字段）
func (e *Executor) GetPodYaml(ctx context.Context, namespace, podName string) (string, error) {
	pod, err := e.clientset.CoreV1().Pods(namespace).Get(ctx, podName, metav1.GetOptions{})
	if err != nil {
		return "", err
	}
	pod.TypeMeta = metav1.TypeMeta{Kind: "Pod", APIVersion: "v1"}
	pod.ManagedFields = nil
	pod.ResourceVersion = ""
	pod.UID = ""
	pod.CreationTimestamp = metav1.Time{}
	data, err := yaml.Marshal(pod)
	if err != nil {
		return "", fmt.Errorf("YAML 序列化失败: %w", err)
	}
	return string(data), nil
}

// ApplyPodYaml 应用 Pod YAML（server-side apply；仅允许 Pod 资源）
func (e *Executor) ApplyPodYaml(ctx context.Context, namespace, yamlContent string) error {
	obj := &unstructured.Unstructured{}
	if err := yaml.Unmarshal([]byte(yamlContent), obj); err != nil {
		return fmt.Errorf("YAML 解析失败: %w", err)
	}
	if obj.GetKind() != "Pod" {
		return fmt.Errorf("仅支持修改 Pod YAML，当前 Kind=%q", obj.GetKind())
	}
	if ns := obj.GetNamespace(); ns != "" {
		namespace = ns
	}
	if namespace == "" {
		namespace = "default"
	}

	dynamicClient, err := dynamic.NewForConfig(e.restConfig)
	if err != nil {
		return fmt.Errorf("创建 dynamic client 失败: %w", err)
	}
	gvr := schema.GroupVersionResource{Group: "", Version: "v1", Resource: "pods"}
	_, err = dynamicClient.Resource(gvr).Namespace(namespace).Apply(
		ctx, obj.GetName(), obj,
		metav1.ApplyOptions{FieldManager: "api-ops", Force: true},
	)
	if err != nil {
		return fmt.Errorf("apply 失败: %w", err)
	}
	return nil
}
