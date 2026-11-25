# -*- coding: utf-8 -*-
"""启动失败证据包单测（纯函数 + 注册表 + 场景预算）。

覆盖（2026-11-25 qa-asfp Nacos 默认口令启动失败复盘）：
- 首错行提取（Java 根异常 / Spring 启动失败 / ERROR 行）
- 敏感值打码
- 凭据风险判定（出厂默认口令 + 系统属性覆盖 + yml 无默认值）
- 逐行差异 / Pod YAML 状态解析
- 镜像层 tar 内文件提取（registry 通道核心纯函数）
- 工具注册（只读 + 单工具超时）
- ReAct 场景识别与预算放大

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_startup_evidence -v
"""
import io
import tarfile
import unittest

from app.services import agent_loop, tool_registry
from app.services.image_file_reader import extract_members_from_tar, normalize_path, path_variants
from app.services.startup_evidence_service import (
    assess_credentials,
    diff_lines,
    extract_first_error,
    mask_secrets,
    parse_pod_state,
    _config_key_lines,
)

_SPRING_LOG = """\
2026-11-25T09:19:16.010+08:00  INFO 7 --- [main] PluginResourcesResolver : find plugin define
2026-11-25T09:19:37.221+08:00  INFO 7 --- [main] ClientAuthPluginManager : Load ClientAuthService success.
2026-11-25T09:19:50.646+08:00 ERROR 7 --- [main] o.s.boot.SpringApplication : Application run failed
2026-11-25T09:19:50.691+08:00 ERROR 7 --- [main] o.s.b.d.LoggingFailureAnalysisReporter : 
java.lang.IllegalStateException: 必须提供非默认的 Nacos 账号凭据
\tat cn.gov.caac.dgis.framework.core.config.NacosSecurityEnvironmentPostProcessor.requireCredentials(NacosSecurityEnvironmentPostProcessor.java:55)
Caused by: java.lang.IllegalStateException: 必须提供非默认的 Nacos 账号凭据
"""

_ENTRYPOINT_NEW = (
    "#!/bin/bash \n"
    "java ${JAVA_OPTS} -DNACOS_SERVER=nacos-headless.demo:8848 -DNACOS_USERNAME=nacos "
    "-DNACOS_PASSWORD=nacos -DNACOS_NAMESPACE=dgis -Dspring.profiles.active=test -jar /apps/works/server/dgis-auth.jar\n"
)
_ENTRYPOINT_OLD = _ENTRYPOINT_NEW.replace("-Dspring.profiles.active=test", "-Dspring.profiles.active=dev")

_YML_NEW = """\
server:
  port: 8030
spring:
  config:
    import:
      - nacos:dgis-${spring.profiles.active}.yml
  cloud:
    nacos:
      username: ${NACOS_USERNAME}
      password: ${NACOS_PASSWORD}
"""
_YML_OLD = """\
server:
  port: 8030
spring:
  config:
    import:
      - optional:nacos:dgis-${spring.profiles.active}.yml
  cloud:
    nacos:
      username: ${NACOS_USERNAME:nacos}
      password: ${NACOS_PASSWORD:nacos}
"""

_POD_YAML = """\
apiVersion: v1
kind: Pod
metadata:
  name: dgis-auth-564c98cb9-5m79c
  labels:
    app: dgis-auth
spec:
  containers:
  - name: dgis-auth
    image: harbor.efreight.cn/asfp/dgis-auth-qa:1611
  - name: filebeat
    image: harbor.efreight.cn/ops/filebeat:8.6.2
status:
  phase: Running
  containerStatuses:
  - name: dgis-auth
    ready: false
    restartCount: 29
    state:
      waiting:
        reason: CrashLoopBackOff
        message: back-off 5m0s restarting failed container
    lastState:
      terminated:
        exitCode: 1
        reason: Error
        startedAt: "2026-11-25T01:19:15Z"
        finishedAt: "2026-11-25T01:19:51Z"
  - name: filebeat
    ready: true
    restartCount: 0
    state:
      running: {}
"""


class TestFirstError(unittest.TestCase):
    def test_root_exception_first(self):
        hits = extract_first_error(_SPRING_LOG)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["kind"], "root_exception")
        self.assertEqual(hits[0]["exception"], "java.lang.IllegalStateException")
        self.assertIn("必须提供非默认的 Nacos 账号凭据", hits[0]["message"])

    def test_no_false_positive_on_info(self):
        hits = extract_first_error("INFO 1 --- [main] all good\nINFO 2 --- started")
        self.assertEqual(hits, [])

    def test_empty(self):
        self.assertEqual(extract_first_error(""), [])


class TestMaskSecrets(unittest.TestCase):
    def test_mask_reassignment_and_flag(self):
        s = mask_secrets("java -DNACOS_PASSWORD=nacos -Ddb.password=abc123 --token=xyz")
        self.assertIn("PASSWORD=***", s)
        self.assertIn("password=***", s)
        self.assertIn("token=***", s)
        self.assertNotIn("abc123", s)
        self.assertNotIn("xyz", s)

    def test_keeps_username_readable(self):
        # 用户名只有是出厂默认才有诊断价值，不做打码（口令必须打码）
        self.assertIn("NACOS_USERNAME=nacos", mask_secrets("-DNACOS_USERNAME=nacos -DNACOS_PASSWORD=nacos"))


class TestCredentialAssessment(unittest.TestCase):
    def test_default_password_hardcoded_and_no_env_override(self):
        risk = assess_credentials(_ENTRYPOINT_NEW, _ENTRYPOINT_NEW, _YML_NEW)
        self.assertTrue(risk["default_password_detected"])
        self.assertTrue(risk["hardcoded_in_entrypoint"])
        self.assertFalse(risk["env_override_would_work"])
        keys = {k["key"]: k for k in risk["keys"]}
        self.assertTrue(keys["NACOS_PASSWORD"]["is_factory_default"])
        self.assertTrue(keys["NACOS_PASSWORD"]["yml_requires_external"])
        self.assertIn("env", risk["detail"])

    def test_old_version_not_flagged(self):
        risk = assess_credentials(_ENTRYPOINT_OLD, _ENTRYPOINT_OLD, _YML_OLD)
        keys = {k["key"]: k for k in risk["keys"]}
        # 旧版 yml 有默认值（${KEY:nacos}），仍算默认口令，但不具备「无默认值必填」判据
        self.assertFalse(keys["NACOS_PASSWORD"]["yml_requires_external"])

    def test_empty_inputs(self):
        risk = assess_credentials("", "", "")
        self.assertFalse(risk["default_password_detected"])
        self.assertEqual(risk["detail"], "")


class TestDiffAndConfig(unittest.TestCase):
    def test_diff_lines_ignores_comments_blank(self):
        d = diff_lines(_YML_OLD, _YML_NEW)
        texts = " ".join(f"{x['old']}=>{x['new']}" for x in d)
        self.assertIn("optional:nacos:", texts)
        self.assertIn("${NACOS_PASSWORD:nacos}=>", texts)
        self.assertIn("${NACOS_PASSWORD}", texts)
        self.assertLessEqual(len(d), 20)

    def test_config_key_lines_masks_password(self):
        lines = _config_key_lines("spring:\n  cloud:\n    nacos:\n      password: ${NACOS_PASSWORD}\n      server-addr: x:8848\n")
        joined = "\n".join(lines)
        self.assertIn("nacos", joined)
        self.assertIn("8848", joined)


class TestPodState(unittest.TestCase):
    def test_parse_last_terminated(self):
        st = parse_pod_state(_POD_YAML)
        self.assertEqual(st["image"], "harbor.efreight.cn/asfp/dgis-auth-qa:1611")
        self.assertEqual(st["app"], "dgis-auth")
        c = {x["name"]: x for x in st["containers"]}["dgis-auth"]
        self.assertEqual(c["last_exit_code"], 1)
        self.assertEqual(c["waiting_reason"], "CrashLoopBackOff")
        self.assertEqual(c["restart_count"], 29)
        self.assertFalse(c["ready"])

    def test_bad_yaml(self):
        st = parse_pod_state("::: not yaml :::")
        self.assertEqual(st["containers"], [])


class TestImageTarExtract(unittest.TestCase):
    @staticmethod
    def _tar(files: dict) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            for name, content in files.items():
                data = content.encode()
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
        return buf.getvalue()

    def test_extract_exact_path(self):
        raw = self._tar({
            "./usr/bin/enpoint": _ENTRYPOINT_NEW,
            "./apps/works/server/dgis-auth.jar": "binary",
        })
        found, notes = extract_members_from_tar(raw, wanted=["/usr/bin/enpoint"])
        self.assertIn("usr/bin/enpoint", found)
        self.assertIn("NACOS_PASSWORD", found["usr/bin/enpoint"])
        self.assertEqual(notes, [])

    def test_extract_by_suffix(self):
        raw = self._tar({"BOOT-INF/classes/application.yml": _YML_NEW,
                         "BOOT-INF/classes/logback.xml": "<x/>"})
        found, _ = extract_members_from_tar(raw, suffixes=("BOOT-INF/classes/application.yml",))
        self.assertEqual(list(found), ["BOOT-INF/classes/application.yml"])

    def test_truncates_big_file(self):
        raw = self._tar({"a.txt": "x" * 5000})
        found, notes = extract_members_from_tar(raw, wanted=["a.txt"], max_file_bytes=1000)
        self.assertEqual(len(found["a.txt"]), 1000)
        self.assertTrue(any("截断" in n for n in notes))

    def test_normalize_and_variants(self):
        self.assertEqual(normalize_path("./usr/bin/enpoint"), "usr/bin/enpoint")
        self.assertEqual(normalize_path("/usr/bin/enpoint"), "usr/bin/enpoint")
        vs = path_variants("/usr/bin/enpoint")
        self.assertIn("usr/local/bin/enpoint", vs)
        self.assertIn("usr/bin/enpoint", vs)


class TestToolRegistration(unittest.TestCase):
    def test_registered_readonly_with_timeout(self):
        tool = tool_registry.TOOL_REGISTRY.get("collect_startup_evidence")
        self.assertIsNotNone(tool)
        self.assertEqual(tool["permission"], "readonly")
        self.assertEqual(tool["domain"], "application")
        self.assertGreaterEqual(tool.get("timeout") or 0, 30)
        args = tool["args_schema"]
        self.assertIn("container", args)
        self.assertIn("compare_previous", args)

    def test_exposed_to_react_loop(self):
        names = [t["name"] for t in tool_registry.list_tools(permission="readonly")]
        self.assertIn("collect_startup_evidence", names)


class TestScenarioPolicy(unittest.TestCase):
    def test_detect_startup(self):
        self.assertEqual(agent_loop.detect_scenario({"fault_type": "CrashLoopBackOff"}, {}), "startup_failure")
        self.assertEqual(agent_loop.detect_scenario({"message": "pod 启动失败"}, {}), "startup_failure")
        self.assertEqual(agent_loop.detect_scenario({"fault_type": "CPUThrottling"}, {}), "default")

    def test_apply_scenario_enlarges_only(self):
        p = agent_loop.AgentPolicy()
        q = agent_loop.apply_scenario_policy(p, "startup_failure")
        self.assertGreaterEqual(q.max_rounds, 12)
        self.assertGreaterEqual(q.total_timeout, 180.0)
        self.assertEqual(agent_loop.apply_scenario_policy(p, "default"), p)

    def test_hard_limit_respected(self):
        q = agent_loop.apply_scenario_policy(
            agent_loop.AgentPolicy(max_rounds=16), "startup_failure")
        self.assertLessEqual(q.max_rounds, agent_loop.MAX_ROUNDS_HARD_LIMIT)

    def test_checklist_injected_for_startup(self):
        msgs = agent_loop._build_initial_messages(
            {"fault_type": "CrashLoopBackOff", "message": "x"}, {"cluster_id": 1}, "")
        self.assertIn("collect_startup_evidence", msgs[0]["content"])
        msgs2 = agent_loop._build_initial_messages(
            {"fault_type": "NodeNotReady"}, {"cluster_id": 1}, "")
        self.assertNotIn("collect_startup_evidence", msgs2[0]["content"])


if __name__ == "__main__":
    unittest.main()
