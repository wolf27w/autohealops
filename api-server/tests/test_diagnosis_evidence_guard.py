# -*- coding: utf-8 -*-
"""诊断证据校验（幻觉拦截）单测 —— 复现 2026-11-24 两次真实幻觉。"""
import unittest

from app.services.diagnosis_evidence_guard import MARK, apply_guard, extract_claims, verify

LOGBACK_EVIDENCE = """[] ERROR in ch.qos.logback.core.rolling.RollingFileAppender[CONFIG_LOG_FILE]
 - Failed to create parent directories for [/home/job/logs/nacos/config.log]
java.lang.IllegalStateException: Logback configuration error detected
org.springframework.boot.SpringApplication - Application run failed"""

HALLUCINATED_RC = (
    "应用启动阶段直接失败：Spring Boot 启动过程中抛出 java.lang.IllegalStateException"
    "『必须提供非默认的 Nacos 账号凭据』，导致 Application run failed，进程退出。"
)


class TestExtractClaims(unittest.TestCase):
    def test_extracts_quoted_and_exception(self):
        quoted, excs = extract_claims(HALLUCINATED_RC)
        self.assertIn("必须提供非默认的 Nacos 账号凭据", quoted)
        self.assertTrue(any("IllegalStateException" in e for e in excs))


class TestVerify(unittest.TestCase):
    def test_hallucinated_quote_detected(self):
        ok, missing = verify(HALLUCINATED_RC, LOGBACK_EVIDENCE)
        self.assertFalse(ok)
        self.assertIn("必须提供非默认的 Nacos 账号凭据", missing)

    def test_true_evidence_passes(self):
        rc = "启动失败：java.lang.IllegalStateException『Logback configuration error detected』"
        ok, missing = verify(rc, LOGBACK_EVIDENCE)
        self.assertTrue(ok, msg=str(missing))

    def test_plain_conclusion_without_claims_passes(self):
        ok, missing = verify("镜像缺陷导致容器启动异常退出，回滚到上一版本", LOGBACK_EVIDENCE)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_missing_exception_class_detected(self):
        ok, missing = verify("java.lang.OutOfMemoryError 导致进程退出", LOGBACK_EVIDENCE)
        self.assertFalse(ok)
        self.assertIn("java.lang.OutOfMemoryError", missing)

    def test_short_name_fallback(self):
        # 日志只打短名时，全限定名也应命中
        rc = "抛出 IllegalStateException"
        ok, _ = verify(rc, "java.lang.IllegalStateException: boom")
        self.assertTrue(ok)


class TestApplyGuard(unittest.TestCase):
    def test_marks_untrusted(self):
        new_rc, missing = apply_guard(HALLUCINATED_RC, LOGBACK_EVIDENCE)
        self.assertTrue(new_rc.startswith(MARK))
        self.assertIn("必须提供非默认的 Nacos 账号凭据", new_rc)
        self.assertTrue(missing)

    def test_idempotent(self):
        once, _ = apply_guard(HALLUCINATED_RC, LOGBACK_EVIDENCE)
        twice, missing2 = apply_guard(once, LOGBACK_EVIDENCE)
        self.assertEqual(once, twice)
        self.assertEqual(missing2, [])

    def test_pass_through_when_supported(self):
        rc = "启动失败：java.lang.IllegalStateException『Logback configuration error detected』"
        new_rc, missing = apply_guard(rc, LOGBACK_EVIDENCE)
        self.assertEqual(new_rc, rc)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
