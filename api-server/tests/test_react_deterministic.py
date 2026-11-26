# -*- coding: utf-8 -*-
"""ReAct 降级时的确定性收尾 + 首错行优先级（2026-11-26）

现场（qa-asfp/dgis-module-universals）：
- ReAct 已通过 collect_startup_evidence 拿到崩溃日志（含 APPLICATION FAILED TO START +
  ParserConfig.getProvider() 不存在），但循环 total_timeout 后直接输出「未能确定根因」——
  证据在手却不说，老板看到的就是「诊断不行」。
- 同时 first_error_line 会先命中日志里无关的 WARN（nacosGracefulShutdownDelegate），
  模型拿到的「首错行」与真因无关。

锁住：
1. 已采证据命中硬错误（fastjson 类冲突 / NoSuchMethod / OOM / 缺类 / 缺文件）→ 降级时给确定性结论；
2. 没有硬错误 → 仍回「未能确定根因」（不编）；
3. first_error_line 优先硬错误行，不被前面的 WARN 抢走。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_react_deterministic -v
"""
import unittest

from app.services import agent_loop
from app.services.termination_evidence import first_error_line

FASTJSON_OUTPUT = """【启动失败证据包】dgis-module-universals-78b9d9865-fjtnp（qa-asfp）
- 容器 dgis-module-universals: ready=False restarts=4 waiting=- lastTerminated={exitCode=1, reason=Error}
- 首错行（日志）：
  · [L6] org.springframework.boot.diagnostics.LoggingFailureAnalysisReporter :
APPLICATION FAILED TO START
Description:
An attempt was made to call a method that does not exist. The attempt was made from the following location:
    cn.gov.caac.dgis.framework.core.config.FastJsonSecurityConfiguration.<init>(FastJsonSecurityConfiguration.java:14)
The following method did not exist:
    'com.alibaba.fastjson2.reader.ObjectReaderProvider com.alibaba.fastjson.parser.ParserConfig.getProvider()'
Action:
Correct the classpath of your application so that it contains compatible versions of the classes
"""


def _state(output, tool="collect_startup_evidence"):
    return {"rounds": 3, "tool_steps": [{"step": 1, "tool": tool, "success": True, "output": output}]}


class TestDeterministicRootCause(unittest.TestCase):
    def test_fastjson_conflict_detected(self):
        out = agent_loop._deterministic_root_cause(_state(FASTJSON_OUTPUT), 0.0)
        self.assertIsNotNone(out)
        self.assertIn("依赖冲突", out["root_cause"])
        self.assertIn("fastjson", out["root_cause"].lower())
        self.assertTrue(out["suggestions"])
        self.assertEqual(out["degrade_reason"], "deterministic_evidence")
        self.assertEqual(out["root_cause_layer"], "B")

    def test_no_hard_evidence_returns_none(self):
        self.assertIsNone(agent_loop._deterministic_root_cause(_state("一切正常 INFO 没有错误"), 0.0))
        self.assertIsNone(agent_loop._deterministic_root_cause({"tool_steps": []}, 0.0))

    def test_failed_tool_output_not_used(self):
        state = {"rounds": 1, "tool_steps": [
            {"tool": "inspect_jar", "success": False, "output": "java.lang.NoSuchMethodError x"}]}
        self.assertIsNone(agent_loop._deterministic_root_cause(state, 0.0))


class TestFirstErrorLinePriority(unittest.TestCase):
    def test_prefers_hard_error_over_earlier_warn(self):
        text = "\n".join([
            "WARN  7 --- [main] o.s.c.a.AnnotationConfigApplicationContext : Exception thrown from ApplicationListener",
            "com.tongweb.container.core.StandardService : Stopping service [TongWeb]",
            "ERROR 7 --- LoggingFailureAnalysisReporter : ",
            "APPLICATION FAILED TO START",
            "The following method did not exist:",
            "'com.alibaba.fastjson.parser.ParserConfig.getProvider()'",
        ])
        line = first_error_line(text)
        self.assertIn("APPLICATION FAILED TO START", line)

    def test_falls_back_to_normal_marker(self):
        self.assertIn("failed to connect", first_error_line("INFO start\nWARN slow\nfailed to connect to db"))


if __name__ == "__main__":
    unittest.main()
