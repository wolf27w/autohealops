// Package rbac 云原生 RBAC（Impersonation）接入：能力探测、ClusterRole 下发、自举账号、RoleBinding reconcile
//
// 身份映射约定（与 api-server 侧一致）：
//   - 用户       →  autohealops:<username>
//   - 项目/角色  →  group autohealops:proj:<id>:<read|operate|manager>
//   - 自动化     →  autohealops:automation
package rbac

import (
	"context"
	"fmt"
	"strings"

	authorizationv1 "k8s.io/api/authorization/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

// 三档 ClusterRole 名称
const (
	RoleRead    = "autohealops-read"
	RoleOperate = "autohealops-operate"
	RoleManager = "autohealops-manager"
	// 自动化账号（AI 诊断 / 自愈）使用的 ServiceAccount
	AutomationSA          = "autohealops-platform"
	AutomationSANamespace = "autohealops"
	// 用户身份前缀（与 api-server 侧保持一致）
	UserPrefix = "autohealops:"
)

// 平台管理的 RoleBinding 名称前缀（用于 reconcile 时识别并清理）
const bindingNamePrefix = "autohealops-"

// 三档 ClusterRole 权限定义
// read=只读；operate=read + exec/logs/port-forward/scale；manager=operate + delete/patch/update
func clusterRoles() []*rbacv1.ClusterRole {
	readRules := []rbacv1.PolicyRule{
		{APIGroups: []string{""}, Resources: []string{"pods", "services", "events", "namespaces", "nodes", "persistentvolumes", "persistentvolumeclaims", "configmaps", "secrets"}, Verbs: []string{"get", "list", "watch"}},
		{APIGroups: []string{"apps"}, Resources: []string{"deployments", "statefulsets", "daemonsets", "replicasets"}, Verbs: []string{"get", "list", "watch"}},
		{APIGroups: []string{"batch"}, Resources: []string{"jobs", "cronjobs"}, Verbs: []string{"get", "list", "watch"}},
		{APIGroups: []string{"autoscaling"}, Resources: []string{"horizontalpodautoscalers"}, Verbs: []string{"get", "list", "watch"}},
		{APIGroups: []string{""}, Resources: []string{"pods/log"}, Verbs: []string{"get"}},
	}

	operateRules := append(append([]rbacv1.PolicyRule{}, readRules...),
		rbacv1.PolicyRule{APIGroups: []string{""}, Resources: []string{"pods/exec", "pods/attach", "pods/portforward"}, Verbs: []string{"create"}},
		rbacv1.PolicyRule{APIGroups: []string{"apps"}, Resources: []string{"deployments/scale", "statefulsets/scale"}, Verbs: []string{"get", "update", "patch"}},
	)

	managerRules := append(append([]rbacv1.PolicyRule{}, operateRules...),
		rbacv1.PolicyRule{APIGroups: []string{"apps"}, Resources: []string{"deployments", "statefulsets", "daemonsets"}, Verbs: []string{"delete", "patch", "update"}},
		rbacv1.PolicyRule{APIGroups: []string{"autoscaling"}, Resources: []string{"horizontalpodautoscalers"}, Verbs: []string{"create", "update", "patch", "delete"}},
		rbacv1.PolicyRule{APIGroups: []string{""}, Resources: []string{"pods", "services"}, Verbs: []string{"delete", "patch", "update"}},
		rbacv1.PolicyRule{APIGroups: []string{""}, Resources: []string{"configmaps", "secrets"}, Verbs: []string{"create", "update", "patch", "delete"}},
	)

	return []*rbacv1.ClusterRole{
		{ObjectMeta: metav1.ObjectMeta{Name: RoleRead}, Rules: readRules},
		{ObjectMeta: metav1.ObjectMeta{Name: RoleOperate}, Rules: operateRules},
		{ObjectMeta: metav1.ObjectMeta{Name: RoleManager}, Rules: managerRules},
	}
}

// SetupResult 接入结果
type SetupResult struct {
	RBACCapable        bool     // 能否 create RBAC（决定能否下发 ClusterRole/RoleBinding）
	ImpersonateCapable bool     // 能否 impersonate（决定能否透传用户身份）
	ClusterRoles       []string // 实际创建/更新的 ClusterRole 名
	SACreated          bool     // 是否自举了 automation SA + binding
	Error              string   // 失败原因（成功时为空）
}

// Setup 能力探测 + 下发三档 ClusterRole + 可选自举 automation SA
//
// 策略：能力不够时不失败、只降级（返回 RBACCapable=false），由 api-server 决定降级为「平台权限 + 白名单」模式。
func Setup(ctx context.Context, client *kubernetes.Clientset) SetupResult {
	res := SetupResult{}

	// 1. 能力探测 + 下发三档 ClusterRole
	res.RBACCapable = true
	for _, role := range clusterRoles() {
		_, err := client.RbacV1().ClusterRoles().Create(ctx, role, metav1.CreateOptions{})
		if err != nil {
			if k8serrors.IsAlreadyExists(err) {
				// 已存在 → 更新规则（幂等，覆盖）
				existing, gerr := client.RbacV1().ClusterRoles().Get(ctx, role.Name, metav1.GetOptions{})
				if gerr != nil {
					res.RBACCapable = false
					res.Error = fmt.Sprintf("获取 ClusterRole %s 失败: %v", role.Name, gerr)
					return res
				}
				existing.Rules = role.Rules
				if _, uerr := client.RbacV1().ClusterRoles().Update(ctx, existing, metav1.UpdateOptions{}); uerr != nil {
					res.RBACCapable = false
					res.Error = fmt.Sprintf("更新 ClusterRole %s 失败: %v", role.Name, uerr)
					return res
				}
			} else {
				// 无 RBAC 管理权限（Forbidden 等）
				res.RBACCapable = false
				res.Error = fmt.Sprintf("创建 ClusterRole 失败（无 RBAC 管理权限）: %v", err)
				return res
			}
		}
		res.ClusterRoles = append(res.ClusterRoles, role.Name)
	}

	// 2. 能力探测：能否 impersonate（SelfSubjectAccessReview 探测 impersonate users/groups 权限）
	res.ImpersonateCapable = probeImpersonate(ctx, client)

	// 3. 自举 automation SA + binding（自动化账号独立于 cluster-admin kubeconfig）
	res.SACreated = bootstrapAutomationSA(ctx, client)

	return res
}

// probeImpersonate 用 SelfSubjectAccessReview 探测平台账号是否有 impersonate users/groups 权限
func probeImpersonate(ctx context.Context, client *kubernetes.Clientset) bool {
	for _, res := range []string{"users", "groups"} {
		review := &authorizationv1.SelfSubjectAccessReview{
			Spec: authorizationv1.SelfSubjectAccessReviewSpec{
				ResourceAttributes: &authorizationv1.ResourceAttributes{
					Verb:     "impersonate",
					Resource: res,
				},
			},
		}
		resp, err := client.AuthorizationV1().SelfSubjectAccessReviews().Create(ctx, review, metav1.CreateOptions{})
		if err != nil {
			return false
		}
		if !resp.Status.Allowed {
			return false
		}
	}
	return true
}

// bootstrapAutomationSA 自举 automation ServiceAccount + ClusterRoleBinding（绑定 autohealops-manager）
// 失败不阻断（返回 false），平台仍可用 cluster-admin kubeconfig 运行。
func bootstrapAutomationSA(ctx context.Context, client *kubernetes.Clientset) bool {
	// 1. namespace autohealops（不存在则建）
	if _, err := client.CoreV1().Namespaces().Get(ctx, AutomationSANamespace, metav1.GetOptions{}); err != nil {
		if k8serrors.IsNotFound(err) {
			ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: AutomationSANamespace}}
			if _, cerr := client.CoreV1().Namespaces().Create(ctx, ns, metav1.CreateOptions{}); cerr != nil {
				return false
			}
		} else {
			return false
		}
	}
	// 2. SA
	if _, err := client.CoreV1().ServiceAccounts(AutomationSANamespace).Get(ctx, AutomationSA, metav1.GetOptions{}); err != nil {
		if k8serrors.IsNotFound(err) {
			sa := &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Name: AutomationSA, Namespace: AutomationSANamespace}}
			if _, cerr := client.CoreV1().ServiceAccounts(AutomationSANamespace).Create(ctx, sa, metav1.CreateOptions{}); cerr != nil {
				return false
			}
		} else {
			return false
		}
	}
	// 3. ClusterRoleBinding：automation → autohealops-manager
	crb := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: "autohealops-automation"},
		Subjects: []rbacv1.Subject{
			{Kind: rbacv1.ServiceAccountKind, Name: AutomationSA, Namespace: AutomationSANamespace},
		},
		RoleRef: rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: RoleManager},
	}
	if _, err := client.RbacV1().ClusterRoleBindings().Create(ctx, crb, metav1.CreateOptions{}); err != nil {
		if !k8serrors.IsAlreadyExists(err) {
			return false
		}
	}
	return true
}

// BindingSpec 一条项目角色绑定：namespace + group → clusterRole
type BindingSpec struct {
	Namespace   string // 项目关联的 namespace
	Group       string // autohealops:proj:<id>:<read|operate|manager>
	ClusterRole string // autohealops-read / autohealops-operate / autohealops-manager
}

// ReconcileResult reconcile 结果
type ReconcileResult struct {
	Created int
	Updated int
	Deleted int
	Error   string
}

// ReconcileRoleBindings 按期望的绑定集合 reconcile 集群内 RoleBinding（幂等）
//
// 平台管理的 RoleBinding 命名：autohealops-<namespace>-<group>（group 冒号转连字符），reconcile 时：
//  1. upsert 期望的绑定（namespace + group → clusterRole）
//  2. 清理该 namespace 内平台管理但已不在期望集合的过期绑定
func ReconcileRoleBindings(ctx context.Context, client *kubernetes.Clientset, bindings []BindingSpec) ReconcileResult {
	res := ReconcileResult{}

	// 按 namespace 分组期望绑定：ns -> group -> spec
	want := map[string]map[string]BindingSpec{}
	for _, b := range bindings {
		if b.Namespace == "" || b.Group == "" || b.ClusterRole == "" {
			continue
		}
		if want[b.Namespace] == nil {
			want[b.Namespace] = map[string]BindingSpec{}
		}
		want[b.Namespace][b.Group] = b
	}

	for ns, groups := range want {
		for group, spec := range groups {
			name := bindingName(ns, group)
			rb := &rbacv1.RoleBinding{
				ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns},
				Subjects:   []rbacv1.Subject{{Kind: rbacv1.GroupKind, APIGroup: rbacv1.GroupName, Name: group}},
				RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: spec.ClusterRole},
			}
			_, err := client.RbacV1().RoleBindings(ns).Create(ctx, rb, metav1.CreateOptions{})
			if err != nil {
				if k8serrors.IsAlreadyExists(err) {
					existing, gerr := client.RbacV1().RoleBindings(ns).Get(ctx, name, metav1.GetOptions{})
					if gerr != nil {
						res.Error = fmt.Sprintf("获取 RoleBinding %s/%s 失败: %v", ns, name, gerr)
						return res
					}
					existing.Subjects = rb.Subjects
					existing.RoleRef = rb.RoleRef
					if _, uerr := client.RbacV1().RoleBindings(ns).Update(ctx, existing, metav1.UpdateOptions{}); uerr != nil {
						res.Error = fmt.Sprintf("更新 RoleBinding %s/%s 失败: %v", ns, name, uerr)
						return res
					}
					res.Updated++
				} else {
					res.Error = fmt.Sprintf("创建 RoleBinding %s/%s 失败: %v", ns, name, err)
					return res
				}
			} else {
				res.Created++
			}
		}

		// 清理该 namespace 内平台管理、但已不在期望集合的过期 RoleBinding
		list, err := client.RbacV1().RoleBindings(ns).List(ctx, metav1.ListOptions{})
		if err != nil {
			res.Error = fmt.Sprintf("列出 namespace %s RoleBinding 失败: %v", ns, err)
			return res
		}
		for _, existing := range list.Items {
			if !strings.HasPrefix(existing.Name, bindingNamePrefix) {
				continue // 非平台管理，不动
			}
			group := ""
			if len(existing.Subjects) > 0 {
				group = existing.Subjects[0].Name
			}
			if _, stillWant := groups[group]; !stillWant {
				if err := client.RbacV1().RoleBindings(ns).Delete(ctx, existing.Name, metav1.DeleteOptions{}); err == nil {
					res.Deleted++
				}
			}
		}
	}
	return res
}

// bindingName 生成平台管理的 RoleBinding 名（ns + group，冒号转连字符避免非法字符）
func bindingName(ns, group string) string {
	short := strings.ReplaceAll(strings.TrimPrefix(group, UserPrefix), ":", "-")
	return fmt.Sprintf("%s%s-%s", bindingNamePrefix, ns, short)
}
