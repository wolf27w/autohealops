// k8s-executor 入口：配置 → 日志 → 数据库 → 客户端池 → watch → 调度器 → gRPC server
package main

import (
	"context"
	"fmt"
	"net"
	"os"
	"os/signal"
	"syscall"

	"github.com/api-ops-platform/k8s-executor/internal/admission"
	"github.com/api-ops-platform/k8s-executor/internal/config"
	"github.com/api-ops-platform/k8s-executor/internal/database"
	execgrpc "github.com/api-ops-platform/k8s-executor/internal/grpc"
	"github.com/api-ops-platform/k8s-executor/internal/health"
	"github.com/api-ops-platform/k8s-executor/internal/k8sclient"
	"github.com/api-ops-platform/k8s-executor/internal/metrics"
	"github.com/api-ops-platform/k8s-executor/internal/quota"
	"github.com/api-ops-platform/k8s-executor/internal/script"
	"github.com/api-ops-platform/k8s-executor/internal/watcher"
	pb "github.com/api-ops-platform/k8s-executor/proto"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"
)

func main() {
	// 1. 配置
	cfg, err := config.Load()
	if err != nil {
		fmt.Fprintf(os.Stderr, "加载配置失败: %v\n", err)
		os.Exit(1)
	}

	// 2. zap logger（JSON 结构化）
	logger := newLogger(cfg.LogLevel)
	defer logger.Sync()

	// 3. 数据库（不迁移，只校验连接）
	if err := database.Init(cfg.DatabaseURL); err != nil {
		logger.Fatal("数据库连接失败", zap.Error(err))
	}

	// 4. 加密密钥
	if err := k8sclient.SetEncryptionKey(cfg.EncryptionKey); err != nil {
		logger.Fatal("加密密钥配置错误", zap.Error(err))
	}

	// 5. 多集群客户端池（从数据库加载 kubeconfig）
	manager := k8sclient.NewManager(loadKubeconfig)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// 6. watch 管理器 + 启动所有集群监听
	watcher.InitAlertNotify(cfg.APIURL, cfg.InternalToken, logger)
	watchManager := watcher.NewWatchManager(manager, logger)
	if err := watchManager.StartAllWatches(ctx); err != nil {
		logger.Warn("启动 watch 失败", zap.Error(err))
	}

	// 7. 健康检查 + 配额采集调度器
	healthChecker := health.NewChecker(manager, logger)
	healthChecker.StartHealthCheckScheduler(ctx)
	quotaCollector := quota.NewCollector(manager, logger)
	quotaCollector.StartQuotaCollectScheduler(ctx)

	// 7.5 Pod 指标采集（CPU/内存/网络，每分钟全量，保留 3 天）
	metricsCollector := metrics.NewCollector(manager, logger)
	metricsCollector.StartCollectScheduler(ctx)

	// 8. 脚本执行器
	scriptRunner := script.NewRunner(manager, logger)

	// 9. gRPC server
	grpcServer := grpc.NewServer()
	server := execgrpc.NewServer(cfg, manager, watchManager, healthChecker, quotaCollector, scriptRunner, logger)
	pb.RegisterExecutorServiceServer(grpcServer, server)
	reflection.Register(grpcServer)

	// 9.5 资源治理·准入 webhook（可选，默认关）
	var admissionSrv *admission.Server
	if cfg.AdmissionEnabled && cfg.AdmissionAddr != "" {
		admissionSrv = admission.NewServer(cfg.AdmissionClusterID, cfg.AdmissionAddr, cfg.AdmissionCertFile, cfg.AdmissionKeyFile, logger)
		if err := admissionSrv.Start(ctx); err != nil {
			logger.Warn("准入 webhook 启动失败", zap.Error(err))
		}
	}

	lis, err := net.Listen("tcp", ":"+cfg.GRPCPort)
	if err != nil {
		logger.Fatal("gRPC 监听失败", zap.String("port", cfg.GRPCPort), zap.Error(err))
	}

	logger.Info("k8s-executor 启动", zap.String("grpc_port", cfg.GRPCPort))

	go func() {
		if err := grpcServer.Serve(lis); err != nil {
			logger.Error("gRPC server 退出", zap.Error(err))
		}
	}()

	// 10. 优雅退出
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	logger.Info("收到退出信号，优雅关闭...")
	grpcServer.GracefulStop()
	if admissionSrv != nil {
		admissionSrv.Stop()
	}
	cancel()
}

// loadKubeconfig 从数据库加载并解密 kubeconfig（供 k8sclient.Manager 回调）
func loadKubeconfig(ctx context.Context, clusterID uint) (string, error) {
	var cluster database.Cluster
	if err := database.GetDB().First(&cluster, clusterID).Error; err != nil {
		return "", fmt.Errorf("查询集群 %d 失败: %w", clusterID, err)
	}
	return k8sclient.DecryptKubeconfig(cluster.KubeconfigEncrypted)
}

// newLogger 构建 JSON 结构化 zap logger
func newLogger(level string) *zap.Logger {
	var lvl zapcore.Level
	switch level {
	case "debug":
		lvl = zapcore.DebugLevel
	case "warn":
		lvl = zapcore.WarnLevel
	case "error":
		lvl = zapcore.ErrorLevel
	default:
		lvl = zapcore.InfoLevel
	}
	cfg := zap.NewProductionConfig()
	cfg.Level = zap.NewAtomicLevelAt(lvl)
	cfg.Encoding = "json"
	cfg.EncoderConfig.TimeKey = "ts"
	cfg.EncoderConfig.MessageKey = "msg"
	cfg.EncoderConfig.LevelKey = "level"
	logger, _ := cfg.Build()
	return logger
}
