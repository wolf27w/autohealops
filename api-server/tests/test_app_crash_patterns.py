# -*- coding: utf-8 -*-
"""应用代码异常识别（APP_CRASH_PATTERNS）回归测试

背景（2026-10-21）：旧特征表里有 "java.lang." / "at org." / "Caused by" / "Connection refused"，
任何 Spring Boot 崩溃栈都会命中 → CrashLoopBackOff 全部被判成「应用代码问题」，
AI 诊断被短路。这里锁住新口径：只有明确的代码 bug 特征才算，基础设施类问题必须放行给 AI。
"""
import unittest

from app.services.alert_auto_service import _match_app_crash, _is_application_crash


SPRING_INFRA_CRASH = """
--- efreight-afbase 上次崩溃日志 ---
2026-10-21 09:42:01.312 ERROR 1 --- [main] o.s.b.web.embedded.tomcat.TomcatWebServer :
  Application run failed
org.springframework.boot.web.server.PortInUseException: Port 8080 was already in use
\tat org.springframework.boot.web.embedded.tomcat.TomcatWebServer.start(TomcatWebServer.java:229)
\tat org.springframework.boot.SpringApplication.run(SpringApplication.java:1303)
Caused by: java.net.BindException: Address already in use
\tat java.base/sun.nio.ch.Net.bind0(Native Method)
"""

NPE_CRASH = """
--- efreight-afbase 上次崩溃日志 ---
Exception in thread "main" java.lang.NullPointerException: Cannot invoke "Order.getId()" because "order" is null
\tat com.efreight.afbase.OrderService.calc(OrderService.java:88)
"""

PY_TRACEBACK = """
Traceback (most recent call last):
  File "/app/main.py", line 12, in <module>
    cfg = load()["db"]
KeyError: 'db'
"""

DEP_REFUSED = """
--- app 上次崩溃日志 ---
2026-10-21 09:40:00 ERROR connecting to redis 10.0.0.9:6379 - Connection refused
retry 1/5 ...
"""


class TestAppCrashDetection(unittest.TestCase):
    def test_spring_infra_crash_not_app_bug(self):
        """端口占用/依赖不通这类基础设施问题 → 不判成应用代码问题（要放给 AI 诊断）"""
        self.assertEqual(_match_app_crash(SPRING_INFRA_CRASH), "")
        self.assertFalse(_is_application_crash(SPRING_INFRA_CRASH))

    def test_connection_refused_not_app_bug(self):
        self.assertFalse(_is_application_crash(DEP_REFUSED))

    def test_npe_is_app_bug(self):
        self.assertEqual(_match_app_crash(NPE_CRASH), "NullPointerException")

    def test_python_traceback_is_app_bug(self):
        self.assertTrue(_is_application_crash(PY_TRACEBACK))

    def test_empty_text(self):
        self.assertFalse(_is_application_crash(""))
        self.assertFalse(_is_application_crash(None))


if __name__ == "__main__":
    unittest.main()


class TestCrashLoopRebuild(unittest.TestCase):
    """CrashLoop 先重建一次（2026-10-21 老板口径）的边界：命令生成 + 只试一次标记"""

    def _alert(self, ft="CrashLoopBackOff", name="web-6d8f9-abcde", ns="qa-demo", msg=""):
        class A:
            pass
        a = A()
        a.fault_type = ft
        a.resource_name = name
        a.namespace = ns
        a.message = msg
        return a

    def test_standard_steps_delete_pod(self):
        from app.services.alert_auto_service import build_standard_steps
        steps = build_standard_steps(self._alert())
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["command"], "kubectl delete pod web-6d8f9-abcde -n qa-demo")

    def test_illegal_name_refused(self):
        from app.services.alert_auto_service import build_standard_steps
        steps = build_standard_steps(self._alert(name="web; rm -rf /"))
        self.assertEqual(steps, [])

    def test_tried_marker(self):
        from app.services.alert_auto_service import _has_tried_rebuild, REBUILD_TRIED_MARK
        self.assertFalse(_has_tried_rebuild(self._alert()))
        self.assertTrue(_has_tried_rebuild(self._alert(msg="x" + REBUILD_TRIED_MARK)))


if __name__ == "__main__":
    unittest.main()
