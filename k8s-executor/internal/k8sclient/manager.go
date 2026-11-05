// Package k8sclient 多集群客户端池：从数据库加载 kubeconfig 解密后创建 clientset
package k8sclient

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"
	"k8s.io/client-go/tools/clientcmd/api"
	metricsclientset "k8s.io/metrics/pkg/client/clientset/versioned"
)

// ClusterClient 单集群客户端
type ClusterClient struct {
	ID            uint
	Clientset     *kubernetes.Clientset
	MetricsClient *metricsclientset.Clientset // metrics-server（可为 nil，未安装则跳过指标采集）
	RestConfig    *rest.Config                // 供 remotecommand（ExecInPod）使用
}

// Manager 多集群客户端池（并发安全）
type Manager struct {
	mu      sync.RWMutex
	clients map[uint]*ClusterClient
	// getKubeconfig 回调：从数据库加载并解密 kubeconfig（由 main 注入，避免循环依赖）
	getKubeconfig func(ctx context.Context, clusterID uint) (string, error)
	// inCluster 标记「平台自身所在集群」：不走 kubeconfig，直接用 ServiceAccount
	inCluster map[uint]bool
}

// NewManager 创建客户端池
func NewManager(getKubeconfig func(ctx context.Context, clusterID uint) (string, error)) *Manager {
	return &Manager{
		clients:       make(map[uint]*ClusterClient),
		getKubeconfig: getKubeconfig,
		inCluster:     make(map[uint]bool),
	}
}

// NewClusterClient 解析 kubeconfig JSON 创建 clientset
// 注意：显式指定 context（多集群 kubeconfig 常有多个 context），不依赖 current-context
func NewClusterClient(clusterID uint, kubeconfigJSON string) (*ClusterClient, error) {
	restConfig, err := buildRestConfig(clusterID, kubeconfigJSON)
	if err != nil {
		return nil, err
	}

	clientset, err := kubernetes.NewForConfig(restConfig)
	if err != nil {
		return nil, classifyErr("clientset 创建失败", err)
	}
	// metrics client（metrics-server 未安装时请求会 404，创建本身通常不会失败；失败则降级为 nil）
	var metricsClient *metricsclientset.Clientset
	if mc, err := metricsclientset.NewForConfig(restConfig); err == nil {
		metricsClient = mc
	}
	return &ClusterClient{ID: clusterID, Clientset: clientset, MetricsClient: metricsClient, RestConfig: restConfig}, nil
}

// newClientFromRest 用现成 rest.Config 构建客户端（in-cluster 用）
func newClientFromRest(clusterID uint, rc *rest.Config) (*ClusterClient, error) {
	clientset, err := kubernetes.NewForConfig(rc)
	if err != nil {
		return nil, classifyErr("clientset 创建失败", err)
	}
	var mc *metricsclientset.Clientset
	if m, err := metricsclientset.NewForConfig(rc); err == nil {
		mc = m
	}
	return &ClusterClient{ID: clusterID, Clientset: clientset, MetricsClient: mc, RestConfig: rc}, nil
}

// NewClusterClientFromRest 用现成 rest.Config 构建客户端（agent 模式：集群内直接用 rc/cs）
func NewClusterClientFromRest(clusterID uint, rc *rest.Config) (*ClusterClient, error) {
	return newClientFromRest(clusterID, rc)
}

// buildRestConfig 解析 kubeconfig 并构建 REST config（显式选择 context）
func buildRestConfig(clusterID uint, kubeconfigJSON string) (*rest.Config, error) {
	cfg, err := clientcmd.NewClientConfigFromBytes([]byte(kubeconfigJSON))
	if err != nil {
		return nil, classifyErr("kubeconfig 解析失败", err)
	}

	raw, err := clientcmd.Load([]byte(kubeconfigJSON))
	if err != nil {
		return nil, classifyErr("kubeconfig 加载失败", err)
	}
	contextName := pickContext(raw, clusterID)
	restConfig, err := cfg.ClientConfig()
	if err != nil {
		return nil, classifyErr("client config 构建失败", err)
	}
	// 若需要指定 context，用 clientcmd.NewNonInteractiveDeferredLoadingClientConfig 重建
	if contextName != "" && raw.CurrentContext != contextName {
		restConfig, err = buildConfigWithContext(kubeconfigJSON, contextName)
		if err != nil {
			return nil, classifyErr("指定 context 构建失败", err)
		}
	}
	// 客户端限流放宽（2026-10-21）：client-go 默认 QPS=5/Burst=10，
	// 监控大屏一次刷新并发 15~18 个查询会被串行化成数秒（实测 4.4s）。
	// executor 只服务本平台，50/100 足够安全，配合 prometheus 包的发现缓存使用。
	if restConfig.QPS < 50 {
		restConfig.QPS = 50
	}
	if restConfig.Burst < 100 {
		restConfig.Burst = 100
	}
	return restConfig, nil
}

// ImpersonatedClient 基于集群平台账号配置派生带 impersonation 的 clientset（云原生 RBAC 运行链）
//
//   - user:   impersonate 的用户名（autohealops:<username>）
//   - groups: impersonate 的组（autohealops:proj:<id>:<role>、autohealops:automation 等）
//
// 平台账号本身需具备 impersonate 权限（接入时探测）；透传后由 k8s RBAC 判定实际权限。
func (m *Manager) ImpersonatedClient(ctx context.Context, clusterID uint, user string, groups []string) (*ClusterClient, error) {
	base, err := m.GetClient(ctx, clusterID)
	if err != nil {
		return nil, err
	}
	cfg := rest.CopyConfig(base.RestConfig)
	cfg.Impersonate = rest.ImpersonationConfig{
		UserName: user,
		Groups:   groups,
	}
	clientset, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		return nil, fmt.Errorf("创建 impersonation clientset 失败: %w", err)
	}
	return &ClusterClient{ID: clusterID, Clientset: clientset, MetricsClient: nil, RestConfig: cfg}, nil
}

// pickContext 选择 kubeconfig 中的 context：
// 优先取与 clusterID 同名的 context，其次取第一个；无则返回空（用 current-context）
func pickContext(raw *api.Config, clusterID uint) string {
	if raw == nil {
		return ""
	}
	if name, ok := raw.Contexts[fmt.Sprintf("cluster-%d", clusterID)]; ok && name != nil {
		return fmt.Sprintf("cluster-%d", clusterID)
	}
	for name := range raw.Contexts {
		return name
	}
	return ""
}

// buildConfigWithContext 指定 context 构建 REST 配置（基于传入的 kubeconfigJSON，不读默认文件）
func buildConfigWithContext(kubeconfigJSON, contextName string) (*rest.Config, error) {
	raw, err := clientcmd.Load([]byte(kubeconfigJSON))
	if err != nil {
		return nil, err
	}
	return clientcmd.NewDefaultClientConfig(*raw, &clientcmd.ConfigOverrides{CurrentContext: contextName}).ClientConfig()
}

// classifyErr 错误分类：证书过期/网络不通/权限不足/其他
func classifyErr(prefix string, err error) error {
	msg := err.Error()
	switch {
	case strings.Contains(msg, "x509") || strings.Contains(msg, "certificate") ||
		strings.Contains(msg, "expired") || strings.Contains(msg, "unable to read client-cert"):
		return fmt.Errorf("%s: 证书问题（可能过期或格式错误）: %w", prefix, err)
	case strings.Contains(msg, "connection refused") || strings.Contains(msg, "no such host") ||
		strings.Contains(msg, "timeout") || strings.Contains(msg, "dial tcp"):
		return fmt.Errorf("%s: 网络不通（API Server 不可达）: %w", prefix, err)
	case strings.Contains(msg, "forbidden") || strings.Contains(msg, "unauthorized") ||
		strings.Contains(msg, "permission denied"):
		return fmt.Errorf("%s: 权限不足: %w", prefix, err)
	default:
		return fmt.Errorf("%s: %w", prefix, err)
	}
}

// MarkInCluster 标记该集群为「本集群」：客户端用 in-cluster 配置（ServiceAccount，自动轮转）
func (m *Manager) MarkInCluster(clusterID uint) {
	m.mu.Lock()
	m.inCluster[clusterID] = true
	m.mu.Unlock()
}

// GetClient 从缓存获取客户端；不存在则从数据库加载 kubeconfig 创建
func (m *Manager) GetClient(ctx context.Context, clusterID uint) (*ClusterClient, error) {
	m.mu.RLock()
	c, ok := m.clients[clusterID]
	m.mu.RUnlock()
	if ok {
		return c, nil
	}

	m.mu.RLock()
	inCluster := m.inCluster[clusterID]
	m.mu.RUnlock()
	if inCluster {
		rc, err := rest.InClusterConfig()
		if err != nil {
			return nil, fmt.Errorf("本集群 in-cluster 配置不可用: %w", err)
		}
		rc.QPS, rc.Burst = 50, 100
		c, err = newClientFromRest(clusterID, rc)
		if err != nil {
			return nil, err
		}
		m.mu.Lock()
		m.clients[clusterID] = c
		m.mu.Unlock()
		return c, nil
	}

	if m.getKubeconfig == nil {
		return nil, fmt.Errorf("kubeconfig loader 未配置")
	}
	kubeconfigJSON, err := m.getKubeconfig(ctx, clusterID)
	if err != nil {
		return nil, err
	}
	c, err = NewClusterClient(clusterID, kubeconfigJSON)
	if err != nil {
		return nil, err
	}
	m.mu.Lock()
	m.clients[clusterID] = c
	m.mu.Unlock()
	return c, nil
}

// RemoveClient 移除并关闭客户端（删除集群/连接失败时）
func (m *Manager) RemoveClient(clusterID uint) {
	m.mu.Lock()
	delete(m.clients, clusterID)
	m.mu.Unlock()
}

// ListClients 返回全部客户端 ID
func (m *Manager) ListClients() []uint {
	m.mu.RLock()
	defer m.mu.RUnlock()
	ids := make([]uint, 0, len(m.clients))
	for id := range m.clients {
		ids = append(ids, id)
	}
	return ids
}

// DecryptKubeconfig 解密数据库中的 kubeconfig 密文（AES-256-GCM，见 docs/ENCRYPTION.md）
func DecryptKubeconfig(encrypted string) (string, error) {
	return DecryptAES256GCM(encrypted)
}

// ParseKubeconfigForCluster 辅助：校验 kubeconfig 可解析（供集群添加时预检）
func ParseKubeconfigForCluster(kubeconfigJSON string) error {
	_, err := clientcmd.Load([]byte(kubeconfigJSON))
	if err != nil {
		return classifyErr("kubeconfig 无效", err)
	}
	return nil
}

// 避免未使用告警：json 保留给后续扩展
var _ = json.Valid
