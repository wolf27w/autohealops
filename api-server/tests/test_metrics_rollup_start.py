# -*- coding: utf-8 -*-
"""指标降采样水位单测（B17，2026-11-16）

只测 `_rollup_start` 纯函数：增量起点不能越过原始保留期，也不能跳掉未写满的桶。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_metrics_rollup_start -v
"""
import unittest
from datetime import datetime, timedelta

from app.services.metrics_rollup import (
    BUCKET_SECONDS, RAW_RETENTION_HOURS, _rollup_start,
)


class TestRollupStart(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 16, 12, 0, 0)

    def test_first_run_uses_lookback(self):
        start = _rollup_start(self.now, None, lookback_hours=6)
        self.assertEqual(start, self.now - timedelta(hours=6))

    def test_incremental_starts_two_buckets_before_watermark(self):
        wm = self.now - timedelta(minutes=15)
        start = _rollup_start(self.now, wm, lookback_hours=2)
        self.assertEqual(start, wm - timedelta(seconds=BUCKET_SECONDS * 2))
        # 正常情况只处理几分钟数据，不再重复扫 2h
        self.assertGreater(start, self.now - timedelta(hours=1))

    def test_never_before_raw_retention_floor(self):
        ancient = self.now - timedelta(days=30)
        start = _rollup_start(self.now, ancient, lookback_hours=2)
        self.assertEqual(start, self.now - timedelta(hours=RAW_RETENTION_HOURS))

    def test_first_run_lookback_also_clamped_by_floor(self):
        start = _rollup_start(self.now, None, lookback_hours=RAW_RETENTION_HOURS * 10)
        self.assertEqual(start, self.now - timedelta(hours=RAW_RETENTION_HOURS))


if __name__ == "__main__":
    unittest.main()
