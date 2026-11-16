# -*- coding: utf-8 -*-
"""告警屏蔽服务纯函数单元测试（R11，标准库 unittest，无外部依赖）。

运行：
    cd api-server && ./.venv/bin/python -m unittest tests.test_alert_silence_service -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.alert_silence_service import (  # noqa: E402
    SHANGHAI_TZ,
    is_in_window,
    matcher_filters,
    silence_status,
    validate_matchers,
    validate_recurrence,
    window_state,
)
from app.services.notification_service import inhibition_matches, silence_matches  # noqa: E402


def _alert(cluster_id=1, namespace="default", resource_type="pod",
           resource_name="svc-a-7f8d9", fault_type="CrashLoopBackOff", severity="critical"):
    return SimpleNamespace(
        cluster_id=cluster_id, namespace=namespace, resource_type=resource_type,
        resource_name=resource_name, fault_type=fault_type, severity=severity,
    )


def _once_sl(start, end, recurrence="{}"):
    return SimpleNamespace(starts_at=start, ends_at=end, recurrence=recurrence)


def _recur_sl(recurrence):
    return SimpleNamespace(starts_at=None, ends_at=None, recurrence=recurrence)


class TestMatcherValidation(unittest.TestCase):
    def test_valid_matchers_no_error(self):
        ms = [
            {"key": "namespace", "op": "=", "value": "default"},
            {"key": "severity", "op": "!=", "value": "low"},
            {"key": "resource_name", "op": "=~", "value": "^svc-"},
            {"key": "fault_type", "op": "!~", "value": "^(Test|Debug)$"},
        ]
        self.assertEqual(validate_matchers(ms), [])

    def test_invalid_key(self):
        ms = [{"key": "bogus", "op": "=", "value": "x"}]
        errs = validate_matchers(ms)
        self.assertTrue(any("key 非法" in e for e in errs))

    def test_invalid_op(self):
        ms = [{"key": "namespace", "op": ">", "value": "x"}]
        errs = validate_matchers(ms)
        self.assertTrue(any("op 非法" in e for e in errs))

    def test_invalid_regex(self):
        ms = [{"key": "namespace", "op": "=~", "value": "["}]
        errs = validate_matchers(ms)
        self.assertTrue(any("正则非法" in e for e in errs))

    def test_not_a_list(self):
        self.assertEqual(validate_matchers({"key": "namespace"}), ["matchers 必须是数组"])

    def test_none_ok(self):
        self.assertEqual(validate_matchers(None), [])


class TestMatcherSemantics(unittest.TestCase):
    """复用 notification_service.silence_matches 语义（不改其行为）。"""

    def test_equal(self):
        a = _alert(namespace="default")
        self.assertTrue(silence_matches([{"key": "namespace", "op": "=", "value": "default"}], a))
        self.assertFalse(silence_matches([{"key": "namespace", "op": "=", "value": "kube-system"}], a))

    def test_not_equal(self):
        a = _alert(namespace="default")
        self.assertTrue(silence_matches([{"key": "namespace", "op": "!=", "value": "kube-system"}], a))
        self.assertFalse(silence_matches([{"key": "namespace", "op": "!=", "value": "default"}], a))

    def test_regex_match(self):
        a = _alert(resource_name="svc-a-7f8d9")
        self.assertTrue(silence_matches([{"key": "resource_name", "op": "=~", "value": "^svc-"}], a))
        self.assertFalse(silence_matches([{"key": "resource_name", "op": "=~", "value": "^db-"}], a))

    def test_regex_not_match(self):
        a = _alert(fault_type="CrashLoopBackOff")
        self.assertTrue(silence_matches([{"key": "fault_type", "op": "!~", "value": "^(Test|Debug)$"}], a))

    def test_invalid_regex_never_matches_eq_tilde(self):
        a = _alert(namespace="default")
        self.assertFalse(silence_matches([{"key": "namespace", "op": "=~", "value": "["}], a))

    def test_cluster_id_string_compare(self):
        a = _alert(cluster_id=3)
        self.assertTrue(silence_matches([{"key": "cluster_id", "op": "=", "value": "3"}], a))
        self.assertFalse(silence_matches([{"key": "cluster_id", "op": "=", "value": "5"}], a))

    def test_empty_matchers_false(self):
        self.assertFalse(silence_matches([], _alert()))

    def test_all_must_match_and(self):
        a = _alert(namespace="default", severity="critical")
        ms = [
            {"key": "namespace", "op": "=", "value": "default"},
            {"key": "severity", "op": "=", "value": "low"},
        ]
        self.assertFalse(silence_matches(ms, a))


class TestOnceWindow(unittest.TestCase):
    def test_active_within(self):
        sl = _once_sl(datetime(2026, 11, 16, 8, 0), datetime(2026, 11, 16, 18, 0))
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 16, 12, 0)))

    def test_not_started(self):
        sl = _once_sl(datetime(2026, 11, 16, 8, 0), datetime(2026, 11, 16, 18, 0))
        st = window_state(sl, datetime(2026, 11, 16, 7, 0))
        self.assertFalse(st["active"])
        self.assertEqual(st["reason"], "once_not_started")
        self.assertEqual(st["next_start"], datetime(2026, 11, 16, 8, 0, tzinfo=SHANGHAI_TZ))

    def test_ended(self):
        sl = _once_sl(datetime(2026, 11, 16, 8, 0), datetime(2026, 11, 16, 18, 0))
        st = window_state(sl, datetime(2026, 11, 16, 19, 0))
        self.assertFalse(st["active"])
        self.assertEqual(st["reason"], "once_ended")

    def test_boundary_end_exclusive(self):
        sl = _once_sl(datetime(2026, 11, 16, 8, 0), datetime(2026, 11, 16, 18, 0))
        self.assertFalse(is_in_window(sl, datetime(2026, 11, 16, 18, 0)))


class TestDailyWeeklyWindow(unittest.TestCase):
    def test_daily_within(self):
        sl = _recur_sl({"type": "daily", "start": "02:00", "end": "04:00"})
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 16, 3, 0)))
        self.assertFalse(is_in_window(sl, datetime(2026, 11, 16, 5, 0)))

    def test_daily_cross_midnight(self):
        sl = _recur_sl({"type": "daily", "start": "23:00", "end": "02:00"})
        # 深夜 23:30 → 生效
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 16, 23, 30)))
        # 凌晨 01:00（前一天 23:00 开始）→ 生效
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 17, 1, 0)))
        # 中午 → 不生效
        self.assertFalse(is_in_window(sl, datetime(2026, 11, 16, 12, 0)))

    def test_weekly_day_match(self):
        # 2026-11-16 是周一（isoweekday=1）
        sl = _recur_sl({"type": "weekly", "days": [1, 2, 3, 4, 5], "start": "02:00", "end": "04:00"})
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 16, 3, 0)))   # 周一
        self.assertFalse(is_in_window(sl, datetime(2026, 11, 21, 3, 0)))  # 周六

    def test_weekly_cross_midnight_tail(self):
        # 周一开始 23:00 ~ 次日 02:00：周二凌晨 01:00 仍属于周一窗口
        sl = _recur_sl({"type": "weekly", "days": [1], "start": "23:00", "end": "02:00"})
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 16, 23, 30)))  # 周一晚
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 17, 1, 0)))    # 周二凌晨（周一尾）
        self.assertFalse(is_in_window(sl, datetime(2026, 11, 17, 23, 30)))  # 周二晚（不在 days）

    def test_weekly_empty_days_means_everyday(self):
        sl = _recur_sl({"type": "weekly", "days": [], "start": "02:00", "end": "04:00"})
        self.assertTrue(is_in_window(sl, datetime(2026, 11, 16, 3, 0)))

    def test_start_equals_end_invalid(self):
        sl = _recur_sl({"type": "daily", "start": "02:00", "end": "02:00"})
        st = window_state(sl, datetime(2026, 11, 16, 3, 0))
        self.assertFalse(st["active"])
        self.assertEqual(st["reason"], "daily_invalid")

    def test_next_start_for_weekly(self):
        # 周三（11-18）落在 days，窗口未开始前应给出当天 02:00 起点
        sl = _recur_sl({"type": "weekly", "days": [1, 3, 5], "start": "02:00", "end": "04:00"})
        st = window_state(sl, datetime(2026, 11, 18, 0, 30))  # 周三凌晨
        self.assertFalse(st["active"])
        self.assertEqual(st["next_start"], datetime(2026, 11, 18, 2, 0, tzinfo=SHANGHAI_TZ))


class TestRecurrenceValidation(unittest.TestCase):
    def test_valid(self):
        self.assertIsNone(validate_recurrence({"type": "weekly", "days": [1, 2, 3, 4, 5], "start": "02:00", "end": "04:00"}))
        self.assertIsNone(validate_recurrence("{}"))
        self.assertIsNone(validate_recurrence(None))

    def test_invalid_type(self):
        self.assertIn("type 非法", validate_recurrence({"type": "hourly"}))

    def test_invalid_time(self):
        self.assertIn("HH:MM", validate_recurrence({"type": "daily", "start": "25:00", "end": "04:00"}))

    def test_invalid_days(self):
        self.assertIn("1~7", validate_recurrence({"type": "weekly", "days": [0, 1], "start": "02:00", "end": "04:00"}))


class TestSilenceStatus(unittest.TestCase):
    def test_status_shape(self):
        sl = _once_sl(datetime(2026, 11, 16, 8, 0), datetime(2026, 11, 16, 18, 0))
        st = silence_status(sl, datetime(2026, 11, 16, 12, 0))
        self.assertEqual(set(st.keys()), {"active", "next_start", "next_end", "reason"})
        self.assertTrue(st["active"])
        self.assertEqual(st["reason"], "once_active")


class TestInhibitionMatches(unittest.TestCase):
    def _rule(self, src, tgt, eq):
        return SimpleNamespace(
            source_matchers=src, target_matchers=tgt, equal_labels=eq,
            enabled=True,
        )

    def test_match_basic(self):
        rule = self._rule(
            [{"key": "fault_type", "op": "=", "value": "NodeNotReady"}],
            [{"key": "severity", "op": "=", "value": "low"}],
            ["namespace"],
        )
        src = _alert(fault_type="NodeNotReady", namespace="default")
        tgt = _alert(severity="low", namespace="default")
        self.assertTrue(inhibition_matches(src, tgt, rule))

    def test_equal_label_mismatch(self):
        rule = self._rule(
            [{"key": "fault_type", "op": "=", "value": "NodeNotReady"}],
            [{"key": "severity", "op": "=", "value": "low"}],
            ["namespace"],
        )
        src = _alert(fault_type="NodeNotReady", namespace="default")
        tgt = _alert(severity="low", namespace="kube-system")
        self.assertFalse(inhibition_matches(src, tgt, rule))

    def test_source_not_match(self):
        rule = self._rule(
            [{"key": "fault_type", "op": "=", "value": "NodeNotReady"}],
            [{"key": "severity", "op": "=", "value": "low"}],
            [],
        )
        self.assertFalse(inhibition_matches(_alert(fault_type="Other"), _alert(severity="low"), rule))

    def test_empty_matchers_false(self):
        rule = self._rule([], [{"key": "severity", "op": "=", "value": "low"}], [])
        self.assertFalse(inhibition_matches(_alert(), _alert(), rule))

    def test_json_string_matchers(self):
        import json
        rule = SimpleNamespace(
            source_matchers=json.dumps([{"key": "fault_type", "op": "=", "value": "NodeNotReady"}]),
            target_matchers=json.dumps([{"key": "severity", "op": "=", "value": "low"}]),
            equal_labels=json.dumps(["namespace"]),
            enabled=True,
        )
        self.assertTrue(inhibition_matches(
            _alert(fault_type="NodeNotReady", namespace="default"),
            _alert(severity="low", namespace="default"),
            rule,
        ))


class TestMatcherFilters(unittest.TestCase):
    """预览 SQL 过滤条件构造（只构造不查询）。"""

    def test_builds_one_cond_per_matcher(self):
        from app.models import Alert
        ms = [
            {"key": "namespace", "op": "=", "value": "default"},
            {"key": "severity", "op": "!=", "value": "low"},
        ]
        conds = matcher_filters(Alert, ms)
        self.assertEqual(len(conds), 2)

    def test_cluster_id_invalid_returns_false_cond(self):
        from app.models import Alert
        conds = matcher_filters(Alert, [{"key": "cluster_id", "op": "=", "value": "abc"}])
        self.assertEqual(len(conds), 1)

    def test_regex_uses_regexp(self):
        from app.models import Alert
        conds = matcher_filters(Alert, [{"key": "resource_name", "op": "=~", "value": "^svc-"}])
        self.assertEqual(len(conds), 1)


if __name__ == "__main__":
    unittest.main()
