// R6 实时状态推送：把 apiserver 的 Pod / Deployment Watch 事件以 NDJSON 流式透传给
// api-server（再转 WebSocket 给浏览器）。纯 HTTP，零 proto 改动。
//
// GET /watch?cluster_id=&namespace=&name=（name=Deployment 名，可选；给则同时 watch 该 Deployment）
// 每条 = 一行 JSON：{"res":"pod|deploy","ev":"SNAPSHOT|ADDED|MODIFIED|DELETED", ...}
package logstream

import (
	"encoding/json"
	"net/http"
	"strconv"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func podReady(p *corev1.Pod) bool {
	for _, c := range p.Status.Conditions {
		if c.Type == corev1.PodReady {
			return c.Status == corev1.ConditionTrue
		}
	}
	return false
}

func podRestarts(p *corev1.Pod) int32 {
	var n int32
	for _, cs := range p.Status.ContainerStatuses {
		n += cs.RestartCount
	}
	return n
}

func podLine(ev string, p *corev1.Pod) []byte {
	containers := make([]string, 0, len(p.Spec.Containers))
	for _, c := range p.Spec.Containers {
		containers = append(containers, c.Name)
	}
	m := map[string]any{
		"res": "pod", "ev": ev, "namespace": p.Namespace, "name": p.Name,
		"phase": string(p.Status.Phase), "ready": podReady(p), "restarts": podRestarts(p),
		"node": p.Spec.NodeName, "podIP": p.Status.PodIP, "containers": containers,
		"deployment": deploymentOf(p.Labels),
		"startTime":  p.Status.StartTime,
	}
	b, _ := json.Marshal(m)
	return append(b, '\n')
}

func deployLine(ev string, d *appsv1.Deployment) []byte {
	m := map[string]any{
		"res": "deploy", "ev": ev, "namespace": d.Namespace, "name": d.Name,
		"replicas": d.Status.Replicas, "readyReplicas": d.Status.ReadyReplicas,
		"updatedReplicas": d.Status.UpdatedReplicas, "availableReplicas": d.Status.AvailableReplicas,
		"observedGeneration": d.Status.ObservedGeneration, "generation": d.Generation,
	}
	b, _ := json.Marshal(m)
	return append(b, '\n')
}

// deploymentOf 从 Pod 标签里尽力推断所属 Deployment（常见 name / app 标签）
func deploymentOf(labels map[string]string) string {
	for _, k := range []string{"app", "app.kubernetes.io/name", "k8s-app", "name"} {
		if v := labels[k]; v != "" {
			return v
		}
	}
	return ""
}

// handleWatch GET /watch?cluster_id=&namespace=&name=
func (s *Server) handleWatch(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	cid, err := strconv.ParseUint(q.Get("cluster_id"), 10, 64)
	if err != nil {
		http.Error(w, "cluster_id 非法", http.StatusBadRequest)
		return
	}
	ns := q.Get("namespace")
	name := q.Get("name")
	if ns == "" {
		http.Error(w, "namespace 必填", http.StatusBadRequest)
		return
	}
	cc, err := s.manager.GetClient(r.Context(), uint(cid))
	if err != nil {
		http.Error(w, "集群客户端不可用: "+err.Error(), http.StatusBadGateway)
		return
	}

	w.Header().Set("Content-Type", "application/x-ndjson")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	flusher, _ := w.(http.Flusher)
	ctx := r.Context()
	out := make(chan []byte, 256)

	// 初始快照（Pod + 指定 Deployment）
	go func() {
		if pods, e := cc.Clientset.CoreV1().Pods(ns).List(ctx, metav1.ListOptions{}); e == nil {
			for i := range pods.Items {
				out <- podLine("SNAPSHOT", &pods.Items[i])
			}
		}
		if name != "" {
			if d, e := cc.Clientset.AppsV1().Deployments(ns).Get(ctx, name, metav1.GetOptions{}); e == nil {
				out <- deployLine("SNAPSHOT", d)
			}
		}
	}()

	// watch Pods
	go func() {
		wi, e := cc.Clientset.CoreV1().Pods(ns).Watch(ctx, metav1.ListOptions{})
		if e != nil {
			return
		}
		defer wi.Stop()
		for ev := range wi.ResultChan() {
			if p, ok := ev.Object.(*corev1.Pod); ok {
				out <- podLine(string(ev.Type), p)
			}
		}
	}()

	// watch Deployment（可选）
	if name != "" {
		go func() {
			wi, e := cc.Clientset.AppsV1().Deployments(ns).Watch(ctx, metav1.ListOptions{
				FieldSelector: "metadata.name=" + name,
			})
			if e != nil {
				return
			}
			defer wi.Stop()
			for ev := range wi.ResultChan() {
				if d, ok := ev.Object.(*appsv1.Deployment); ok {
					out <- deployLine(string(ev.Type), d)
				}
			}
		}()
	}

	for {
		select {
		case <-ctx.Done():
			return
		case b := <-out:
			if _, werr := w.Write(b); werr != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
	}
}
