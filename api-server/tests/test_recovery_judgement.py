# -*- coding: utf-8 -*-
"""恢复判定回归（2026-11-26 老板 15:15）：不能用 Deployment 总的 ready_replicas 判「服务已恢复」

场景（老板指出）：
  期望副本=1，滚动更新时**新版本起不来**，旧 Pod 仍然 Ready →
  Deployment 的 ready_replicas 仍=1 → 若按「总就绪数」判会被误判为已恢复 → 永远不告警。
  正确判据：**当前 revision** 的 ready_replicas 是否 >= desired_replicas。

场景（老板 14:40）：回滚后新版本正常、旧 RS 残留 Pod 崩溃 → 当前 revision 全就绪 → 应关闭。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_recovery_judgement -v
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.services import alert_auto_service as aas


def _acall_factory(*, pod_ready, dep_pods_ready, all_ready, revisions):
    async def _acall(fn, *args, **kw):
        name = getattr(fn, "__name__", "")
        if name == "get_pod_status":
            return {"success": pod_ready is not None, "ready": bool(pod_ready),
                    "message": "" if pod_ready is not None else "pods \"x\" not found"}
        if name == "get_pod_detail":
            return {"success": True, "detail": {"owner_kind": "Deployment"}}
        if name == "list_deployment_pods":
            return {"success": True, "all_ready": all_ready,
                    "pods": [{"name": "p", "ready": dep_pods_ready}], "message": ""}
        if name == "list_revisions":
            return {"success": True, "items": revisions, "current_revision": 0}
        if name == "get_deployment_status":
            return {"success": True, "replicas": 1, "ready_replicas": 1}
        return {"success": False, "message": "unexpected"}
    return _acall


class TestRecoveryJudgement(unittest.IsolatedAsyncioTestCase):
    async def _run(self, **kw):
        with patch.object(aas.executor_client, "acall", _acall_factory(**kw)):
            return await aas.check_pod_recovered_detail(1, "ns", "app-abc-1")

    async def test_new_version_failing_must_not_close(self):
        """新版本起不来：当前 revision ready=0/desired=1 → 不关（即使旧 Pod ready）"""
        ok, reason = await self._run(
            pod_ready=False, dep_pods_ready=True, all_ready=False,
            revisions=[{"revision": 13, "is_current": True, "desired_replicas": 1, "ready_replicas": 0},
                       {"revision": 12, "is_current": False, "desired_replicas": 0, "ready_replicas": 0}])
        self.assertFalse(ok, "新版本启动失败时绝不能判为已恢复")

    async def test_rolled_back_and_new_rs_ready_closes(self):
        """回滚后：当前 revision 全就绪，旧 RS 残留崩溃 Pod → 关闭"""
        ok, reason = await self._run(
            pod_ready=False, dep_pods_ready=False, all_ready=False,
            revisions=[{"revision": 14, "is_current": True, "desired_replicas": 1, "ready_replicas": 1},
                       {"revision": 13, "is_current": False, "desired_replicas": 0, "ready_replicas": 0}])
        self.assertTrue(ok)
        self.assertEqual(reason, aas.RECOVERY_SERVICE_READY)

    async def test_pod_ready_closes_immediately(self):
        ok, _ = await self._run(pod_ready=True, dep_pods_ready=True, all_ready=True, revisions=[])
        self.assertTrue(ok)
