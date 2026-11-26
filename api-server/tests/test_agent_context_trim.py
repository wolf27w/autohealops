"""ReAct 上下文瘦身 + 会话入口场景清单（2026-11-26 老板口径）单测。"""
import unittest

from app.services import agent_loop as al


class TestTrimMessages(unittest.TestCase):
    def test_truncate_and_keep_recent(self):
        msgs = [{"role": "user", "content": "初始"}]
        for i in range(12):
            msgs.append({"role": "tool", "tool": f"t{i}", "content": f"OUT-{i}-" + "x" * 5000})
        al._trim_agent_messages(msgs)
        tools = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(len(tools), al.AGENT_KEEP_TOOL_MSGS)
        self.assertNotIn("OUT-0-", str(msgs))
        self.assertIn("OUT-11-", str(msgs))
        self.assertTrue(any("已省略的较早工具输出" in (m.get("content") or "") for m in msgs))
        # 单条截断
        self.assertTrue(all(len(m["content"]) <= al.AGENT_TOOL_MSG_CHARS + 20 for m in tools))

    def test_marker_not_duplicated(self):
        msgs = [{"role": "user", "content": "初始"}]
        for i in range(12):
            msgs.append({"role": "tool", "tool": f"t{i}", "content": "x" * 3000})
        al._trim_agent_messages(msgs)
        for i in range(12, 20):
            msgs.append({"role": "tool", "tool": f"t{i}", "content": "x" * 3000})
            al._trim_agent_messages(msgs)
        markers = [m for m in msgs if "已省略的较早工具输出" in (m.get("content") or "")]
        self.assertEqual(len(markers), 1)

    def test_small_history_untouched(self):
        msgs = [{"role": "user", "content": "a"}, {"role": "tool", "tool": "t", "content": "b"}]
        al._trim_agent_messages(msgs)
        self.assertEqual(len(msgs), 2)


class TestScenarioChecklist(unittest.TestCase):
    def test_oom(self):
        sc = al.detect_scenario({"message": "容器 OOMKilled 反复重启"}, {})
        self.assertEqual(sc, "oom")
        self.assertIn("collect_oom_evidence", al.scenario_checklist_text(sc))

    def test_startup(self):
        sc = al.detect_scenario({"message": "CrashLoopBackOff 启动失败"}, {})
        self.assertEqual(sc, "startup_failure")
        self.assertIn("collect_startup_evidence", al.scenario_checklist_text(sc))

    def test_default_probe(self):
        sc = al.detect_scenario({"message": "readiness 探针失败"}, {})
        self.assertEqual(sc, "default")
        text = al.scenario_checklist_text(sc)
        self.assertIn("describe_pod", text)
        self.assertIn("search_logs", text)
        self.assertIn("探针", text)


if __name__ == "__main__":
    unittest.main()
