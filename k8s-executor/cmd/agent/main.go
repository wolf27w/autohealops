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
	"path/filepath"
	"syscall"
	"time"

	"github.com/autohealops/k8s-executor/internal/executor"
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
		if err := runTunnel(ctx, conn, *clusterID, *token, exec); err != nil {
			fmt.Fprintf(os.Stderr, "[agent] 隧道断开: %v，3s 后重连\n", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(3 * time.Second):
		}
	}
}

func runTunnel(ctx context.Context, conn *grpc.ClientConn, clusterID int64, token string, exec *executor.Executor) error {
	client := agentpb.NewAgentServiceClient(conn)
	stream, err := client.Tunnel(ctx)
	if err != nil {
		return err
	}
	// 注册
	if err := stream.Send(&agentpb.AgentFrame{Type: "register", ClusterId: clusterID, Token: token}); err != nil {
		return err
	}
	fmt.Printf("[agent] 已向平台注册集群 %d，等待指令\n", clusterID)
	for {
		frame, err := stream.Recv()
		if err != nil {
			return err
		}
		switch frame.Type {
		case "register_ack":
			fmt.Println("[agent] 注册成功")
		case "ping":
			_ = stream.Send(&agentpb.AgentFrame{Type: "pong", ReqId: frame.ReqId})
		case "request":
			resp := handleRequest(ctx, exec, frame)
			if err := stream.Send(resp); err != nil {
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
