// k8s-agent：R12 agent 主动外连接入模式（单 Deployment）。
// agent 部署在被管集群内，主动连平台 gateway 建立 gRPC 双向隧道；
// 平台把 executor 请求（method + 序列化 payload）经隧道下发，agent 在集群内执行后回传响应。
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/autohealops/k8s-executor/internal/agentbridge"
	"github.com/autohealops/k8s-executor/internal/agentdb"
	"github.com/autohealops/k8s-executor/internal/eventstore"
	"github.com/autohealops/k8s-executor/internal/hostagent"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"github.com/autohealops/k8s-executor/internal/metrics"
	"github.com/autohealops/k8s-executor/internal/prometheus"
	"github.com/autohealops/k8s-executor/internal/quota"
	"github.com/autohealops/k8s-executor/internal/watcher"
	pb "github.com/autohealops/k8s-executor/proto"
	agentpb "github.com/autohealops/k8s-executor/proto/agent"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/keepalive"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

const defaultWhitelist = "get,list,describe,logs,top,explain,rollout,scale,set,apply,patch,delete,exec,create,edit,cordon,uncordon,drain,label,taint"

// version 由构建时注入：-ldflags "-X main.version=<tag>"
var version = "dev"

// featureCapabilities：agent 支持的「字段级特性」标记（R59 能力协商）。
// 方法级能力（如 ListHPA）由 executor.proto 的服务描述自动派生（buildCapabilities）；
// 这里维护的是「方法名没变、但入参语义扩展」的特性，例如：
//   log_time_range = GetPodLogs 支持 timestamps/since_seconds 时间段过滤（R55）。
// 老 agent 二进制里没有这些标记 → 平台按能力缺失提示升级，不阻断现有功能。
var featureCapabilities = []string{
	"log_time_range",
}

// buildCapabilities 汇总 agent 的能力列表：ExecutorService 全部方法名 + 字段级特性标记。
// 方法名从编译期生成的服务描述派生（agent 内嵌完整 ExecutorService，支持哪些方法由二进制决定），
// 这样「平台新增 RPC → 重新打包 agent」后能力列表自动更新，无需手工维护。
func buildCapabilities() []string {
	caps := make([]string, 0, len(pb.ExecutorService_ServiceDesc.Methods)+len(pb.ExecutorService_ServiceDesc.Streams)+len(featureCapabilities))
	for _, m := range pb.ExecutorService_ServiceDesc.Methods {
		caps = append(caps, m.MethodName)
	}
	for _, s := range pb.ExecutorService_ServiceDesc.Streams {
		caps = append(caps, s.StreamName)
	}
	caps = append(caps, featureCapabilities...)
	return caps
}

func buildRestConfig(kubeconfig string) (*rest.Config, error) {
	if kubeconfig != "" {
		return clientcmd.BuildConfigFromFlags("", kubeconfig)
	}
	if cfg, err := rest.InClusterConfig(); err == nil {
		return cfg, nil
	}
	// 回退本地 kubeconfig（测试用）
	home, _ := os.UserHomeDir()
	p := filepath.Join(home, ".kube", "config")
	return clientcmd.BuildConfigFromFlags("", p)
}

func main() {
	platform := flag.String("platform", "127.0.0.1:9095", "平台 gateway 地址 host:port")
	clusterID := flag.Int64("cluster-id", 0, "集群 id（注册用）")
	token := flag.String("token", "", "接入令牌")
	kubeconfig := flag.String("kubeconfig", "", "kubeconfig 路径（空=in-cluster，回退 ~/.kube/config）")
	watch := flag.Bool("watch", true, "是否在 agent 内跑告警检测（Pod/Node watcher → 经隧道上报平台）")
	whitelist := flag.String("whitelist", defaultWhitelist, "命令白名单")
	caFile := flag.String("ca-file", "", "平台 CA 证书（mTLS；为空则明文，仅调试用）")
	certFile := flag.String("cert-file", "", "agent 客户端证书（mTLS）")
	keyFile := flag.String("key-file", "", "agent 客户端私钥（mTLS）")
	serverName := flag.String("server-name", "", "TLS SNI/校验用主机名（默认取 -platform 的 host）")
	dbConfig := flag.String("db-config", "", "数据库配置文件（JSON；也可用 AGENT_DB_CONFIG_FILE / AGENT_DB_CONFIG）")
	dbInterval := flag.Duration("db-interval", 10*time.Second, "数据库指标采集间隔")
	dbSlowInterval := flag.Duration("db-slow-interval", 60*time.Second, "慢 SQL 采集间隔（内部子循环）")
	hostConfig := flag.String("host-config", "", "主机代采配置 hosts.json 路径（空=不启用）")
	sshKey := flag.String("ssh-key", "", "主机代采 SSH 私钥路径")
	// R14 双接入：push=主动连平台（反向隧道，默认）/ pull=平台直连拉取（agent 只监听）
	mode := flag.String("mode", "push", "接入模式：push=主动连平台 / pull=平台拉取")
	listen := flag.String("listen", ":9100", "pull 模式监听地址（如 :9100）")
	pullToken := flag.String("pull-token", "", "pull 模式访问令牌（与平台侧配置一致；空=不校验，仅限内网调试）")
	pullCert := flag.String("pull-cert", "", "pull 模式服务端 TLS 证书（与 -pull-key 一起提供才启用 TLS）")
	pullKey := flag.String("pull-key", "", "pull 模式服务端 TLS 私钥")
	pullCA := flag.String("pull-ca", "", "pull 模式客户端 CA（配置后要求并校验客户端证书 = mTLS）")
	showVersion := flag.Bool("version", false, "打印版本后退出")
	flag.Parse()

	if *showVersion {
		fmt.Println(version)
		return
	}

	if *clusterID == 0 {
		fmt.Fprintln(os.Stderr, "必须指定 -cluster-id")
		os.Exit(1)
	}

	hosts, err := hostagent.LoadHosts(*hostConfig)
	if err != nil {
		fmt.Fprintf(os.Stderr, "加载主机代采配置失败: %v\n", err)
		os.Exit(1)
	}

	rc, err := buildRestConfig(*kubeconfig)
	if err != nil {
		fmt.Fprintf(os.Stderr, "构建 kube 配置失败: %v\n", err)
		os.Exit(1)
	}
	cs, err := kubernetes.NewForConfig(rc)
	if err != nil {
		fmt.Fprintf(os.Stderr, "创建 clientset 失败: %v\n", err)
		os.Exit(1)
	}
	// 指标采集客户端（metrics-server + kubelet summary）：agent 在集群内采集，经隧道上报平台落库
	mc, err := k8sclient.NewClusterClientFromRest(uint(*clusterID), rc)
	if err != nil {
		fmt.Fprintf(os.Stderr, "构建指标客户端失败: %v\n", err)
		os.Exit(1)
	}
	// P1：agent 进程内起一个真实的 ExecutorService，隧道请求按方法名原样转发 →
	// 33 个 unary RPC 全部可用（原先只有 ExecuteCommand/GetPodLogs 两个）
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	// 数据库配置（R6）：账密只在集群内，平台不保存；未配置则不采集
	dbCfgs, err := agentdb.LoadConfigs(*dbConfig)
	if err != nil {
		fmt.Fprintf(os.Stderr, "数据库配置解析失败: %v\n", err)
		os.Exit(1)
	}
	agentdb.SetConfigPath(*dbConfig) // 供「按 key 查询」复用同一份配置
	if len(dbCfgs) > 0 {
		fmt.Printf("[agent] 已加载 %d 个数据库配置（采集间隔 %s，慢SQL %s；凭据仅在本集群）\n",
			len(dbCfgs), dbInterval.String(), dbSlowInterval.String())
	}
	bridge, err := agentbridge.New(uint(*clusterID), rc, cs, splitCSV(*whitelist), logger)
	if err != nil {
		fmt.Fprintf(os.Stderr, "初始化 agent 桥失败: %v\n", err)
		os.Exit(1)
	}
	defer bridge.Close()

	// 连平台 gateway（反向隧道）：默认 mTLS；未给证书时退回明文并告警（仅本地调试）
	dialOpts := []grpc.DialOption{}
	if *caFile != "" && *certFile != "" && *keyFile != "" {
		caPEM, e1 := os.ReadFile(*caFile)
		if e1 != nil {
			fmt.Fprintf(os.Stderr, "读取 CA 失败: %v\n", e1)
			os.Exit(1)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(caPEM) {
			fmt.Fprintln(os.Stderr, "CA 证书解析失败")
			os.Exit(1)
		}
		clientCert, e2 := tls.LoadX509KeyPair(*certFile, *keyFile)
		if e2 != nil {
			fmt.Fprintf(os.Stderr, "加载客户端证书失败: %v\n", e2)
			os.Exit(1)
		}
		tcfg := &tls.Config{RootCAs: pool, Certificates: []tls.Certificate{clientCert}, MinVersion: tls.VersionTLS12}
		if *serverName != "" {
			tcfg.ServerName = *serverName
		}
		dialOpts = append(dialOpts, grpc.WithTransportCredentials(credentials.NewTLS(tcfg)))
	} else {
		fmt.Fprintln(os.Stderr, "[agent] 警告：未配置 -ca-file/-cert-file/-key-file，隧道为明文（仅限本地调试）")
		dialOpts = append(dialOpts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	}
	// 2026-11-23：开启 gRPC keepalive。平台重启/网络闪断时 TCP 可能半开，
	// 没有 keepalive 时客户端永远等不到错误 → 不触发下面的 3s 重连，表现为
	// “平台显示已接入，但随后所有 RPC 都不通、只能重启 agent Pod”。
	dialOpts = append(dialOpts, grpc.WithKeepaliveParams(keepalive.ClientParameters{
		Time:                30 * time.Second,
		Timeout:             10 * time.Second,
		PermitWithoutStream: true,
	}))

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		c := make(chan os.Signal, 1)
		signal.Notify(c, syscall.SIGINT, syscall.SIGTERM)
		<-c
		cancel()
	}()

	// R14 pull 模式：不连平台，直接监听 gRPC 端口等平台来拉取。
	// 注意：推送型能力（watcher 告警上报 / 指标 / 事件 / 配额定时上报）依赖隧道，pull 模式下不启用。
	if strings.EqualFold(strings.TrimSpace(*mode), "pull") {
		fmt.Printf("[agent] pull 模式启动：监听 %s（令牌 %v，TLS %v，mTLS %v）\n",
			*listen, *pullToken != "", *pullCert != "" && *pullKey != "", *pullCA != "")
		if err := bridge.ServePull(*listen, *pullToken, *pullCert, *pullKey, *pullCA); err != nil {
			fmt.Fprintf(os.Stderr, "[agent] pull 服务退出: %v\n", err)
			os.Exit(1)
		}
		return
	}

	conn, err := grpc.NewClient(*platform, dialOpts...)
	if err != nil {
		fmt.Fprintf(os.Stderr, "连接平台失败: %v\n", err)
		os.Exit(1)
	}
	defer conn.Close()

	// 断线自动重连
	for ctx.Err() == nil {
		if err := runTunnel(ctx, conn, *clusterID, *token, bridge, cs, mc, *watch, dbCfgs, *dbInterval, *dbSlowInterval, hosts, *sshKey, logger); err != nil {
			fmt.Fprintf(os.Stderr, "[agent] 隧道断开: %v，3s 后重连\n", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(3 * time.Second):
		}
	}
}

func runTunnel(ctx context.Context, conn *grpc.ClientConn, clusterID int64, token string,
	bridge *agentbridge.Bridge, cs *kubernetes.Clientset, mc *k8sclient.ClusterClient, enableWatch bool,
	dbCfgs []agentdb.DBConfig, dbInterval, dbSlowInterval time.Duration,
	hosts []hostagent.HostConfig, sshKeyPath string, logger *zap.Logger) error {
	client := agentpb.NewAgentServiceClient(conn)
	stream, err := client.Tunnel(ctx)
	if err != nil {
		return err
	}
	// stream.Send 不是并发安全的：主循环回响应 + watcher goroutine 上报事件都要写，
	// 统一走 sendMu 串行化。
	var sendMu sync.Mutex
	send := func(f *agentpb.AgentFrame) error {
		sendMu.Lock()
		defer sendMu.Unlock()
		return stream.Send(f)
	}

	// 注册（R59：带版本 + 能力列表，供平台能力协商/版本可见）
	if err := send(&agentpb.AgentFrame{Type: "register", ClusterId: clusterID, Token: token, Version: version, Capabilities: buildCapabilities()}); err != nil {
		return err
	}
	fmt.Printf("[agent] 已向平台注册集群 %d（version=%s，能力 %d 项），等待指令\n", clusterID, version, len(buildCapabilities()))

	// 指标上报（agent 集群无平台侧采集器 → agent 在集群内采集 Pod 指标，经隧道上报平台落库）
	stopMetrics := startMetricsPush(ctx, mc, send, uint(clusterID), logger)
	defer stopMetrics()

	// 数据库指标上报（R6）：agent 侧配置的库，每 10s 采一次；慢 SQL 按 60s 子循环
	stopDBMetrics := startDBMetricsPush(ctx, send, dbCfgs, dbInterval, dbSlowInterval, logger)
	defer stopDBMetrics()

	// 配额 + 资源快照上报（对齐 config 接入：agent 在集群内采集，经隧道上报平台落库）
	stopQuota := startQuotaPush(ctx, mc, send, uint(clusterID), logger)
	defer stopQuota()

	// k8s 事件历史上报（k8s 默认只留 1h；平台自存 7 天，供历史事件回溯）
	stopEvents := startEventPush(ctx, mc, send, uint(clusterID), logger)
	defer stopEvents()

	// 主机代采上报（R7）：agent 在集群内 SSH 采集外部 VM，经隧道上报平台
	stopHostMetrics := startHostMetricsPush(ctx, send, hosts, sshKeyPath, logger)
	defer stopHostMetrics()

	// 告警检测（P2）：watcher 在 agent 内跑，事件经隧道上报平台，平台统一写库/触发处理
	var pw *watcher.PodWatcher
	var nw *watcher.NodeWatcher
	stopWatch := func() {
		if pw != nil {
			pw.Stop()
			pw = nil
		}
		if nw != nil {
			nw.Stop()
			nw = nil
		}
	}
	defer stopWatch()

	// 收到平台下发的 config（告警策略）后再启动 watcher；超时没收到就用默认策略
	var watchOnce sync.Once
	startWatch := func(policies []watcher.PolicyRow) {
		if !enableWatch {
			return
		}
		watchOnce.Do(func() {
			watcher.SetSink(watcher.NewTunnelSink(uint(clusterID), func(method string, payload []byte) error {
				return send(&agentpb.AgentFrame{Type: "event", Method: method, Payload: payload})
			}, logger))
			pw = watcher.NewPodWatcher(uint(clusterID), cs, logger)
			nw = watcher.NewNodeWatcher(uint(clusterID), cs, logger)
			if len(policies) > 0 {
				pw.ApplyPolicies(policies)
			}
			pw.Start()
			nw.Start()
			fmt.Printf("[agent] 告警检测已启动（Pod/Node watcher，策略 %d 条）\n", len(policies))
		})
	}
	// 流式会话（2026-11-04）：stream_open 开启，stream_msg/stream_end 双向转发
	type streamState struct {
		in     chan *agentpb.AgentFrame
		cancel context.CancelFunc
	}
	var streamsMu sync.Mutex
	streams := make(map[string]*streamState)
	closeAllStreams := func() {
		streamsMu.Lock()
		defer streamsMu.Unlock()
		for id, st := range streams {
			st.cancel()
			close(st.in)
			delete(streams, id)
		}
	}
	defer closeAllStreams()

	// 兜底：15s 内没收到 config 也先把 watcher 跑起来（用默认策略）
	cfgTimer := time.AfterFunc(15*time.Second, func() { startWatch(nil) })
	defer cfgTimer.Stop()

	for {
		frame, err := stream.Recv()
		if err != nil {
			stopWatch()
			return err
		}
		switch frame.Type {
		case "register_ack":
			fmt.Println("[agent] 注册成功")
		case "ping":
			_ = send(&agentpb.AgentFrame{Type: "pong", ReqId: frame.ReqId})
		case "config":
			var cfg struct {
				Policies []watcher.PolicyRow `json:"policies"`
			}
			if json.Unmarshal(frame.Payload, &cfg) == nil {
				fmt.Printf("[agent] 收到平台策略 %d 条\n", len(cfg.Policies))
				startWatch(cfg.Policies)
			}
		case "request":
			resp := handleRequest(ctx, bridge, frame, mc)
			if err := send(resp); err != nil {
				return err
			}
		case "stream_open":
			stCtx, cancel := context.WithCancel(ctx)
			st := &streamState{in: make(chan *agentpb.AgentFrame, 64), cancel: cancel}
			streamsMu.Lock()
			streams[frame.ReqId] = st
			streamsMu.Unlock()

			if frame.Method == "ExecPodStream" {
				// 双向流（交互终端）：平台发的 stdin → 本地流；本地流响应 → 平台
				sendFn, err := bridge.Bidi(stCtx, frame.Method, func(b []byte) error {
					return send(&agentpb.AgentFrame{Type: "stream_msg", ReqId: frame.ReqId, Payload: b})
				}, func(e error) {
					if e != nil {
						_ = send(&agentpb.AgentFrame{Type: "stream_error", ReqId: frame.ReqId, Error: e.Error()})
					} else {
						_ = send(&agentpb.AgentFrame{Type: "stream_end", ReqId: frame.ReqId})
					}
					cancel()
				})
				if err != nil {
					_ = send(&agentpb.AgentFrame{Type: "stream_error", ReqId: frame.ReqId, Error: err.Error()})
					streamsMu.Lock()
					delete(streams, frame.ReqId)
					streamsMu.Unlock()
					continue
				}
				go func() {
					for f := range st.in {
						if f.Type == "stream_end" {
							// 平台关闭发送方向：本地流也需要 CloseSend，Bidi 未暴露 → 依赖会话结束
							return
						}
						if err := sendFn(f.Payload); err != nil {
							cancel()
							return
						}
					}
				}()
				// 首帧（含 cluster_id/namespace/... 的会话参数）
				if len(frame.Payload) > 0 {
					if err := sendFn(frame.Payload); err != nil {
						_ = send(&agentpb.AgentFrame{Type: "stream_error", ReqId: frame.ReqId, Error: err.Error()})
					}
				}
				continue
			}

			// 服务端流式（ExecutePlan / BatchExecute）
			go func(reqID, method string, payload []byte) {
				defer cancel()
				err := bridge.ServerStream(stCtx, method, payload, func(b []byte) error {
					return send(&agentpb.AgentFrame{Type: "stream_msg", ReqId: reqID, Payload: b})
				})
				if err != nil {
					_ = send(&agentpb.AgentFrame{Type: "stream_error", ReqId: reqID, Error: err.Error()})
				} else {
					_ = send(&agentpb.AgentFrame{Type: "stream_end", ReqId: reqID})
				}
				streamsMu.Lock()
				delete(streams, reqID)
				streamsMu.Unlock()
			}(frame.ReqId, frame.Method, frame.Payload)
		case "stream_msg":
			streamsMu.Lock()
			st := streams[frame.ReqId]
			streamsMu.Unlock()
			if st != nil {
				select {
				case st.in <- frame:
				default:
				}
			}
		case "stream_end":
			streamsMu.Lock()
			st := streams[frame.ReqId]
			streamsMu.Unlock()
			if st != nil {
				select {
				case st.in <- frame:
				default:
				}
			}
		}
	}
}

// handleRequest 把隧道请求转给进程内 ExecutorService（P1：全方法反向代理，不再逐方法 switch）；
// QueryDatabase 例外：由 agent 在集群内直接连数据库执行（不经 executor）。
func handleRequest(ctx context.Context, bridge *agentbridge.Bridge, f *agentpb.AgentFrame,
	mc *k8sclient.ClusterClient) *agentpb.AgentFrame {
	resp := &agentpb.AgentFrame{Type: "response", ReqId: f.ReqId, Method: f.Method}
	// PromRange：Prometheus 区间查询（gRPC 侧只有即时查询；agent 集群没有 kubeconfig，
	// 平台无法走 executor 的 /prom/range，必须由 agent 在集群内代查）——2026-11-06 加
	if f.Method == "PromRange" {
		var req struct {
			Query string `json:"query"`
			Start int64  `json:"start"`
			End   int64  `json:"end"`
			Step  int    `json:"step"`
		}
		if err := json.Unmarshal(f.Payload, &req); err != nil {
			resp.Error = "解析 PromRange 请求失败: " + err.Error()
			return resp
		}
		if req.Query == "" {
			resp.Error = "query 必填"
			return resp
		}
		if req.Step <= 0 {
			req.Step = 60
		}
		out, err := prometheus.QueryRange(mc.Clientset, mc.RestConfig, req.Query, req.Start, req.End, req.Step)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "DbHealth" {
		out, err := agentdb.HandleHealth(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "DbObjectDDL" {
		out, err := agentdb.ObjectDDLByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "DbExport" {
		out, err := agentdb.ExportByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "DbImport" {
		out, err := agentdb.ImportByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "CreateDatabase" {
		out, err := agentdb.CreateDatabaseByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "DropDatabase" {
		out, err := agentdb.DropDatabaseByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "ImportSqlRaw" {
		out, err := agentdb.ImportSqlRawByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "Backup" {
		out, err := agentdb.BackupByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "DbSchemaByKey" {
		out, err := agentdb.SchemaByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "QueryDatabaseByKey" {
		out, err := agentdb.QueryByKey(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	if f.Method == "QueryDatabase" {
		out, err := agentdb.HandleQuery(ctx, f.Payload)
		if err != nil {
			resp.Error = err.Error()
			return resp
		}
		resp.Payload = out
		return resp
	}
	out, err := bridge.Call(ctx, f.Method, f.Payload)
	if err != nil {
		resp.Error = err.Error()
		return resp
	}
	resp.Payload = out
	return resp
}

func splitCSV(s string) []string {
	var out []string
	cur := ""
	for _, c := range s {
		if c == ',' {
			if cur != "" {
				out = append(out, cur)
			}
			cur = ""
		} else {
			cur += string(c)
		}
	}
	if cur != "" {
		out = append(out, cur)
	}
	return out
}

// startDBMetricsPush 数据库指标上报（R6）：agent 侧配置的库每 10s 采一次；
// 慢 SQL（performance_schema 全量 digest）按 slowInterval（默认 60s）子循环，避免把库拖慢。
func startDBMetricsPush(ctx context.Context, send func(*agentpb.AgentFrame) error,
	cfgs []agentdb.DBConfig, interval, slowInterval time.Duration, logger *zap.Logger) func() {
	if len(cfgs) == 0 {
		return func() {}
	}
	if interval <= 0 {
		interval = 10 * time.Second
	}
	if slowInterval <= 0 {
		slowInterval = 60 * time.Second
	}
	done := make(chan struct{})
	go func() {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		var lastSlow time.Time
		push := func() {
			withSlow := lastSlow.IsZero() || time.Since(lastSlow) >= slowInterval
			if withSlow {
				lastSlow = time.Now()
			}
			rows := agentdb.CollectOnce(ctx, cfgs, withSlow)
			if len(rows) == 0 {
				return
			}
			payload, err := json.Marshal(map[string]any{"rows": rows})
			if err != nil {
				return
			}
			if err := send(&agentpb.AgentFrame{Type: "event", Method: "DbMetrics", Payload: payload}); err != nil {
				logger.Debug("数据库指标上报失败（隧道可能已断）", zap.Error(err))
			}
		}
		push()
		for {
			select {
			case <-ctx.Done():
				return
			case <-done:
				return
			case <-ticker.C:
				push()
			}
		}
	}()
	return func() { close(done) }
}

// startHostMetricsPush 主机代采上报循环（R7）：启动即采一次，之后每 60s 采集一次，
// 把主机进程指标经隧道 event 帧（method=HostMetrics）上报平台，由平台自动建主机/更新状态。
func startHostMetricsPush(ctx context.Context, send func(*agentpb.AgentFrame) error,
	hosts []hostagent.HostConfig, sshKeyPath string, logger *zap.Logger) func() {
	if len(hosts) == 0 {
		return func() {}
	}
	done := make(chan struct{})
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		push := func() {
			rows := hostagent.CollectOnce(ctx, hosts, sshKeyPath, logger)
			if len(rows) == 0 {
				return
			}
			payload, err := json.Marshal(map[string]any{"hosts": rows})
			if err != nil {
				logger.Warn("主机代采指标序列化失败", zap.Error(err))
				return
			}
			if err := send(&agentpb.AgentFrame{Type: "event", Method: "HostMetrics", Payload: payload}); err != nil {
				logger.Debug("主机代采指标上报失败（隧道可能已断）", zap.Error(err))
			}
		}
		push()
		for {
			select {
			case <-ctx.Done():
				return
			case <-done:
				return
			case <-ticker.C:
				push()
			}
		}
	}()
	return func() { close(done) }
}

// startMetricsPush 启动指标上报循环：启动即采一次，之后每 60s 采集一次，
// 把 Pod 指标经隧道 event 帧（method=PodMetrics）上报平台，由平台落库。
// 返回停止函数（隧道断开时调用）。
func startMetricsPush(ctx context.Context, mc *k8sclient.ClusterClient, send func(*agentpb.AgentFrame) error,
	clusterID uint, logger *zap.Logger) func() {
	done := make(chan struct{})
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		push := func() {
			rows := metrics.CollectClusterOnce(ctx, mc, clusterID, logger)
			if len(rows) == 0 {
				return
			}
			payload, err := json.Marshal(map[string]any{"rows": rows})
			if err != nil {
				logger.Warn("指标序列化失败", zap.Error(err))
				return
			}
			if err := send(&agentpb.AgentFrame{Type: "event", Method: "PodMetrics", Payload: payload}); err != nil {
				logger.Debug("指标上报失败（隧道可能已断）", zap.Error(err))
			}
		}
		push()
		for {
			select {
			case <-ctx.Done():
				return
			case <-done:
				return
			case <-ticker.C:
				push()
			}
		}
	}()
	return func() { close(done) }
}

// startEventPush 启动 k8s 事件上报：启动即采一次，之后每 60s；只上报最近 15 分钟活跃事件。
func startEventPush(ctx context.Context, client *k8sclient.ClusterClient, send func(*agentpb.AgentFrame) error,
	clusterID uint, logger *zap.Logger) func() {
	done := make(chan struct{})
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		push := func() {
			rows, err := eventstore.CollectEventsOnce(ctx, client, clusterID, time.Now())
			if err != nil || len(rows) == 0 {
				if err != nil {
					logger.Debug("事件采集失败", zap.Error(err))
				}
				return
			}
			payload, err := json.Marshal(map[string]any{"events": rows})
			if err != nil {
				return
			}
			if err := send(&agentpb.AgentFrame{Type: "event", Method: "Events", Payload: payload}); err != nil {
				logger.Debug("事件上报失败（隧道可能已断）", zap.Error(err))
			}
		}
		push()
		for {
			select {
			case <-ctx.Done():
				return
			case <-done:
				return
			case <-ticker.C:
				push()
			}
		}
	}()
	return func() { close(done) }
}

// startQuotaPush 启动配额 + 资源快照上报循环：启动即采一次，之后每 60s 采集一次，
// 把配额/资源快照经隧道 event 帧（method=CollectSnapshot）上报平台，由平台落库。
func startQuotaPush(ctx context.Context, client *k8sclient.ClusterClient, send func(*agentpb.AgentFrame) error,
	clusterID uint, logger *zap.Logger) func() {
	done := make(chan struct{})
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		push := func() {
			res, err := quota.CollectClusterQuotaOnce(ctx, client, clusterID, time.Now())
			if err != nil {
				logger.Debug("配额/快照采集失败", zap.Error(err))
				return
			}
			if len(res.QuotaSnapshots) == 0 && len(res.ResourceSnapshots) == 0 {
				return
			}
			payload, err := json.Marshal(map[string]any{
				"quota_snapshots":    res.QuotaSnapshots,
				"resource_snapshots": res.ResourceSnapshots,
			})
			if err != nil {
				logger.Warn("快照序列化失败", zap.Error(err))
				return
			}
			if err := send(&agentpb.AgentFrame{Type: "event", Method: "CollectSnapshot", Payload: payload}); err != nil {
				logger.Debug("快照上报失败（隧道可能已断）", zap.Error(err))
			}
		}
		push()
		for {
			select {
			case <-ctx.Done():
				return
			case <-done:
				return
			case <-ticker.C:
				push()
			}
		}
	}()
	return func() { close(done) }
}
