# -*- coding: utf-8 -*-
"""分片导入的 Redis 进度键与聚合逻辑单测（R16 §16.8，2026-11-19）

只测纯函数 + mock Redis（不连真实 Redis）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_import_chunk -v
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.api import db_connections


class TestImportJobKeys(unittest.TestCase):
    def test_key_shapes(self):
        meta, done = db_connections._import_job_keys(7, "job-abc")
        self.assertEqual(meta, "db:import:job:7:job-abc")
        self.assertEqual(done, "db:import:job:7:job-abc:done")


class TestImportJobProgress(unittest.IsolatedAsyncioTestCase):
    async def test_progress_reads_meta_and_done(self):
        meta = {"total_chunks": "5", "executed": "12", "skipped": "2", "failed": "1"}
        fake_redis = AsyncMock()
        fake_redis.hgetall = AsyncMock(return_value=meta)
        fake_redis.scard = AsyncMock(return_value=3)
        with patch.object(db_connections, "redis_pool", fake_redis):
            p = await db_connections._import_job_progress("meta", "done")
        self.assertEqual(p["total_chunks"], 5)
        self.assertEqual(p["done_chunks"], 3)
        self.assertEqual(p["executed"], 12)
        self.assertEqual(p["failed"], 1)
        self.assertFalse(p["complete"])  # 3/5 未完成

    async def test_progress_complete(self):
        meta = {"total_chunks": "3", "executed": "9", "skipped": "0", "failed": "0"}
        fake_redis = AsyncMock()
        fake_redis.hgetall = AsyncMock(return_value=meta)
        fake_redis.scard = AsyncMock(return_value=3)
        with patch.object(db_connections, "redis_pool", fake_redis):
            p = await db_connections._import_job_progress("meta", "done")
        self.assertTrue(p["complete"])

    async def test_progress_redis_down_degrades(self):
        fake_redis = AsyncMock()
        fake_redis.hgetall = AsyncMock(side_effect=RuntimeError("down"))
        fake_redis.scard = AsyncMock(side_effect=RuntimeError("down"))
        with patch.object(db_connections, "redis_pool", fake_redis):
            p = await db_connections._import_job_progress("meta", "done")
        self.assertEqual(p["done_chunks"], 0)
        self.assertEqual(p["total_chunks"], 0)


if __name__ == "__main__":
    unittest.main()
