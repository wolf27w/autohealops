// Package grpc gRPC 服务端：实现 ExecutorService 全部 RPC
package grpc

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/autohealops/k8s-executor/internal/config"
	"github.com/autohealops/k8s-executor/internal/executor"
	"github.com/autohealops/k8s-executor/internal/health"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"github.com/autohealops/k8s-executor/internal/prometheus"
	"github.com/autohealops/k8s-executor/internal/quota"
	"github.com/autohealops/k8s-executor/internal/rbac"
	"github.com/autohealops/k8s-executor/internal/script"
	"github.com/autohealops/k8s-executor/internal/watcher"
	pb "github.com/autohealops/k8s-executor/proto"
	"go.uber.org/zap"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// Server gRPC 服务实现
type Server struct {
	pb.UnimplementedExecutorServiceServer
	cfg          *config.Config
	manager      *k8sclient.Manager
	watchManager *watcher.WatchManager
	health       *health.Checker
	quota        *quota.Collector
	script       *script.Runner
	logger       *zap.Logger
}

// NewServer 创建 gRPC 服务
func NewServer(
	cfg *config.Config,
	manager *k8sclient.Manager,
	watchManager *watcher.WatchManager,
	healthChecker *health.Checker,
	quotaCollector *quota.Collector,
	scriptRunner *script.Runner,
	logger *zap.Logger,
) *Server {
	return &Server{
		cfg:          cfg,
		manager:      manager,
		watchManager: watchManager,
		health:       healthChecker,
		quota:        quotaCollector,
		script:       scriptRunner,
		logger:       logger,
	}
}

// traceID 从 gRPC metadata 提取 trace_id
func traceID(ctx context.Context) string {
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		if vals := md.Get("trace-id"); len(vals) > 0 {
			return vals[0]
		}
	}
	return ""
}

// getExecutor 构建指定集群的执行器
func (s *Server) getExecutor(ctx context.Context, clusterID int64) (*executor.Executor, error) {
	client, err := s.manager.GetClient(ctx, uint(clusterID))
	if err != nil {
		return nil, status.Errorf(codes.Internal, "获取集群客户端失败: %v", err)
	}
	return executor.NewExecutor(uint(clusterID), client.Clientset, client.RestConfig, s.cfg.CommandWhitelist), nil
}

// QueryPrometheus 集群内 Prometheus 查询代理（port-forward，容器资源使用率等）
func (s *Server) QueryPrometheus(ctx context.Context, req *pb.QueryPrometheusRequest) (*pb.QueryPrometheusResponse, error) {
	if req == nil || req.Promql == "" {
		return &pb.QueryPrometheusResponse{Success: false, Error: "promql 必填"}, nil
	}
	client, err := s.manager.GetClient(ctx, uint(req.ClusterId))
	if err != nil {
		return &pb.QueryPrometheusResponse{Success: false, Error: "获取集群客户端失败: " + err.Error()}, nil
	}
	data, err := prometheus.Query(client.Clientset, client.RestConfig, req.Promql)
	if err != nil {
		s.logger.Warn("Prometheus 查询失败", zap.Int64("cluster", req.ClusterId), zap.Error(err))
		return &pb.QueryPrometheusResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.QueryPrometheusResponse{Success: true, Data: string(data)}, nil
}

// StartWatch 启动集群监听
func (s *Server) StartWatch(ctx context.Context, req *pb.StartWatchRequest) (*pb.StartWatchResponse, error) {
	if err := s.watchManager.StartClusterWatch(ctx, uint(req.ClusterId)); err != nil {
		return &pb.StartWatchResponse{Success: false, Message: err.Error()}, nil
	}
	return &pb.StartWatchResponse{Success: true, Message: "watch started"}, nil
}

// StopWatch 停止集群监听
func (s *Server) StopWatch(ctx context.Context, req *pb.StopWatchRequest) (*pb.StopWatchResponse, error) {
	s.watchManager.StopClusterWatch(uint(req.ClusterId))
	return &pb.StopWatchResponse{Success: true, Message: "watch stopped"}, nil
}

// ExecuteCommand 执行单条命令
func (s *Server) ExecuteCommand(ctx context.Context, req *pb.ExecuteCommandRequest) (*pb.ExecuteCommandResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return nil, err
	}
	output, err := ex.ExecuteCommand(ctx, req.Namespace, req.Command)
	if err != nil {
		return &pb.ExecuteCommandResponse{Success: false, Output: "", Error: err.Error()}, nil
	}
	return &pb.ExecuteCommandResponse{Success: true, Output: output}, nil
}

// ExecutePlan 流式执行修复计划
func (s *Server) ExecutePlan(req *pb.ExecutePlanRequest, stream pb.ExecutorService_ExecutePlanServer) error {
	ctx := stream.Context()
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return err
	}
	gray := executor.NewGrayExecutor(ex)
	notify := func(stepIndex int, command string, success bool, output, message string, finished, circuitTriggered bool, rollbackCommand string) {
		resp := &pb.ExecutePlanResponse{
			StepIndex:        int32(stepIndex),
			StepCommand:      command,
			Success:          success,
			Output:           output,
			Message:          message,
			Finished:         finished,
			CircuitTriggered: circuitTriggered,
			RollbackCommand:  rollbackCommand,
		}
		if err := stream.Send(resp); err != nil {
			s.logger.Error("发送进度失败", zap.Error(err))
		}
	}
	return gray.ExecuteWithGrayStrategy(ctx, uint(req.PlanId), req.PlanJson, notify)
}

// RollbackPlan 回滚修复计划
func (s *Server) RollbackPlan(ctx context.Context, req *pb.RollbackPlanRequest) (*pb.RollbackPlanResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return nil, err
	}
	executed, err := ex.RollbackPlan(ctx, uint(req.PlanId), req.PlanJson)
	if err != nil {
		return &pb.RollbackPlanResponse{Success: false, Message: err.Error(), ExecutedRollbacks: executed}, nil
	}
	return &pb.RollbackPlanResponse{Success: true, Message: "回滚完成", ExecutedRollbacks: executed}, nil
}

// HealthCheck 健康检查
func (s *Server) HealthCheck(ctx context.Context, req *pb.HealthCheckRequest) (*pb.HealthCheckResponse, error) {
	statusStr, version, err := s.health.HealthCheck(ctx, uint(req.ClusterId))
	if err != nil {
		return &pb.HealthCheckResponse{Success: false, Status: "unreachable", Error: err.Error()}, nil
	}
	return &pb.HealthCheckResponse{Success: true, Status: statusStr, Version: version}, nil
}

// CollectQuota 采集配额
func (s *Server) CollectQuota(ctx context.Context, req *pb.CollectQuotaRequest) (*pb.CollectQuotaResponse, error) {
	if err := s.quota.CollectClusterQuota(ctx, uint(req.ClusterId)); err != nil {
		return &pb.CollectQuotaResponse{Success: false, Message: err.Error()}, nil
	}
	return &pb.CollectQuotaResponse{Success: true, Message: "配额采集完成"}, nil
}

// RunScript 执行脚本
func (s *Server) RunScript(ctx context.Context, req *pb.RunScriptRequest) (*pb.RunScriptResponse, error) {
	output, err := s.script.RunScript(ctx, uint(req.ClusterId), req.ScriptContent, req.Parameters, uint(req.TaskId))
	if err != nil {
		return &pb.RunScriptResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.RunScriptResponse{Success: true, Output: output}, nil
}

// BatchExecute 流式批量执行
func (s *Server) BatchExecute(req *pb.BatchExecuteRequest, stream pb.ExecutorService_BatchExecuteServer) error {
	ctx := stream.Context()
	for _, clusterID := range req.ClusterIds {
		ex, err := s.getExecutor(ctx, clusterID)
		if err != nil {
			_ = stream.Send(&pb.BatchExecuteResponse{ClusterId: clusterID, Success: false, Error: err.Error()})
			continue
		}
		output, err := ex.ExecuteCommand(ctx, req.Namespace, req.Command)
		_ = stream.Send(&pb.BatchExecuteResponse{
			ClusterId: clusterID,
			Success:   err == nil,
			Output:    output,
			Error:     errStr(err),
		})
	}
	return nil
}

// GetPodStatus 查询 Pod 结构化状态（恢复判定）
func (s *Server) GetPodStatus(ctx context.Context, req *pb.GetPodStatusRequest) (*pb.GetPodStatusResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.GetPodStatusResponse{Success: false, Message: err.Error()}, nil
	}
	st, err := ex.GetPodStatus(ctx, req.Namespace, req.PodName)
	if err != nil {
		return &pb.GetPodStatusResponse{Success: false, Message: err.Error()}, nil
	}
	css := make([]*pb.ContainerStatusInfo, 0, len(st.ContainerStates))
	for _, c := range st.ContainerStates {
		css = append(css, &pb.ContainerStatusInfo{
			Name:         c.Name,
			State:        c.State,
			Ready:        c.Ready,
			RestartCount: c.RestartCount,
			Reason:       c.Reason,
		})
	}
	return &pb.GetPodStatusResponse{
		Success:           true,
		Phase:             st.Phase,
		Ready:             st.Ready,
		RestartCount:      st.RestartCount,
		ContainerStatuses: css,
		Message:           "ok",
	}, nil
}

// RepairImagePull 修复镜像拉取失败（定位 Deployment → 补 imagePullSecrets）
func (s *Server) RepairImagePull(ctx context.Context, req *pb.RepairImagePullRequest) (*pb.RepairImagePullResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.RepairImagePullResponse{Success: false, Message: err.Error(), Fixed: false}, nil
	}
	res := ex.RepairImagePull(ctx, req.Namespace, req.PodName)
	return &pb.RepairImagePullResponse{Success: res.Success, Message: res.Message, Fixed: res.Fixed}, nil
}

// GetDeploymentStatus 查询 Deployment 副本状态（Pod 重建后判断是否恢复）
func (s *Server) GetDeploymentStatus(ctx context.Context, req *pb.GetDeploymentStatusRequest) (*pb.GetDeploymentStatusResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.GetDeploymentStatusResponse{Success: false, Message: err.Error()}, nil
	}
	st, err := ex.GetDeploymentStatus(ctx, req.Namespace, req.DeploymentName)
	if err != nil {
		return &pb.GetDeploymentStatusResponse{Success: false, Message: err.Error()}, nil
	}
	return &pb.GetDeploymentStatusResponse{
		Success:       true,
		Replicas:      st.Replicas,
		ReadyReplicas: st.ReadyReplicas,
		Ready:         st.Ready,
		Message:       "ok",
	}, nil
}

// ExecInPod 容器内命令执行（应用诊断，可安装/配置等写操作，单命令不经 shell）
func (s *Server) ExecInPod(ctx context.Context, req *pb.ExecInPodRequest) (*pb.ExecInPodResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return nil, err
	}
	output, err := ex.ExecInPod(ctx, req.Namespace, req.PodName, req.Container, req.Command)
	if err != nil {
		return &pb.ExecInPodResponse{Success: false, Output: output, Error: err.Error()}, nil
	}
	return &pb.ExecInPodResponse{Success: true, Output: output}, nil
}

// ListDeploymentPods 列出 Deployment 所有 Pod 状态（验证修复是否真正恢复）
func (s *Server) ListDeploymentPods(ctx context.Context, req *pb.ListDeploymentPodsRequest) (*pb.ListDeploymentPodsResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.ListDeploymentPodsResponse{Success: false, Message: err.Error()}, nil
	}
	pods, allReady, err := ex.ListDeploymentPods(ctx, req.Namespace, req.DeploymentName)
	if err != nil {
		return &pb.ListDeploymentPodsResponse{Success: false, Message: err.Error()}, nil
	}
	infos := make([]*pb.DeploymentPodInfo, 0, len(pods))
	for _, p := range pods {
		infos = append(infos, &pb.DeploymentPodInfo{
			Name: p.Name, Phase: p.Phase, Ready: p.Ready, RestartCount: p.RestartCount,
		})
	}
	return &pb.ListDeploymentPodsResponse{Success: true, Pods: infos, AllReady: allReady, Message: "ok"}, nil
}

// ListPodRestartInfo 列出集群全部 Pod 的重启信息（N5：重启原因 AI 诊断检测数据源）
func (s *Server) ListPodRestartInfo(ctx context.Context, req *pb.ListPodRestartInfoRequest) (*pb.ListPodRestartInfoResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.ListPodRestartInfoResponse{Success: false, Error: err.Error()}, nil
	}
	infos, err := ex.ListPodRestartInfo(ctx)
	if err != nil {
		return &pb.ListPodRestartInfoResponse{Success: false, Error: err.Error()}, nil
	}
	pods := make([]*pb.PodRestartInfo, 0, len(infos))
	for _, p := range infos {
		pi := &pb.PodRestartInfo{
			Namespace: p.Namespace, Name: p.Name, RestartCount: p.RestartCount,
			RestartedAt: p.RestartedAt, Phase: p.Phase, Ready: p.Ready,
		}
		for _, c := range p.Containers {
			pi.Containers = append(pi.Containers, &pb.ContainerRestartInfo{
				Name:                   c.Name,
				RestartCount:           c.RestartCount,
				LastTerminatedReason:   c.LastTerminatedReason,
				LastTerminatedExitCode: c.LastTerminatedExitCode,
				LastTerminatedMessage:  c.LastTerminatedMessage,
			})
		}
		pods = append(pods, pi)
	}
	return &pb.ListPodRestartInfoResponse{Success: true, Pods: pods}, nil
}

// GetPodLogs 查看 Pod 容器日志
func (s *Server) GetPodLogs(ctx context.Context, req *pb.GetPodLogsRequest) (*pb.GetPodLogsResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return nil, err
	}
	logs, err := ex.GetPodLogs(ctx, req.Namespace, req.PodName, req.Container, req.TailLines, req.Previous)
	if err != nil {
		return &pb.GetPodLogsResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.GetPodLogsResponse{Success: true, Logs: logs}, nil
}

// GetPodYaml 获取 Pod YAML
func (s *Server) GetPodYaml(ctx context.Context, req *pb.GetPodYamlRequest) (*pb.GetPodYamlResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return nil, err
	}
	y, err := ex.GetPodYaml(ctx, req.Namespace, req.PodName)
	if err != nil {
		return &pb.GetPodYamlResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.GetPodYamlResponse{Success: true, Yaml: y}, nil
}

// ApplyPodYaml 应用 Pod YAML
func (s *Server) ApplyPodYaml(ctx context.Context, req *pb.ApplyPodYamlRequest) (*pb.ApplyPodYamlResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return nil, err
	}
	if err := ex.ApplyPodYaml(ctx, req.Namespace, req.YamlContent); err != nil {
		return &pb.ApplyPodYamlResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.ApplyPodYamlResponse{Success: true, Message: "已应用"}, nil
}

// ListPods 命名空间 Pod 列表（含创建时间）
func (s *Server) ListPods(ctx context.Context, req *pb.ListPodsRequest) (*pb.ListPodsResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.ListPodsResponse{Success: false, Error: err.Error()}, nil
	}
	items, err := ex.ListPods(ctx, req.Namespace)
	if err != nil {
		return &pb.ListPodsResponse{Success: false, Error: err.Error()}, nil
	}
	pods := make([]*pb.PodBriefInfo, 0, len(items))
	for _, p := range items {
		pods = append(pods, &pb.PodBriefInfo{
			Namespace: p.Namespace, Name: p.Name, Phase: p.Phase, Ready: p.Ready,
			RestartCount: p.RestartCount, CreatedAt: p.CreatedAt, NodeName: p.NodeName, PodIp: p.PodIP,
			Containers: p.Containers, InitContainers: p.InitContainers,
			Status: p.Status, ReadyContainers: p.ReadyContainers, TotalContainers: p.TotalContainers,
		})
	}
	return &pb.ListPodsResponse{Success: true, Pods: pods}, nil
}

// GetPodEvents 单个 Pod 的 k8s Event
func (s *Server) GetPodEvents(ctx context.Context, req *pb.GetPodEventsRequest) (*pb.GetPodEventsResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.GetPodEventsResponse{Success: false, Error: err.Error()}, nil
	}
	items, err := ex.GetPodEvents(ctx, req.Namespace, req.PodName)
	if err != nil {
		return &pb.GetPodEventsResponse{Success: false, Error: err.Error()}, nil
	}
	events := make([]*pb.PodEventInfo, 0, len(items))
	for _, e := range items {
		events = append(events, &pb.PodEventInfo{
			Type: e.Type, Reason: e.Reason, Message: e.Message, Count: e.Count,
			FirstTimestamp: e.FirstTimestamp, LastTimestamp: e.LastTimestamp, Source: e.Source,
		})
	}
	return &pb.GetPodEventsResponse{Success: true, Events: events}, nil
}

// GetPodDetail Pod 详情（含容器组内容）
func (s *Server) GetPodDetail(ctx context.Context, req *pb.GetPodDetailRequest) (*pb.GetPodDetailResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.GetPodDetailResponse{Success: false, Error: err.Error()}, nil
	}
	d, err := ex.GetPodDetail(ctx, req.Namespace, req.PodName)
	if err != nil {
		return &pb.GetPodDetailResponse{Success: false, Error: err.Error()}, nil
	}
	conv := func(in []executor.ContainerDetail) []*pb.ContainerDetail {
		out := make([]*pb.ContainerDetail, 0, len(in))
		for _, c := range in {
			out = append(out, &pb.ContainerDetail{
				Name: c.Name, Image: c.Image, State: c.State, Ready: c.Ready,
				RestartCount: c.RestartCount, Reason: c.Reason,
				StartedAt: c.StartedAt, FinishedAt: c.FinishedAt, IsInit: c.IsInit,
				Mounts: convMounts(c.Mounts),
			})
		}
		return out
	}
	convVols := func(in []executor.VolumeInfo) []*pb.VolumeInfo {
		out := make([]*pb.VolumeInfo, 0, len(in))
		for _, v := range in {
			out = append(out, &pb.VolumeInfo{Name: v.Name, Type: v.Type, Source: v.Source, ReadOnly: v.ReadOnly})
		}
		return out
	}
	return &pb.GetPodDetailResponse{
		Success: true, Name: d.Name, Namespace: d.Namespace, Phase: d.Phase,
		Ready: d.Ready, RestartCount: d.RestartCount, CreatedAt: d.CreatedAt,
		NodeName: d.NodeName, PodIp: d.PodIP, HostIp: d.HostIP,
		Containers: conv(d.Containers), InitContainers: conv(d.InitContainers),
		Volumes: convVols(d.Volumes),
		OwnerKind: d.OwnerKind, OwnerName: d.OwnerName, OwnerNamespace: d.OwnerNamespace,
	}, nil
}

// convMounts 容器挂载转换
func convMounts(in []executor.VolumeMountInfo) []*pb.VolumeMountInfo {
	out := make([]*pb.VolumeMountInfo, 0, len(in))
	for _, m := range in {
		out = append(out, &pb.VolumeMountInfo{
			Name: m.Name, MountPath: m.MountPath, SubPath: m.SubPath,
			ReadOnly: m.ReadOnly, SubPathExpr: m.SubPathExpr, Propagation: m.Propagation,
		})
	}
	return out
}

// GetWorkloadMounts 工作负载 Pod 模板的挂载配置
func (s *Server) GetWorkloadMounts(ctx context.Context, req *pb.GetWorkloadMountsRequest) (*pb.GetWorkloadMountsResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.GetWorkloadMountsResponse{Success: false, Error: err.Error()}, nil
	}
	m, err := ex.GetWorkloadMounts(ctx, req.Kind, req.Namespace, req.Name)
	if err != nil {
		return &pb.GetWorkloadMountsResponse{Success: false, Error: err.Error()}, nil
	}
	vols := make([]*pb.VolumeInfo, 0, len(m.Volumes))
	for _, v := range m.Volumes {
		vols = append(vols, &pb.VolumeInfo{Name: v.Name, Type: v.Type, Source: v.Source, ReadOnly: v.ReadOnly})
	}
	conv := func(in []executor.ContainerMounts) []*pb.ContainerMounts {
		out := make([]*pb.ContainerMounts, 0, len(in))
		for _, c := range in {
			out = append(out, &pb.ContainerMounts{Name: c.Name, Mounts: convMounts(c.Mounts)})
		}
		return out
	}
	return &pb.GetWorkloadMountsResponse{
		Success: true, Volumes: vols,
		Containers: conv(m.Containers), InitContainers: conv(m.InitContainers),
	}, nil
}

// ListMiscResources 集群管理其它资源（服务/路由/ConfigMap/Secret/PVC）
func (s *Server) ListMiscResources(ctx context.Context, req *pb.ListMiscResourcesRequest) (*pb.ListMiscResourcesResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.ListMiscResourcesResponse{Success: false, Error: err.Error()}, nil
	}
	r, err := ex.ListMiscResources(ctx, req.Namespace)
	if err != nil {
		return &pb.ListMiscResourcesResponse{Success: false, Error: err.Error()}, nil
	}
	resp := &pb.ListMiscResourcesResponse{Success: true}
	for _, v := range r.Services {
		resp.Services = append(resp.Services, &pb.ServiceInfo{Namespace: v.Namespace, Name: v.Name, Type: v.Type, ClusterIp: v.ClusterIP, Ports: v.Ports, ExternalIp: v.ExternalIP})
	}
	for _, v := range r.Ingresses {
		resp.Ingresses = append(resp.Ingresses, &pb.IngressInfo{Namespace: v.Namespace, Name: v.Name, Hosts: v.Hosts, Class: v.Class, Address: v.Address})
	}
	for _, v := range r.ConfigMaps {
		resp.Configmaps = append(resp.Configmaps, &pb.ConfigMapInfo{Namespace: v.Namespace, Name: v.Name, DataCount: v.DataCount})
	}
	for _, v := range r.Secrets {
		resp.Secrets = append(resp.Secrets, &pb.SecretInfo{Namespace: v.Namespace, Name: v.Name, Type: v.Type, DataCount: v.DataCount})
	}
	for _, v := range r.PVCs {
		resp.Pvcs = append(resp.Pvcs, &pb.PvcInfo{Namespace: v.Namespace, Name: v.Name, Status: v.Status, Capacity: v.Capacity, StorageClass: v.StorageClass, VolumeName: v.VolumeName, AccessModes: v.AccessModes})
	}
	for _, v := range r.StorageClasses {
		resp.StorageClasses = append(resp.StorageClasses, &pb.StorageClassInfo{Name: v.Name, Provisioner: v.Provisioner, IsDefault: v.IsDefault})
	}
	return resp, nil
}

// DeleteResource 删除配置类对象（ConfigMap/Secret）
func (s *Server) DeleteResource(ctx context.Context, req *pb.DeleteResourceRequest) (*pb.DeleteResourceResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.DeleteResourceResponse{Success: false, Error: err.Error()}, nil
	}
	if err := ex.DeleteResource(ctx, req.Kind, req.Namespace, req.Name); err != nil {
		return &pb.DeleteResourceResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.DeleteResourceResponse{Success: true, Message: "已删除"}, nil
}

// GetWorkloadYaml 获取工作负载（整个服务）YAML
func (s *Server) GetWorkloadYaml(ctx context.Context, req *pb.GetWorkloadYamlRequest) (*pb.GetWorkloadYamlResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.GetWorkloadYamlResponse{Success: false, Error: err.Error()}, nil
	}
	y, err := ex.GetWorkloadYaml(ctx, req.Kind, req.Namespace, req.Name)
	if err != nil {
		return &pb.GetWorkloadYamlResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.GetWorkloadYamlResponse{Success: true, Yaml: y}, nil
}

// ApplyWorkloadYaml 应用工作负载（整个服务）YAML
func (s *Server) ApplyWorkloadYaml(ctx context.Context, req *pb.ApplyWorkloadYamlRequest) (*pb.ApplyWorkloadYamlResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.ApplyWorkloadYamlResponse{Success: false, Error: err.Error()}, nil
	}
	if err := ex.ApplyWorkloadYaml(ctx, req.Kind, req.Namespace, req.Name, req.YamlContent); err != nil {
		return &pb.ApplyWorkloadYamlResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.ApplyWorkloadYamlResponse{Success: true, Message: "已应用"}, nil
}

// SetupRBAC 云原生 RBAC 接入：能力探测 + 下发 ClusterRole + 自举 automation SA
func (s *Server) SetupRBAC(ctx context.Context, req *pb.SetupRBACRequest) (*pb.SetupRBACResponse, error) {
	client, err := s.manager.GetClient(ctx, uint(req.ClusterId))
	if err != nil {
		return &pb.SetupRBACResponse{Success: false, Error: "获取集群客户端失败: " + err.Error()}, nil
	}
	result := rbac.Setup(ctx, client.Clientset)
	s.logger.Info("RBAC 接入完成",
		zap.Int64("cluster", req.ClusterId),
		zap.Bool("rbac_capable", result.RBACCapable),
		zap.Bool("impersonate_capable", result.ImpersonateCapable),
		zap.Strings("cluster_roles", result.ClusterRoles),
		zap.Bool("sa_created", result.SACreated),
	)
	return &pb.SetupRBACResponse{
		Success:            result.RBACCapable,
		RbacCapable:        result.RBACCapable,
		ImpersonateCapable: result.ImpersonateCapable,
		ClusterRoles:       result.ClusterRoles,
		SaCreated:          result.SACreated,
		Error:              result.Error,
	}, nil
}

// ReconcileRoleBindings 项目/成员变更 → reconcile RoleBinding（绑组）
func (s *Server) ReconcileRoleBindings(ctx context.Context, req *pb.ReconcileRoleBindingsRequest) (*pb.ReconcileRoleBindingsResponse, error) {
	client, err := s.manager.GetClient(ctx, uint(req.ClusterId))
	if err != nil {
		return &pb.ReconcileRoleBindingsResponse{Success: false, Error: "获取集群客户端失败: " + err.Error()}, nil
	}
	bindings := make([]rbac.BindingSpec, 0, len(req.Bindings))
	for _, b := range req.Bindings {
		bindings = append(bindings, rbac.BindingSpec{
			Namespace:   b.Namespace,
			Group:       b.Group,
			ClusterRole: b.ClusterRole,
		})
	}
	result := rbac.ReconcileRoleBindings(ctx, client.Clientset, bindings)
	s.logger.Info("RoleBinding reconcile 完成",
		zap.Int64("cluster", req.ClusterId),
		zap.Int("created", result.Created),
		zap.Int("updated", result.Updated),
		zap.Int("deleted", result.Deleted),
		zap.String("error", result.Error),
	)
	return &pb.ReconcileRoleBindingsResponse{
		Success: result.Error == "",
		Created: int32(result.Created),
		Updated: int32(result.Updated),
		Deleted: int32(result.Deleted),
		Error:   result.Error,
	}, nil
}

func errStr(err error) string {
	if err != nil {
		return err.Error()
	}
	return ""
}

// ============ 工作负载运维（专项 D） ============

// ListWorkloads 列出工作负载（无状态/有状态/守护集/定时任务）
func (s *Server) ListWorkloads(ctx context.Context, req *pb.ListWorkloadsRequest) (*pb.ListWorkloadsResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.ListWorkloadsResponse{Success: false, Error: err.Error()}, nil
	}
	items, err := ex.ListWorkloads(ctx, req.Namespace)
	if err != nil {
		return &pb.ListWorkloadsResponse{Success: false, Error: err.Error()}, nil
	}
	wls := make([]*pb.WorkloadInfo, 0, len(items))
	for _, w := range items {
		wls = append(wls, &pb.WorkloadInfo{Kind: w.Kind, Name: w.Name, Namespace: w.Namespace, Replicas: w.Replicas, ReadyReplicas: w.ReadyReplicas})
	}
	return &pb.ListWorkloadsResponse{Success: true, Workloads: wls}, nil
}

// ScaleWorkload 副本增减
func (s *Server) ScaleWorkload(ctx context.Context, req *pb.ScaleWorkloadRequest) (*pb.ScaleWorkloadResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.ScaleWorkloadResponse{Success: false, Error: err.Error()}, nil
	}
	msg, err := ex.ScaleWorkload(ctx, req.Kind, req.Namespace, req.Name, req.Replicas)
	if err != nil {
		return &pb.ScaleWorkloadResponse{Success: false, Message: msg, Error: err.Error()}, nil
	}
	return &pb.ScaleWorkloadResponse{Success: true, Message: msg}, nil
}

// RolloutWorkload 回滚/重启
func (s *Server) RolloutWorkload(ctx context.Context, req *pb.RolloutWorkloadRequest) (*pb.RolloutWorkloadResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.RolloutWorkloadResponse{Success: false, Error: err.Error()}, nil
	}
	msg, err := ex.RolloutWorkload(ctx, req.Kind, req.Namespace, req.Name, req.Action, req.Revision)
	if err != nil {
		return &pb.RolloutWorkloadResponse{Success: false, Message: msg, Error: err.Error()}, nil
	}
	return &pb.RolloutWorkloadResponse{Success: true, Message: msg}, nil
}

// RolloutHistory 回滚历史
func (s *Server) RolloutHistory(ctx context.Context, req *pb.RolloutHistoryRequest) (*pb.RolloutHistoryResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.RolloutHistoryResponse{Success: false, Error: err.Error()}, nil
	}
	revs, err := ex.RolloutHistory(ctx, req.Namespace, req.Name)
	if err != nil {
		return &pb.RolloutHistoryResponse{Success: false, Error: err.Error()}, nil
	}
	return &pb.RolloutHistoryResponse{Success: true, Revisions: revs}, nil
}

// UpdateProbe 探针便捷编辑
func (s *Server) UpdateProbe(ctx context.Context, req *pb.UpdateProbeRequest) (*pb.UpdateProbeResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.UpdateProbeResponse{Success: false, Error: err.Error()}, nil
	}
	msg, err := ex.UpdateProbe(ctx, req.Namespace, req.Name, req.Container, executor.ProbeSpec{
		ProbeType:           req.ProbeType,
		ProbeKind:           req.ProbeKind,
		Path:                req.Path,
		Port:                req.Port,
		Command:             req.Command,
		InitialDelaySeconds: req.InitialDelaySeconds,
		PeriodSeconds:       req.PeriodSeconds,
		TimeoutSeconds:      req.TimeoutSeconds,
		FailureThreshold:    req.FailureThreshold,
		SuccessThreshold:    req.SuccessThreshold,
	})
	if err != nil {
		return &pb.UpdateProbeResponse{Success: false, Message: msg, Error: err.Error()}, nil
	}
	return &pb.UpdateProbeResponse{Success: true, Message: msg}, nil
}

// RecreateWorkload 重新创建服务
func (s *Server) RecreateWorkload(ctx context.Context, req *pb.RecreateWorkloadRequest) (*pb.RecreateWorkloadResponse, error) {
	ex, err := s.getExecutor(ctx, req.ClusterId)
	if err != nil {
		return &pb.RecreateWorkloadResponse{Success: false, Error: err.Error()}, nil
	}
	msg, err := ex.RecreateWorkload(ctx, req.Kind, req.Namespace, req.Name)
	if err != nil {
		return &pb.RecreateWorkloadResponse{Success: false, Message: msg, Error: err.Error()}, nil
	}
	return &pb.RecreateWorkloadResponse{Success: true, Message: msg}, nil
}

// ExecPodStream 交互式终端（TTY 双向流）
func (s *Server) ExecPodStream(stream pb.ExecutorService_ExecPodStreamServer) error {
	ctx := stream.Context()

	// 首帧：会话参数
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	clusterID := first.ClusterId
	namespace := first.Namespace
	podName := first.PodName
	container := first.Container
	tty := first.Tty

	ex, err := s.getExecutor(ctx, clusterID)
	if err != nil {
		_ = stream.Send(&pb.ExecPodStreamFrame{Closed: true, ExitCode: -1, Error: err.Error()})
		return nil
	}

	sess, err := ex.StartExecStream(ctx, namespace, podName, container, tty)
	if err != nil {
		_ = stream.Send(&pb.ExecPodStreamFrame{Closed: true, ExitCode: -1, Error: err.Error()})
		return nil
	}
	defer sess.Close()

	// 若首帧已带 stdin，写入
	if len(first.Stdin) > 0 {
		_, _ = sess.Write(first.Stdin)
	}

	// 输出转发 goroutine：stdout/stderr → stream
	outDone := make(chan struct{})
	go func() {
		defer close(outDone)
		buf := make([]byte, 4096)
		for {
			n, rerr := sess.ReadStdout(buf)
			if n > 0 {
				if serr := stream.Send(&pb.ExecPodStreamFrame{Stdout: buf[:n]}); serr != nil {
					return
				}
			}
			if rerr != nil {
				return
			}
		}
	}()

	// stderr 转发 goroutine（非 TTY 才独立）
	errDone := make(chan struct{})
	if !tty {
		go func() {
			defer close(errDone)
			buf := make([]byte, 4096)
			for {
				n, rerr := sess.ReadStderr(buf)
				if n > 0 {
					if serr := stream.Send(&pb.ExecPodStreamFrame{Stderr: buf[:n]}); serr != nil {
						return
					}
				}
				if rerr != nil {
					return
				}
			}
		}()
	} else {
		close(errDone)
	}

	// 输入接收 goroutine：stream → stdin/resize
	inputDone := make(chan struct{})
	go func() {
		defer close(inputDone)
		for {
			frame, rerr := stream.Recv()
			if rerr != nil {
				sess.CloseStdin()
				return
			}
			if len(frame.Stdin) > 0 {
				if _, werr := sess.Write(frame.Stdin); werr != nil {
					return
				}
			}
			if frame.Rows > 0 && frame.Cols > 0 {
				sess.Resize(frame.Rows, frame.Cols)
			}
		}
	}()

	// 等待会话结束（带静默超时：无输入 5 分钟自动断开）
	idleCheck := time.NewTicker(30 * time.Second)
	defer idleCheck.Stop()
	var execErr error
	idleClosed := false
waitLoop:
	for {
		select {
		case execErr = <-sess.Done():
			break waitLoop
		case <-idleCheck.C:
			if sess.Idle() >= executor.IdleTimeout {
				s.logger.Info("exec 静默超时，自动断开", zap.Int64("cluster", clusterID), zap.String("pod", podName))
				idleClosed = true
				sess.Close()
				execErr = fmt.Errorf("idle timeout")
				break waitLoop
			}
		}
	}
	sess.CloseStdin()
	<-outDone
	<-errDone

	if execErr != nil && !idleClosed {
		s.logger.Warn("exec 会话结束（含错误）", zap.Int64("cluster", clusterID), zap.String("pod", podName), zap.Error(execErr))
	}
	code := executor.ExitCode(execErr)
	if idleClosed {
		code = 130 // 模拟 Ctrl+C 退出码，前端提示静默断开
	}
	final := &pb.ExecPodStreamFrame{Closed: true, ExitCode: int32(code)}
	if execErr != nil && !idleClosed {
		final.Error = execErr.Error() // 错误透传，前端展示原因（如镜像无 shell / 容器未运行）
	}
	_ = stream.Send(final)
	return nil
}

var _ = json.Valid
var _ = time.Second
