// Package executor ServiceAccount 列表（角色反查 + 关联 secret）。
package executor

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ServiceAccountInfo ServiceAccount 简况
type ServiceAccountInfo struct {
	Name      string
	Namespace string
	Role      string
	Secrets   []string
	CreatedAt string
}

// ListServiceAccounts 列出 namespace（空=全部）下的 ServiceAccount，并反查其角色。
//
// 角色判定顺序（最小实现）：
//  1. 注解 kubesphere.io/role；
//  2. 注解 rbac.authorization.k8s.io/*（任一非空值）；
//  3. 兜底：list RoleBinding + ClusterRoleBinding，匹配 subjects[] 里
//     kind == ServiceAccount 且 name == sa.Name，取 roleRef.name；
//  4. 仍取不到 → 空串 ""。
//
// secrets 取 SA 的 secrets[].name（旧版 K8s 才有，新版为空数组）。
func (e *Executor) ListServiceAccounts(ctx context.Context, namespace string) ([]ServiceAccountInfo, error) {
	saList, err := e.clientset.CoreV1().ServiceAccounts(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list serviceaccounts: %w", err)
	}

	// role 反查表：key = "<namespace>/<saName>" → role 名。
	// RoleBinding 优先（namespace 精确），ClusterRoleBinding 兜底（cluster-scoped）。
	roleMap := map[string]string{}

	if rbList, err := e.clientset.RbacV1().RoleBindings("").List(ctx, metav1.ListOptions{}); err == nil {
		for i := range rbList.Items {
			rb := &rbList.Items[i]
			for _, subj := range rb.Subjects {
				if subj.Kind == "ServiceAccount" && subj.Name != "" {
					roleMap[rb.Namespace+"/"+subj.Name] = rb.RoleRef.Name
				}
			}
		}
	}
	if crbList, err := e.clientset.RbacV1().ClusterRoleBindings().List(ctx, metav1.ListOptions{}); err == nil {
		for i := range crbList.Items {
			crb := &crbList.Items[i]
			for _, subj := range crb.Subjects {
				if subj.Kind == "ServiceAccount" && subj.Name != "" {
					roleMap[subj.Namespace+"/"+subj.Name] = crb.RoleRef.Name
				}
			}
		}
	}

	out := make([]ServiceAccountInfo, 0, len(saList.Items))
	for i := range saList.Items {
		sa := &saList.Items[i]

		role := ""
		if v, ok := sa.Annotations["kubesphere.io/role"]; ok && v != "" {
			role = v
		} else {
			for k, v := range sa.Annotations {
				if strings.HasPrefix(k, "rbac.authorization.k8s.io/") && v != "" {
					role = v
					break
				}
			}
		}
		if role == "" {
			role = roleMap[sa.Namespace+"/"+sa.Name]
		}

		secrets := make([]string, 0, len(sa.Secrets))
		for _, s := range sa.Secrets {
			secrets = append(secrets, s.Name)
		}

		out = append(out, ServiceAccountInfo{
			Name:      sa.Name,
			Namespace: sa.Namespace,
			Role:      role,
			Secrets:   secrets,
			CreatedAt: sa.CreationTimestamp.Format(time.RFC3339),
		})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Namespace != out[j].Namespace {
			return out[i].Namespace < out[j].Namespace
		}
		return out[i].Name < out[j].Name
	})
	return out, nil
}
