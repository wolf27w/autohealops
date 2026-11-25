# -*- coding: utf-8 -*-
"""get_workload_env 工具测试（env 打码 + 凭据来源校验）"""
import unittest
from unittest.mock import patch

import yaml as pyyaml

from app.services import tool_registry as tr

_DEPLOY = {
    "apiVersion": "apps/v1", "kind": "Deployment",
    "metadata": {"name": "demo"},
    "spec": {"template": {"spec": {"containers": [
        {"name": "app", "image": "harbor/x:1611",
         "env": [
             {"name": "SPRING_PROFILES_ACTIVE", "value": "qa"},
             {"name": "NACOS_PASSWORD", "value": "nacos"},
             {"name": "NACOS_USER", "valueFrom": {"secretKeyRef": {"name": "nacos-cred", "key": "username"}}},
         ],
         "envFrom": [{"configMapRef": {"name": "app-config"}}]},
    ]}}},
}


class TestMask(unittest.TestCase):
    def test_sensitive_masked(self):
        self.assertIn("***", tr._mask_env_value("NACOS_PASSWORD", "nacos"))

    def test_normal_visible(self):
        self.assertEqual(tr._mask_env_value("SPRING_PROFILES_ACTIVE", "qa"), "qa")


class TestSummarize(unittest.TestCase):
    def test_env_and_refs(self):
        text, refs = tr._summarize_workload_env(_DEPLOY, "deployment", "demo", "qa-ns")
        self.assertIn("image=harbor/x:1611", text)
        self.assertIn("SPRING_PROFILES_ACTIVE = qa", text)
        self.assertNotIn("= nacos", text)                      # 密码值必须打码
        self.assertIn("secret nacos-cred/username", text)
        self.assertIn("envFrom configmap app-config", text)
        self.assertEqual(refs["secret"], {"nacos-cred"})
        self.assertEqual(refs["configmap"], {"app-config"})


class TestHandler(unittest.IsolatedAsyncioTestCase):
    async def test_missing_secret_reported(self):
        async def fake_acall(fn, *a, **kw):
            name = getattr(fn, "__name__", "")
            if name == "get_workload_yaml":
                return {"success": True, "yaml": pyyaml.safe_dump(_DEPLOY), "error": ""}
            if name == "list_misc_resources":
                return {"success": True, "secrets": [], "configmaps": [
                    {"name": "app-config", "data_count": 3}]}
            raise AssertionError(name)

        with patch.object(tr.executor_client, "acall", fake_acall):
            out = await tr._h_get_workload_env(
                {"workload": "demo", "namespace": "qa-ns"}, {"cluster_id": 1, "namespace": "qa-ns"})

        self.assertTrue(out["success"])
        self.assertIn("Secret nacos-cred: **不存在**", out["output"])
        self.assertIn("ConfigMap app-config: 存在（data 键数 3）", out["output"])

    async def test_missing_context(self):
        out = await tr._h_get_workload_env({"workload": ""}, {})
        self.assertFalse(out["success"])


if __name__ == "__main__":
    unittest.main()
