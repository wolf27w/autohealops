# -*- coding: utf-8 -*-
"""恢复判定回归：StatefulSet/DaemonSet 的 Pod 存在但未就绪时不得判「已恢复」（2026-11-06）

背景：旧逻辑对「Pod 存在但未 ready」也走 Deployment 名字推断（broker-a-0 → broker），
查不到当作"资源已删除"→ 2 秒关掉告警，真故障被静默吞掉。修法：按 owner_kind 判定。

运行：cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import unittest
from unittest.mock import patch

from app.services import alert_auto_service as aas


def _fake_fn(name: str):
    def f(*a, **k):
        pass
    f.__name__ = name
    return f


class _FakeExec:
    """替身 executor_client：按 RPC 名字返回预置结果"""

    def __init__(self, pod_status, owner_kind="", dep_pods=None):
        self.get_pod_status = _fake_fn("get_pod_status")
        self.get_pod_detail = _fake_fn("get_pod_detail")
        self.list_deployment_pods = _fake_fn("list_deployment_pods")
        self._pod_status = pod_status
        self._owner_kind = owner_kind
        self._dep_pods = dep_pods or {}

    async def acall(self, fn, *args, **kw):
        name = getattr(fn, "__name__", "")
        if name == "get_pod_status":
            return self._pod_status
        if name == "get_pod_detail":
            return {"success": True, "detail": {"owner_kind": self._owner_kind}}
        if name == "list_deployment_pods":
            return self._dep_pods
        return {}


class TestPodRecoveredOwnerAware(unittest.IsolatedAsyncioTestCase):
    async def _check(self, fake, pod="x-0"):
        with patch.object(aas, "executor_client", fake):
            return await aas.check_pod_recovered(1, "ns", pod)

    async def test_statefulset_not_ready_is_not_recovered(self):
        fake = _FakeExec(
            {"success": True, "ready": False},
            owner_kind="StatefulSet",
            dep_pods={"success": False, "message": 'deployments.apps "broker" not found'},
        )
        self.assertFalse(await self._check(fake, "broker-a-0"))

    async def test_daemonset_not_ready_is_not_recovered(self):
        fake = _FakeExec({"success": True, "ready": False}, owner_kind="DaemonSet",
                         dep_pods={"success": True, "all_ready": True, "pods": [1]})
        self.assertFalse(await self._check(fake, "agent-x8k2p"))

    async def test_deployment_pod_deleted_all_ready(self):
        fake = _FakeExec(
            {"success": False, "message": 'pods "web-1" not found'},
            owner_kind="Deployment",
            dep_pods={"success": True, "all_ready": True, "pods": [1, 2]},
        )
        self.assertTrue(await self._check(fake, "web-6d8f7c9b6-abcde"))

    async def test_deployment_pod_not_ready_siblings_ready(self):
        fake = _FakeExec(
            {"success": True, "ready": False},
            owner_kind="Deployment",
            dep_pods={"success": True, "all_ready": True, "pods": [1, 2]},
        )
        self.assertTrue(await self._check(fake, "web-6d8f7c9b6-abcde"))

    async def test_query_failure_stays_pending(self):
        fake = _FakeExec({"success": False, "message": "timeout"})
        self.assertFalse(await self._check(fake, "x-1"))


if __name__ == "__main__":
    unittest.main()
