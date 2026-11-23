# -*- coding: utf-8 -*-
"""发布变更关联（2026-11-23）单测

覆盖：
- diff_pod_templates：镜像 / env（新增 + 修改 + 打码）/ 探针参数 / resources / 完全一致返回空
- detect_release_change（patch executor_client.acall）：current>prev、recent 判定（1h 前 vs 10h 前）、
  只有 1 个 revision、RPC 失败 → 均返回 changed=False 且不抛异常

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_release_change -v
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import yaml

from app.services import release_change_service as rcs
from app.services.release_change_service import detect_release_change, diff_pod_templates

_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _pod_tpl(image="harbor.example.com/bgs/app:93", env=None, liveness=None,
             resources=None, command=None, args=None, volumes=None, name="app"):
    """构造 PodTemplateSpec dict（与 yaml.safe_load 结果同构）"""
    c = {"name": name, "image": image}
    if env is not None:
        c["env"] = env
    if command is not None:
        c["command"] = command
    if args is not None:
        c["args"] = args
    if liveness is not None:
        c["livenessProbe"] = liveness
    if resources is not None:
        c["resources"] = resources
    spec = {"containers": [c]}
    if volumes is not None:
        spec["volumes"] = volumes
    return {"metadata": {"name": f"{name}-abc123"}, "spec": spec}


class TestDiffPodTemplates(unittest.TestCase):
    def test_image_change(self):
        prev = _pod_tpl(image="harbor:93")
        cur = _pod_tpl(image="harbor:94")
        diffs = diff_pod_templates(cur, prev)
        self.assertTrue(any("镜像" in d and ":93" in d and ":94" in d for d in diffs), diffs)

    def test_env_add_and_modify_with_masking(self):
        prev = _pod_tpl(env=[{"name": "DB_PASSWORD", "value": "old-secret"},
                             {"name": "MODE", "value": "dev"}])
        cur = _pod_tpl(env=[{"name": "DB_PASSWORD", "value": "new-secret"},
                             {"name": "MODE", "value": "dev"},
                             {"name": "SPRING_PROFILES_ACTIVE", "value": "qa"}])
        diffs = diff_pod_templates(cur, prev)
        # env 修改：敏感 key 打码，不泄露明文
        self.assertTrue(any("DB_PASSWORD" in d and "***" in d for d in diffs), diffs)
        # env 新增
        self.assertTrue(any("新增 SPRING_PROFILES_ACTIVE=qa" in d for d in diffs), diffs)
        # 明文密码不得出现
        self.assertFalse(any("new-secret" in d or "old-secret" in d for d in diffs), diffs)
        # 未变化的 MODE 不产生差异行
        self.assertFalse(any("MODE" in d for d in diffs), diffs)

    def test_probe_param_change(self):
        prev = _pod_tpl(liveness={"httpGet": {"path": "/health", "port": 8080},
                                  "timeoutSeconds": 3, "initialDelaySeconds": 30})
        cur = _pod_tpl(liveness={"httpGet": {"path": "/health", "port": 8080},
                                 "timeoutSeconds": 5, "initialDelaySeconds": 30})
        diffs = diff_pod_templates(cur, prev)
        self.assertTrue(any("livenessProbe.timeout" in d and "3 → 5" in d for d in diffs), diffs)

    def test_resources_change(self):
        prev = _pod_tpl(resources={"requests": {"cpu": "100m"}, "limits": {"memory": "1Gi"}})
        cur = _pod_tpl(resources={"requests": {"cpu": "100m"}, "limits": {"memory": "2Gi"}})
        diffs = diff_pod_templates(cur, prev)
        self.assertTrue(any("resources.limits.memory" in d and "1Gi → 2Gi" in d for d in diffs), diffs)

    def test_command_and_args_change(self):
        prev = _pod_tpl(command=["java", "-jar"], args=["--x=1"])
        cur = _pod_tpl(command=["java", "-jar"], args=["--x=2"])
        diffs = diff_pod_templates(cur, prev)
        self.assertTrue(any("args" in d for d in diffs), diffs)

    def test_volume_configmap_change(self):
        prev = _pod_tpl(volumes=[{"name": "cfg", "configMap": {"name": "app-config-v1"}}])
        cur = _pod_tpl(volumes=[{"name": "cfg", "configMap": {"name": "app-config-v2"}}])
        diffs = diff_pod_templates(cur, prev)
        self.assertTrue(any("configMap/app-config-v2" in d for d in diffs), diffs)

    def test_identical_returns_empty(self):
        prev = _pod_tpl(env=[{"name": "MODE", "value": "dev"}])
        cur = _pod_tpl(env=[{"name": "MODE", "value": "dev"}])
        self.assertEqual(diff_pod_templates(cur, prev), [])


def _rev_item(revision, created_at, is_current=False):
    return {"revision": revision, "replicaset": f"app-{revision}", "images": [],
            "desired_replicas": 1, "ready_replicas": 1, "created_at": created_at,
            "is_current": is_current, "failed": False, "change_cause": "",
            "pod_template_hash": f"hash{revision}"}


def _mk_acall(revisions, templates, list_error=None, template_error=None):
    """构造 executor_client.acall 的 mock，按方法名路由。"""
    async def _acall(fn, *args, **kwargs):
        name = getattr(fn, "__name__", "")
        if name == "list_revisions":
            if list_error is not None:
                raise list_error
            return revisions
        if name == "get_revision_template":
            if template_error is not None:
                raise template_error
            rev = args[3]
            return templates.get(rev, {"success": False, "yaml": "", "replicaset": "", "error": "not found"})
        return {"success": False, "error": "unexpected method"}
    return _acall


class TestDetectReleaseChange(unittest.IsolatedAsyncioTestCase):
    async def test_recent_change(self):
        now = datetime.now(_TZ)
        revs = {"success": True, "current_revision": 5, "error": "",
                "items": [_rev_item(4, (now - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%S%z")),
                          _rev_item(5, (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z"), is_current=True)]}
        templates = {5: {"success": True, "yaml": yaml.safe_dump(_pod_tpl(image="harbor:94")), "error": ""},
                     4: {"success": True, "yaml": yaml.safe_dump(_pod_tpl(image="harbor:93")), "error": ""}}
        with patch.object(rcs.executor_client, "acall", _mk_acall(revs, templates)):
            res = await detect_release_change(1, "qa-bgs", "bgs-service")
        self.assertTrue(res["changed"])
        self.assertTrue(res["recent"])
        self.assertEqual(res["current_revision"], 5)
        self.assertEqual(res["rollback_to"], 4)
        self.assertIn("当前 revision 5", res["summary"])
        self.assertIn("harbor:93 → harbor:94", res["summary"])

    async def test_old_change_not_recent(self):
        now = datetime.now(_TZ)
        revs = {"success": True, "current_revision": 5, "error": "",
                "items": [_rev_item(4, (now - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%S%z")),
                          _rev_item(5, (now - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%S%z"), is_current=True)]}
        templates = {5: {"success": True, "yaml": yaml.safe_dump(_pod_tpl(image="harbor:94")), "error": ""},
                     4: {"success": True, "yaml": yaml.safe_dump(_pod_tpl(image="harbor:93")), "error": ""}}
        with patch.object(rcs.executor_client, "acall", _mk_acall(revs, templates)):
            res = await detect_release_change(1, "qa-bgs", "bgs-service")
        self.assertTrue(res["changed"])
        self.assertFalse(res["recent"])

    async def test_single_revision_returns_not_changed(self):
        now = datetime.now(_TZ)
        revs = {"success": True, "current_revision": 3, "error": "",
                "items": [_rev_item(3, (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z"), is_current=True)]}
        with patch.object(rcs.executor_client, "acall", _mk_acall(revs, {})):
            res = await detect_release_change(1, "qa", "dep")
        self.assertFalse(res["changed"])
        self.assertEqual(res["rollback_to"], 0)
        self.assertEqual(res["summary"], "")

    async def test_list_revisions_rpc_failure(self):
        with patch.object(rcs.executor_client, "acall", _mk_acall(None, {}, list_error=RuntimeError("boom"))):
            res = await detect_release_change(1, "qa", "dep")
        self.assertFalse(res["changed"])

    async def test_template_rpc_failure(self):
        now = datetime.now(_TZ)
        revs = {"success": True, "current_revision": 5, "error": "",
                "items": [_rev_item(4, (now - timedelta(hours=20)).strftime("%Y-%m-%dT%H:%M:%S%z")),
                          _rev_item(5, (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S%z"))]}
        with patch.object(rcs.executor_client, "acall", _mk_acall(revs, {}, template_error=RuntimeError("boom"))):
            res = await detect_release_change(1, "qa", "dep")
        self.assertFalse(res["changed"])

    async def test_no_deployment_returns_not_changed(self):
        res = await detect_release_change(1, "qa", "")
        self.assertFalse(res["changed"])


if __name__ == "__main__":
    unittest.main()
