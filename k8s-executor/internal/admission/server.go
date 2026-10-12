package admission

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"os"
	"time"

	"go.uber.org/zap"
	admissionv1 "k8s.io/api/admission/v1"
	appsv1 "k8s.io/api/apps/v1"

	"github.com/api-ops-platform/k8s-executor/internal/database"
)

// GovRow 是 resource_governance 表的读取行（GORM Scan 用）。
type GovRow struct {
	Container       string `gorm:"column:container"`
	Replicas        int    `gorm:"column:replicas"`
	CPURequest      string `gorm:"column:cpu_request"`
	CPULimit        string `gorm:"column:cpu_limit"`
	MemoryRequest   string `gorm:"column:memory_request"`
	MemoryLimit     string `gorm:"column:memory_limit"`
	JVMXms          string `gorm:"column:jvm_xms"`
	JVMXmx          string `gorm:"column:jvm_xmx"`
	JVMXss          string `gorm:"column:jvm_xss"`
	JVMMaxMetaspace string `gorm:"column:jvm_max_metaspace"`
}

// LoadGovs 读取某集群/命名空间/工作负载的启用中管控条目。
func LoadGovs(clusterID int, namespace, workload string) ([]Gov, error) {
	db := database.GetDB()
	if db == nil {
		return nil, fmt.Errorf("database 未初始化")
	}
	var rows []GovRow
	err := db.Raw(`SELECT container, replicas, cpu_request, cpu_limit, memory_request, memory_limit,
		jvm_xms, jvm_xmx, jvm_xss, jvm_max_metaspace
		FROM resource_governance
		WHERE cluster_id = ? AND namespace = ? AND workload_kind = 'deployment'
		  AND workload_name = ? AND admission_enabled = 1`, clusterID, namespace, workload).Scan(&rows).Error
	if err != nil {
		return nil, err
	}
	out := make([]Gov, 0, len(rows))
	for _, r := range rows {
		out = append(out, Gov{
			Container: r.Container, Replicas: r.Replicas,
			CPURequest: r.CPURequest, CPULimit: r.CPULimit,
			MemoryRequest: r.MemoryRequest, MemoryLimit: r.MemoryLimit,
			JVMXms: r.JVMXms, JVMXmx: r.JVMXmx, JVMXss: r.JVMXss, JVMMaxMetaspace: r.JVMMaxMetaspace,
		})
	}
	return out, nil
}

// Server 准入 webhook 服务（HTTPS）。
type Server struct {
	clusterID int
	addr      string
	certFile  string
	keyFile   string
	logger    *zap.Logger
	caPEM     []byte
	srv       *http.Server
}

// NewServer 创建准入服务。cert/key 为空时自动生成自签证书（并记录 CA PEM 供安装 webhook 用）。
func NewServer(clusterID int, addr, certFile, keyFile string, logger *zap.Logger) *Server {
	return &Server{clusterID: clusterID, addr: addr, certFile: certFile, keyFile: keyFile, logger: logger}
}

// CAPEM 返回自签 CA 证书 PEM（安装 MutatingWebhookConfiguration 的 caBundle 用）。
func (s *Server) CAPEM() []byte { return s.caPEM }

// Start 启动 HTTPS 服务（非阻塞）。
func (s *Server) Start(_ context.Context) error {
	cert, caPEM, err := s.loadOrGenCert()
	if err != nil {
		return err
	}
	s.caPEM = caPEM
	mux := http.NewServeMux()
	mux.HandleFunc("/mutate", s.handleMutate)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(200) })
	s.srv = &http.Server{
		Addr:      s.addr,
		Handler:   mux,
		TLSConfig: &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12},
	}
	ln, err := net.Listen("tcp", s.addr)
	if err != nil {
		return err
	}
	go func() {
		if err := s.srv.ServeTLS(ln, "", ""); err != nil && err != http.ErrServerClosed {
			s.logger.Error("准入 webhook 服务退出", zap.Error(err))
		}
	}()
	s.logger.Info("准入 webhook 已启动", zap.Int("cluster", s.clusterID), zap.String("addr", s.addr))
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

func (s *Server) handleMutate(w http.ResponseWriter, r *http.Request) {
	defer func() {
		if rec := recover(); rec != nil {
			s.logger.Error("准入 handler panic（fail-open）", zap.Any("panic", rec))
		}
	}()
	raw, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read body error", http.StatusBadRequest)
		return
	}
	var review admissionv1.AdmissionReview
	if err := json.Unmarshal(raw, &review); err != nil {
		http.Error(w, "bad admission review", http.StatusBadRequest)
		return
	}
	resp := s.decide(&review)
	if review.Request != nil {
		resp.UID = review.Request.UID
	}
	out := admissionv1.AdmissionReview{TypeMeta: review.TypeMeta, Response: resp}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// decide 计算准入结论（默认放行=fail-open）。
func (s *Server) decide(review *admissionv1.AdmissionReview) *admissionv1.AdmissionResponse {
	allow := &admissionv1.AdmissionResponse{Allowed: true}
	req := review.Request
	if req == nil || req.Kind.Kind != "Deployment" {
		return allow
	}
	if req.Operation != admissionv1.Create && req.Operation != admissionv1.Update {
		return allow
	}
	var deploy appsv1.Deployment
	if err := json.Unmarshal(req.Object.Raw, &deploy); err != nil {
		s.logger.Warn("解析 Deployment 失败（放行）", zap.Error(err))
		return allow
	}
	ns := req.Namespace
	if ns == "" {
		ns = deploy.Namespace
	}
	govs, err := LoadGovs(s.clusterID, ns, deploy.Name)
	if err != nil {
		s.logger.Warn("读取管控表失败（放行）", zap.Error(err))
		return allow
	}
	if len(govs) == 0 {
		return allow
	}
	patchBytes, err := BuildPatch(&deploy, govs)
	if err != nil || patchBytes == nil {
		return allow
	}
	pt := admissionv1.PatchTypeJSONPatch
	allow.PatchType = &pt
	allow.Patch = patchBytes
	return allow
}

// ---- 自签证书 ----

func (s *Server) loadOrGenCert() (tls.Certificate, []byte, error) {
	if s.certFile != "" && s.keyFile != "" {
		cert, err := tls.LoadX509KeyPair(s.certFile, s.keyFile)
		if err != nil {
			return tls.Certificate{}, nil, err
		}
		ca, _ := os.ReadFile(s.certFile)
		return cert, ca, nil
	}
	return genSelfSigned()
}

func genSelfSigned() (tls.Certificate, []byte, error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return tls.Certificate{}, nil, err
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	tmpl := x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "apiops-admission"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().AddDate(10, 0, 0),
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		IsCA:                  true,
		DNSNames:              []string{"apiops-admission", "apiops-admission.kube-system", "localhost"},
		IPAddresses:           []net.IP{net.ParseIP("127.0.0.1")},
	}
	der, err := x509.CreateCertificate(rand.Reader, &tmpl, &tmpl, &key.PublicKey, key)
	if err != nil {
		return tls.Certificate{}, nil, err
	}
	certPEM := pemEncode("CERTIFICATE", der)
	keyDER, _ := x509.MarshalPKCS8PrivateKey(key)
	keyPEM := pemEncode("PRIVATE KEY", keyDER)
	cert, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		return tls.Certificate{}, nil, err
	}
	return cert, certPEM, nil
}

func pemEncode(typ string, der []byte) []byte {
	return pem.EncodeToMemory(&pem.Block{Type: typ, Bytes: der})
}
