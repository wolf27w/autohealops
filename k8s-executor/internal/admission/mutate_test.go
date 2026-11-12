package admission

import (
	"encoding/json"
	"strings"
	"testing"

	"go.uber.org/zap"
	admissionv1 "k8s.io/api/admission/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
)

func nopLogger() *zap.Logger { return zap.NewNop() }

func testServer(t *testing.T, rulesJSON string) *Server {
	t.Helper()
	rules, err := ParseRules([]byte(rulesJSON))
	if err != nil {
		t.Fatalf("parse rules: %v", err)
	}
	return NewServer(rules, ":0", nopLogger())
}

func review(t *testing.T, kind string, obj map[string]interface{}) *admissionv1.AdmissionReview {
	t.Helper()
	raw, err := json.Marshal(obj)
	if err != nil {
		t.Fatalf("marshal obj: %v", err)
	}
	return &admissionv1.AdmissionReview{
		Request: &admissionv1.AdmissionRequest{
			UID:       types.UID("test-uid"),
			Kind:      metav1.GroupVersionKind{Kind: kind},
			Operation: admissionv1.Create,
			Namespace: "default",
			Name:      "demo",
			Object:    runtime.RawExtension{Raw: raw},
		},
	}
}

func TestMutateClampReplicas(t *testing.T) {
	s := testServer(t, `{"rules":[{"namespaces":["default"],"minReplicas":2,"maxReplicas":5}]}`)
	ar := review(t, "Deployment", map[string]interface{}{
		"metadata": map[string]interface{}{"name": "demo"},
		"spec": map[string]interface{}{
			"replicas": 10,
			"template": map[string]interface{}{"spec": map[string]interface{}{"containers": []interface{}{map[string]interface{}{"name": "c1"}}}},
		},
	})
	resp := s.mutate(ar)
	if !resp.Allowed {
		t.Fatalf("expected allowed, got deny: %v", resp.Result)
	}
	if len(resp.Patch) == 0 {
		t.Fatal("expected patch")
	}
	var ops []patchOp
	if err := json.Unmarshal(resp.Patch, &ops); err != nil {
		t.Fatalf("unmarshal patch: %v", err)
	}
	found := false
	for _, op := range ops {
		if op.Path == "/spec/replicas" && op.Value == float64(5) {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected replicas clamp to 5, got %s", resp.Patch)
	}
}

func TestMutateFillDefaults(t *testing.T) {
	s := testServer(t, `{"rules":[{"namespaces":["default"],"defaultRequests":{"cpu":"100m","memory":"128Mi"},"defaultLimits":{"cpu":"500m","memory":"512Mi"}}]}`)
	ar := review(t, "Deployment", map[string]interface{}{
		"metadata": map[string]interface{}{"name": "demo"},
		"spec": map[string]interface{}{
			"template": map[string]interface{}{"spec": map[string]interface{}{"containers": []interface{}{map[string]interface{}{"name": "c1", "resources": map[string]interface{}{"limits": map[string]interface{}{"memory": "1Gi"}}}}}},
		},
	})
	resp := s.mutate(ar)
	if !resp.Allowed {
		t.Fatalf("expected allowed: %v", resp.Result)
	}
	patch := string(resp.Patch)
	if !strings.Contains(patch, `"cpu":"100m"`) || !strings.Contains(patch, `"memory":"1Gi"`) {
		t.Fatalf("expected defaults cpu=100m and preserved limit memory=1Gi, got %s", patch)
	}
}

func TestMutateIgnoreLabel(t *testing.T) {
	s := testServer(t, `{"rules":[{"namespaces":["default"],"minReplicas":3}]}`)
	ar := review(t, "Deployment", map[string]interface{}{
		"metadata": map[string]interface{}{"name": "demo", "labels": map[string]interface{}{"kubedoor-ignore": "true"}},
		"spec": map[string]interface{}{
			"replicas": 1,
			"template": map[string]interface{}{"spec": map[string]interface{}{"containers": []interface{}{}}},
		},
	})
	resp := s.mutate(ar)
	if !resp.Allowed || len(resp.Patch) != 0 {
		t.Fatalf("expected pass-through for ignored object, got allowed=%v patch=%s", resp.Allowed, resp.Patch)
	}
}

func TestMutateFailClosedOnBadObject(t *testing.T) {
	s := testServer(t, `{"rules":[{"namespaces":["default"],"minReplicas":3}]}`)
	ar := &admissionv1.AdmissionReview{
		Request: &admissionv1.AdmissionRequest{
			UID:       types.UID("u"),
			Kind:      metav1.GroupVersionKind{Kind: "Deployment"},
			Operation: admissionv1.Create,
			Namespace: "default",
			Object:    runtime.RawExtension{Raw: []byte(`{"spec": "not-an-object"}`)},
		},
	}
	resp := s.mutate(ar)
	if resp.Allowed {
		t.Fatal("expected deny (fail-closed) on unparseable object")
	}
	if resp.Result == nil || resp.Result.Message == "" {
		t.Fatal("expected explicit deny message")
	}
}
