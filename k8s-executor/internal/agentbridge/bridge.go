// Package agentbridge —— agent 侧「集群内 executor」桥（P1：全方法反向代理）
//
// 背景：agent 模式原先只实现了 ExecuteCommand / GetPodLogs 两个方法，其余 30+ 个 RPC
// 一律返回「agent 暂不支持」，导致 agent 接入的集群在平台上「连得上但用不了」
// （容器诊断/节点管理/镜像更新/工作负载 YAML 全部不可用）。
//
// 做法：不再逐方法写 switch —— agent 在进程内起一个**真实的 ExecutorService gRPC server**
// （复用 internal/grpc.Server 全部实现），把隧道请求按「方法名 + 原始 protobuf 字节」原样转发进去。
// 这样 33 个 unary 方法全部自动可用，将来平台侧加新 RPC 也不用改 agent。
//
// 传输：本机 loopback 监听 + 自定义 raw codec（客户端收发原始字节；服务端用标准 proto
// 反序列化到具体 message），无需知道每个方法的请求/响应 Go 类型。
//
// 暂不支持：3 个流式 RPC（ExecutePlan / BatchExecute / ExecPodStream）—— 隧道是请求/响应模型，
// 流式需要多帧协议，留作 P1 后续（会返回明确错误，而不是静默不可用）。
package agentbridge

import (
	"context"
	"fmt"
	"net"
	"time"

	"github.com/autohealops/k8s-executor/internal/config"
	"github.com/autohealops/k8s-executor/internal/health"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"github.com/autohealops/k8s-executor/internal/quota"
	"github.com/autohealops/k8s-executor/internal/script"
	"github.com/autohealops/k8s-executor/internal/watcher"
	grpcsrv "github.com/autohealops/k8s-executor/internal/grpc"
	pb "github.com/autohealops/k8s-executor/proto"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	clientcmdapi "k8s.io/client-go/tools/clientcmd/api"
)

// 流式 RPC：隧道当前是请求/响应，暂不支持
var streamingMethods = map[string]bool{
	"ExecutePlan":   true,
	"BatchExecute":  true,
	"ExecPodStream": true,
}

// Bridge agent 侧进程内 executor（隧道请求 → 本地 ExecutorService）
type Bridge struct {
	clusterID uint
	conn      *grpc.ClientConn
	server    *grpc.Server
	lis       net.Listener
	logger    *zap.Logger
}

// kubeconfigFromRest 由 rest.Config 合成一份单集群 kubeconfig（给 Manager 用；
// in-cluster 模式没有 kubeconfig 文件，只能这样回填 SA token/CA）。
func kubeconfigFromRest(rc *rest.Config) (string, error) {
	const name = "agent"
	cfg := clientcmdapi.NewConfig()
	cfg.Clusters[name] = &clientcmdapi.Cluster{
		Server:                   rc.Host,
		CertificateAuthorityData: rc.CAData,
		InsecureSkipTLSVerify:    rc.Insecure,
	}
	cfg.AuthInfos[name] = &clientcmdapi.AuthInfo{
		Token:                 rc.BearerToken,
		TokenFile:             rc.BearerTokenFile,
		ClientCertificateData: rc.CertData,
		ClientKeyData:         rc.KeyData,
		Username:              rc.Username,
		Password:              rc.Password,
	}
	cfg.Contexts[name] = &clientcmdapi.Context{Cluster: name, AuthInfo: name}
	cfg.CurrentContext = name
	out, err := clientcmd.Write(*cfg)
	if err != nil {
		return "", err
	}
	return string(out), nil
}

// New 创建桥并启动进程内 server（失败即返回错误，agent 启动时调用一次）
func New(clusterID uint, rc *rest.Config, cs *kubernetes.Clientset, whitelist []string, logger *zap.Logger) (*Bridge, error) {
	kubeJSON, err := kubeconfigFromRest(rc)
	if err != nil {
		return nil, fmt.Errorf("合成 kubeconfig 失败: %w", err)
	}
	// 客户端池：不论请求里的 cluster_id 是多少，都返回本集群（agent 只服务一个集群）
	manager := k8sclient.NewManager(func(_ context.Context, id uint) (string, error) {
		logger.Debug("agent 桥加载 kubeconfig", zap.Uint("request_cluster_id", id), zap.Int("bytes", len(kubeJSON)))
		return kubeJSON, nil
	})

	cfg := &config.Config{CommandWhitelist: whitelist}
	impl := grpcsrv.NewServer(
		cfg, manager,
		watcher.NewWatchManager(manager, logger),
		health.NewChecker(manager, logger),
		quota.NewCollector(manager, logger),
		script.NewRunner(manager, logger),
		logger,
	)

	svc := &bridgeService{Server: impl, clientset: cs, logger: logger}
	srv := grpc.NewServer(grpc.ForceServerCodec(rawServerCodec{}))
	pb.RegisterExecutorServiceServer(srv, svc)

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("进程内监听失败: %w", err)
	}
	go func() {
		if err := srv.Serve(lis); err != nil {
			logger.Warn("agent 进程内 executor 退出", zap.Error(err))
		}
	}()

	conn, err := grpc.NewClient(lis.Addr().String(),
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithDefaultCallOptions(grpc.ForceCodec(rawClientCodec{})),
	)
	if err != nil {
		srv.Stop()
		return nil, fmt.Errorf("连接进程内 executor 失败: %w", err)
	}

	logger.Info("agent 进程内 executor 已就绪", zap.Uint("cluster", clusterID), zap.String("addr", lis.Addr().String()))
	return &Bridge{clusterID: clusterID, conn: conn, server: srv, lis: lis, logger: logger}, nil
}

// Close 关闭桥
func (b *Bridge) Close() {
	if b.conn != nil {
		_ = b.conn.Close()
	}
	if b.server != nil {
		b.server.Stop()
	}
}

// Call 把一次隧道请求转发给本地 ExecutorService，返回响应原始 protobuf 字节。
// method 就是 proto 里的 rpc 名（ExecuteCommand / ListPods / GetPodDetail ...）。
func (b *Bridge) Call(ctx context.Context, method string, payload []byte) ([]byte, error) {
	if method == "" {
		return nil, fmt.Errorf("method 为空")
	}
	if streamingMethods[method] {
		return nil, fmt.Errorf("流式方法 %s 暂不支持经 agent 隧道调用（P1 后续）", method)
	}
	if _, ok := ctx.Deadline(); !ok {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, 60*time.Second)
		defer cancel()
	}
	out := &rawMessage{}
	if err := b.conn.Invoke(ctx, "/executor.ExecutorService/"+method, &rawMessage{data: payload}, out); err != nil {
		return nil, err
	}
	return out.data, nil
}

// ==================== raw codec ====================

// rawMessage 隧道里的原始 protobuf 字节容器
type rawMessage struct{ data []byte }

// rawClientCodec 客户端侧：收发原始字节，不关心具体 message 类型
type rawClientCodec struct{}

func (rawClientCodec) Marshal(v any) ([]byte, error) {
	m, ok := v.(*rawMessage)
	if !ok {
		return nil, fmt.Errorf("rawClientCodec: 期望 *rawMessage，得到 %T", v)
	}
	return m.data, nil
}

func (rawClientCodec) Unmarshal(data []byte, v any) error {
	m, ok := v.(*rawMessage)
	if !ok {
		return fmt.Errorf("rawClientCodec: 期望 *rawMessage，得到 %T", v)
	}
	m.data = append([]byte(nil), data...)
	return nil
}

func (rawClientCodec) Name() string { return "raw" }

// rawServerCodec 服务端侧：入参/出参仍是真实 protobuf message（由 ForceServerCodec 注入）
type rawServerCodec struct{}

func (rawServerCodec) Marshal(v any) ([]byte, error) {
	pm, ok := v.(proto.Message)
	if !ok {
		return nil, fmt.Errorf("rawServerCodec: 期望 proto.Message，得到 %T", v)
	}
	return proto.Marshal(pm)
}

func (rawServerCodec) Unmarshal(data []byte, v any) error {
	pm, ok := v.(proto.Message)
	if !ok {
		return fmt.Errorf("rawServerCodec: 期望 proto.Message，得到 %T", v)
	}
	return proto.Unmarshal(data, pm)
}

func (rawServerCodec) Name() string { return "raw" }

// ==================== agent 里无数据库的 RPC 覆盖 ====================
//
// internal/grpc.Server 的少数方法会写平台库（health 写 clusters.status、quota 写 quota_snapshots）。
// agent 部署在客户集群内、没有平台数据库，直接调用会 gorm nil panic → 这里覆盖掉，改成纯集群内实现。

type bridgeService struct {
	*grpcsrv.Server
	clientset *kubernetes.Clientset
	logger    *zap.Logger
}

// HealthCheck 集群连通性 + 版本（不写库；平台侧由 agents 会话/心跳自行维护状态）
func (s *bridgeService) HealthCheck(ctx context.Context, _ *pb.HealthCheckRequest) (*pb.HealthCheckResponse, error) {
	v, err := s.clientset.Discovery().ServerVersion()
	if err != nil {
		return &pb.HealthCheckResponse{Success: false, Status: "unreachable", Error: err.Error()}, nil
	}
	return &pb.HealthCheckResponse{Success: true, Status: "healthy", Version: v.GitVersion}, nil
}

// CollectQuota 配额快照需要写平台库 → agent 模式不采集（平台侧 executor 或后续 P3 补齐）
func (s *bridgeService) CollectQuota(_ context.Context, _ *pb.CollectQuotaRequest) (*pb.CollectQuotaResponse, error) {
	return &pb.CollectQuotaResponse{Success: false,
		Message: "agent 模式：配额快照由平台侧采集（agent 无平台数据库）"}, nil
}
