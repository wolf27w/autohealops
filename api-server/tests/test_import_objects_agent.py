# -*- coding: utf-8 -*-
"""回归：agent 库「导入」不能落进直连分支（double-execute + 假失败）

背景（2026-11-19）：`import_objects` 的 agent 分支原来没有 else，
agent 执行成功拿到 result 后，继续落进下面的 `if not _db_supports(c, "import")`：
agent 连接没有直连凭据 → 要么 400（不支持），要么 import_content 用空密码重试 → 恒 500。
实际后果：agent 侧已经真实导入，接口却报失败（既重复执行又假失败）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_import_objects_agent -v
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import db_connections


def _agent_conn():
    return SimpleNamespace(
        id=7, source="agent", cluster_id=3, agent_db_key="dbkey", database="app",
        db_type="mysql", host="", port=0, username="", password_encrypted="",
    )


class _FakeSession:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, _model, _pk):
        return self._conn


class TestImportObjectsAgent(unittest.IsolatedAsyncioTestCase):
    async def test_agent_import_returns_result_without_direct_fallback(self):
        user = SimpleNamespace(role="admin", username="tester", id=1)
        payload = {"confirm": True, "content": "INSERT INTO t VALUES (1);", "format": "sql", "table": "t"}
        agent_raw = json.dumps({"executed": 1, "errors": 0}).encode()

        supports = MagicMock(return_value=False)
        import_content = AsyncMock()
        with patch.object(db_connections, "async_session", lambda: _FakeSession(_agent_conn())), \
                patch.object(db_connections, "_db_ready", lambda c: (True, "")), \
                patch.object(db_connections, "_audit_sql", AsyncMock()), \
                patch.object(db_connections, "_db_supports", supports), \
                patch.object(db_connections.asyncio, "to_thread", AsyncMock(return_value=agent_raw)), \
                patch("app.services.db_direct_service.import_content", import_content):
            resp = await db_connections.import_objects(7, payload, user=user)

        self.assertEqual(resp["code"], 200, resp)
        self.assertEqual(resp["data"]["executed"], 1)
        # 关键：绝不能因为 agent 连接没有直连能力就走到直连分支
        supports.assert_not_called()
        import_content.assert_not_awaited()

    async def test_agent_import_error_maps_to_500(self):
        user = SimpleNamespace(role="admin", username="tester", id=1)
        payload = {"confirm": True, "content": "SELECT 1;", "format": "sql"}
        with patch.object(db_connections, "async_session", lambda: _FakeSession(_agent_conn())), \
                patch.object(db_connections, "_db_ready", lambda c: (True, "")), \
                patch.object(db_connections, "_audit_sql", AsyncMock()), \
                patch.object(db_connections.asyncio, "to_thread",
                             AsyncMock(side_effect=RuntimeError("agent 超时"))):
            resp = await db_connections.import_objects(7, payload, user=user)

        self.assertEqual(resp["code"], 500)
        self.assertIn("agent 超时", resp["message"])


if __name__ == "__main__":
    unittest.main()
