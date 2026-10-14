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
	"strconv"
	"time"

	"go.uber.org/zap"
	corev1 "k8s.io/api/core/v1"

	"github.com/api-ops-platform/k8s-executor/internal/k8sclient"
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
