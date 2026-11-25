# -*- coding: utf-8 -*-
"""platform_actions 平台动作构建器单测（纯函数 + prepare 打桩，不连集群/DB）。

运行：
    cd api-server && ./.venv/bin/python -m unittest tests.test_platform_actions -v
"""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

from app.services.platform_actions import (
    build_rollout_restart,
    build_set_env,
    build_set_image,
    build_set_replicas,
    build_set_resources,
    parse_workload_yaml,
    prepare,
)

DEPLOYMENT_YAML = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
  namespace: prod-ns
spec:
  replicas: 3
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: app
          image: nginx:1.19
          env:
            - name: LOG_LEVEL
              value: info
            - name: APP_MODE
              value: prod
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: 500m
              memory: 512Mi
        - name: sidecar
          image: busybox:1.36
          env:
            - name: SECRET_TOKEN
              valueFrom:
                secretKeyRef:
                  name: app-secret
                  key: token
"""


def _run(coro):
    return asyncio.run(coro)


class TestBuildSetEnv(unittest.TestCase):
    def test_set_new_and_existing_and_unset(self):
        current = {"env": {"app": {"LOG_LEVEL": "info", "OLD_KEY": "oldval"}}}
        params = {
            "namespace": "prod-ns",
            "workload": "web",
            "container": "app",
            "env": {"LOG_LEVEL": "debug", "NEW_FLAG": "1"},
            "unset": ["OLD_KEY"],
        }
        out = build_set_env(current, params)
        self.assertTrue(out["changed"])
        cmd = out["command"]
        self.assertEqual(
            cmd,
            "kubectl set env deployment/web -n prod-ns --containers=app "
            "LOG_LEVEL=debug NEW_FLAG=1 OLD_KEY-",
        )
        # 已有 key 还原旧值；新增 key 用 K- 删除；被删掉的 key 还原旧值
        self.assertEqual(
            out["rollback_command"],
            "kubectl set env deployment/web -n prod-ns --containers=app "
            "LOG_LEVEL=info NEW_FLAG- OLD_KEY=oldval",
        )
        self.assertIn("环境变量", out["description"])

    def test_new_key_rollback_is_delete(self):
        out = build_set_env(
            {"env": {"app": {"A": "1"}}},
            {"namespace": "ns", "workload": "w", "container": "app",
             "env": {"BRAND_NEW": "x"}},
        )
        self.assertIn("BRAND_NEW-", out["rollback_command"])
        self.assertNotIn("BRAND_NEW=", out["rollback_command"])

    def test_without_container_flag(self):
        out = build_set_env(
            {},
            {"namespace": "ns", "workload": "w", "env": {"K": "v"}},
        )
        self.assertEqual(out["command"], "kubectl set env deployment/w -n ns K=v")
        self.assertIn("K-", out["rollback_command"])

    def test_kind_override_statefulset(self):
        out = build_set_env(
            {},
            {"namespace": "ns", "workload": "db", "kind": "statefulset",
             "container": "mysql", "env": {"K": "v"}},
        )
        self.assertTrue(out["command"].startswith("kubectl set env statefulset/db -n ns"))

    def test_quoting_value_with_space(self):
        out = build_set_env(
            {},
            {"namespace": "ns", "workload": "w", "env": {"MSG": "hello world"}},
        )
        self.assertIn("'MSG=hello world'", out["command"])

    def test_missing_workload_raises(self):
        with self.assertRaises(ValueError) as ctx:
            build_set_env({}, {"namespace": "ns", "env": {"K": "v"}})
        self.assertIn("workload", str(ctx.exception))

    def test_empty_env_and_unset_raises(self):
        with self.assertRaises(ValueError):
            build_set_env({}, {"namespace": "ns", "workload": "w"})

    def test_conflict_env_and_unset_raises(self):
        with self.assertRaises(ValueError):
            build_set_env(
                {}, {"namespace": "ns", "workload": "w",
                     "env": {"K": "v"}, "unset": ["K"]},
            )

    def test_value_from_overwrite_raises(self):
        with self.assertRaises(ValueError) as ctx:
            build_set_env(
                {"env": {"app": {}}, "env_value_from": ["SECRET_TOKEN"]},
                {"namespace": "ns", "workload": "w", "container": "app",
                 "env": {"SECRET_TOKEN": "plain"}},
            )
        self.assertIn("valueFrom", str(ctx.exception))

    def test_unsupported_kind_raises(self):
        with self.assertRaises(ValueError):
            build_set_env({}, {"namespace": "ns", "workload": "w",
                               "kind": "job", "env": {"K": "v"}})


class TestBuildSetResources(unittest.TestCase):
    def test_normal_change(self):
        current = {"resources": {"app": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        }}}
        params = {
            "namespace": "prod-ns", "workload": "web", "container": "app",
            "requests": {"cpu": "200m", "memory": "256Mi"},
            "limits": {"cpu": "1", "memory": "1Gi"},
        }
        out = build_set_resources(current, params)
        self.assertTrue(out["changed"])
        self.assertEqual(
            out["command"],
            "kubectl set resources deployment/web -n prod-ns -c app "
            "--requests=cpu=200m,memory=256Mi --limits=cpu=1,memory=1Gi",
        )
        self.assertEqual(
            out["rollback_command"],
            "kubectl set resources deployment/web -n prod-ns -c app "
            "--requests=cpu=100m,memory=128Mi --limits=cpu=500m,memory=512Mi",
        )

    def test_limits_only_no_requests_flag(self):
        current = {"resources": {"app": {"limits": {"cpu": "500m"}}}}
        out = build_set_resources(
            current,
            {"namespace": "ns", "workload": "w", "container": "app",
             "limits": {"cpu": "1"}},
        )
        self.assertNotIn("--requests", out["command"])
        self.assertEqual(
            out["command"],
            "kubectl set resources deployment/w -n ns -c app --limits=cpu=1",
        )
        self.assertEqual(
            out["rollback_command"],
            "kubectl set resources deployment/w -n ns -c app --limits=cpu=500m",
        )
        self.assertNotIn("--requests", out["rollback_command"])

    def test_no_current_rollback_empty(self):
        out = build_set_resources(
            {},
            {"namespace": "ns", "workload": "w", "container": "app",
             "requests": {"cpu": "100m"}},
        )
        self.assertEqual(out["rollback_command"], "")
        self.assertIn("人工恢复", out["description"])

    def test_missing_container_raises(self):
        with self.assertRaises(ValueError) as ctx:
            build_set_resources(
                {}, {"namespace": "ns", "workload": "w", "requests": {"cpu": "1"}})
        self.assertIn("container", str(ctx.exception))

    def test_missing_both_dims_raises(self):
        with self.assertRaises(ValueError):
            build_set_resources(
                {}, {"namespace": "ns", "workload": "w", "container": "app"})


class TestBuildSetImage(unittest.TestCase):
    def test_normal_change(self):
        out = build_set_image(
            {"image": {"app": "nginx:1.19"}},
            {"namespace": "prod-ns", "workload": "web",
             "container": "app", "image": "nginx:1.21"},
        )
        self.assertTrue(out["changed"])
        self.assertEqual(
            out["command"],
            "kubectl set image deployment/web -n prod-ns app=nginx:1.21",
        )
        self.assertEqual(
            out["rollback_command"],
            "kubectl set image deployment/web -n prod-ns app=nginx:1.19",
        )

    def test_flat_current_image(self):
        out = build_set_image(
            {"image": "redis:6"},
            {"namespace": "ns", "workload": "cache",
             "container": "redis", "image": "redis:7"},
        )
        self.assertIn("redis=redis:7", out["command"])
        self.assertIn("redis=redis:6", out["rollback_command"])

    def test_no_current_image_no_rollback(self):
        out = build_set_image(
            {},
            {"namespace": "ns", "workload": "w", "container": "app",
             "image": "nginx:1.21"},
        )
        self.assertFalse(out["changed"])
        self.assertEqual(out["rollback_command"], "")
        self.assertIn("无法生成回滚", out["description"])

    def test_missing_image_raises(self):
        with self.assertRaises(ValueError) as ctx:
            build_set_image(
                {}, {"namespace": "ns", "workload": "w", "container": "app"})
        self.assertIn("image", str(ctx.exception))


class TestBuildSetReplicas(unittest.TestCase):
    def test_normal_change(self):
        out = build_set_replicas(
            {"replicas": 3},
            {"namespace": "prod-ns", "workload": "web", "replicas": 5},
        )
        self.assertTrue(out["changed"])
        self.assertEqual(out["command"],
                         "kubectl scale deployment/web -n prod-ns --replicas=5")
        self.assertEqual(out["rollback_command"],
                         "kubectl scale deployment/web -n prod-ns --replicas=3")

    def test_prod_zero_raises(self):
        with self.assertRaises(ValueError) as ctx:
            build_set_replicas(
                {"replicas": 3},
                {"namespace": "prod-ns", "workload": "web",
                 "replicas": 0, "env": "prod"},
            )
        self.assertIn("生产环境禁止", str(ctx.exception))

    def test_test_env_zero_ok(self):
        out = build_set_replicas(
            {"replicas": 2},
            {"namespace": "test-ns", "workload": "web",
             "replicas": 0, "env": "test"},
        )
        self.assertEqual(out["command"],
                         "kubectl scale deployment/web -n test-ns --replicas=0")
        self.assertEqual(out["rollback_command"],
                         "kubectl scale deployment/web -n test-ns --replicas=2")

    def test_env_from_current_is_str(self):
        with self.assertRaises(ValueError):
            build_set_replicas(
                {"replicas": 1, "env": "production"},
                {"namespace": "ns", "workload": "w", "replicas": 0},
            )

    def test_no_current_replicas(self):
        out = build_set_replicas(
            {}, {"namespace": "ns", "workload": "w", "replicas": 2})
        self.assertEqual(out["rollback_command"], "")
        self.assertIn("无法生成回滚", out["description"])

    def test_missing_replicas_raises(self):
        with self.assertRaises(ValueError):
            build_set_replicas({}, {"namespace": "ns", "workload": "w"})

    def test_negative_replicas_raises(self):
        with self.assertRaises(ValueError):
            build_set_replicas({}, {"namespace": "ns", "workload": "w",
                                    "replicas": -1})


class TestBuildRolloutRestart(unittest.TestCase):
    def test_command(self):
        out = build_rollout_restart(
            {}, {"namespace": "prod-ns", "workload": "web"})
        self.assertEqual(
            out["command"],
            "kubectl rollout restart deployment/web -n prod-ns",
        )
        self.assertEqual(out["rollback_command"], "")
        self.assertIn("无需回滚", out["description"])
        self.assertTrue(out["changed"])

    def test_kind_daemonset(self):
        out = build_rollout_restart(
            {}, {"namespace": "ns", "workload": "agent", "kind": "daemonset"})
        self.assertIn("daemonset/agent", out["command"])

    def test_missing_namespace_raises(self):
        with self.assertRaises(ValueError):
            build_rollout_restart({}, {"workload": "web"})


class TestParseWorkloadYaml(unittest.TestCase):
    def test_parse(self):
        cur = parse_workload_yaml(DEPLOYMENT_YAML)
        self.assertEqual(cur["replicas"], 3)
        self.assertEqual(cur["image"]["app"], "nginx:1.19")
        self.assertEqual(cur["env"]["app"]["LOG_LEVEL"], "info")
        self.assertEqual(cur["resources"]["app"]["requests"]["cpu"], "100m")
        self.assertEqual(cur["resources"]["app"]["limits"]["memory"], "512Mi")
        self.assertEqual(cur["env_value_from"], ["SECRET_TOKEN"])

    def test_parse_bad_yaml(self):
        self.assertEqual(parse_workload_yaml("a: [1, 2"), {})   # YAML 语法错
        self.assertEqual(parse_workload_yaml(""), {})            # 空 YAML
        self.assertEqual(parse_workload_yaml("- just\n- a list\n"), {})  # 非对象


class TestPrepare(unittest.TestCase):
    def _patched_client(self, yaml_resp):
        client = MagicMock()
        client.get_workload_yaml = MagicMock(return_value=yaml_resp)

        async def _acall(fn, *a, **kw):
            return fn(*a, **kw)

        client.acall = _acall
        return client

    def test_prepare_set_env_parses_yaml(self):
        client = self._patched_client(
            {"success": True, "yaml": DEPLOYMENT_YAML, "error": ""})
        with patch("app.services.platform_actions.executor_client", client):
            out = _run(prepare(
                "set_env",
                {"namespace": "prod-ns", "workload": "web", "container": "app",
                 "env": {"LOG_LEVEL": "debug"}},
                cluster_id=7, trace_id="t-1",
            ))
        self.assertTrue(out["current_loaded"])
        self.assertEqual(out["platform_action"], "set_env")
        self.assertEqual(out["params"]["container"], "app")
        self.assertEqual(out["command"],
                         "kubectl set env deployment/web -n prod-ns "
                         "--containers=app LOG_LEVEL=debug")
        self.assertEqual(out["rollback_command"],
                         "kubectl set env deployment/web -n prod-ns "
                         "--containers=app LOG_LEVEL=info")
        client.get_workload_yaml.assert_called_once()
        args = client.get_workload_yaml.call_args[0]
        self.assertEqual(args[:4], (7, "deployment", "prod-ns", "web"))

    def test_prepare_set_image_parses_yaml(self):
        client = self._patched_client(
            {"success": True, "yaml": DEPLOYMENT_YAML, "error": ""})
        with patch("app.services.platform_actions.executor_client", client):
            out = _run(prepare(
                "set_image",
                {"namespace": "prod-ns", "workload": "web",
                 "container": "app", "image": "nginx:1.21"},
                cluster_id=7,
            ))
        self.assertIn("app=nginx:1.21", out["command"])
        self.assertIn("app=nginx:1.19", out["rollback_command"])
        self.assertTrue(out["changed"])

    def test_prepare_set_replicas_parses_yaml(self):
        client = self._patched_client(
            {"success": True, "yaml": DEPLOYMENT_YAML, "error": ""})
        with patch("app.services.platform_actions.executor_client", client):
            out = _run(prepare(
                "set_replicas",
                {"namespace": "prod-ns", "workload": "web", "replicas": 6},
                cluster_id=7,
            ))
        self.assertEqual(out["command"],
                         "kubectl scale deployment/web -n prod-ns --replicas=6")
        self.assertEqual(out["rollback_command"],
                         "kubectl scale deployment/web -n prod-ns --replicas=3")

    def test_prepare_yaml_failure_still_builds(self):
        client = self._patched_client(
            {"success": False, "yaml": "", "error": "not found"})
        with patch("app.services.platform_actions.executor_client", client):
            out = _run(prepare(
                "set_env",
                {"namespace": "prod-ns", "workload": "web",
                 "env": {"NEW": "1"}},
                cluster_id=7,
            ))
        self.assertFalse(out["current_loaded"])
        self.assertIn("NEW=1", out["command"])
        self.assertIn("NEW-", out["rollback_command"])
        self.assertIn("未读到当前工作负载配置", out["description"])

    def test_prepare_yaml_exception_still_builds(self):
        client = MagicMock()
        client.get_workload_yaml = MagicMock(side_effect=RuntimeError("boom"))

        async def _acall(fn, *a, **kw):
            return fn(*a, **kw)

        client.acall = _acall
        with patch("app.services.platform_actions.executor_client", client):
            out = _run(prepare(
                "rollout_restart",
                {"namespace": "prod-ns", "workload": "web"},
                cluster_id=7,
            ))
        self.assertFalse(out["current_loaded"])
        self.assertEqual(out["command"],
                         "kubectl rollout restart deployment/web -n prod-ns")

    def test_prepare_unknown_action_raises(self):
        with self.assertRaises(ValueError) as ctx:
            _run(prepare("delete_pod", {"namespace": "ns", "workload": "w"}, 1))
        self.assertIn("未知动作", str(ctx.exception))

    def test_prepare_missing_workload_raises(self):
        with self.assertRaises(ValueError):
            _run(prepare("set_env", {"namespace": "ns", "env": {"K": "v"}}, 1))


if __name__ == "__main__":
    unittest.main()
