// k8s-agent：R12 agent 主动外连接入模式（单 Deployment）。
// agent 部署在被管集群内，主动连平台 gateway 建立 gRPC 双向隧道；
// 平台把 executor 请求（method + 序列化 payload）经隧道下发，agent 在集群内执行后回传响应。
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"encoding/json"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"github.com/autohealops/k8s-executor/internal/executor"
	"github.com/autohealops/k8s-executor/internal/watcher"
	"go.uber.org/zap"
	agentpb "github.com/autohealops/k8s-executor/proto/agent"
	pb "github.com/autohealops/k8s-executor/proto"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
)

const defaultWhitelist = "get,list,describe,logs,top,explain,rollout,scale,set,apply,patch,delete,exec,create,edit,cordon,uncordon,drain"

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
	flag.Parse()

	if *clusterID == 0 {
		fmt.Fprintln(os.Stderr, "必须指定 -cluster-id")
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
	exec := executor.NewExecutor(uint(*clusterID), cs, rc, splitCSV(*whitelist))

	// 连平台 gateway（反向隧道）
	conn, err := grpc.NewClient(*platform, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		fmt.Fprintf(os.Stderr, "连接平台失败: %v\n", err)
		os.Exit(1)
	}
	defer conn.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		c := make(chan os.Signal, 1)
		signal.Notify(c, syscall.SIGINT, syscall.SIGTERM)
		<-c
		cancel()
	}()

	// 断线自动重连
	for ctx.Err() == nil {
		if err := runTunnel(ctx, conn, *clusterID, *token, exec, cs, *watch); err != nil {
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
	exec *executor.Executor, cs *kubernetes.Clientset, enableWatch bool) error {
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

	// 注册
	if err := send(&agentpb.AgentFrame{Type: "register", ClusterId: clusterID, Token: token}); err != nil {
		return err
	}
	fmt.Printf("[agent] 已向平台注册集群 %d，等待指令\n", clusterID)

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
			logger, _ := zap.NewProduction()
			watcher.SetSink(watcher.NewTunnelSink(uint(clusterID), func(payload []byte) error {
				return send(&agentpb.AgentFrame{Type: "event", Method: "AlertEvent", Payload: payload})
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
			resp := handleRequest(ctx, exec, frame)
			if err := send(resp); err != nil {
				return err
			}
		}
	}
}

// handleRequest 执行一个隧道请求并构造响应帧（method 对应 executor.proto 的 rpc 名）
func handleRequest(ctx context.Context, exec *executor.Executor, f *agentpb.AgentFrame) *agentpb.AgentFrame {
	resp := &agentpb.AgentFrame{Type: "response", ReqId: f.ReqId, Method: f.Method}
	switch f.Method {
	case "ExecuteCommand":
		var req pb.ExecuteCommandRequest
		if err := proto.Unmarshal(f.Payload, &req); err != nil {
			resp.Error = "解码请求失败: " + err.Error()
			return resp
		}
		out, err := exec.ExecuteCommand(ctx, req.Namespace, req.Command)
		r := &pb.ExecuteCommandResponse{Success: err == nil, Output: out}
		if err != nil {
			r.Error = err.Error()
		}
		resp.Payload, _ = proto.Marshal(r)
	case "GetPodLogs":
		var req pb.GetPodLogsRequest
		if err := proto.Unmarshal(f.Payload, &req); err != nil {
			resp.Error = "解码请求失败: " + err.Error()
			return resp
		}
		logs, err := exec.GetPodLogs(ctx, req.Namespace, req.PodName, req.Container, int64(req.TailLines), req.Previous)
		r := &pb.GetPodLogsResponse{Success: err == nil, Logs: logs}
		if err != nil {
			r.Error = err.Error()
		}
		resp.Payload, _ = proto.Marshal(r)
	default:
		resp.Error = "agent 暂不支持的方法: " + f.Method
	}
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
