package agentbridge

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/autohealops/k8s-executor/internal/k8sclient"
	pb "github.com/autohealops/k8s-executor/proto"
	"go.uber.org/zap"
	"google.golang.org/protobuf/proto"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/clientcmd"
)

// 回归：由 rest.Config 合成的 kubeconfig 必须能被 Manager.GetClient 正常解析
// （历史 bug：合成时漏了 Contexts/CurrentContext → clientcmd 报 no configuration has been provided）
func TestKubeconfigFromRestIsUsable(t *testing.T) {
	dir := t.TempDir()
	kubeconfigPath := filepath.Join(dir, "kubeconfig")
	content := `apiVersion: v1
kind: Config
clusters:
- name: fake
  cluster:
    server: https://127.0.0.1:6443
    insecure-skip-tls-verify: true
users:
- name: fake
  user:
    token: dummy-token
contexts:
- name: fake
  context:
    cluster: fake
    user: fake
current-context: fake
`
	if err := os.WriteFile(kubeconfigPath, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	rc, err := clientcmd.BuildConfigFromFlags("", kubeconfigPath)
	if err != nil {
		t.Fatalf("build rc: %v", err)
	}

	jsonCfg, err := kubeconfigFromRest(rc)
	if err != nil {
		t.Fatalf("kubeconfigFromRest: %v", err)
	}
	if len(jsonCfg) == 0 {
		t.Fatal("kubeconfigFromRest 返回空串")
	}
	if _, err := clientcmd.NewClientConfigFromBytes([]byte(jsonCfg)); err != nil {
		t.Fatalf("NewClientConfigFromBytes: %v", err)
	}

	mgr := k8sclient.NewManager(func(_ context.Context, _ uint) (string, error) { return jsonCfg, nil })
	c, err := mgr.GetClient(context.Background(), 999025)
	if err != nil {
		t.Fatalf("GetClient 失败（合成 kubeconfig 不可用）: %v", err)
	}
	if c == nil || c.Clientset == nil {
		t.Fatal("client 为空")
	}
	_ = zap.NewNop()
}

// 真实链路：用测试集群 kubeconfig 起一个桥，模拟平台下发的 HealthCheck / ListNamespaces。
// 设置环境变量 KUBECONFIG_TC 指向 kubeconfig 才执行。
func TestBridgeCallRealCluster(t *testing.T) {
	kc := os.Getenv("KUBECONFIG_TC")
	if kc == "" {
		t.Skip("未设置 KUBECONFIG_TC，跳过真实集群用例")
	}
	rc, err := clientcmd.BuildConfigFromFlags("", kc)
	if err != nil {
		t.Fatalf("build rc: %v", err)
	}
	cs, err := kubernetes.NewForConfig(rc)
	if err != nil {
		t.Fatalf("clientset: %v", err)
	}
	logger := zap.NewNop()
	b, err := New(999025, rc, cs, nil, logger)
	if err != nil {
		t.Fatalf("bridge New: %v", err)
	}
	defer b.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()

	out, err := b.Call(ctx, "HealthCheck", mustMarshal(&pb.HealthCheckRequest{ClusterId: 999025}))
	resp := &pb.HealthCheckResponse{}
	if err == nil {
		_ = proto.Unmarshal(out, resp)
	}
	t.Logf("HealthCheck: err=%v success=%v status=%s version=%s error=%s", err, resp.Success, resp.Status, resp.Version, resp.Error)

	out2, err2 := b.Call(ctx, "ListNamespaces", mustMarshal(&pb.ListNamespacesRequest{ClusterId: 999025}))
	resp2 := &pb.ListNamespacesResponse{}
	if err2 == nil {
		_ = proto.Unmarshal(out2, resp2)
	}
	t.Logf("ListNamespaces: err=%v success=%v count=%d error=%s", err2, resp2.Success, len(resp2.Namespaces), resp2.Error)

	if err != nil || !resp.Success {
		t.Fatalf("HealthCheck 走桥失败: err=%v error=%s", err, resp.Error)
	}
}

func mustMarshal(m proto.Message) []byte {
	b, err := proto.Marshal(m)
	if err != nil {
		panic(err)
	}
	return b
}
