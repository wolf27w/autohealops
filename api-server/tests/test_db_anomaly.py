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


class TestSqlSuggestions(unittest.TestCase):
    def test_select_star(self):
        from app.services.db_anomaly_service import sql_suggestions
        rules = [s["rule"] for s in sql_suggestions("SELECT * FROM users")]
        self.assertIn("select_star", rules)
        self.assertIn("no_where", rules)  # 无 WHERE

    def test_like_leading_wildcard(self):
        from app.services.db_anomaly_service import sql_suggestions
        rules = [s["rule"] for s in sql_suggestions("SELECT * FROM t WHERE name LIKE '%abc'")]
        self.assertIn("like_leading_wildcard", rules)

    def test_large_in(self):
        from app.services.db_anomaly_service import sql_suggestions
        ins = ",".join(str(i) for i in range(20))
        rules = [s["rule"] for s in sql_suggestions(f"SELECT * FROM t WHERE id IN ({ins})")]
        self.assertIn("large_in", rules)

    def test_function_on_column(self):
        from app.services.db_anomaly_service import sql_suggestions
        rules = [s["rule"] for s in sql_suggestions("SELECT * FROM t WHERE DATE(created_at) = '2026-01-01'")]
        self.assertIn("function_on_column", rules)

    def test_suggestion_shape(self):
        from app.services.db_anomaly_service import sql_suggestions
        for s in sql_suggestions("SELECT * FROM t ORDER BY x"):
            self.assertTrue(all(k in s for k in ("rule", "hint", "action", "risk")))


if __name__ == "__main__":
    unittest.main()
