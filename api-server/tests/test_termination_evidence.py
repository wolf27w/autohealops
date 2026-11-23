# -*- coding: utf-8 -*-
"""容器退出证据解析（termination_evidence）单元测试

2026-11-23 老板反馈：服务启动失败时平台诊断"只说是探针问题"，没分析真正的启动失败原因。
本测试锁定 parse_describe_termination / first_error_line 两个纯函数的真实 describe 兼容性：
1. CrashLoop 容器有 Last State + Exit Code；正常容器无 Last State；
2. 乱文本/空文本不抛异常、返回空结构；
3. first_error_line 能提取第一条强错误行、无错误返回空串。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_termination_evidence -v
"""
import unittest

from app.services.termination_evidence import (
    apply_probe_guard, extract_exit_code, first_error_line, parse_describe_termination,
)

# 真实 kubectl describe pod 样例（缩进、字段名与真实输出一致）
DESCRIBE_CRASHLOOP = """Name:             gsa-service-oss-6bcc94bb9c-x7qgg
Namespace:        qa-gsa
Priority:         0
Node:             k8s-node41/192.168.6.22
Start Time:       Mon, 23 Nov 2026 10:00:00 +0800
Labels:           app=gsa-service
Status:           Running
IP:               10.0.0.1
Containers:
  app:
    Container ID:   containerd://abc123
    Image:          registry.example.com/gsa-service:1.2.3
    Image ID:       registry.example.com/gsa-service@sha256:deadbeef
    Port:           8080/TCP
    Host Port:      0/TCP
    State:          Waiting
      Reason:       CrashLoopBackOff
    Last State:     Terminated
      Reason:       Error
      Exit Code:    1
      Started:      Mon, 23 Nov 2026 10:00:00 +0800
      Finished:     Mon, 23 Nov 2026 10:00:30 +0800
    Ready:          False
    Restart Count:  4
    Limits:
      cpu:     2
      memory:  2Gi
    Requests:
      cpu:      100m
      memory:   256Mi
  sidecar:
    Container ID:   containerd://def456
    Image:          registry.example.com/sidecar:2.0.0
    State:          Running
      Started:      Mon, 23 Nov 2026 09:00:00 +0800
    Ready:          True
    Restart Count:  0
Conditions:
  Type              Status
  Initialized       True
  Ready             False
  ContainersReady   False
  PodScheduled      True
Events:
  Type     Reason     Age   From               Message
  ----     ------     ----  ----               -------
  Warning  BackOff    4m    kubelet            Back-off restarting failed container
  Warning  Unhealthy  4m    kubelet            Liveness probe failed: Get "http://10.0.0.1:8080/health": dial tcp 10.0.0.1:8080: connect: connection refused
"""


class TestParseDescribeTermination(unittest.TestCase):
    def test_crashloop_container_has_last_state_and_exit_code(self):
        r = parse_describe_termination(DESCRIBE_CRASHLOOP)
        by_name = {c["name"]: c for c in r["containers"]}
        self.assertIn("app", by_name)
        app = by_name["app"]
        self.assertEqual(app["state"], "Waiting")
        self.assertEqual(app["state_reason"], "CrashLoopBackOff")
        self.assertEqual(app["last_reason"], "Error")
        self.assertEqual(app["last_exit_code"], 1)
        self.assertTrue(app["last_started"])
        self.assertTrue(app["last_finished"])
        self.assertIs(app["ready"], False)
        self.assertEqual(app["restart_count"], 4)

    def test_normal_container_has_no_last_state(self):
        r = parse_describe_termination(DESCRIBE_CRASHLOOP)
        by_name = {c["name"]: c for c in r["containers"]}
        self.assertIn("sidecar", by_name)
        sidecar = by_name["sidecar"]
        self.assertEqual(sidecar["state"], "Running")
        self.assertIsNone(sidecar["last_reason"])
        self.assertIsNone(sidecar["last_exit_code"])
        self.assertIs(sidecar["ready"], True)
        self.assertEqual(sidecar["restart_count"], 0)

    def test_summary_contains_exit_code(self):
        r = parse_describe_termination(DESCRIBE_CRASHLOOP)
        self.assertIn("exitCode=1", r["summary"])
        self.assertIn("app", r["summary"])

    def test_garbage_text_no_exception(self):
        r = parse_describe_termination("这不是 describe 输出\n乱七八糟的一行\n" + "x" * 5000)
        self.assertEqual(r, {"containers": [], "summary": ""})

    def test_empty_and_none_no_exception(self):
        self.assertEqual(parse_describe_termination(""), {"containers": [], "summary": ""})
        self.assertEqual(parse_describe_termination(None), {"containers": [], "summary": ""})


class TestFirstErrorLine(unittest.TestCase):
    def test_finds_exception_line(self):
        logs = 'INFO 启动\nException in thread "main" java.lang.NullPointerException\n\tat com.foo.Bar.main(Bar.java:1)'
        self.assertEqual(
            first_error_line(logs),
            'Exception in thread "main" java.lang.NullPointerException',
        )

    def test_finds_caused_by(self):
        logs = ("Starting application...\n"
                "Caused by: java.lang.IllegalStateException: port 8080 in use")
        self.assertIn("Caused by", first_error_line(logs))

    def test_finds_failed_to(self):
        logs = "WARN 正在连接...\nFailed to start component [Connector[HTTP/1.1-8080]]"
        self.assertEqual(first_error_line(logs), "Failed to start component [Connector[HTTP/1.1-8080]]")

    def test_no_error_returns_empty(self):
        self.assertEqual(first_error_line("all good\nno problem here"), "")

    def test_empty_returns_empty(self):
        self.assertEqual(first_error_line(""), "")
        self.assertEqual(first_error_line(None), "")


class TestProbeGuard(unittest.TestCase):
    """2026-11-23：有 exitCode 证据时，禁止把「探针/健康检查」当根因（ReAct 与单次诊断共用）"""

    def test_corrects_probe_only_with_nonzero_exit(self):
        rc, code = apply_probe_guard("就绪探针配置不合理导致容器反复重启", "容器 app: LastState=Error, exitCode=1")
        self.assertEqual(code, 1)
        self.assertTrue(rc.startswith("【证据校正】"))
        self.assertIn("exitCode=1", rc)
        self.assertIn("就绪探针配置不合理", rc)

    def test_keeps_real_root_cause(self):
        rc, code = apply_probe_guard("应用启动时 DuplicateKeyException 导致退出，探针未通过是伴随现象",
                                     "LastState=Error, exitCode=1")
        self.assertEqual(code, 1)
        self.assertFalse(rc.startswith("【证据校正】"))

    def test_liveness_kill_exit_137_not_corrected(self):
        rc, code = apply_probe_guard("存活探针超时被 kubelet 杀死", "LastState=Error, exitCode=137")
        self.assertEqual(code, 137)
        self.assertFalse(rc.startswith("【证据校正】"))

    def test_no_exit_code_no_change(self):
        rc, code = apply_probe_guard("探针配置不合理", "无退出码证据")
        self.assertIsNone(code)
        self.assertEqual(rc, "探针配置不合理")

    def test_idempotent(self):
        rc1, _ = apply_probe_guard("探针配置不合理", "exitCode=2")
        rc2, _ = apply_probe_guard(rc1, "exitCode=2")
        self.assertEqual(rc1, rc2)

    def test_extract_exit_code_forms(self):
        self.assertEqual(extract_exit_code("Exit Code: 1"), 1)
        self.assertEqual(extract_exit_code("exitCode=-1"), -1)
        self.assertIsNone(extract_exit_code("no code"))


if __name__ == "__main__":
    unittest.main()
