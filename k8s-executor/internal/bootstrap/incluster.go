// Package bootstrap：平台自身部署在被管集群内时的「本集群自动接入」（P3-a，2026-11-04）
//
// 目标（老板 2026-10-21 方向）：
//   - 平台装进集群后，启动即把「本集群」登记进 clusters（source=in-cluster），不落任何 kubeconfig；
//   - 凭据由 kubelet 自动轮转（ServiceAccount token），不存在证书到期问题；
//   - 幂等键 = apiserver 地址（同一集群重复启动不会重复插入）。
package bootstrap

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"

	"go.uber.org/zap"
	"gorm.io/gorm"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"

	"github.com/autohealops/k8s-executor/internal/database"
	"github.com/autohealops/k8s-executor/internal/k8sclient"
)

// EnsureInCluster 在集群内运行时自动登记本集群；不在集群内则直接返回 (0, nil)
func EnsureInCluster(ctx context.Context, manager *k8sclient.Manager, logger *zap.Logger) (uint, error) {
	// 不在集群内（例如本地 Mac 开发）：KUBERNETES_SERVICE_HOST 不存在
	if os.Getenv("KUBERNETES_SERVICE_HOST") == "" {
		return 0, nil
	}
	if strings.EqualFold(os.Getenv("AUTO_REGISTER_CLUSTER"), "false") {
		logger.Info("AUTO_REGISTER_CLUSTER=false，跳过本集群自动接入")
		return 0, nil
	}
	rc, err := rest.InClusterConfig()
	if err != nil {
		return 0, fmt.Errorf("in-cluster 配置不可用: %w", err)
	}

	name := strings.TrimSpace(os.Getenv("CLUSTER_NAME"))
	if name == "" {
		name = "本集群"
	}
	// 集群真实名称/UID 只用于日志与排查（UID 作为跨重装集群的幂等参考）
	clusterUID := ""
	if cs, e := kubernetes.NewForConfig(rc); e == nil {
		if ns, e2 := cs.CoreV1().Namespaces().Get(ctx, "kube-system", metav1.GetOptions{}); e2 == nil {
			clusterUID = string(ns.UID)
		}
	}

	db := database.GetDB()
	var c database.Cluster
	err = db.Where("source = ? AND api_server_url = ?", "in-cluster", rc.Host).First(&c).Error
	switch {
	case errors.Is(err, gorm.ErrRecordNotFound):
		c = database.Cluster{
			Name:                name,
			Description:         "平台所在集群（in-cluster 自动接入，凭据由 ServiceAccount 自动轮转）",
			KubeconfigEncrypted: "",
			Source:              "in-cluster",
			APIServerURL:        rc.Host,
			EnvTag:              envOrDefault("CLUSTER_ENV_TAG", "prod"),
			Status:              "unknown",
		}
		if e := db.Create(&c).Error; e != nil {
			// 同名冲突（例如上次换 apiserver 地址后残留）→ 换名重试一次
			c.Name = fmt.Sprintf("%s-%d", name, os.Getpid())
			if e2 := db.Create(&c).Error; e2 != nil {
				return 0, fmt.Errorf("登记本集群失败: %v / %v", e, e2)
			}
		}
		logger.Info("本集群已自动接入", zap.Uint("cluster", c.ID), zap.String("name", c.Name),
			zap.String("api_server", rc.Host), zap.String("uid", clusterUID))
	case err != nil:
		return 0, fmt.Errorf("查询本集群失败: %w", err)
	default:
		// 已存在：只刷新名称/地址，不动用户改过的环境标签
		updates := map[string]any{"api_server_url": rc.Host}
		if c.Name != name && os.Getenv("CLUSTER_NAME") != "" {
			updates["name"] = name
		}
		db.Model(&c).Updates(updates)
		logger.Info("本集群已存在，跳过登记", zap.Uint("cluster", c.ID))
	}

	manager.MarkInCluster(c.ID)
	return c.ID, nil
}

func envOrDefault(k, def string) string {
	if v := strings.TrimSpace(os.Getenv(k)); v != "" {
		return v
	}
	return def
}
