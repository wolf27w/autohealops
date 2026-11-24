// Package executor 回滚 ControllerRevision 选择/解析纯函数测试（R75.5，不依赖真实集群）
package executor

import (
	"context"
	"fmt"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/fake"
)

func mkCR(rev int64) *appsv1.ControllerRevision {
	return &appsv1.ControllerRevision{Revision: rev}
}

func mkCRs(revs ...int64) []*appsv1.ControllerRevision {
	out := make([]*appsv1.ControllerRevision, 0, len(revs))
	for _, r := range revs {
		out = append(out, mkCR(r))
	}
	return out
}

func TestPickControllerRevision(t *testing.T) {
	cases := []struct {
		name       string
		revisions  []*appsv1.ControllerRevision
		toRevision int64
		current    int64
		wantRev    int64
		wantErr    bool
	}{
		{"精确命中非当前版本", mkCRs(1, 2, 3), 2, 3, 2, false},
		{"精确命中当前版本报错", mkCRs(1, 2, 3), 3, 3, 0, true},
		{"回退到小于当前的最大版本", mkCRs(1, 2, 3), 0, 3, 2, false},
		{"目标版本不存在", mkCRs(1, 2, 3), 9, 3, 0, true},
		{"空列表", nil, 0, 3, 0, true},
		{"仅一个版本回退", mkCRs(5), 0, 5, 0, true},
		{"current=0 时取次新版本", mkCRs(1, 2, 3), 0, 0, 2, false},
		{"乱序列表回退仍正确", mkCRs(3, 1, 2), 0, 3, 2, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, gotRev, err := pickControllerRevision(c.revisions, c.toRevision, c.current)
			if c.wantErr {
				if err == nil {
					t.Fatalf("期望报错，但成功返回 rev=%d", gotRev)
				}
				return
			}
			if err != nil {
				t.Fatalf("期望成功，但报错: %v", err)
			}
			if gotRev != c.wantRev {
				t.Fatalf("期望 rev=%d，得到 %d", c.wantRev, gotRev)
			}
			if got == nil || got.Revision != c.wantRev {
				t.Fatalf("返回对象 revision 不符: %+v", got)
			}
		})
	}
}

func TestTemplateFromControllerRevision(t *testing.T) {
	raw := []byte(`{"spec":{"template":{"metadata":{"labels":{"app":"demo"}},"spec":{"containers":[{"name":"main","image":"nginx:1.25"}]},"$patch":"replace"}}}`)
	cr := &appsv1.ControllerRevision{Data: runtime.RawExtension{Raw: raw}}
	pt, err := templateFromControllerRevision(cr)
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	if len(pt.Spec.Containers) != 1 {
		t.Fatalf("期望 1 个容器，得到 %d", len(pt.Spec.Containers))
	}
	if pt.Spec.Containers[0].Image != "nginx:1.25" {
		t.Fatalf("镜像不符: %q", pt.Spec.Containers[0].Image)
	}
	if pt.Labels["app"] != "demo" {
		t.Fatalf("标签不符: %v", pt.Labels)
	}
}

func TestTemplateFromControllerRevisionInvalid(t *testing.T) {
	cr := &appsv1.ControllerRevision{Data: runtime.RawExtension{Raw: []byte("{not-json")}}
	if _, err := templateFromControllerRevision(cr); err == nil {
		t.Fatal("期望非法 JSON 报错，但成功了")
	}
}

// crPodTemplateData 构造 STS/DS ControllerRevision.Data（替换 spec.template 的 strategic merge patch）
func crPodTemplateData(image string) []byte {
	return []byte(fmt.Sprintf(
		`{"spec":{"template":{"metadata":{"labels":{"app":"demo"}},"spec":{"containers":[{"name":"main","image":%q}]},"$patch":"replace"}}}`,
		image))
}

// mkCRWithOwner 构造带 ownerRef 的 ControllerRevision（模拟 STS/DS 控制器产物）
func mkCRWithOwner(rev int64, uid types.UID, name, ns, image string) *appsv1.ControllerRevision {
	return &appsv1.ControllerRevision{
		ObjectMeta: metav1.ObjectMeta{
			Name:      fmt.Sprintf("%s-%d", name, rev),
			Namespace: ns,
			OwnerReferences: []metav1.OwnerReference{
				{UID: uid, Kind: "StatefulSet", Name: name},
			},
			CreationTimestamp: metav1.Now(),
		},
		Revision: rev,
		Data:     runtime.RawExtension{Raw: crPodTemplateData(image)},
	}
}

// TestListControllerRevisionRevisions 验证 STS/DS 走 ControllerRevision 的发布历史解析：
// 按 revision 降序、镜像取自模板、当前 revision 由注解解析。
func TestListControllerRevisionRevisions(t *testing.T) {
	ns, name, uid := "qa-bgs", "bgs-sts", types.UID("sts-uid-1")
	clientset := fake.NewSimpleClientset(
		mkCRWithOwner(1, uid, name, ns, "nginx:1.25"),
		mkCRWithOwner(2, uid, name, ns, "nginx:1.26"),
	)

	items, curRev, err := listControllerRevisionRevisions(context.Background(), clientset, ns, name, uid, 2)
	if err != nil {
		t.Fatalf("listControllerRevisionRevisions 失败: %v", err)
	}
	if curRev != 2 {
		t.Fatalf("期望当前 revision=2，得到 %d", curRev)
	}
	if len(items) != 2 {
		t.Fatalf("期望 2 个 revision，得到 %d", len(items))
	}
	if items[0].Revision != 2 || !items[0].IsCurrent {
		t.Fatalf("rev2 应为当前且排首位: %+v", items[0])
	}
	if len(items[0].Images) == 0 || items[0].Images[0] != "nginx:1.26" {
		t.Fatalf("rev2 镜像不符: %v", items[0].Images)
	}
	if items[0].CreatedAt == "" {
		t.Fatalf("rev2 创建时间不应为空")
	}
	if items[0].Replicaset == "" {
		t.Fatalf("rev2 应携带 ControllerRevision 名")
	}
	if items[1].Revision != 1 || items[1].IsCurrent {
		t.Fatalf("rev1 不应为当前: %+v", items[1])
	}
}

// TestListControllerRevisionRevisionsNoAnnotation 验证注解缺失时当前 revision 取最大。
func TestListControllerRevisionRevisionsNoAnnotation(t *testing.T) {
	ns, name, uid := "qa-bgs", "bgs-ds", types.UID("ds-uid-1")
	clientset := fake.NewSimpleClientset(
		mkCRWithOwner(1, uid, name, ns, "nginx:1.25"),
		mkCRWithOwner(3, uid, name, ns, "nginx:1.27"),
		mkCRWithOwner(2, uid, name, ns, "nginx:1.26"),
	)

	items, curRev, err := listControllerRevisionRevisions(context.Background(), clientset, ns, name, uid, 0)
	if err != nil {
		t.Fatalf("listControllerRevisionRevisions 失败: %v", err)
	}
	if curRev != 3 {
		t.Fatalf("注解缺失应取最大 revision=3，得到 %d", curRev)
	}
	if len(items) != 3 {
		t.Fatalf("期望 3 个 revision，得到 %d", len(items))
	}
	if items[0].Revision != 3 || !items[0].IsCurrent {
		t.Fatalf("最大 revision 应为当前且排首位: %+v", items[0])
	}
}

// TestGetControllerRevisionTemplate 验证 STS/DS 的 revision 模板读取（ControllerRevision → YAML）
func TestGetControllerRevisionTemplate(t *testing.T) {
	ns, name, uid := "qa-bgs", "bgs-sts", types.UID("sts-uid-1")
	clientset := fake.NewSimpleClientset(
		mkCRWithOwner(1, uid, name, ns, "nginx:1.25"),
		mkCRWithOwner(2, uid, name, ns, "nginx:1.26"),
	)

	y, objName, err := getControllerRevisionTemplate(context.Background(), clientset, ns, uid, 2)
	if err != nil {
		t.Fatalf("getControllerRevisionTemplate 失败: %v", err)
	}
	if objName == "" {
		t.Fatalf("应返回 revision 对象名，得到空")
	}
	if y == "" || !strings.Contains(y, "nginx:1.26") {
		t.Fatalf("模板 YAML 应包含镜像 nginx:1.26，得到: %q", y)
	}
	if strings.Contains(y, "managedFields") {
		t.Fatalf("模板 YAML 不应包含 managedFields: %q", y)
	}
}

// TestGetControllerRevisionTemplateMissing 验证 revision 不存在时报错。
func TestGetControllerRevisionTemplateMissing(t *testing.T) {
	ns, name, uid := "qa-bgs", "bgs-sts", types.UID("sts-uid-1")
	clientset := fake.NewSimpleClientset(
		mkCRWithOwner(1, uid, name, ns, "nginx:1.25"),
	)
	if _, _, err := getControllerRevisionTemplate(context.Background(), clientset, ns, uid, 99); err == nil {
		t.Fatal("期望 revision 不存在时报错，但成功了")
	}
}
