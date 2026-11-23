// Package executor 回滚 ControllerRevision 选择/解析纯函数测试（R75.5，不依赖真实集群）
package executor

import (
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	"k8s.io/apimachinery/pkg/runtime"
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
