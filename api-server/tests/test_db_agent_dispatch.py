# -*- coding: utf-8 -*-
"""B20 回归：agent 接入库 4 个运维能力的分发与「版本过低」返回口径（2026-11-19）

B20 四个能力（对照 docs/BACKLOG-2026-11-13.md:303）：
- 建库 / 删库：CreateDatabase / DropDatabase
- 原始 SQL 导入：ImportSqlRaw（stop/skip 错误模式，不限大小）
- 整库备份（仅下载不落盘）：Backup（mysqldump 语义，流式回传）
- 导出 mode=structure|data|both：DbExport（扩展 mode 入参）

本测试只锁「api-server 侧分发口径」，**不连真实库、不连真实 agent**：
- _agent_call：agent 版本过低（unknown/unsupported/方法不存在）→ (None, "", True)；
  通用错误 / 超时 → (None, msg, False)；成功 → (data, "", False)
- _run_sql：source=agent 走隧道 QueryDatabaseByKey（allow_write 透传），
  source=direct 走 db_direct_service.query / exec_sql
- _db_ready / _db_supports：agent 缺 key/cluster_id 拒绝、直连缺密码拒绝、未知 db_type 拒绝（pg 已支持）
- 端点返回：create_database / drop_database / export_objects / import_sql_file / backup_database
  对「agent 版本过低」返回 400（不落 500 静默失败），通用失败返回 500

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_agent_dispatch -v
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import db_connections


def _conn(**kw):
    base = dict(
        id=1, db_type="mysql", host="h", port=3306, database="db", username="u",
        password_encrypted="enc", readonly_password_encrypted="",
        source="direct", cluster_id=None, agent_db_key="", name="n",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _agent_conn(**kw):
    return _conn(source="agent", cluster_id=7, agent_db_key="k", password_encrypted="", **kw)


def _user(role="admin"):
    return SimpleNamespace(role=role, id=1, username="u")


def _patch_session(fake_conn):
    """patch db_connections.async_session：async with 里 get() 返回 fake_conn。"""
    session = MagicMock()
    session.get = AsyncMock(return_value=fake_conn)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(db_connections, "async_session", return_value=ctx)


class TestAgentCall(unittest.IsolatedAsyncioTestCase):
    """_agent_call 的三态返回：成功 / 版本过低 / 通用失败。"""

    async def test_success(self):
        with patch("app.agent_gateway.call", return_value=b'{"ok": true}') as call:
            data, err, unsupported = await db_connections._agent_call(7, "M", {"k": 1}, 30.0)
        self.assertEqual(data, {"ok": True})
        self.assertEqual(err, "")
        self.assertFalse(unsupported)
        call.assert_called_once()

    async def test_empty_raw_ok(self):
        with patch("app.agent_gateway.call", return_value=b""):
            data, err, unsupported = await db_connections._agent_call(7, "M", {}, 30.0)
        self.assertEqual(data, {})
        self.assertEqual(err, "")
        self.assertFalse(unsupported)

    async def test_unknown_method_is_unsupported(self):
        with patch("app.agent_gateway.call", side_effect=RuntimeError("unknown method: CreateDatabase")):
            data, err, unsupported = await db_connections._agent_call(7, "CreateDatabase", {}, 30.0)
        self.assertIsNone(data)
        self.assertEqual(err, "")
        self.assertTrue(unsupported)

    async def test_unsupported_method_is_unsupported(self):
        with patch("app.agent_gateway.call", side_effect=RuntimeError("unsupported method")):
            _d, _e, unsupported = await db_connections._agent_call(7, "Backup", {}, 30.0)
        self.assertTrue(unsupported)

    async def test_chinese_method_missing_is_unsupported(self):
        with patch("app.agent_gateway.call", side_effect=RuntimeError("方法不存在：Backup")):
            _d, _e, unsupported = await db_connections._agent_call(7, "Backup", {}, 30.0)
        self.assertTrue(unsupported)

    async def test_generic_error_not_unsupported(self):
        with patch("app.agent_gateway.call", side_effect=RuntimeError("connection refused")):
            data, err, unsupported = await db_connections._agent_call(7, "M", {}, 30.0)
        self.assertIsNone(data)
        self.assertEqual(err, "connection refused")
        self.assertFalse(unsupported)

    async def test_timeout_not_unsupported(self):
        with patch("app.agent_gateway.call", side_effect=TimeoutError("agent 隧道超时（M）")):
            data, err, unsupported = await db_connections._agent_call(7, "M", {}, 30.0)
        self.assertIsNone(data)
        self.assertIn("超时", err)
        self.assertFalse(unsupported)


class TestRunSqlDispatch(unittest.IsolatedAsyncioTestCase):
    """_run_sql 按 source 分发：agent 走隧道 / direct 走平台直连。"""

    async def test_agent_dispatches_query_database_by_key(self):
        c = _agent_conn()
        with patch("app.agent_gateway.call", return_value=b'{"columns":["a"],"rows":[[1]]}') as call:
            data = await db_connections._run_sql(c, "select 1", limit=50)
        self.assertEqual(data, {"columns": ["a"], "rows": [[1]]})
        args = call.call_args.args
        self.assertEqual(args[1], "QueryDatabaseByKey")
        payload = json.loads(args[2].decode())
        self.assertEqual(payload["key"], "k")
        self.assertEqual(payload["sql"], "select 1")
        self.assertEqual(payload["limit"], 50)
        self.assertFalse(payload["allow_write"])

    async def test_agent_write_flag_passthrough(self):
        c = _agent_conn()
        with patch("app.agent_gateway.call", return_value=b"{}") as call:
            await db_connections._run_sql(c, "update t set x=1", allow_write=True)
        payload = json.loads(call.call_args.args[2].decode())
        self.assertTrue(payload["allow_write"])

    async def test_direct_read(self):
        c = _conn()
        with patch("app.services.db_direct_service.query",
                   AsyncMock(return_value={"columns": [], "rows": []})) as q:
            data = await db_connections._run_sql(c, "select 1", limit=10)
        q.assert_awaited_once()
        self.assertEqual(data, {"columns": [], "rows": []})

    async def test_direct_write(self):
        c = _conn()
        with patch("app.services.db_direct_service.exec_sql",
                   AsyncMock(return_value={"affected_rows": 1})) as e:
            data = await db_connections._run_sql(c, "delete from t", allow_write=True)
        e.assert_awaited_once()
        self.assertEqual(data, {"affected_rows": 1})


class TestDbReadyAndSupports(unittest.TestCase):
    """_db_ready / _db_supports：agent 归属信息与直连凭据/方言口径。"""

    def test_agent_missing_cluster_id(self):
        ok, why = db_connections._db_ready(_conn(source="agent", cluster_id=None, agent_db_key="k"))
        self.assertFalse(ok)
        self.assertIn("cluster_id", why)

    def test_agent_missing_key(self):
        ok, why = db_connections._db_ready(_conn(source="agent", cluster_id=7, agent_db_key=""))
        self.assertFalse(ok)
        self.assertIn("key", why)

    def test_agent_ready(self):
        ok, why = db_connections._db_ready(_agent_conn())
        self.assertTrue(ok)
        self.assertEqual(why, "")

    def test_direct_mysql_ready(self):
        ok, why = db_connections._db_ready(_conn())
        self.assertTrue(ok)

    def test_direct_missing_password(self):
        ok, why = db_connections._db_ready(_conn(password_encrypted="", readonly_password_encrypted=""))
        self.assertFalse(ok)
        self.assertIn("密码", why)

    def test_direct_pg_ready(self):
        ok, why = db_connections._db_ready(_conn(db_type="pg", password_encrypted="enc"))
        self.assertTrue(ok)

    def test_direct_unknown_type_unsupported(self):
        ok, why = db_connections._db_ready(_conn(db_type="sqlite", password_encrypted="enc"))
        self.assertFalse(ok)
        self.assertIn("sqlite", why)

    def test_db_supports(self):
        self.assertTrue(db_connections._db_supports(_conn(db_type="mysql"), "query"))
        self.assertTrue(db_connections._db_supports(_conn(db_type="oracle"), "query"))
        self.assertTrue(db_connections._db_supports(_conn(db_type="pg"), "query"))
        self.assertTrue(db_connections._db_supports(_conn(db_type="pg"), "schema"))
        self.assertFalse(db_connections._db_supports(_conn(db_type="pg"), "export"))
        self.assertFalse(db_connections._db_supports(_conn(db_type="pg"), "backup"))
        self.assertFalse(db_connections._db_supports(_conn(db_type="oracle"), "export"))
        self.assertFalse(db_connections._db_supports(_conn(db_type="oracle"), "backup"))


class TestAgentUnsupportedReturn(unittest.IsolatedAsyncioTestCase):
    """端点返回口径：agent 版本过低 → 400「agent 版本过低」，通用失败 → 500。"""

    async def test_create_database_agent_unsupported_400(self):
        c = _agent_conn()
        with _patch_session(c):
            with patch.object(db_connections, "_agent_call",
                              AsyncMock(return_value=(None, "", True))) as ac:
                resp = await db_connections.create_database(7, {"name": "foo"}, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("agent 版本过低", resp["message"])
        ac.assert_awaited_once()

    async def test_create_database_agent_error_500(self):
        c = _agent_conn()
        with _patch_session(c):
            with patch.object(db_connections, "_agent_call",
                              AsyncMock(return_value=(None, "boom", False))):
                resp = await db_connections.create_database(7, {"name": "foo"}, user=_user())
        self.assertEqual(resp["code"], 500)
        self.assertIn("boom", resp["message"])

    async def test_drop_database_agent_unsupported_400(self):
        c = _agent_conn()
        with _patch_session(c):
            with patch.object(db_connections, "_agent_call",
                              AsyncMock(return_value=(None, "", True))):
                resp = await db_connections.drop_database(7, "foo", confirm=True, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("agent 版本过低", resp["message"])

    async def test_drop_database_requires_confirm(self):
        resp = await db_connections.drop_database(7, "foo", confirm=False, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("confirm", resp["message"])

    async def test_export_invalid_mode_400(self):
        c = _agent_conn()
        with _patch_session(c):
            with patch.object(db_connections, "_check_approval", AsyncMock(return_value=None)):
                resp = await db_connections.export_objects(
                    7, {"scope": "db", "mode": "bogus"}, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("mode", resp["message"])

    async def test_export_agent_unsupported_400(self):
        c = _agent_conn()
        with _patch_session(c):
            with patch.object(db_connections, "_check_approval", AsyncMock(return_value=None)):
                with patch.object(db_connections, "_agent_call",
                                  AsyncMock(return_value=(None, "", True))) as ac:
                    resp = await db_connections.export_objects(
                        7, {"scope": "db", "mode": "structure"}, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("agent 版本过低", resp["message"])
        # mode 已透传给 agent 侧 DbExport
        payload = ac.call_args.args[2]
        self.assertEqual(payload["mode"], "structure")

    async def test_import_sql_agent_unsupported_400(self):
        c = _agent_conn()
        with _patch_session(c):
            with patch.object(db_connections, "_audit_sql", AsyncMock(return_value=None)):
                with patch.object(db_connections, "_agent_call",
                                  AsyncMock(return_value=(None, "", True))) as ac:
                    resp = await db_connections.import_sql_file(
                        7, {"sql": "select 1;", "mode": "stop"}, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("agent 版本过低", resp["message"])
        self.assertEqual(ac.call_args.args[1], "ImportSqlRaw")

    async def test_import_sql_invalid_mode_400(self):
        c = _agent_conn()
        with _patch_session(c):
            resp = await db_connections.import_sql_file(
                7, {"sql": "select 1;", "mode": "bogus"}, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("mode", resp["message"])

    async def test_backup_agent_unsupported_400(self):
        c = _agent_conn()
        with _patch_session(c):
            with patch.object(db_connections, "_agent_call",
                              AsyncMock(return_value=(None, "", True))) as ac:
                resp = await db_connections.backup_database(7, user=_user())
        self.assertEqual(resp["code"], 400)
        self.assertIn("agent 版本过低", resp["message"])
        self.assertEqual(ac.call_args.args[1], "Backup")


if __name__ == "__main__":
    unittest.main()
