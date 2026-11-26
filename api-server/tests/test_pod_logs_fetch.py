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

        async def fake_acall(fn, cluster_id, namespace, pod_name="", container="", tail_lines=0,
                             previous=False, **kw):
            # 只关心 get_pod_logs；get_pod_yaml（读日志挂载点）等其它 RPC 直接给失败
            if getattr(fn, "__name__", "") != "get_pod_logs":
                return {"success": False, "output": "", "error": "not supported in test"}
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


class TestDeadPodFallback(unittest.IsolatedAsyncioTestCase):
    """告警上的 Pod 已重建（CrashLoop 下常见）→ 不能因 NotFound 就判「证据不足」"""

    async def test_falls_back_to_live_pod(self):
        async def fake_acall(fn, *args, **kw):
            name = getattr(fn, "__name__", "")
            if name == "get_pod_logs":
                pod = args[2]
                if pod.endswith("-5k562"):
                    return {"success": False, "logs": "", "error": 'pods "x" not found'}
                return {"success": True, "logs": APP_LOG}
            if name == "list_deployment_pods":
                return {"success": True, "pods": [
                    {"name": "dgis-module-universals-78b9d9865-fjtnp", "ready": False, "restart_count": 9},
                    {"name": "dgis-module-universals-5c4c574c85-6vr6s", "ready": True, "restart_count": 0},
                ], "all_ready": False}
            return {"success": False, "logs": "", "error": "unexpected"}

        facts = {"containers": [{"name": "dgis-module-universals"}], "summary": "x"}
        with patch.object(pod_logs.executor_client, "acall", fake_acall), \
                patch("app.services.termination_evidence.collect_pod_termination",
                      AsyncMock(return_value=facts)):
            pod_logs._CTN_CACHE.clear()
            out = await pod_logs.fetch_pod_logs(
                999025, "qa-asfp", "dgis-module-universals-78b9d9865-5k562")

        self.assertIn("已不存在", out)
        self.assertIn("dgis-module-universals-78b9d9865-fjtnp", out)
        self.assertIn("APPLICATION FAILED TO START", out)


class TestContainerLogFilesAndNoise(unittest.TestCase):
    """2026-11-26（老板）：日志要看容器内文件；且不能把 INFO/DEBUG 噪声当错误行"""

    def test_log_dirs_from_launch_args(self):
        dirs = pod_logs._log_dirs_from_hints(
            "java ... -DJM.LOG.PATH=/apps/works/logs/afbase/ -XX:MaxRAMPercentage=60 "
            "-Dapp.log.path=/data/logs/app.log  LOG_PATH=/var/log/x")
        self.assertIn("/apps/works/logs/afbase", dirs)
        self.assertIn("/data/logs", dirs)
        self.assertIn("/var/log/x", dirs)

    def test_error_line_excludes_info_noise(self):
        self.assertFalse(pod_logs._is_error_line(
            "2026-11-25 09:54:43.863 INFO [main] ParamUtil [http-client] connect timeout:1000"))
        self.assertFalse(pod_logs._is_error_line(
            "2026-11-25 09:54:23.578 DEBUG x : ==>  Preparing: SELECT * FROM t WHERE a = ?"))
        self.assertTrue(pod_logs._is_error_line(
            "2026-11-25 09:54:44 ERROR 7 --- [main] c.e.X : java.lang.OutOfMemoryError: Java heap space"))
        self.assertTrue(pod_logs._is_error_line(
            "/usr/bin/enpoint: line 2:     7 Killed  java ${JAVA_OPTS}"))
        self.assertTrue(pod_logs._is_error_line(
            "2026-11-25 WARN 7 --- [main] o.s.b.d : APPLICATION FAILED TO START"))

    def test_render_drops_debug_noise(self):
        text = "\n".join([
            "2026-11-25 09:54:23.578 DEBUG c.e.a.d.C 159 : ==>  Preparing: SELECT * FROM af_rounting_sign_detail",
            "2026-11-25 09:54:44.980 ERROR 7 --- [main] c.e.X : java.lang.OutOfMemoryError: Java heap space",
            "2026-11-25 09:54:45.000 INFO 7 --- [main] c.e.Y : done",
        ])
        seg = pod_logs._render("t", text)
        self.assertIn("OutOfMemoryError", seg)
        self.assertNotIn("Preparing: SELECT", seg)


class TestStackRootCause(unittest.TestCase):
    """2026-11-26（老板）：诊断只说「代码问题」不行，必须给具体问题点（类:行 + 异常）"""

    LOG = """2026-11-26 13:23:51,125 [main] WARN o.s.b.w.s.c.AnnotationConfigServletWebServerApplicationContext 633 - Exception encountered during context initialization
org.springframework.beans.factory.BeanCreationException: Error creating bean with name 'fileClient'
Caused by: org.springframework.beans.BeanInstantiationException: Failed to instantiate [com.efreight.cud.common.file.client.api.FileClient]: Factory method 'fileClient' threw exception with message: file service requires an HTTPS origin
	at org.springframework.beans.factory.support.SimpleInstantiationStrategy.instantiate(SimpleInstantiationStrategy.java:178)
Caused by: java.lang.IllegalArgumentException: file service requires an HTTPS origin
	at com.efreight.cud.common.file.client.internal.HttpFileClient.validateUri(HttpFileClient.java:110)
	at com.efreight.cud.common.file.client.internal.HttpFileClient.<init>(HttpFileClient.java:67)
	at com.efreight.cud.common.file.client.config.FileClientAutoConfiguration.fileClient(FileClientAutoConfiguration.java:34)
	at java.base/jdk.internal.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
"""

    def test_extract_deepest_cause_and_app_frame(self):
        got = pod_logs.extract_stack_root_cause(self.LOG)
        self.assertIn("IllegalArgumentException", got["root_exception"])
        self.assertIn("requires an HTTPS origin", got["root_exception"])
        self.assertEqual(got["app_frame"], "com.efreight.cud.common.file.client.internal.HttpFileClient.validateUri(HttpFileClient.java:110)")
        self.assertTrue(any("BeanInstantiationException" in c for c in got["all_causes"]))

    def test_render_puts_problem_point_first(self):
        seg = pod_logs._render("app·上次实例(--previous)", self.LOG)
        self.assertIn("问题点（根异常）", seg)
        self.assertIn("HttpFileClient.java:110", seg)
        self.assertLess(seg.index("问题点（根异常）"), seg.index("日志末尾"))

    def test_no_stack_no_block(self):
        self.assertEqual(pod_logs.extract_stack_root_cause("INFO all good"), {})


class TestSidecarSharedVolume(unittest.TestCase):
    def test_sidecar_paths_for_log_volume(self):
        containers = [
            {"name": "app", "mounts": [{"volume": "log", "path": "/apps/works/logs/x"},
                                       {"volume": "kube-api-access-x", "path": "/var/run/secrets"}]},
            {"name": "filebeat", "mounts": [{"volume": "log", "path": "/var/log"},
                                            {"volume": "filebeat-config", "path": "/usr/share/filebeat"}]},
        ]
        got = pod_logs._sidecar_log_paths(containers, "app")
        self.assertEqual(got, [("filebeat", "/var/log")])

    def test_no_shared_volume(self):
        containers = [
            {"name": "app", "mounts": [{"volume": "data", "path": "/data"}]},
            {"name": "side", "mounts": [{"volume": "other", "path": "/other"}]},
        ]
        self.assertEqual(pod_logs._sidecar_log_paths(containers, "app"), [])


if __name__ == "__main__":
    unittest.main()
