# -*- coding: utf-8 -*-
"""诊断日志采集回归（2026-11-26：sidecar 把 --previous 整条搞挂 → 崩溃日志全丢）

现场（qa-asfp/dgis-module-universals-78b9d9865-5k562）：
- Pod 有 filebeat sidecar，从未重启过；
- 旧实现 `kubectl logs --all-containers=true --previous` → BadRequest（filebeat 无 previous）
  → 整条命令失败，上次崩溃日志全丢；
- 当前实例的 `--all-containers` 日志又被 filebeat 每 30s 一条的超大 JSON 挤满 tail；
- 结果诊断上下文 recent_logs 为空 → 只能给「证据不足」/只看到发布变更。

锁住：
1. 只按「非 sidecar」容器取日志；
2. 某容器 previous 失败不影响其它容器/当前实例；
3. Spring Boot 启动失败现场（APPLICATION FAILED TO START + Description/Action + NoSuchMethod）必须进上下文。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_pod_logs_fetch -v
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.utils import pod_logs

APP_LOG = "\n".join([
    "[] 2026-11-26 10:29:43.000  INFO 7 --- [main] c.g.c.d.Application : Starting...",
    "[] 2026-11-26 10:29:44.744 ERROR 7 --- [main] o.s.boot.diagnostics.LoggingFailureAnalysisReporter : ",
    "***************************",
    "APPLICATION FAILED TO START",
    "***************************",
    "Description:",
    "An attempt was made to call a method that does not exist. The attempt was made from the following location:",
    "    cn.gov.caac.dgis.framework.core.config.FastJsonSecurityConfiguration.<init>(FastJsonSecurityConfiguration.java:14)",
    "The following method did not exist:",
    "    'com.alibaba.fastjson2.reader.ObjectReaderProvider com.alibaba.fastjson.parser.ParserConfig.getProvider()'",
    "Action:",
    "Correct the classpath of your application so that it contains compatible versions of the classes",
])
FILEBEAT_LOG = '{"log.level":"info","message":"Non-zero metrics in the last 30s","metrics":{}}'


class TestFetchPodLogs(unittest.IsolatedAsyncioTestCase):
    async def _run(self, containers, behaviour):
        """behaviour(container, previous) -> dict"""

        async def fake_acall(fn, cluster_id, namespace, pod_name, container="", tail_lines=0,
                             previous=False, **kw):
            return behaviour(container, previous)

        facts = {"containers": [{"name": n} for n in containers], "summary": "x"}
        with patch.object(pod_logs.executor_client, "acall", fake_acall), \
                patch("app.services.termination_evidence.collect_pod_termination",
                      AsyncMock(return_value=facts)):
            pod_logs._CTN_CACHE.clear()
            return await pod_logs.fetch_pod_logs(999025, "qa-asfp", "dgis-module-universals-x")

    async def test_sidecar_skipped_and_previous_failure_isolated(self):
        calls = []

        def behaviour(container, previous):
            calls.append((container, previous))
            if container == "filebeat":       # 不该被调用（sidecar 跳过）
                return {"success": False, "logs": "", "error": "should not be called"}
            if previous:
                return {"success": True, "logs": APP_LOG}
            return {"success": True, "logs": APP_LOG}

        out = await self._run(["dgis-module-universals", "filebeat"], behaviour)
        self.assertEqual({c for c, _ in calls}, {"dgis-module-universals"})
        self.assertIn("上次实例(--previous)", out)
        self.assertIn("当前实例", out)
        self.assertIn("APPLICATION FAILED TO START", out)
        self.assertIn("ParserConfig.getProvider()", out)
        self.assertIn("Thefollowingmethoddidnotexist", out.replace(" ", ""))

    async def test_previous_missing_does_not_kill_current(self):
        def behaviour(container, previous):
            if previous:
                return {"success": False, "logs": "", "error": "previous terminated container not found"}
            return {"success": True, "logs": APP_LOG}

        out = await self._run(["app"], behaviour)
        self.assertIn("当前实例", out)
        self.assertNotIn("上次实例", out)
        self.assertIn("ParserConfig.getProvider()", out)

    async def test_no_pod_returns_empty(self):
        self.assertEqual(await pod_logs.fetch_pod_logs(1, "ns", ""), "")


if __name__ == "__main__":
    unittest.main()
