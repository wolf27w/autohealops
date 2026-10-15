# -*- coding: utf-8 -*-
"""关键路径单元测试（A3，标准库 unittest，无外部依赖）

运行：
    cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import os
import sys
import unittest

# 允许从 api-server 目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMessagingParse(unittest.TestCase):
    """IM 反馈解析（审批回复 → 动作 + 审批ID）"""

    def test_approve(self):
        from app.services.messaging import parse_feedback
        action, aid, _ = parse_feedback("批准 12")
        self.assertEqual(action, "approve")
        self.assertEqual(str(aid), "12")

    def test_reject(self):
        from app.services.messaging import parse_feedback
        action, aid, _ = parse_feedback("拒绝13")
        self.assertEqual(action, "reject")
        self.assertEqual(str(aid), "13")

    def test_noise(self):
        from app.services.messaging import parse_feedback
        action, aid, _ = parse_feedback("早上好")
        self.assertFalse(action)
        self.assertFalse(aid)


class TestToolRegistryValidate(unittest.TestCase):
    """工具参数校验（P0-3 护栏）"""

    def test_missing_required(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("exec_in_pod")
        ok, err = tool_registry.validate_args(tool, {})
        self.assertFalse(ok)
        self.assertIn("command", err)

    def test_type_error(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("query_db_health")
        ok, err = tool_registry.validate_args(tool, {"conn_id": "abc"})
        self.assertFalse(ok)

    def test_ok(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("exec_in_pod")
        ok, err = tool_registry.validate_args(tool, {"command": "ps aux"})
        self.assertTrue(ok, err)

    def test_unknown_tool(self):
        from app.services import tool_registry
        self.assertIsNone(tool_registry.get_tool("not_exist_tool"))


class TestCoerceArgs(unittest.TestCase):
    """参数类型容错（LLM 把 int 传成字符串 → 自动归一化，避免浪费工具预算）"""

    def test_str_to_int_search_logs(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("search_logs")
        out = tool_registry.coerce_args(tool, {"tail": "100"})
        self.assertEqual(out["tail"], 100)
        ok, err = tool_registry.validate_args(tool, out)
        self.assertTrue(ok, err)

    def test_str_to_int_rag_search(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("rag_search")
        out = tool_registry.coerce_args(tool, {"query": "oom", "top_k": "5"})
        self.assertEqual(out["top_k"], 5)
        ok, err = tool_registry.validate_args(tool, out)
        self.assertTrue(ok, err)

    def test_str_to_int_conn_id(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("query_db_health")
        out = tool_registry.coerce_args(tool, {"conn_id": "3"})
        self.assertEqual(out["conn_id"], 3)
        ok, err = tool_registry.validate_args(tool, out)
        self.assertTrue(ok, err)

    def test_uncoercible_keeps_type_error(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("query_db_health")
        out = tool_registry.coerce_args(tool, {"conn_id": "abc"})
        self.assertEqual(out["conn_id"], "abc")
        ok, err = tool_registry.validate_args(tool, out)
        self.assertFalse(ok)

    def test_float_int_and_bool(self):
        from app.services import tool_registry
        tool = {"args_schema": {"n": {"type": "int"}, "flag": {"type": "bool"}}}
        out = tool_registry.coerce_args(tool, {"n": 5.0, "flag": "true"})
        self.assertEqual(out["n"], 5)
        self.assertIs(out["flag"], True)

    def test_empty_and_none(self):
        from app.services import tool_registry
        tool = tool_registry.get_tool("search_logs")
        self.assertEqual(tool_registry.coerce_args(tool, {}), {})
        self.assertEqual(tool_registry.coerce_args(tool, None), {})

    def test_schema_exposes_type_in_args_desc(self):
        from app.services import tool_registry
        tools = {t["name"]: t for t in tool_registry.list_tools(domain="application")}
        self.assertIn("int", tools["search_logs"]["args"]["tail"])


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return iter(self._items)


class _FakeDB:
    """最小 async_session 替身（历史构建用，返回空历史）"""

    async def execute(self, *a, **k):
        return _FakeResult([])

    async def get(self, *a, **k):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAIClient:
    """按脚本返回决策；记录调用次数"""

    def __init__(self, decisions):
        self._decisions = list(decisions)
        self.calls = 0

    async def agent_decide(self, messages, tools, context, severity="high"):
        self.calls += 1
        if self._decisions:
            return self._decisions.pop(0)
        return {"action": "final_answer", "root_cause": "兜底", "suggestions": [], "handling_advice": ""}


class _FakeSession:
    id = 999
    cluster_id = 1
    namespace = "ns"
    pod_name = "pod"


def _tool_call(name="describe_pod", args=None):
    return {"action": "tool_call", "tool": name, "args": args or {}}


class TestAgentLoopBudget(unittest.TestCase):
    """深度诊断编排循环：重复调用不占步数 / 连续重复强制收敛 / 预算用尽强制出结论"""

    def _run(self, decisions, max_rounds=None):
        import asyncio
        from unittest import mock
        from app.services import agent_service

        fake_ai = _FakeAIClient(decisions)
        executed = []

        async def fake_exec(tool_name, args, session):
            executed.append((tool_name, args))
            return {"success": True, "output": f"{tool_name} ok", "error": ""}

        async def fake_persist(*a, **k):
            return None

        async def fake_max_steps():
            return agent_service.DEFAULT_MAX_TOOL_STEPS

        with mock.patch.object(agent_service, "ai_client", fake_ai), \
             mock.patch.object(agent_service, "_execute_tool", fake_exec), \
             mock.patch.object(agent_service, "_persist_message", fake_persist), \
             mock.patch.object(agent_service, "_get_max_tool_steps", fake_max_steps), \
             mock.patch.object(agent_service, "async_session", _FakeDB):
            res = asyncio.run(agent_service.run_agent_turn(_FakeSession(), "内存高", "c1", deep=True))
        return res, executed, fake_ai

    def test_duplicate_does_not_consume_step_budget(self):
        """1 次真实执行 + 2 次重复调用：真实步数只算 1（旧逻辑会白烧 3 个额度）"""
        res, executed, fake_ai = self._run([
            _tool_call("describe_pod"),           # 执行
            _tool_call("describe_pod"),           # 重复 → 不占额
            _tool_call("describe_pod"),           # 连续第 2 次重复 → 强制收敛
        ])
        real_steps = [t for t in res["tool_steps"] if not t.get("duplicate")]
        dup_steps = [t for t in res["tool_steps"] if t.get("duplicate")]
        self.assertEqual(len(executed), 1, "重复调用不应触发真实执行")
        self.assertEqual(len(real_steps), 1, "重复调用不应占用有效步数")
        self.assertEqual(len(dup_steps), 2)
        # 强制收敛轮会再问一次，最终拿到正常结论（非 _fallback）
        self.assertNotIn("_fallback", res["final_answer"])

    def test_budget_exhausted_forces_conclusion(self):
        """12 次有效不同调用后用尽预算 → 触发强制收敛轮，拿到 final_answer"""
        from app.services import agent_service
        calls = [_tool_call("exec_in_pod", {"command": f"cmd{i}"}) for i in range(agent_service.DEFAULT_MAX_TOOL_STEPS)]
        calls.append({"action": "final_answer", "root_cause": "内存泄漏", "suggestions": ["加大内存并排查"], "handling_advice": "重启并观察"})
        res, executed, fake_ai = self._run(calls)
        self.assertEqual(len(executed), agent_service.DEFAULT_MAX_TOOL_STEPS)
        self.assertEqual(fake_ai.calls, agent_service.DEFAULT_MAX_TOOL_STEPS + 1, "预算用尽后应追加一次强制收敛决策")
        self.assertEqual(res["final_answer"]["root_cause"], "内存泄漏")
        self.assertNotIn("_fallback", res["final_answer"])

    def test_force_conclude_failure_falls_back_with_reason(self):
        """强制收敛轮也拿不到结论 → 兜底结论需带真实原因（而非笼统的“达到最大步数”）"""
        res, executed, fake_ai = self._run([
            _tool_call("describe_pod"),
            _tool_call("describe_pod"),
            _tool_call("describe_pod"),
            _tool_call("describe_pod"),   # 收敛轮仍要求调工具 → 走兜底
        ])
        self.assertTrue(res["final_answer"].get("_fallback"))
        self.assertIn("重复调用", res["final_answer"]["root_cause"])


class TestMessagingRespOk(unittest.TestCase):
    """发送成功判定：HTTP 2xx + 业务返回码为 0；飞书 webhook 地址预检

    复现历史坑：飞书渠道误填「卡片回调地址」，对方返回 HTTP 200 + body null，
    旧逻辑「status==200 即 success」会误报成功但飞书收不到。
    """

    CALLBACK_URL = "https://ae.feishu.cn/oda/api/v1/card-action/callback"
    BOT_HOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"

    def test_feishu_callback_url_is_rejected(self):
        from app.services.messaging import check_webhook_url, resp_ok
        self.assertTrue(check_webhook_url("feishu", self.CALLBACK_URL))
        # 旧行为会误判成功，现在必须 False
        self.assertFalse(resp_ok("feishu", {"status": 200, "body": "null"}, self.CALLBACK_URL))

    def test_feishu_bot_hook_success(self):
        from app.services.messaging import resp_ok
        self.assertTrue(resp_ok("feishu", {"status": 200, "body": '{"code":0,"msg":"success"}'}, self.BOT_HOOK))
        self.assertTrue(resp_ok("feishu", {"status": 200, "body": '{"StatusCode":0,"StatusMessage":"success"}'}, self.BOT_HOOK))

    def test_feishu_bot_hook_error_code(self):
        from app.services.messaging import resp_ok
        self.assertFalse(resp_ok("feishu", {"status": 200, "body": '{"code":19021,"msg":"sign match fail"}'}, self.BOT_HOOK))

    def test_dingtalk_errcode(self):
        from app.services.messaging import resp_ok
        url = "https://oapi.dingtalk.com/robot/send?access_token=x"
        self.assertTrue(resp_ok("dingtalk", {"status": 200, "body": '{"errcode":0,"errmsg":"ok"}'}, url))
        self.assertFalse(resp_ok("dingtalk", {"status": 200, "body": '{"errcode":310000,"errmsg":"keywords not in content"}'}, url))

    def test_non_json_body_falls_back_to_http_status(self):
        from app.services.messaging import resp_ok
        url = "https://open.feishu.cn/open-apis/bot/v2/hook/abc123"
        self.assertTrue(resp_ok("feishu", {"status": 200, "body": ""}, url))
        self.assertFalse(resp_ok("feishu", {"status": 500, "body": "oops"}, url))


class TestNotifyConfigValidate(unittest.TestCase):
    """通知渠道配置校验（创建/更新共用，重点：应用机器人模式）"""

    def _v(self, payload):
        from app.api.notifications import _validate_config
        return _validate_config(payload)

    def test_webhook_needs_url(self):
        self.assertTrue(self._v({"channel_type": "feishu", "name": "x"}))
        self.assertFalse(self._v({"channel_type": "feishu", "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}))

    def test_app_mode_requires_creds_and_target(self):
        base = {"channel_type": "feishu", "send_mode": "app"}
        self.assertTrue(self._v(base))
        self.assertTrue(self._v({**base, "app_id": "cli_x", "app_secret": "s"}))          # 缺接收方
        self.assertTrue(self._v({**base, "app_id": "cli_x", "receive_id": "oc_1"}))      # 缺 secret
        self.assertFalse(self._v({**base, "app_id": "cli_x", "app_secret": "s", "receive_id": "oc_1"}))

    def test_app_mode_feishu_only(self):
        self.assertTrue(self._v({"channel_type": "dingtalk", "send_mode": "app",
                                 "app_id": "a", "app_secret": "s", "receive_id": "r"}))


class TestFeishuExtractText(unittest.TestCase):
    """飞书消息 content(JSON) → 文本抽取"""

    def test_text(self):
        from app.services.long_conn_client import _extract_text
        self.assertEqual(_extract_text("text", '{"text":"批准 1"}'), "批准 1")

    def test_post(self):
        from app.services.long_conn_client import _extract_text
        content = '{"title":"t","content":[[{"tag":"text","text":"拒绝"},{"tag":"text","text":" 2"}]]}'
        self.assertEqual(_extract_text("post", content), "拒绝 2")

    def test_bad_json_falls_back(self):
        from app.services.long_conn_client import _extract_text
        self.assertEqual(_extract_text("text", "not-json"), "not-json")
        self.assertEqual(_extract_text("image", '{"image_key":"x"}'), "")


class TestNotifySourceMatch(unittest.TestCase):
    """告警来源过滤（通知渠道 sources）"""

    def _cfg(self, sources):
        from types import SimpleNamespace
        return SimpleNamespace(sources=sources)

    def test_empty_sources_matches_all(self):
        from app.services.notification_service import _source_match
        for rt in ("pod", "node", "log", "db"):
            self.assertTrue(_source_match(self._cfg(""), rt))

    def test_explicit_sources(self):
        from app.services.notification_service import _source_match
        c = self._cfg('["log", "db"]')
        self.assertTrue(_source_match(c, "log"))
        self.assertTrue(_source_match(c, "db"))
        self.assertFalse(_source_match(c, "pod"))


class TestDutyTimeRange(unittest.TestCase):
    """值班时段匹配（白班/夜班按时间路由，左闭右开不重叠）"""

    def test_day_night_no_overlap(self):
        from app.services.on_duty_service import _in_time_range
        # 白班 09:00-18:00
        self.assertTrue(_in_time_range("09:00", "09:00", "18:00"))
        self.assertTrue(_in_time_range("17:59", "09:00", "18:00"))
        self.assertFalse(_in_time_range("18:00", "09:00", "18:00"))
        # 夜班 18:00-09:00（跨天）
        self.assertTrue(_in_time_range("18:00", "18:00", "09:00"))
        self.assertTrue(_in_time_range("23:30", "18:00", "09:00"))
        self.assertTrue(_in_time_range("08:59", "18:00", "09:00"))
        self.assertFalse(_in_time_range("09:00", "18:00", "09:00"))

    def test_empty_and_degenerate(self):
        from app.services.on_duty_service import _in_time_range
        self.assertFalse(_in_time_range("12:00", "", ""))
        self.assertFalse(_in_time_range("12:00", "10:00", "10:00"))


class TestResourceAlertPromql(unittest.TestCase):
    """容器资源告警 PromQL 口径（内存需排除 page cache 防误报）"""

    def test_memory_excludes_cache(self):
        from app.services.resource_alert_service import METRIC_META, PROMQL_TEMPLATES
        q = PROMQL_TEMPLATES["memory"]
        self.assertIn("container_memory_working_set_bytes", q)
        self.assertIn("container_memory_cache", q)
        self.assertIn("kube_pod_container_resource_limits", q)
        self.assertEqual(METRIC_META["memory"][0], "内存使用率（不含缓存）")

    def test_scan_interval_15s(self):
        from app.services.resource_alert_service import SCAN_INTERVAL
        self.assertEqual(SCAN_INTERVAL, 15)


class TestInspectionReportRender(unittest.TestCase):
    """巡检/诊断报告 → Markdown（下载文件用）"""

    def test_cluster_markdown(self):
        from types import SimpleNamespace
        from app.api.inspections import render_report_markdown
        rep = SimpleNamespace(
            id=7, task_type="cluster", conclusion="一切正常", root_cause="", suggestion="",
            raw_output='{"cluster_id":1,"health":"healthy","inspected_at":"2026-09-21 10:00",'
                       '"items":[{"category":"节点","name":"就绪","value":"3/3","ok":true}],'
                       '"stats":{"nodes":3}}',
            created_at="2026-09-21 10:00:00",
        )
        md = render_report_markdown(rep, "测试环境")
        self.assertIn("# 集群巡检报告 #7", md)
        self.assertIn("测试环境", md)
        self.assertIn("节点", md)
        self.assertIn("## 原始数据", md)

    def test_db_markdown(self):
        from types import SimpleNamespace
        from app.api.inspections import render_report_markdown
        rep = SimpleNamespace(
            id=9, task_type="db", conclusion="慢SQL偏多", root_cause="缺索引", suggestion="- 加索引",
            raw_output='{"db_name":"prod","db_type":"mysql"}', created_at="2026-09-21",
        )
        md = render_report_markdown(rep)
        self.assertIn("数据库巡检报告 #9", md)
        self.assertIn("prod", md)
        self.assertIn("加索引", md)


class TestInspectionSuggestionRender(unittest.TestCase):
    """优化建议渲染（结构化 JSON：SQL 前后对比 + 影响）"""

    def test_structured(self):
        from app.api.inspections import _render_suggestions
        raw = ('[{"title":"建复合索引","detail":"消除相关子查询",'
               '"sql_before":"SELECT 1","sql_after":"ALTER TABLE t ADD INDEX ix (a,b)",'
               '"impact":"预计 2400s → 0.03s"}]')
        out = "\n".join(_render_suggestions(raw))
        self.assertIn("建复合索引", out)
        self.assertIn("优化前 SQL", out)
        self.assertIn("ALTER TABLE", out)
        self.assertIn("预计", out)

    def test_plain_text_fallback(self):
        from app.api.inspections import _render_suggestions
        self.assertIn("hello", "\n".join(_render_suggestions("hello")))


class TestTerminatingStuckForceDelete(unittest.TestCase):
    """TerminatingStuck 免审批强删（老板口径：审不审都要删，不走人工审核）"""

    def _alert(self, name="app-7f9c-x2k", ns="default"):
        from types import SimpleNamespace
        return SimpleNamespace(resource_name=name, namespace=ns, fault_type="TerminatingStuck")

    def test_force_delete_command(self):
        from app.services.alert_auto_service import build_standard_steps
        steps = build_standard_steps(self._alert())
        self.assertEqual(len(steps), 1)
        cmd = steps[0]["command"]
        self.assertIn("kubectl delete pod app-7f9c-x2k -n default", cmd)
        self.assertIn("--force", cmd)
        self.assertIn("--grace-period=0", cmd)

    def test_illegal_name_rejected(self):
        from app.services.alert_auto_service import build_standard_steps
        self.assertEqual(build_standard_steps(self._alert(name="Bad_Name!")), [])
        self.assertEqual(build_standard_steps(self._alert(ns="Bad NS")), [])


class TestJvmDiagnosisInAppDiag(unittest.TestCase):
    """应用诊断：Java 应用自动采集 JVM 证据 + 容器日志口径（2026-10-15）"""

    def test_java_proc_regex(self):
        from app.services.agent_service import _JAVA_PROC_RE
        self.assertIsNotNone(_JAVA_PROC_RE.search("1234 /usr/lib/jvm/bin/java -Xmx2g -jar app.jar"))
        self.assertIsNotNone(_JAVA_PROC_RE.search("java"))
        self.assertIsNone(_JAVA_PROC_RE.search("1234 ps -eo pid,args"))
        self.assertIsNone(_JAVA_PROC_RE.search("-javaagent:/opt/x.jar"))

    def test_jvm_tool_registered(self):
        from app.services import tool_registry
        t = tool_registry.get_tool("jvm_diag")
        self.assertIsNotNone(t)
        self.assertEqual(t["domain"], "application")
        self.assertEqual(t["permission"], "readonly")

    def test_evidence_actions_are_valid(self):
        from app.services.agent_service import JVM_EVIDENCE_ACTIONS
        from app.api.jvm import _ACTIONS
        self.assertTrue(JVM_EVIDENCE_ACTIONS)
        for a in JVM_EVIDENCE_ACTIONS:
            self.assertIn(a, _ACTIONS)
        # 重操作不应出现在自动预采集里
        self.assertNotIn("heap_dump", JVM_EVIDENCE_ACTIONS)
        self.assertNotIn("jfr_start", JVM_EVIDENCE_ACTIONS)

    def test_jvm_diag_handler_success(self):
        import asyncio
        from unittest import mock
        from app.services import tool_registry
        handler = tool_registry.get_tool("jvm_diag")["handler"]
        fake = {"code": 200, "data": {"output": "VM flags: -Xmx2g", "backend": "jdk", "file": False}}
        with mock.patch("app.api.jvm.run_jvm_action", new=mock.AsyncMock(return_value=fake)):
            res = asyncio.run(handler({"action": "vm_flags"},
                                      {"cluster_id": 1, "namespace": "ns", "pod_name": "p"}))
        self.assertTrue(res["success"])
        self.assertIn("-Xmx2g", res["output"])

    def test_jvm_diag_handler_error(self):
        import asyncio
        from unittest import mock
        from app.services import tool_registry
        handler = tool_registry.get_tool("jvm_diag")["handler"]
        fake = {"code": 500, "message": "容器无 JDK 工具", "data": None}
        with mock.patch("app.api.jvm.run_jvm_action", new=mock.AsyncMock(return_value=fake)):
            res = asyncio.run(handler({"action": "vm_flags"},
                                      {"cluster_id": 1, "namespace": "ns", "pod_name": "p"}))
        self.assertFalse(res["success"])
        self.assertIn("JDK", res["error"])

    def test_search_logs_uses_container_stdout(self):
        """search_logs 应走容器 stdout（fetch_pod_logs），不再走 ES"""
        import asyncio
        from unittest import mock
        from app.services import tool_registry
        handler = tool_registry.get_tool("search_logs")["handler"]
        with mock.patch("app.utils.pod_logs.fetch_pod_logs",
                        new=mock.AsyncMock(return_value="【上次实例(--previous)】boom")):
            res = asyncio.run(handler({"tail": 10},
                                      {"cluster_id": 1, "namespace": "ns", "pod_name": "p"}))
        self.assertTrue(res["success"])
        self.assertIn("boom", res["output"])

    def test_classify_fault(self):
        from app.services.agent_service import classify_fault
        keys = lambda s: [t["key"] for t in classify_fault(s)]
        self.assertIn("probe", keys("容器反复重启，提示 liveness 探针失败"))
        self.assertIn("oom", keys("Pod OOMKilled，内存一直涨"))
        self.assertIn("resource", keys("CPU 使用率飙升，HPA 一直扩容"))
        self.assertIn("crash", keys("应用崩溃重启 backoff"))
        self.assertEqual(keys("什么都正常"), [])

    def test_probe_config_tool_registered(self):
        from app.services import tool_registry
        t = tool_registry.get_tool("get_probe_config")
        self.assertIsNotNone(t)
        self.assertEqual(t["domain"], "application")
        self.assertEqual(t["permission"], "readonly")

    def test_probe_template_has_probe_tool(self):
        from app.services.agent_service import FAULT_TEMPLATES
        probe = next(t for t in FAULT_TEMPLATES if t["key"] == "probe")
        self.assertIn(("get_probe_config", {}), probe["tools"])


class TestScheduleToNode(unittest.TestCase):
    """A4：调度到指定节点（build_schedule_doc 纯函数）"""

    def test_set_selector(self):
        from app.api.resources import build_schedule_doc
        doc, info = build_schedule_doc({"spec": {"template": {"spec": {"containers": []}}}}, "k8s-node01")
        sel = doc["spec"]["template"]["spec"]["nodeSelector"]
        self.assertEqual(sel["kubernetes.io/hostname"], "k8s-node01")
        self.assertEqual(info["new_node_selector"]["kubernetes.io/hostname"], "k8s-node01")

    def test_preserve_other_selector(self):
        from app.api.resources import build_schedule_doc
        doc, _ = build_schedule_doc({"spec": {"template": {"spec": {"nodeSelector": {"disktype": "ssd"}}}}}, "n1")
        sel = doc["spec"]["template"]["spec"]["nodeSelector"]
        self.assertEqual(sel["disktype"], "ssd")
        self.assertEqual(sel["kubernetes.io/hostname"], "n1")

    def test_clear_keeps_others(self):
        from app.api.resources import build_schedule_doc
        doc, _ = build_schedule_doc(
            {"spec": {"template": {"spec": {"nodeSelector": {"kubernetes.io/hostname": "n1", "disktype": "ssd"}}}}}, "")
        self.assertEqual(doc["spec"]["template"]["spec"].get("nodeSelector"), {"disktype": "ssd"})

    def test_clear_removes_empty(self):
        from app.api.resources import build_schedule_doc
        doc, _ = build_schedule_doc({"spec": {"template": {"spec": {"nodeSelector": {"kubernetes.io/hostname": "n1"}}}}}, "")
        self.assertNotIn("nodeSelector", doc["spec"]["template"]["spec"])


class TestExecutorHealth(unittest.TestCase):
    """M2：executor 掉线状态翻转判定"""

    def test_transitions(self):
        from app.services.executor_health_service import evaluate_transition as ev
        self.assertEqual(ev(None, True), ("", "up"))
        self.assertEqual(ev("up", True), ("", "up"))
        self.assertEqual(ev("up", False), ("down", "down"))
        self.assertEqual(ev("down", False), ("", "down"))
        self.assertEqual(ev("down", True), ("up", "up"))
        self.assertEqual(ev(None, False), ("down", "down"))


class TestAiUsageSpike(unittest.TestCase):
    """M12：AI 用量异常判定"""

    CFG = {"cost_ratio": 3.0, "calls_ratio": 3.0, "cost_abs": 0.0, "calls_abs": 0.0}

    def test_normal(self):
        from app.services.ai_usage_service import evaluate_usage
        self.assertEqual(evaluate_usage(10.0, 100, 10.0, 100, self.CFG), [])

    def test_cost_spike(self):
        from app.services.ai_usage_service import evaluate_usage
        a = evaluate_usage(40.0, 100, 10.0, 100, self.CFG)
        self.assertTrue(any("成本突增" in x for x in a))

    def test_calls_spike(self):
        from app.services.ai_usage_service import evaluate_usage
        a = evaluate_usage(10.0, 400, 10.0, 100, self.CFG)
        self.assertTrue(any("调用量异常" in x for x in a))

    def test_no_baseline_no_ratio_alert(self):
        # 基线为 0（新部署）→ 不做倍数告警，避免误报
        from app.services.ai_usage_service import evaluate_usage
        self.assertEqual(evaluate_usage(100.0, 1000, 0.0, 0.0, self.CFG), [])

    def test_abs_threshold(self):
        from app.services.ai_usage_service import evaluate_usage
        cfg = {**self.CFG, "cost_abs": 50.0}
        a = evaluate_usage(60.0, 10, 0.0, 0.0, cfg)
        self.assertTrue(any("绝对阈值" in x for x in a))


if __name__ == "__main__":
    unittest.main(verbosity=2)
