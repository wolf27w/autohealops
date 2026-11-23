# -*- coding: utf-8 -*-
"""R75.2 配置补丁建议单测

覆盖：配置线索提取（各类正则）、B 层才执行、敏感值打码、异常返回 []、
键级状态判定（missing/present/empty）与来源标注。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_config_patch_suggestion -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from app.services import config_patch_suggestion_service as cps
from app.services.config_patch_suggestion_service import (
    extract_clues,
    is_sensitive_key,
    mask_value,
    suggest_config_patches,
)


def _alert(**kw) -> SimpleNamespace:
    base = dict(cluster_id=1, namespace="qa-bgs", resource_type="pod",
                resource_name="app-abc123", deployment_name="app", message="", id=9)
    base.update(kw)
    return SimpleNamespace(**base)


def _diag(root_cause="", layer="B", **kw) -> dict:
    d = {"root_cause": root_cause, "root_cause_layer": layer}
    d.update(kw)
    return d


# ============ 线索提取（纯函数） ============

class TestExtractClues(unittest.TestCase):
    def test_bind_props(self):
        clues = extract_clues("Failed to bind properties under 'spring.datasource.url'")
        self.assertIn({"type": "config_key", "key": "spring.datasource.url"}, clues)

    def test_spring_key(self):
        clues = extract_clues("启动失败 spring.datasource.url 配置有问题")
        self.assertIn({"type": "config_key", "key": "spring.datasource.url"}, clues)

    def test_placeholder(self):
        clues = extract_clues("Could not resolve placeholder 'MYSQL_HOST'")
        self.assertIn({"type": "placeholder", "key": "MYSQL_HOST"}, clues)

    def test_no_bean(self):
        clues = extract_clues("No qualifying bean of type 'com.x.MailSender'")
        self.assertIn({"type": "no_bean", "key": "com.x.MailSender"}, clues)

    def test_env_var(self):
        clues = extract_clues("missing environment variable DATABASE_URL")
        self.assertIn({"type": "env_var", "key": "DATABASE_URL"}, clues)

    def test_dup_key(self):
        clues = extract_clues("Caused by: DuplicateKeyException: ...")
        self.assertIn({"type": "dup_key", "key": "__DUPLICATE_KEY__"}, clues)

    def test_missing_env_generic(self):
        clues = extract_clues("启动失败：missing env")
        self.assertIn({"type": "env_var", "key": "__MISSING_ENV__"}, clues)

    def test_dedup_bind_props_and_spring_key(self):
        # 同一 key 两种正则命中 → 只保留一条 config_key
        clues = extract_clues("Failed to bind properties under 'spring.datasource.url'")
        keys = [c["key"] for c in clues if c["type"] == "config_key"]
        self.assertEqual(keys, ["spring.datasource.url"])


# ============ 敏感值打码 ============

class TestMasking(unittest.TestCase):
    def test_sensitive_key(self):
        self.assertTrue(is_sensitive_key("DB_PASSWORD"))
        self.assertTrue(is_sensitive_key("spring.datasource.password"))
        self.assertTrue(is_sensitive_key("API_TOKEN"))
        self.assertFalse(is_sensitive_key("spring.datasource.url"))
        self.assertFalse(is_sensitive_key("server.port"))

    def test_mask_value(self):
        self.assertEqual(mask_value("s3cret", "DB_PASSWORD"), "***")
        self.assertEqual(mask_value("jdbc:mysql://x", "spring.datasource.url"),
                         "jdbc:mysql://x")


def json_dumps_ish(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


# ============ 主入口行为（async） ============

class TestSuggestConfigPatches(unittest.IsolatedAsyncioTestCase):
    async def test_no_plaintext_secret_leak(self):
        # 挂了一个含明文密钥的 Secret，输出不得泄露明文
        async def _acall(fn, *args, **kwargs):
            name = getattr(fn, "__name__", "")
            if name == "get_pod_detail":
                return {"success": True, "error": "", "detail": {
                    "name": "app-abc123", "namespace": "qa-bgs", "phase": "CrashLoopBackOff",
                    "ready": False, "restart_count": 3, "created_at": "", "node_name": "",
                    "pod_ip": "", "host_ip": "", "owner_kind": "Deployment", "owner_name": "app",
                    "owner_namespace": "qa-bgs",
                    "containers": [{"name": "app", "image": "x", "state": "waiting",
                                    "ready": False, "restart_count": 3, "reason": "CrashLoopBackOff",
                                    "started_at": "", "finished_at": "", "is_init": False,
                                    "mounts": [{"name": "sec", "mount_path": "/etc/secret",
                                                "sub_path": "", "read_only": True,
                                                "sub_path_expr": "", "propagation": ""}]}],
                    "init_containers": [],
                    "volumes": [{"name": "sec", "type": "secret", "source": "app-secret",
                                 "read_only": True}],
                }}
            if name == "get_workload_mounts":
                return {"success": True, "error": "", "volumes": [
                    {"name": "sec", "type": "secret", "source": "app-secret", "read_only": True}],
                    "containers": [], "init_containers": []}
            if name == "get_workload_yaml":
                # args: (cluster_id, kind, namespace, name)
                if args[1] == "secret":
                    return {"success": True, "error": "",
                            "yaml": yaml.safe_dump({"data": {"DB_PASSWORD": "cGxhaW50ZXh0X3NlY3JldA=="}})}
                if args[1] == "deployment":
                    return {"success": True, "error": "",
                            "yaml": yaml.safe_dump({"spec": {"template": {"spec": {"containers": [{"name": "app", "env": []}]}}}})}
                return {"success": False, "error": "not found"}
            return {"success": False, "error": "unexpected"}

        diag = _diag(root_cause="Failed to bind properties under 'spring.datasource.password'")
        with patch.object(cps.executor_client, "acall", _acall):
            out = await suggest_config_patches(_alert(), diag)
        rendered = json_dumps_ish(out)
        self.assertNotIn("plaintext_secret", rendered)
        self.assertNotIn("cGxhaW50ZXh0X3NlY3JldA==", rendered)
    async def test_non_b_layer_returns_empty(self):
        out = await suggest_config_patches(_alert(), _diag(root_cause="x", layer="A"))
        self.assertEqual(out, [])

    async def test_no_layer_returns_empty(self):
        out = await suggest_config_patches(_alert(), _diag(root_cause="x", layer=""))
        self.assertEqual(out, [])

    async def test_no_clues_returns_empty(self):
        async def _acall(fn, *args, **kwargs):
            raise AssertionError("不应读取挂载")
        with patch.object(cps.executor_client, "acall", _acall):
            out = await suggest_config_patches(_alert(), _diag(root_cause="普通崩溃"))
        self.assertEqual(out, [])

    async def test_read_failure_degrades_gracefully(self):
        # gRPC 读取挂载失败 → 不返回 []，而是降级出「missing + source=unknown」的补丁建议
        async def _boom(fn, *args, **kwargs):
            raise RuntimeError("gRPC 挂了")
        diag = _diag(root_cause="Failed to bind properties under 'spring.datasource.url'")
        with patch.object(cps.executor_client, "acall", _boom):
            out = await suggest_config_patches(_alert(), diag)
        self.assertEqual(len(out), 1, out)
        self.assertEqual(out[0]["status"], "missing")
        self.assertEqual(out[0]["source"], "unknown")

    async def test_unexpected_exception_returns_empty(self):
        # 非读取类的意外异常（如线索提取）→ 整体返回 []，不阻断工单
        diag = _diag(root_cause="Failed to bind properties under 'spring.datasource.url'")
        with patch.object(cps, "extract_clues", side_effect=RuntimeError("boom")):
            out = await suggest_config_patches(_alert(), diag)
        self.assertEqual(out, [])

    async def test_missing_config_key_no_mounts(self):
        async def _acall(fn, *args, **kwargs):
            name = getattr(fn, "__name__", "")
            if name == "get_pod_detail":
                return {"success": True, "error": "", "detail": {
                    "name": "app-abc123", "namespace": "qa-bgs", "phase": "CrashLoopBackOff",
                    "ready": False, "restart_count": 3, "created_at": "", "node_name": "",
                    "pod_ip": "", "host_ip": "", "owner_kind": "Deployment", "owner_name": "app",
                    "owner_namespace": "qa-bgs",
                    "containers": [], "init_containers": [], "volumes": []}}
            if name == "get_workload_mounts":
                return {"success": True, "error": "", "volumes": [], "containers": [],
                        "init_containers": []}
            if name == "get_workload_yaml":
                return {"success": True, "error": "", "yaml": yaml.safe_dump(
                    {"spec": {"template": {"spec": {"containers": [{"name": "app", "env": []}]}}}})}
            return {"success": False, "error": "unexpected"}

        diag = _diag(root_cause="Failed to bind properties under 'spring.datasource.url'")
        with patch.object(cps.executor_client, "acall", _acall):
            out = await suggest_config_patches(_alert(), diag)
        self.assertEqual(len(out), 1)
        item = out[0]
        self.assertEqual(item["key"], "spring.datasource.url")
        self.assertEqual(item["status"], "missing")
        self.assertEqual(item["source"], "unknown")

    async def test_present_from_configmap(self):
        async def _acall(fn, *args, **kwargs):
            name = getattr(fn, "__name__", "")
            if name == "get_pod_detail":
                return {"success": True, "error": "", "detail": {
                    "name": "app-abc123", "namespace": "qa-bgs", "phase": "CrashLoopBackOff",
                    "ready": False, "restart_count": 3, "created_at": "", "node_name": "",
                    "pod_ip": "", "host_ip": "", "owner_kind": "Deployment", "owner_name": "app",
                    "owner_namespace": "qa-bgs",
                    "containers": [], "init_containers": [],
                    "volumes": [{"name": "cfg", "type": "configMap", "source": "app-config",
                                 "read_only": True}]}}
            if name == "get_workload_mounts":
                return {"success": True, "error": "", "volumes": [
                    {"name": "cfg", "type": "configMap", "source": "app-config", "read_only": True}],
                    "containers": [], "init_containers": []}
            if name == "get_workload_yaml":
                if args[1] == "configmap":
                    return {"success": True, "error": "", "yaml": yaml.safe_dump(
                        {"data": {"application.yml": "spring:\n  datasource:\n    url: jdbc:mysql://x"}})}
                return {"success": True, "error": "", "yaml": yaml.safe_dump(
                    {"spec": {"template": {"spec": {"containers": [{"name": "app", "env": []}]}}}})}
            return {"success": False, "error": "unexpected"}

        diag = _diag(root_cause="Failed to bind properties under 'spring.datasource.url'")
        with patch.object(cps.executor_client, "acall", _acall):
            out = await suggest_config_patches(_alert(), diag)
        self.assertEqual(len(out), 1)
        item = out[0]
        self.assertEqual(item["status"], "present")
        self.assertEqual(item["source"], "configmap/app-config")

    async def test_dup_key_generic_item(self):
        async def _acall(fn, *args, **kwargs):
            name = getattr(fn, "__name__", "")
            if name == "get_pod_detail":
                return {"success": True, "error": "", "detail": {
                    "name": "app-abc123", "namespace": "qa-bgs", "phase": "CrashLoopBackOff",
                    "ready": False, "restart_count": 3, "created_at": "", "node_name": "",
                    "pod_ip": "", "host_ip": "", "owner_kind": "Deployment", "owner_name": "app",
                    "owner_namespace": "qa-bgs",
                    "containers": [], "init_containers": [], "volumes": []}}
            if name == "get_workload_mounts":
                return {"success": True, "error": "", "volumes": [], "containers": [],
                        "init_containers": []}
            if name == "get_workload_yaml":
                return {"success": True, "error": "", "yaml": yaml.safe_dump(
                    {"spec": {"template": {"spec": {"containers": [{"name": "app", "env": []}]}}}})}
            return {"success": False, "error": "unexpected"}

        diag = _diag(root_cause="Caused by: DuplicateKeyException")
        with patch.object(cps.executor_client, "acall", _acall):
            out = await suggest_config_patches(_alert(), diag)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "duplicate")
        self.assertIn("重复", out[0]["suggestion"])


if __name__ == "__main__":
    unittest.main()
