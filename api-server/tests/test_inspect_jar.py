# -*- coding: utf-8 -*-
"""inspect_jar（镜像内 jar 反查）单测：纯函数 + 多步编排（mock executor）。

口径（老板 2026-11-25）：「异常先看日志找根因；代码问题就反查镜像里的 jar，反编译找到具体原因」。
不连集群、不连 Git：executor_client.acall 全部打桩，只验证编排顺序、命令安全与输出分节。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_inspect_jar -v
"""
import unittest
from unittest.mock import patch

from app.services import tool_registry as tr

_CTX = {"cluster_id": 1, "namespace": "qa-bgs", "pod_name": "app-7d9f-abc", "trace_id": "t-1"}

_UNZIP_LIST = """\
Archive:  /app/app.jar
  Length      Date    Time    Name
---------  ---------- -----   ----
        0  2024-01-01 00:00   BOOT-INF/
        0  2024-01-01 00:00   BOOT-INF/classes/
      120  2024-01-01 00:00   BOOT-INF/classes/application.yml
      140  2024-01-01 00:00   BOOT-INF/classes/application-dev.yml
       90  2024-01-01 00:00   BOOT-INF/classes/com/foo/Bar.class
      300  2024-01-01 00:00   BOOT-INF/lib/spring-core-6.0.0.jar
---------                     -------
      650                     6 files
"""

_APP_YML = """\
server:
  port: 8080
spring:
  datasource:
    url: jdbc:mysql://db:3306/qa
    username: qa
"""

_APP_DEV_YML = """\
spring:
  datasource:
    url: jdbc:mysql://db-dev:3306/qa
logging:
  level:
    root: info
"""

_JAVAP_OK = """\
Compiled from "Bar.java"
public class com.foo.Bar {
  public void run();
    Code:
       0: aload_0
       1: invokevirtual #2  // Method java/lang/Object.getClass:()Ljava/lang/Class;
       4: pop
       5: return
}
"""

_CFR_OK = """\
/*
 * Decompiled with CFR 0.152.
 */
package com.foo;

public class Bar {
    public void run() {
        Object o = null;
        o.getClass();   // NullPointerException
    }
}
"""


def _assert_safe_commands(testcase, commands):
    for cmd in commands:
        testcase.assertNotIn("|", cmd)
        testcase.assertNotIn(">", cmd)
        testcase.assertNotIn("<", cmd)
        testcase.assertNotIn(";", cmd)
        testcase.assertNotIn("&&", cmd)
        testcase.assertNotIn("$(", cmd)
        testcase.assertNotIn("'", cmd)
        testcase.assertNotIn('"', cmd)
        # 只允许白名单命令族
        testcase.assertIn(cmd.split(" ", 1)[0], {"find", "ls", "unzip", "javap", "java", "curl"})


class _Recorder:
    """按命令子串路由的假 executor（每次 exec_in_pod = 一条命令）。"""

    def __init__(self, routes):
        self.routes = list(routes)
        self.commands = []

    async def _acall(self, fn, *args, **kwargs):
        cmd = args[3]
        self.commands.append(cmd)
        for needle, resp in self.routes:
            if needle in cmd:
                return dict(resp)
        return {"success": False, "output": "", "error": "unexpected: " + cmd}


class TestPureHelpers(unittest.TestCase):
    def test_is_safe_pattern(self):
        for ok in ("com.foo.Bar", "Bar", "spring.datasource.url", "/app/app.jar",
                   "https://repo1.maven.org/maven2/org/benf/cfr/0.152/cfr-0.152.jar"):
            self.assertTrue(tr._is_safe_pattern(ok), ok)
        for bad in ("", "com.foo.Bar; rm -rf /", "a|b", "a b", "x>y", "$(id)", "a&&b", "'x'", None):
            self.assertFalse(tr._is_safe_pattern(bad), bad)

    def test_norm_class_path(self):
        self.assertEqual(tr._norm_class_path("com.foo.Bar"), ("com.foo.Bar", "com/foo/Bar.class"))
        self.assertEqual(tr._norm_class_path("com/foo/Bar"), ("com.foo.Bar", "com/foo/Bar.class"))
        self.assertEqual(tr._norm_class_path("com.foo.Bar.class"), ("com.foo.Bar", "com/foo/Bar.class"))
        self.assertEqual(tr._norm_class_path("Bar"), ("Bar", "Bar.class"))

    def test_pick_jar_prefers_app_snapshot(self):
        got = tr._pick_jar([
            "/opt/a/lib-1.0-sources.jar",
            "/opt/a/common.jar",
            "/app/demo-1.0-SNAPSHOT.jar",
        ])
        self.assertEqual(got, "/app/demo-1.0-SNAPSHOT.jar")
        self.assertEqual(tr._pick_jar([]), "")
        self.assertEqual(tr._pick_jar(["/tmp/readme.txt"]), "")

    def test_parse_unzip_list(self):
        entries = tr._parse_unzip_list(_UNZIP_LIST)
        self.assertIn("BOOT-INF/classes/application.yml", entries)
        self.assertIn("BOOT-INF/classes/com/foo/Bar.class", entries)
        self.assertNotIn("Name", entries)
        self.assertNotIn("6", entries)  # 尾部 "6 files" 统计行被跳过
        self.assertEqual(len(entries), 6)

    def test_config_entries_rank(self):
        got = tr._config_entries([
            "BOOT-INF/classes/application-prod.yml",
            "BOOT-INF/classes/bootstrap.properties",
            "BOOT-INF/classes/application.yml",
            "BOOT-INF/classes/application-dev.yml",
            "BOOT-INF/lib/x.jar",
        ])
        self.assertEqual(got[0], "BOOT-INF/classes/application.yml")
        self.assertIn("BOOT-INF/classes/bootstrap.properties", got)
        self.assertEqual(len(got), 3)  # 最多 3 个

    def test_filter_config_lines(self):
        hits = tr._filter_config_lines(_APP_YML, "spring.datasource.url")
        self.assertEqual(len(hits), 1)
        lineno, text = hits[0]
        self.assertEqual(lineno, 5)
        self.assertIn("jdbc:mysql://db:3306/qa", text)
        # 大小写不敏感
        self.assertTrue(tr._filter_config_lines("Spring.DataSource.URL=x", "spring.datasource.url"))
        # 注释/空行跳过，最多 max_lines
        self.assertEqual(tr._filter_config_lines("# spring.datasource.url\n\n", "spring.datasource.url"), [])
        many = "\n".join(f"k{i}: v{i}" for i in range(50))
        self.assertLessEqual(len(tr._filter_config_lines(many, "k", max_lines=5)), 5)

    def test_class_entries_and_fqcn(self):
        entries = tr._parse_unzip_list(_UNZIP_LIST)
        cand = tr._class_entries(entries, "com/foo/Bar.class")
        self.assertEqual(cand[0], "BOOT-INF/classes/com/foo/Bar.class")
        self.assertEqual(tr._fqcn_from_entry(cand[0]), "com.foo.Bar")

    def test_javap_ok(self):
        self.assertTrue(tr._javap_ok({"success": True, "output": _JAVAP_OK}))
        self.assertFalse(tr._javap_ok({"success": True, "output": "Error: class not found: com.foo.Bar"}))
        self.assertFalse(tr._javap_ok({"success": False, "output": _JAVAP_OK}))
        self.assertFalse(tr._javap_ok({"success": True, "output": "   "}))

    def test_tool_registered_readonly(self):
        tool = tr.get_tool("inspect_jar")
        self.assertIsNotNone(tool)
        self.assertEqual(tool["permission"], "readonly")
        self.assertEqual(tool["domain"], "application")
        self.assertTrue(tool["args_schema"]["pattern"]["required"])
        names = [t["name"] for t in tr.list_tools(permission="readonly")]
        self.assertIn("inspect_jar", names)


class TestInspectJarFlow(unittest.IsolatedAsyncioTestCase):
    async def test_find_jar_config_and_javap(self):
        rec = _Recorder([
            ("find /app", {"success": True, "output": "/app/app.jar\n/app/lib/x-sources.jar\n"}),
            ("unzip -l", {"success": True, "output": _UNZIP_LIST}),
            ("application-dev.yml", {"success": True, "output": _APP_DEV_YML}),
            ("application.yml", {"success": True, "output": _APP_YML}),
            ("javap", {"success": True, "output": _JAVAP_OK}),
        ])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool("inspect_jar", {"pattern": "com.foo.Bar"}, _CTX)

        self.assertTrue(res["success"], res)
        out = res["output"]
        self.assertIn("【JAR】", out)
        self.assertIn("/app/app.jar", out)
        self.assertIn("【配置命中】", out)  # auto 模式两条路都走，这里没命中
        self.assertIn("【类反编译（javap）】", out)
        self.assertIn("public class com.foo.Bar", out)
        self.assertNotIn("【失败原因】", out)
        # 编排顺序：find → unzip -l → 配置 → javap
        self.assertTrue(rec.commands[0].startswith("find /app"))
        self.assertIn("unzip -l /app/app.jar", rec.commands)
        self.assertIn("javap -p -c -classpath /app/app.jar com.foo.Bar", rec.commands)
        _assert_safe_commands(self, rec.commands)

    async def test_config_key_hits_with_line_numbers(self):
        rec = _Recorder([
            ("unzip -l", {"success": True, "output": _UNZIP_LIST}),
            ("application-dev.yml", {"success": True, "output": _APP_DEV_YML}),
            ("application.yml", {"success": True, "output": _APP_YML}),
        ])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool(
                "inspect_jar",
                {"pattern": "spring.datasource.url", "jar": "/app/app.jar", "mode": "config"},
                _CTX,
            )
        self.assertTrue(res["success"], res)
        out = res["output"]
        self.assertIn("BOOT-INF/classes/application.yml:5", out)
        self.assertIn("jdbc:mysql://db:3306/qa", out)
        self.assertIn("BOOT-INF/classes/application-dev.yml:3", out)
        self.assertFalse(any(c.startswith("javap") for c in rec.commands))  # mode=config 不反编译
        _assert_safe_commands(self, rec.commands)

    async def test_javap_fail_falls_back_to_cfr_download(self):
        rec = _Recorder([
            ("find /app", {"success": True, "output": "/app/app.jar\n"}),
            ("unzip -l", {"success": True, "output": _UNZIP_LIST}),
            ("javap", {"success": True, "output": "Error: class not found: com.foo.Bar"}),
            ("ls /tmp/.autohealops-cfr.jar", {"success": False, "output": "", "error": "No such file"}),
            ("curl", {"success": True, "output": ""}),
            ("java -jar", {"success": True, "output": _CFR_OK}),
        ])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool("inspect_jar", {"pattern": "com.foo.Bar", "mode": "class"}, _CTX)

        self.assertTrue(res["success"], res)
        out = res["output"]
        self.assertIn("【类反编译（cfr）】", out)
        self.assertIn("NullPointerException", out)
        self.assertIn("【失败原因】", out)  # javap 失败原因保留
        self.assertIn("javap 失败", out)
        self.assertTrue(any(c.startswith("curl -fsSL -o /tmp/.autohealops-cfr.jar ") for c in rec.commands))
        self.assertIn("java -jar /tmp/.autohealops-cfr.jar /app/app.jar com.foo.Bar", rec.commands)
        _assert_safe_commands(self, rec.commands)

    async def test_cfr_reused_when_already_present(self):
        rec = _Recorder([
            ("find /app", {"success": True, "output": "/app/app.jar\n"}),
            ("unzip -l", {"success": True, "output": _UNZIP_LIST}),
            ("javap", {"success": False, "output": "", "error": "javap: not found"}),
            ("ls /tmp/.autohealops-cfr.jar", {"success": True, "output": "/tmp/.autohealops-cfr.jar\n"}),
            ("java -jar", {"success": True, "output": _CFR_OK}),
        ])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool("inspect_jar", {"pattern": "Bar", "mode": "class"}, _CTX)

        self.assertTrue(res["success"], res)
        self.assertIn("【类反编译（cfr）】", res["output"])
        self.assertFalse(any(c.startswith("curl") for c in rec.commands))
        _assert_safe_commands(self, rec.commands)

    async def test_no_jdk_and_download_fail_reports_reasons(self):
        rec = _Recorder([
            ("find /app", {"success": True, "output": "/app/app.jar\n"}),
            ("unzip -l", {"success": True, "output": _UNZIP_LIST}),
            ("javap", {"success": False, "output": "", "error": "javap: not found"}),
            ("ls /tmp/.autohealops-cfr.jar", {"success": False, "output": "", "error": "No such file"}),
            ("curl", {"success": False, "output": "", "error": "Could not resolve host"}),
            ("java -jar", {"success": False, "output": "", "error": "java: not found"}),
        ])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool("inspect_jar", {"pattern": "com.foo.Bar", "mode": "class"}, _CTX)

        self.assertTrue(res["success"], res)  # 诊断有结论即成功，失败原因进正文
        self.assertIn("【失败原因】", res["output"])
        self.assertIn("镜像无 JDK 且无法下载反编译器", res["output"])
        _assert_safe_commands(self, rec.commands)

    async def test_jar_not_found(self):
        rec = _Recorder([("find", {"success": True, "output": ""})])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool("inspect_jar", {"pattern": "com.foo.Bar"}, _CTX)

        self.assertFalse(res["success"])
        self.assertIn("镜像内没找到 jar", res["error"])
        self.assertEqual(len(rec.commands), 3)  # 三个 find 位置都试过
        _assert_safe_commands(self, rec.commands)

    async def test_explicit_jar_skips_find(self):
        rec = _Recorder([
            ("unzip -l /opt/x/app.jar", {"success": True, "output": _UNZIP_LIST}),
            ("application.yml", {"success": True, "output": _APP_YML}),
            ("application-dev.yml", {"success": True, "output": _APP_DEV_YML}),
            ("javap", {"success": True, "output": _JAVAP_OK}),
        ])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool(
                "inspect_jar",
                {"pattern": "spring.datasource.url", "jar": "/opt/x/app.jar", "container": "app"},
                _CTX,
            )
        self.assertTrue(res["success"], res)
        self.assertFalse(any(c.startswith("find") for c in rec.commands))
        self.assertIn("/opt/x/app.jar", res["output"])
        _assert_safe_commands(self, rec.commands)

    async def test_class_not_in_jar(self):
        rec = _Recorder([
            ("unzip -l", {"success": True, "output": _UNZIP_LIST}),
        ])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool(
                "inspect_jar", {"pattern": "com.other.Missing", "jar": "/app/app.jar", "mode": "class"}, _CTX
            )
        self.assertTrue(res["success"], res)
        self.assertIn("jar 内未找到类 com.other.Missing", res["output"])
        self.assertFalse(any(c.startswith("javap") for c in rec.commands))

    async def test_illegal_pattern_rejected_without_exec(self):
        rec = _Recorder([])
        with patch.object(tr.executor_client, "acall", rec._acall):
            for bad in ("com.foo.Bar; rm -rf /", "a|b", "a b", "$(id)"):
                res = await tr.execute_tool("inspect_jar", {"pattern": bad}, _CTX)
                self.assertFalse(res["success"], bad)
                self.assertIn("非法字符", res["error"])
        self.assertEqual(rec.commands, [])

    async def test_illegal_jar_path_rejected(self):
        rec = _Recorder([])
        with patch.object(tr.executor_client, "acall", rec._acall):
            res = await tr.execute_tool(
                "inspect_jar", {"pattern": "com.foo.Bar", "jar": "/app/app.jar && rm -rf /"}, _CTX
            )
        self.assertFalse(res["success"])
        self.assertIn("非法字符", res["error"])
        self.assertEqual(rec.commands, [])

    async def test_exec_exception_becomes_error(self):
        async def _boom(fn, *args, **kwargs):
            raise RuntimeError("gRPC 挂了")

        with patch.object(tr.executor_client, "acall", _boom):
            res = await tr.execute_tool("inspect_jar", {"pattern": "com.foo.Bar"}, _CTX)
        self.assertFalse(res["success"])
        # 2026-11-26：exec 失败（容器未运行/CrashLoop）要给真实原因 + 指向镜像层取证，不再只说「没找到 jar」
        self.assertIn("无法执行查找", res["error"])
        self.assertIn("collect_startup_evidence", res["error"])


if __name__ == "__main__":
    unittest.main()
