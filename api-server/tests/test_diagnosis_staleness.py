# -*- coding: utf-8 -*-
"""诊断缓存失效测试：Pod 重建后旧诊断不得复用（2026-11-25）"""
import unittest

from app.services import alert_auto_service as aas


class _Alert:
    def __init__(self, resource_type="pod", resource_name="web-abc"):
        self.resource_type = resource_type
        self.resource_name = resource_name


class TestPodRefs(unittest.TestCase):
    def test_parse_both_forms(self):
        d = {"evidence_refs": ["pod/web-1", "pod:web-2", "container:app:exitCode=1", "image:x:1→2"]}
        self.assertEqual(aas._diagnosis_pod_refs(d), {"web-1", "web-2"})

    def test_empty(self):
        self.assertEqual(aas._diagnosis_pod_refs({}), set())
        self.assertEqual(aas._diagnosis_pod_refs(None), set())


class TestStale(unittest.TestCase):
    def test_same_pod_not_stale(self):
        d = {"evidence_refs": ["pod/web-abc"]}
        self.assertFalse(aas._diagnosis_stale_for_pod(d, _Alert(resource_name="web-abc")))

    def test_new_pod_is_stale(self):
        d = {"evidence_refs": ["pod/web-abc"]}
        self.assertTrue(aas._diagnosis_stale_for_pod(d, _Alert(resource_name="web-xyz")))

    def test_no_pod_ref_keeps_old_behavior(self):
        d = {"evidence_refs": ["image:a:1→2"]}
        self.assertFalse(aas._diagnosis_stale_for_pod(d, _Alert(resource_name="web-xyz")))

    def test_non_pod_resource_not_stale(self):
        d = {"evidence_refs": ["pod/web-abc"]}
        self.assertFalse(aas._diagnosis_stale_for_pod(
            d, _Alert(resource_type="deployment", resource_name="web")))


if __name__ == "__main__":
    unittest.main()
