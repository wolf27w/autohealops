// Package logstream 实时日志跟随（P1-R5）。
//
// executor 本地 HTTP 端点：把 apiserver 的 Pod 日志流（follow=true，带时间戳）以 chunked
// 流式透传给 api-server，api-server 再转 WebSocket 推给浏览器。**纯 HTTP，不走 gRPC proto**
// （本机缺 protoc-gen-go，不能改 proto）。
package logstream

import (
	"context"
	"net"
	"net/http"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"go.uber.org/zap"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	"github.com/autohealops/k8s-executor/internal/jvm"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
	"github.com/autohealops/k8s-executor/internal/prometheus"
)

// Server 日志流 HTTP 服务
type Server struct {
	manager *k8sclient.Manager
	addr    string
	logger  *zap.Logger
	srv     *http.Server
}

// NewServer 创建日志流服务
func NewServer(manager *k8sclient.Manager, addr string, logger *zap.Logger) *Server {
	return &Server{manager: manager, addr: addr, logger: logger}
}

// Start 启动 HTTP 服务（非阻塞）
func (s *Server) Start(_ context.Context) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) })
	mux.HandleFunc("/logs", s.handleLogs)
	mux.HandleFunc("/watch", s.handleWatch)
	mux.HandleFunc("/prom/range", s.handlePromRange)
	mux.HandleFunc("/files", s.handleFile)
	s.srv = &http.Server{Addr: s.addr, Handler: mux}
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return err
	}
	go func() {
		if err := s.srv.Serve(ln); err != nil && err != http.ErrServerClosed {
			s.logger.Error("日志流服务退出", zap.Error(err))
		}
	}()
	s.logger.Info("日志流服务已启动", zap.String("addr", s.addr))
	return nil
}

// Stop 停止服务
func (s *Server) Stop() {
	if s.srv != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = s.srv.Shutdown(ctx)
	}
}

// handleFile GET /files?path=<文件名>：R2 JVM 诊断产物（jfr/heapdump）下载
func (s *Server) handleFile(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("path")
	if name == "" {
		http.Error(w, "path 必填", http.StatusBadRequest)
		return
	}
	// 只允许纯文件名，防路径穿越
	if strings.ContainsAny(name, `/\`) || name == "." || name == ".." {
		http.Error(w, "非法文件名", http.StatusBadRequest)
		return
	}
	http.ServeFile(w, r, filepath.Join(jvm.ArtifactDir, name))
}

// logSidecarNames 常见 sidecar 容器名（默认日志不选它们）
var logSidecarNames = map[string]bool{
	"filebeat": true, "fluent-bit": true, "fluentd": true, "logstash": true,
	"istio-proxy": true, "envoy": true, "linkerd-proxy": true, "sidecar": true,
	"logtail": true, "vector": true, "prometheus": true, "node-exporter": true,
}

// pickLogContainer 选默认日志容器：default-container 注解 → 第一个非 sidecar → 第一个容器
func pickLogContainer(ctx context.Context, cc *k8sclient.ClusterClient, ns, pod string, logger *zap.Logger) string {
	p, err := cc.Clientset.CoreV1().Pods(ns).Get(ctx, pod, metav1.GetOptions{})
	if err != nil || p == nil || len(p.Spec.Containers) == 0 {
		if err != nil && logger != nil {
			logger.Debug("日志流：读取 Pod 选默认容器失败", zap.String("pod", ns+"/"+pod), zap.Error(err))
		}
		return ""
	}
	if v := p.Annotations["kubectl.kubernetes.io/default-container"]; v != "" {
		return v
	}
	for _, c := range p.Spec.Containers {
		if !logSidecarNames[strings.ToLower(c.Name)] {
			return c.Name
		}
	}
	return p.Spec.Containers[0].Name
}

// handleLogs GET /logs?cluster_id=&namespace=&pod=&container=&tail=&previous=&follow=
func (s *Server) handleLogs(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	cid, err := strconv.ParseUint(q.Get("cluster_id"), 10, 64)
	if err != nil {
		http.Error(w, "cluster_id 非法", http.StatusBadRequest)
		return
	}
	ns, pod := q.Get("namespace"), q.Get("pod")
	if ns == "" || pod == "" {
		http.Error(w, "namespace/pod 必填", http.StatusBadRequest)
		return
	}
	container := q.Get("container")
	tail := int64(200)
	if v, e := strconv.ParseInt(q.Get("tail"), 10, 64); e == nil && v > 0 {
		tail = v
	}
	previous := q.Get("previous") == "true"
	follow := q.Get("follow") != "false" // 默认跟随

	cc, err := s.manager.GetClient(r.Context(), uint(cid))
	if err != nil {
		http.Error(w, "集群客户端不可用: "+err.Error(), http.StatusBadGateway)
		return
	}

	// 容器名为空时自动选一个：多容器 Pod（应用 + filebeat 等 sidecar）不指定容器
	// API Server 会直接拒绝（"a container name must be specified for pod ..."），
	// 实时日志页就会 502「取日志失败」。默认策略同 ExecInPod/GetPodLogs。
	if container == "" {
		container = pickLogContainer(r.Context(), cc, ns, pod, s.logger)
	}

	opts := &corev1.PodLogOptions{
		Container: container, TailLines: &tail, Previous: previous,
		Timestamps: true, Follow: follow,
	}
	rc, err := cc.Clientset.CoreV1().Pods(ns).GetLogs(pod, opts).Stream(r.Context())
	if err != nil {
		http.Error(w, "取日志失败: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer rc.Close()

	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	flusher, _ := w.(http.Flusher)
	buf := make([]byte, 32*1024)
	for {
		n, rerr := rc.Read(buf)
		if n > 0 {
			if _, werr := w.Write(buf[:n]); werr != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
		if rerr != nil {
			return // io.EOF / 连接中断 / 客户端断开
		}
	}
}

// handlePromRange GET /prom/range?cluster_id=&query=&minutes=&step=
// 趋势曲线：调 Prometheus query_range（经 apiserver Service Proxy），返回原始 JSON。
func (s *Server) handlePromRange(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	cid, err := strconv.ParseUint(q.Get("cluster_id"), 10, 64)
	if err != nil {
		http.Error(w, "cluster_id 非法", http.StatusBadRequest)
		return
	}
	query := q.Get("query")
	if query == "" {
		http.Error(w, "query 必填", http.StatusBadRequest)
		return
	}
	minutes := 30
	if v, e := strconv.Atoi(q.Get("minutes")); e == nil && v > 0 {
		minutes = v
	}
	step := 30
	if v, e := strconv.Atoi(q.Get("step")); e == nil && v > 0 {
		step = v
	}
	cc, err := s.manager.GetClient(r.Context(), uint(cid))
	if err != nil {
		http.Error(w, "集群客户端不可用: "+err.Error(), http.StatusBadGateway)
		return
	}
	end := time.Now().Unix()
	start := end - int64(minutes)*60
	body, err := prometheus.QueryRange(cc.Clientset, cc.RestConfig, query, start, end, step)
	if err != nil {
		http.Error(w, "Prometheus 范围查询失败: "+err.Error(), http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(body) //nolint:errcheck
}
