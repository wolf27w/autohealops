package watcher

import (
	"context"
	"testing"
	"time"

	"go.uber.org/zap"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/watch"
)

type captureSink struct{ events []AlertEvent }

func (c *captureSink) Report(ev AlertEvent) uint                         { c.events = append(c.events, ev); return 1 }
func (c *captureSink) Resolve(uint, string, string, string, ResolveOpts) {}
func (c *captureSink) LocalFilters() bool                                { return false }

func newEvictedPod(uid, ns, name string, age time.Duration) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			UID:               types.UID("uid-" + uid),
			Name:              name,
			Namespace:         ns,
			CreationTimestamp: metav1.NewTime(time.Now().Add(-age)),
		},
		Status: corev1.PodStatus{Phase: corev1.PodFailed, Reason: "Evicted"},
	}
}

func TestShouldReportEvictedDedupeAndThrottle(t *testing.T) {
	w := &PodWatcher{
		evictedSeen:     map[string]time.Time{},
		evictedThrottle: map[string]time.Time{},
	}
	p := newEvictedPod("1", "prod-eft", "app-abc-xyz-12345", time.Minute)
	if !w.shouldReportEvicted(p) {
		t.Fatal("首次应上报")
	}
	if w.shouldReportEvicted(p) {
		t.Fatal("同一 Pod(UID) 不应重复上报")
	}
	// 同 workload 另一个 Pod，10 分钟内应被节流
	p2 := newEvictedPod("2", "prod-eft", "app-abc-xyz-67890", time.Minute)
	if w.shouldReportEvicted(p2) {
		t.Fatal("同 workload 10 分钟内应节流")
	}
	// 另一个 workload 应正常上报
	p3 := newEvictedPod("3", "prod-eft", "other-abc-xyz-12345", time.Minute)
	if !w.shouldReportEvicted(p3) {
		t.Fatal("不同 workload 应上报")
	}
}

func TestPodEvictedEndToEnd(t *testing.T) {
	cap := &captureSink{}
	SetSink(cap)
	defer SetSink(nil)

	w := &PodWatcher{
		clusterID: 990001, ctx: context.Background(), logger: zap.NewNop(),
		observed: map[string]*observedFault{}, policies: map[string]*policyConfig{},
		evictedSeen: map[string]time.Time{}, evictedThrottle: map[string]time.Time{},
	}

	now := time.Now()
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			UID: types.UID("u-1"), Name: "app-abc-1-2", Namespace: "prod-eft",
			CreationTimestamp: metav1.NewTime(now.Add(-time.Minute)),
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodFailed, Reason: "Evicted",
			Message: "Pod was rejected: The node had condition: [DiskPressure].",
		},
	}
	w.handlePodEvent(watch.Event{Type: watch.Added, Object: pod})
	if len(cap.events) != 1 || cap.events[0].FaultType != "PodEvicted" {
		t.Fatalf("最近驱逐应上报 PodEvicted，实际 %+v", cap.events)
	}

	// 同一 Pod 再来事件 → 不重复
	w.handlePodEvent(watch.Event{Type: watch.Modified, Object: pod})
	if len(cap.events) != 1 {
		t.Fatalf("同一 Pod 不应重复上报，实际 %d 条", len(cap.events))
	}

	// 历史遗留（3 天前创建）→ 不上报
	old := pod.DeepCopy()
	old.UID = types.UID("u-2")
	old.Name = "old-abc-1-2"
	old.CreationTimestamp = metav1.NewTime(now.Add(-72 * time.Hour))
	w.handlePodEvent(watch.Event{Type: watch.Added, Object: old})
	if len(cap.events) != 1 {
		t.Fatalf("历史 Evicted 不应上报，实际 %d 条", len(cap.events))
	}
}
