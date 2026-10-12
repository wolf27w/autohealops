package admission

import (
	"encoding/json"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func deploy1() *appsv1.Deployment {
	one := int32(1)
	return &appsv1.Deployment{
		ObjectMeta: metav1.ObjectMeta{Name: "demo", Namespace: "ns"},
		Spec: appsv1.DeploymentSpec{
			Replicas: &one,
			Template: corev1.PodTemplateSpec{Spec: corev1.PodSpec{
				Containers: []corev1.Container{{Name: "app", Image: "img:1"}},
			}},
		},
	}
}

func opsOf(t *testing.T, p []byte) []map[string]interface{} {
	t.Helper()
	var ops []map[string]interface{}
	if err := json.Unmarshal(p, &ops); err != nil {
		t.Fatalf("unmarshal patch: %v (%s)", err, p)
	}
	return ops
}

func TestResourcesAndReplicas(t *testing.T) {
	d := deploy1()
	govs := []Gov{{Container: "", Replicas: 3, CPURequest: "600m", CPULimit: "1500m", MemoryRequest: "1Gi", MemoryLimit: "1Gi"}}
	p, err := BuildPatch(d, govs)
	if err != nil {
		t.Fatal(err)
	}
	if p == nil {
		t.Fatal("expected patch")
	}
	ops := opsOf(t, p)
	if len(ops) != 2 {
		t.Fatalf("want 2 ops, got %d: %s", len(ops), p)
	}
	if ops[0]["path"] != "/spec/replicas" {
		t.Errorf("op0 path = %v", ops[0]["path"])
	}
	if ops[1]["path"] != "/spec/template/spec/containers/0/resources" {
		t.Errorf("op1 path = %v", ops[1]["path"])
	}
}

func TestIgnoreLabel(t *testing.T) {
	d := deploy1()
	d.Labels = map[string]string{IgnoreLabel: "true"}
	if p, _ := BuildPatch(d, []Gov{{Replicas: 2}}); p != nil {
		t.Errorf("expected nil patch when ignored, got %s", p)
	}
}

func TestJVMArgsMerge(t *testing.T) {
	d := deploy1()
	d.Spec.Template.Spec.Containers[0].Args = []string{"-Xms256m", "-XX:MaxMetaspaceSize=128m", "-jar", "app.jar"}
	govs := []Gov{{Container: "app", Replicas: -1, JVMXms: "512m", JVMXmx: "1024m"}}
	p, err := BuildPatch(d, govs)
	if err != nil || p == nil {
		t.Fatalf("expected patch, err=%v", err)
	}
	ops := opsOf(t, p)
	var args []interface{}
	found := false
	for _, o := range ops {
		if o["path"] == "/spec/template/spec/containers/0/args" {
			args = o["value"].([]interface{})
			found = true
		}
	}
	if !found {
		t.Fatalf("expected args op, got %s", p)
	}
	joined := ""
	for _, a := range args {
		joined += a.(string) + " "
	}
	if !contains(joined, "-Xms512m") || !contains(joined, "-Xmx1024m") || !contains(joined, "-XX:MaxMetaspaceSize=128m") {
		t.Errorf("bad merged args: %s", joined)
	}
}

func TestNoGov(t *testing.T) {
	d := deploy1()
	if p, _ := BuildPatch(d, nil); p != nil {
		t.Errorf("expected nil for empty govs")
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
