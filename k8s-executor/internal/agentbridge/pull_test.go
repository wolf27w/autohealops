package agentbridge

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/autohealops/k8s-executor/internal/k8sclient"
	pb "github.com/autohealops/k8s-executor/proto"
	"go.uber.org/zap"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

// R14 pull 模式：agent 直接监听 gRPC，平台直连调用。
// 用不可达的假 apiserver 起桥 —— 只验证「监听 + 鉴权 + RPC 可达」，
// HealthCheck 会因为连不上 apiserver 返回 success=false（这是预期，不是失败）。
func TestServePullAuthAndReachable(t *testing.T) {
	dir := t.TempDir()
	kcPath := filepath.Join(dir, "kubeconfig")
	content := `apiVersion: v1
kind: Config
clusters:
- name: fake
  cluster:
    server: https://127.0.0.1:1
    insecure-skip-tls-verify: true
users:
- name: fake
  user:
    token: dummy
contexts:
- name: fake
  context: {cluster: fake, user: fake}
current-context: fake
`
	if err := os.WriteFile(kcPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	rc, err := clientcmd.BuildConfigFromFlags("", kcPath)
	if err != nil {
		t.Fatalf("build rc: %v", err)
	}
	cs, err := kubernetes.NewForConfig(rc)
	if err != nil {
		t.Fatalf("clientset: %v", err)
	}
	b, err := New(1, rc, cs, nil, zap.NewNop())
	if err != nil {
		t.Fatalf("bridge New: %v", err)
	}
	defer b.Close()

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	srv, err := b.pullServer("s3cret", "", "", "")
	if err != nil {
		t.Fatalf("pullServer: %v", err)
	}
	go func() { _ = b.servePullOn(lis, srv, "s3cret", "", "", "") }()
	defer srv.Stop()

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	client := pb.NewExecutorServiceClient(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// 1) 无令牌 → Unauthenticated
	_, err = client.HealthCheck(ctx, &pb.HealthCheckRequest{ClusterId: 1})
	if status.Code(err) != codes.Unauthenticated {
		t.Fatalf("无令牌期望 Unauthenticated，实际 err=%v", err)
	}

	// 2) 正确令牌 → 直达业务逻辑（连不上假 apiserver，返回 success=false 但不报鉴权错）
	md := metadata.Pairs("authorization", "Bearer s3cret")
	resp, err := client.HealthCheck(metadata.NewOutgoingContext(ctx, md), &pb.HealthCheckRequest{ClusterId: 1})
	if err != nil {
		t.Fatalf("带令牌调用失败: %v", err)
	}
	if resp.Success || resp.Status != "unreachable" {
		t.Fatalf("期望 unreachable（假 apiserver），实际 success=%v status=%s", resp.Success, resp.Status)
	}

	// 3) 错误令牌 → Unauthenticated
	bad := metadata.Pairs("authorization", "Bearer wrong")
	if _, err = client.HealthCheck(metadata.NewOutgoingContext(ctx, bad), &pb.HealthCheckRequest{ClusterId: 1}); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("错误令牌期望 Unauthenticated，实际 err=%v", err)
	}
}

// pullServer 抽出构建（测试与 ServePull 共用同一套拦截器/凭据）
func (b *Bridge) pullServer(token, certFile, keyFile, caFile string) (*grpc.Server, error) {
	opts, err := b.pullServerOptions(token, certFile, keyFile, caFile)
	if err != nil {
		return nil, err
	}
	srv := grpc.NewServer(opts...)
	pb.RegisterExecutorServiceServer(srv, b.svc)
	return srv, nil
}

var _ = k8sclient.NewManager
