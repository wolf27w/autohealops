# -*- coding: utf-8 -*-
"""日志按时间段过滤纯函数单测（logstream_client.filter_logs_by_time / _parse_time）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_log_time_filter -v
"""
import unittest

from app.services.logstream_client import _parse_time, filter_logs_by_time


class TestParseTime(unittest.TestCase):
    def test_epoch_seconds(self):
        self.assertEqual(_parse_time("1700000000"), 1700000000.0)

    def test_epoch_float(self):
        self.assertAlmostEqual(_parse_time("1700000000.5"), 1700000000.5)

    def test_rfc3339_offset(self):
        # +08:00 → 等价于 UTC 03:04:05
        self.assertAlmostEqual(_parse_time("2026-11-18T11:04:05+08:00"),
                               _parse_time("2026-11-18T03:04:05Z"))

    def test_z_suffix(self):
        self.assertIsNotNone(_parse_time("2026-11-18T03:04:05Z"))

    def test_empty_and_none(self):
        self.assertIsNone(_parse_time(""))
        self.assertIsNone(_parse_time(None))


class TestFilterLogsByTime(unittest.TestCase):
    @staticmethod
    def _line(ts: str, content: str) -> str:
        return f"{ts} {content}"

    def test_range_inclusive(self):
        text = "\n".join([
            self._line("2026-11-18T11:04:05.000000000+08:00", "hello"),
            self._line("2026-11-18T11:05:00.000000000+08:00", "world"),
            self._line("2026-11-18T11:06:00.000000000+08:00", "later"),
        ])
        out = filter_logs_by_time(text, "2026-11-18T11:04:00+08:00", "2026-11-18T11:05:30+08:00")
        self.assertEqual(out.splitlines(), ["hello", "world"])

    def test_epoch_range(self):
        text = "\n".join([
            self._line("2026-11-18T11:04:05.000000000+08:00", "a"),
            self._line("2026-11-18T11:05:00.000000000+08:00", "b"),
        ])
        start = _parse_time("2026-11-18T11:04:00+08:00")
        end = _parse_time("2026-11-18T11:05:30+08:00")
        out = filter_logs_by_time(text, start, end)
        self.assertEqual(out.splitlines(), ["a", "b"])

    def test_continuation_follows_kept_line(self):
        text = "\n".join([
            self._line("2026-11-18T11:04:05.000000000+08:00", "hello"),
            "    at foo()",  # 续行（无时间戳）跟随 hello
            self._line("2026-11-18T11:06:00.000000000+08:00", "later"),
        ])
        out = filter_logs_by_time(text, "2026-11-18T11:04:00+08:00", "2026-11-18T11:05:30+08:00")
        self.assertEqual(out.splitlines(), ["hello", "    at foo()"])

    def test_invalid_params_no_filter(self):
        text = "\n".join([
            self._line("2026-11-18T11:04:05.000000000+08:00", "hello"),
            self._line("2026-11-18T11:06:00.000000000+08:00", "later"),
        ])
        out = filter_logs_by_time(text, "", "2026-11-18T11:05:30+08:00")
        self.assertEqual(out.splitlines(), ["hello", "later"])  # 参数非法 → 仅剥时间戳不过滤

    def test_empty_text(self):
        self.assertEqual(filter_logs_by_time("", "1", "2"), "")


if __name__ == "__main__":
    unittest.main()
