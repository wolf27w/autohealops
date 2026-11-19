# -*- coding: utf-8 -*-
"""慢 SQL 标签（S2 打标能力）单测

覆盖：标签规范化/校验、序列化、明细列表标签合并、按标签筛选条件构建、
打标/编辑/取消标签（mock 会话，不连真实 DB）、标签列表/批量映射。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_slow_sql_tags -v
"""
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import db_slow_event_service as esvc
from app.services import db_slow_tag_service as svc
from app.models import SlowSQLTag


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


def _tag_obj(**kw):
    base = dict(id=1, digest="abc", tag="核心链路", note="", created_by="admin", updated_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _empty_result():
    """execute 返回 None 单行（查询无结果）。"""
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=None)
    return r


class TestPureFunctions(unittest.TestCase):
    def test_normalize_tag(self):
        self.assertEqual(svc.normalize_tag("  核心链路  "), "核心链路")
        self.assertEqual(svc.normalize_tag(None), "")
        self.assertEqual(svc.normalize_tag(""), "")
        self.assertEqual(svc.normalize_tag("x" * 200), "x" * 100)

    def test_normalize_note(self):
        self.assertEqual(svc.normalize_note("  重要链路，已排期优化  "), "重要链路，已排期优化")
        self.assertEqual(svc.normalize_note(None), "")
        self.assertEqual(svc.normalize_note("n" * 600), "n" * 500)

    def test_validate_digest(self):
        self.assertIsNone(svc.validate_digest("abc"))
        self.assertEqual(svc.validate_digest(""), "digest 不能为空")
        self.assertEqual(svc.validate_digest("   "), "digest 不能为空")
        self.assertIn("超长", svc.validate_digest("x" * 65))

    def test_serialize_tag_none(self):
        self.assertIsNone(svc.serialize_tag(None))

    def test_serialize_tag(self):
        d = svc.serialize_tag(_tag_obj(updated_at=datetime(2026, 11, 19, 12, 0, 0)))
        self.assertEqual(d["digest"], "abc")
        self.assertEqual(d["tag"], "核心链路")
        self.assertEqual(d["created_by"], "admin")
        self.assertEqual(d["updated_at"], "2026-11-19T12:00:00")

    def test_apply_tag_map(self):
        items = [{"digest": "a", "tag": ""}, {"digest": "b", "tag": ""}, {"digest": "c", "tag": ""}]
        out = svc.apply_tag_map(items, {"a": "核心链路", "c": "可忽略"})
        self.assertEqual(out[0]["tag"], "核心链路")
        self.assertEqual(out[1]["tag"], "")
        self.assertEqual(out[2]["tag"], "可忽略")


class TestTagFilter(unittest.TestCase):
    def test_tag_filter_adds_subquery(self):
        c = SimpleNamespace(id=2)
        conds = esvc._build_filters(c, None, None, "", "", "", "", None, "", tag="核心链路")
        self.assertEqual(len(conds), 2)  # cid + tag 子查询
        self.assertIn("slow_sql_tags", str(conds[1]))

    def test_no_tag_filter_no_subquery(self):
        c = SimpleNamespace(id=2)
        conds = esvc._build_filters(c, None, None, "", "", "", "", None, "")
        self.assertEqual(len(conds), 1)

    def test_tag_filter_with_other_filters(self):
        c = SimpleNamespace(id=2)
        conds = esvc._build_filters(
            c, datetime(2026, 1, 1), datetime(2026, 1, 2),
            "db", "10.0.0.1", "app", "select", 1.5, "dig1", tag="可忽略",
        )
        # 原有 9 条件（含 digest）+ tag 子查询 = 10
        self.assertEqual(len(conds), 10)
        self.assertIn("slow_sql_tags", str(conds[-1]))


class TestSetTag(unittest.IsolatedAsyncioTestCase):
    def _patch_session(self, session):
        return patch.object(svc, "async_session", lambda: _FakeSessionCtx(session))

    async def test_insert_new(self):
        session = MagicMock()
        session.execute = AsyncMock(return_value=_empty_result())
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        with self._patch_session(session):
            out = await svc.set_slow_sql_tag("abc", "核心链路", "重要", "admin")
        self.assertEqual(out["tag"], "核心链路")
        self.assertEqual(out["note"], "重要")
        self.assertEqual(out["created_by"], "admin")
        session.add.assert_called_once()

    async def test_update_existing(self):
        session = MagicMock()
        existing = _tag_obj(tag="旧标签")
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=existing)
        session.execute = AsyncMock(return_value=r)
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        with self._patch_session(session):
            out = await svc.set_slow_sql_tag("abc", "可忽略", "临时跳过", "ops")
        self.assertEqual(out["tag"], "可忽略")
        self.assertEqual(existing.note, "临时跳过")
        self.assertIsInstance(existing.updated_at, datetime)
        session.add.assert_not_called()

    async def test_empty_tag_clears_row(self):
        session = MagicMock()
        existing = _tag_obj()
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=existing)
        session.execute = AsyncMock(return_value=r)
        session.delete = AsyncMock()
        session.commit = AsyncMock()
        with self._patch_session(session):
            out = await svc.set_slow_sql_tag("abc", "", "", "admin")
        self.assertIsNone(out)
        session.delete.assert_called_once_with(existing)

    async def test_empty_tag_no_row_is_noop(self):
        session = MagicMock()
        session.execute = AsyncMock(return_value=_empty_result())
        session.commit = AsyncMock()
        with self._patch_session(session):
            out = await svc.set_slow_sql_tag("abc", "   ", "", "admin")
        self.assertIsNone(out)
        session.delete.assert_not_called()

    async def test_empty_digest_raises(self):
        session = MagicMock()
        with self._patch_session(session):
            with self.assertRaises(ValueError):
                await svc.set_slow_sql_tag("   ", "x", "", "")


class TestClearTag(unittest.IsolatedAsyncioTestCase):
    def _patch_session(self, session):
        return patch.object(svc, "async_session", lambda: _FakeSessionCtx(session))

    async def test_clear_returns_true(self):
        session = MagicMock()
        result = MagicMock()
        result.rowcount = 1
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        with self._patch_session(session):
            self.assertTrue(await svc.clear_slow_sql_tag("abc"))

    async def test_clear_empty_digest(self):
        self.assertFalse(await svc.clear_slow_sql_tag("  "))


class TestListTags(unittest.IsolatedAsyncioTestCase):
    def _patch_session(self, session):
        return patch.object(svc, "async_session", lambda: _FakeSessionCtx(session))

    async def test_list_filters_empty_tag(self):
        session = MagicMock()
        items = MagicMock()
        items.scalars.return_value.all = MagicMock(return_value=[
            _tag_obj(tag="核心链路"), _tag_obj(digest="d2", tag="可忽略"), _tag_obj(digest="d3", tag=""),
        ])
        session.execute = AsyncMock(return_value=items)
        with self._patch_session(session):
            out = await svc.list_slow_sql_tags()
        self.assertEqual([t["tag"] for t in out], ["核心链路", "可忽略"])


class TestTagMap(unittest.IsolatedAsyncioTestCase):
    def _patch_session(self, session):
        return patch.object(svc, "async_session", lambda: _FakeSessionCtx(session))

    async def test_map(self):
        session = MagicMock()
        items = MagicMock()
        items.scalars.return_value.all = MagicMock(return_value=[
            _tag_obj(digest="abc", tag="核心链路"), _tag_obj(digest="xyz", tag="可忽略"),
        ])
        session.execute = AsyncMock(return_value=items)
        with self._patch_session(session):
            m = await svc.tag_map_for_digests(["abc", "xyz", "zzz"])
        self.assertEqual(m, {"abc": "核心链路", "xyz": "可忽略"})

    async def test_map_empty_digests(self):
        self.assertEqual(await svc.tag_map_for_digests([]), {})


if __name__ == "__main__":
    unittest.main()
