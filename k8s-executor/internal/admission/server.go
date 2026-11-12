package admission

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"

	"go.uber.org/zap"
	admissionv1 "k8s.io/api/admission/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// maxBodySize 请求体上限（AdmissionReview 单对象，4MiB 足够，防内存放大）。
const maxBodySize = 4 << 20

// Server admission webhook HTTP 服务。
type Server struct {
	rules  *Rules
	addr   string
	logger *zap.Logger
	srv    *http.Server
}

// NewServer 创建 webhook 服务（rules 可为空 = 放行）。
func NewServer(rules *Rules, addr string, logger *zap.Logger) *Server {
	if rules == nil {
		rules = &Rules{}
	}
	return &Server{rules: rules, addr: addr, logger: logger}
}

// Start 启动 HTTP 服务（非阻塞）。监听 /mutate 与 /healthz。
func (s *Server) Start(_ context.Context) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/mutate", s.handleMutate)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	s.srv = &http.Server{
		Addr:              s.addr,
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return err
	}
	go func() {
		if err := s.srv.Serve(ln); err != nil && err != http.ErrServerClosed {
			s.logger.Error("admission webhook 服务退出", zap.Error(err))
		}
	}()
	s.logger.Info("admission webhook 已启动",
		zap.String("addr", s.addr),
		zap.Int("rules", len(s.rules.Rules)),
	)
	return nil
}

// Stop 优雅关闭。
func (s *Server) Stop() {
	if s.srv != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = s.srv.Shutdown(ctx)
	}
}

// handleMutate 处理 POST /mutate。fail-closed：任何解析/处理错误（含 panic）都返回 allowed:false，
// 绝不静默放行；超时由 http.Server WriteTimeout 触发断连，配合 K8s failurePolicy=Fail 拒绝请求。
func (s *Server) handleMutate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var uid types.UID
	resp := func() (resp *admissionv1.AdmissionResponse) {
		defer func() {
			if rec := recover(); rec != nil {
				s.logger.Error("admission webhook panic（fail-closed 拒绝）", zap.Any("panic", rec))
				resp = deny(uid, fmt.Sprintf("webhook 内部错误: %v", rec))
			}
		}()

		body, err := io.ReadAll(io.LimitReader(r.Body, maxBodySize))
		if err != nil {
			return deny("", fmt.Sprintf("读取请求体失败: %v", err))
		}
		ar := &admissionv1.AdmissionReview{}
		if err := json.Unmarshal(body, ar); err != nil {
			return deny("", fmt.Sprintf("解析 AdmissionReview 失败: %v", err))
		}
		if ar.Request == nil {
			return deny("", "AdmissionReview.request 为空")
		}
		uid = ar.Request.UID
		return s.mutate(ar)
	}()

	writeReview(w, resp)
}

// writeReview 组装 AdmissionReview 响应并写回（补 UID、apiVersion/kind）。
func writeReview(w http.ResponseWriter, resp *admissionv1.AdmissionResponse) {
	review := admissionv1.AdmissionReview{
		TypeMeta: metav1.TypeMeta{APIVersion: "admission.k8s.io/v1", Kind: "AdmissionReview"},
		Response: resp,
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(review)
}
