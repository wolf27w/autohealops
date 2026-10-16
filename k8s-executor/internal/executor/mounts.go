// 挂载信息：Pod / 工作负载 Pod 模板的 volumes + 容器 volumeMounts（用于 Pod 详情与工作负载挂载配置）
package executor

import (
	"context"
	"fmt"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/dynamic"
)

// VolumeMountInfo 容器挂载项（container.volumeMounts）
type VolumeMountInfo struct {
	Name        string
	MountPath   string
	SubPath     string
	ReadOnly    bool
	SubPathExpr string
	Propagation string
}

// VolumeInfo 数据卷（volumes）
type VolumeInfo struct {
	Name     string
	Type     string
	Source   string
	ReadOnly bool
}

// ContainerMounts 容器名 + 其挂载列表
type ContainerMounts struct {
	Name   string
	Mounts []VolumeMountInfo
}

// WorkloadMounts 工作负载 Pod 模板的挂载配置
type WorkloadMounts struct {
	Volumes        []VolumeInfo
	Containers     []ContainerMounts
	InitContainers []ContainerMounts
}

// buildVolumeMounts 转换容器挂载
func buildVolumeMounts(mounts []corev1.VolumeMount) []VolumeMountInfo {
	out := make([]VolumeMountInfo, 0, len(mounts))
	for _, m := range mounts {
		out = append(out, VolumeMountInfo{
			Name: m.Name, MountPath: m.MountPath, SubPath: m.SubPath,
			ReadOnly: m.ReadOnly, SubPathExpr: m.SubPathExpr,
			Propagation: mountPropagationStr(m.MountPropagation),
		})
	}
	return out
}

// buildContainerMounts 转换一组容器的挂载
func buildContainerMounts(cs []corev1.Container) []ContainerMounts {
	out := make([]ContainerMounts, 0, len(cs))
	for _, c := range cs {
		out = append(out, ContainerMounts{Name: c.Name, Mounts: buildVolumeMounts(c.VolumeMounts)})
	}
	return out
}

// buildVolumes 转换数据卷（归纳类型 + 来源摘要）
func buildVolumes(vols []corev1.Volume) []VolumeInfo {
	out := make([]VolumeInfo, 0, len(vols))
	for _, v := range vols {
		t, s, ro := volumeTypeAndSource(v)
		out = append(out, VolumeInfo{Name: v.Name, Type: t, Source: s, ReadOnly: ro})
	}
	return out
}

func hostPathTypeStr(t *corev1.HostPathType) string {
	if t == nil {
		return ""
	}
	return string(*t)
}

func mountPropagationStr(m *corev1.MountPropagationMode) string {
	if m == nil {
		return ""
	}
	return string(*m)
}

// volumeTypeAndSource 归纳 volume 的类型与来源摘要
func volumeTypeAndSource(v corev1.Volume) (string, string, bool) {
	switch {
	case v.ConfigMap != nil:
		src := v.ConfigMap.Name
		if v.ConfigMap.Optional != nil && *v.ConfigMap.Optional {
			src += "（可选）"
		}
		return "configMap", src, false
	case v.Secret != nil:
		src := v.Secret.SecretName
		if v.Secret.Optional != nil && *v.Secret.Optional {
			src += "（可选）"
		}
		return "secret", src, false
	case v.PersistentVolumeClaim != nil:
		return "persistentVolumeClaim", v.PersistentVolumeClaim.ClaimName, v.PersistentVolumeClaim.ReadOnly
	case v.HostPath != nil:
		if t := hostPathTypeStr(v.HostPath.Type); t != "" {
			return "hostPath", fmt.Sprintf("%s (%s)", v.HostPath.Path, t), false
		}
		return "hostPath", v.HostPath.Path, false
	case v.EmptyDir != nil:
		return "emptyDir", "", false
	case v.NFS != nil:
		return "nfs", fmt.Sprintf("%s:%s", v.NFS.Server, v.NFS.Path), v.NFS.ReadOnly
	case v.Projected != nil:
		return "projected", fmt.Sprintf("%d 个来源", len(v.Projected.Sources)), false
	case v.DownwardAPI != nil:
		return "downwardAPI", fmt.Sprintf("%d 项", len(v.DownwardAPI.Items)), false
	case v.CSI != nil:
		return "csi", v.CSI.Driver, false
	case v.Glusterfs != nil:
		return "glusterfs", fmt.Sprintf("%s:%s", v.Glusterfs.EndpointsName, v.Glusterfs.Path), false
	case v.CephFS != nil:
		return "cephfs", fmt.Sprintf("%s:%s", v.CephFS.Monitors, v.CephFS.Path), false
	default:
		return "其它", "", false
	}
}

// GetWorkloadMounts 获取工作负载 Pod 模板的挂载配置（Deployment/StatefulSet/DaemonSet）
func (e *Executor) GetWorkloadMounts(ctx context.Context, kind, namespace, name string) (*WorkloadMounts, error) {
	if namespace == "" || name == "" {
		return nil, fmt.Errorf("namespace/name 不能为空")
	}
	gvr, err := workloadGVR(kind)
	if err != nil {
		return nil, err
	}
	dc, err := dynamic.NewForConfig(e.restConfig)
	if err != nil {
		return nil, fmt.Errorf("创建 dynamic client 失败: %w", err)
	}
	u, err := dc.Resource(gvr).Namespace(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, err
	}
	tmpl, found, err := unstructured.NestedMap(u.Object, "spec", "template", "spec")
	if err != nil || !found {
		return nil, fmt.Errorf("该工作负载无 Pod 模板（spec.template.spec）")
	}
	var podSpec corev1.PodSpec
	if err := runtime.DefaultUnstructuredConverter.FromUnstructured(tmpl, &podSpec); err != nil {
		return nil, fmt.Errorf("Pod 模板解析失败: %w", err)
	}
	return &WorkloadMounts{
		Volumes:        buildVolumes(podSpec.Volumes),
		Containers:     buildContainerMounts(podSpec.Containers),
		InitContainers: buildContainerMounts(podSpec.InitContainers),
	}, nil
}
