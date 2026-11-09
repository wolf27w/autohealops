# -*- coding: utf-8 -*-
"""R3.1 回归：数据库异常点规则引擎（2026-11-09）

覆盖：连接使用率分级、缓冲池命中率、临时表落盘比例、锁等待、慢 SQL、表空间增长。
运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_anomaly -v
"""
import unittest

from app.services.db_anomaly_service import DEFAULT_THRESHOLDS, analyze


class TestAnalyze(unittest.TestCase):
    def test_connection_usage_high(self):
        out = analyze({"connection_usage_pct": 92}, [], dict(DEFAULT_THRESHOLDS))
        self.assertTrue(any(a["metric"] == "connection_usage_pct" and a["level"] == "high" for a in out))

    def test_connection_usage_warn(self):
        out = analyze({"connection_usage_pct": 75}, [], dict(DEFAULT_THRESHOLDS))
        self.assertTrue(any(a["metric"] == "connection_usage_pct" and a["level"] == "medium" for a in out))

    def test_buffer_pool_hit_rate_low(self):
        out = analyze({"buffer_pool_hit_rate": 80}, [], dict(DEFAULT_THRESHOLDS))
        self.assertTrue(any(a["metric"] == "buffer_pool_hit_rate" for a in out))

    def test_tmp_disk_ratio(self):
        out = analyze({"tmp_disk_tables": 50, "tmp_tables": 100}, [], dict(DEFAULT_THRESHOLDS))
        self.assertTrue(any(a["metric"] == "tmp_disk_ratio" for a in out))

    def test_lock_waits_high(self):
        out = analyze({"lock_waits": 10}, [], dict(DEFAULT_THRESHOLDS))
        self.assertTrue(any(a["metric"] == "lock_waits" and a["level"] == "high" for a in out))

    def test_slow_sql(self):
        out = analyze({}, [{"avg_latency_ms": 3000, "sql_text": "SELECT * FROM t"}], dict(DEFAULT_THRESHOLDS))
        self.assertTrue(any(a["metric"] == "slow_avg_latency_ms" for a in out))

    def test_db_size_growth(self):
        out = analyze({"db_size_growth_pct": 30}, [], dict(DEFAULT_THRESHOLDS))
        self.assertTrue(any(a["metric"] == "db_size_growth_pct" for a in out))

    def test_empty_metrics_no_anomaly(self):
        self.assertEqual(analyze({}, [], dict(DEFAULT_THRESHOLDS)), [])

    def test_sorted_high_first(self):
        out = analyze({"connection_usage_pct": 95, "buffer_pool_hit_rate": 50}, [], dict(DEFAULT_THRESHOLDS))
        self.assertEqual(out[0]["level"], "high")


if __name__ == "__main__":
    unittest.main()
