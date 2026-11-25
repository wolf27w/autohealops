# -*- coding: utf-8 -*-
"""「禁止异常即删 Pod」闸门的纯函数测试（2026-11-25 老板口径）"""
import unittest

from app.services import alert_auto_service as aas


class TestExitCode(unittest.TestCase):
    def test_exit_code_variants(self):
        self.assertEqual(aas._exit_code_of("lastStateExitCode=1"), 1)
        self.assertEqual(aas._exit_code_of("lastStateExitCode: 137"), 137)
        self.assertEqual(aas._exit_code_of("exitCode=137"), 137)
        self.assertIsNone(aas._exit_code_of("no code here"))


class TestCodeFaultSignal(unittest.TestCase):
    def test_framework_exception_is_code_fault(self):
        self.assertTrue(aas.looks_like_code_fault(
            "APPLICATION FAILED TO START\nCaused by: java.lang.IllegalStateException: db url missing"))

    def test_oom_is_not_code_fault(self):
        self.assertFalse(aas.looks_like_code_fault("lastStateExitCode=137 reason=OOMKilled"))

    def test_sigterm_is_not_code_fault(self):
        self.assertFalse(aas.looks_like_code_fault("exitCode=143"))

    def test_exit_code_1_is_code_fault(self):
        self.assertTrue(aas.looks_like_code_fault("lastStateExitCode=1"))

    def test_empty_is_not_code_fault(self):
        self.assertFalse(aas.looks_like_code_fault(""))


class TestDeletePodDetection(unittest.TestCase):
    def test_delete_pod_command(self):
        self.assertTrue(aas._is_delete_pod_action({"command": "kubectl delete pod web-1"}))
        self.assertTrue(aas._is_delete_pod_action({"command": "kubectl delete pods -l app=web"}))

    def test_platform_rollback_is_not_delete_pod(self):
        self.assertFalse(aas._is_delete_pod_action({"platform_action": "rollback_deployment"}))

    def test_patch_env_is_not_delete_pod(self):
        self.assertFalse(aas._is_delete_pod_action({"command": "kubectl set env deploy/web A=1"}))


if __name__ == "__main__":
    unittest.main()
