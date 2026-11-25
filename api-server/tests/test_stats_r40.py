# -*- coding: utf-8 -*-
"""R40.4 大屏数据口径：stats 聚合纯函数回归。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_stats_r40 -v
"""
import json
import unittest
from datetime import datetime

from app.api.stats import (
    _diagnosis_summary,
    _disposal_label,
    _duration_seconds,
    _labor_saved_minutes,
    _parse_diagnosis_json,
    _parse_labor_minutes,
    _severity_level,
    _title_for,
)


class TestSeverityLevel(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(_severity_level("critical"), "P0")
        self.assertEqual(_severity_level("high"), "P1")
        self.assertEqual(_severity_level("medium"), "P2")
        self.assertEqual(_severity_level("low"), "P3")

    def test_unknown_defaults_p2(self):
        self.assertEqual(_severity_level("bogus"), "P2")
        self.assertEqual(_severity_level(""), "P2")
        self.assertEqual(_severity_level(None), "P2")


class TestDisposalLabel(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(_disposal_label("pending"), "待处理")
        self.assertEqual(_disposal_label("awaiting_approval"), "待审批")
        self.assertEqual(_disposal_label("ticketed"), "已转工单")

    def test_unknown_defaults(self):
        self.assertEqual(_disposal_label("resolved"), "待处理")
        self.assertEqual(_disposal_label(""), "待处理")


class TestDurationSeconds(unittest.TestCase):
    def test_positive(self):
        now = datetime(2026, 11, 19, 13, 45, 0)
        self.assertEqual(_duration_seconds(datetime(2026, 11, 19, 13, 40, 0), now), 300)

    def test_none(self):
        self.assertEqual(_duration_seconds(None, datetime.now()), 0)

    def test_future_clamped(self):
        now = datetime(2026, 11, 19, 13, 45, 0)
        self.assertEqual(_duration_seconds(datetime(2026, 11, 19, 14, 0, 0), now), 0)


class TestTitleFor(unittest.TestCase):
    def test_message_priority(self):
        self.assertEqual(_title_for("OOM 告警", "AppOOM", "pod-1"), "OOM 告警")

    def test_fault_and_resource(self):
        self.assertEqual(_title_for("", "CrashLoopBackOff", "app-7d8f"), "CrashLoopBackOff · app-7d8f")

    def test_fault_only(self):
        self.assertEqual(_title_for("", "NodeNotReady", ""), "NodeNotReady")

    def test_empty(self):
        self.assertEqual(_title_for("", "", ""), "未知故障")

    def test_message_truncated(self):
        self.assertEqual(_title_for("x" * 200, "", ""), "x" * 120)


class TestParseDiagnosisJson(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(_parse_diagnosis_json('{"root_cause": "r"}'), {"root_cause": "r"})

    def test_empty(self):
        self.assertEqual(_parse_diagnosis_json(""), {})
        self.assertEqual(_parse_diagnosis_json(None), {})

    def test_invalid(self):
        self.assertEqual(_parse_diagnosis_json("not json"), {})
        self.assertEqual(_parse_diagnosis_json("[1,2]"), {})


class TestDiagnosisSummary(unittest.TestCase):
    def test_full(self):
        dj = json.dumps({"root_cause": "内存泄漏", "impact": "影响支付服务",
                         "suggestions": ["重启", "限流", "扩容"]})
        s = _diagnosis_summary(dj)
        self.assertEqual(s["root_cause"], "内存泄漏")
        self.assertEqual(s["impact"], "影响支付服务")
        self.assertEqual(s["suggestions"], ["重启", "限流"])  # 取前 2

    def test_impact_fallback_root_cause_layer(self):
        s = _diagnosis_summary(json.dumps({"root_cause": "r", "root_cause_layer": "B"}))
        self.assertEqual(s["impact"], "B")

    def test_suggestions_string(self):
        s = _diagnosis_summary(json.dumps({"root_cause": "r", "suggestions": "重启"}))
        self.assertEqual(s["suggestions"], ["重启"])

    def test_no_diagnosis_returns_empty_string(self):
        self.assertEqual(_diagnosis_summary(""), "")
        self.assertEqual(_diagnosis_summary("not json"), "")
        self.assertEqual(_diagnosis_summary(json.dumps({})), "")


class TestLaborSaved(unittest.TestCase):
    def test_calc_default(self):
        self.assertEqual(_labor_saved_minutes(5), 15)  # R53（2026-11-23）：工时基准 30→3 分钟/起
        self.assertEqual(_labor_saved_minutes(0), 0)
        self.assertEqual(_labor_saved_minutes(-1), 0)

    def test_custom_base(self):
        # R53 可配置（2026-11-25）：基准分钟数由系统设置 labor_minutes_per_heal 传入
        self.assertEqual(_labor_saved_minutes(5, 45), 225)
        self.assertEqual(_labor_saved_minutes(5, 0), 0)


class TestParseLaborMinutes(unittest.TestCase):
    """R53 可配置基准解析：默认值 / 自定义值 / 非法值"""

    def test_default(self):
        self.assertEqual(_parse_labor_minutes("3"), 3)
        self.assertEqual(_parse_labor_minutes(""), 3)
        self.assertEqual(_parse_labor_minutes(None), 3)

    def test_custom(self):
        self.assertEqual(_parse_labor_minutes("45"), 45)
        self.assertEqual(_parse_labor_minutes(1440), 1440)
        self.assertEqual(_parse_labor_minutes("1"), 1)

    def test_invalid(self):
        self.assertEqual(_parse_labor_minutes("abc"), 3)
        self.assertEqual(_parse_labor_minutes("0"), 3)
        self.assertEqual(_parse_labor_minutes("-5"), 3)
        self.assertEqual(_parse_labor_minutes("1441"), 3)
        self.assertEqual(_parse_labor_minutes("  "), 3)


if __name__ == "__main__":
    unittest.main()
