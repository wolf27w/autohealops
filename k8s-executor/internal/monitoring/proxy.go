// Package monitoring 集群 Netdata 监控反代：api-server → 本 HTTP 服务 → apiserver Service Proxy → netdata-parent:19999。
// 纯 HTTP，复用集群 restConfig transport（免 port-forward、免新增 proto RPC）。
package monitoring

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/api-ops-platform/k8s-executor/internal/k8sclient"
	"go.uber.org/zap"
	"k8s.io/client-go/rest"
)

const (
	ServiceName = "netdata-parent"
	ServicePort = 19999
)

// Server Netdata 监控反代 HTTP 服务（内部用，仅本机）。
type Server struct {
	addr   string
	mgr    *k8sclient.Manager
	logger *zap.Logger
	srv    *http.Server
}

// NewServer 创建反代服务。
func NewServer(addr string, mgr *k8sclient.Manager, logger *zap.Logger) *Server {
	return &Server{addr: addr, mgr: mgr, logger: logger}
}

// Start 启动 HTTP 服务（非阻塞）。
func (s *Server) Start(_ context.Context) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/proxy/", s.handleProxy)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	s.srv = &http.Server{Addr: s.addr, Handler: mux}
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return err
	}
	go func() {
		if err := s.srv.Serve(ln); err != nil && err != http.ErrServerClosed {
			s.logger.Error("监控反代服务退出", zap.Error(err))
		}
	}()
	s.logger.Info("监控反代服务已启动", zap.String("addr", s.addr))
	return nil
}

// Stop 停止服务。
func (s *Server) Stop() {
	if s.srv != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = s.srv.Shutdown(ctx)
	}
}

// handleProxy 处理 /proxy/{cluster_id}/{namespace}/{path...}
func (s *Server) handleProxy(w http.ResponseWriter, r *http.Request) {
	parts := strings.SplitN(strings.TrimPrefix(r.URL.Path, "/proxy/"), "/", 3)
	if len(parts) < 2 {
		http.Error(w, "path 需为 /proxy/{cluster_id}/{namespace}/{path}", http.StatusBadRequest)
		return
	}
	clusterID, err := strconv.ParseUint(parts[0], 10, 32)
	if err != nil {
		http.Error(w, "cluster_id 非法", http.StatusBadRequest)
		return
	}
	namespace := parts[1]
	restPath := ""
	if len(parts) == 3 {
		restPath = parts[2]
	}

	client, err := s.mgr.GetClient(r.Context(), uint(clusterID))
	if err != nil {
		http.Error(w, "获取集群客户端失败: "+err.Error(), http.StatusBadGateway)
		return
	}
	restCfg := client.RestConfig

	target := fmt.Sprintf("%s/api/v1/namespaces/%s/services/%s:%d/proxy/%s",
		strings.TrimRight(restCfg.Host, "/"), namespace, ServiceName, ServicePort, restPath)
	if r.URL.RawQuery != "" {
		target += "?" + r.URL.RawQuery
	}

	req, err := http.NewRequestWithContext(r.Context(), r.Method, target, r.Body)
	if err != nil {
		http.Error(w, "构建请求失败: "+err.Error(), http.StatusBadGateway)
		return
	}
	for k, vs := range r.Header {
		if strings.EqualFold(k, "Host") || strings.EqualFold(k, "Content-Length") {
			continue
		}
		for _, v := range vs {
			req.Header.Add(k, v)
		}
	}

	transport, err := rest.TransportFor(restCfg)
	if err != nil {
		http.Error(w, "transport 构建失败: "+err.Error(), http.StatusBadGateway)
		return
	}
	hc := &http.Client{Transport: transport, Timeout: 120 * time.Second}
	resp, err := hc.Do(req)
	if err != nil {
		http.Error(w, "反代失败: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for k, vs := range resp.Header {
		for _, v := range vs {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	if _, err := io.Copy(w, resp.Body); err != nil {
		s.logger.Warn("反代响应复制中断", zap.Error(err))
	}
}
