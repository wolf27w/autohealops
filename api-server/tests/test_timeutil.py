# -*- coding: utf-8 -*-
"""时间换算单测（2026-11-26：慢 SQL 明细「最近 1 小时」查不到的真因是时区差 8 小时）。"""
import unittest
from datetime import datetime

from app.utils.timeutil import parse_iso_local, to_local_naive


class TimeUtilTest(unittest.TestCase):
    def test_utc_iso_is_converted_to_local_naive(self):
        # 前端 dayjs().toISOString() 的形态：UTC + Z
        dt = parse_iso_local("2026-11-26T07:43:16.000Z")
        self.assertIsNotNone(dt)
        self.assertIsNone(dt.tzinfo, "比较用的 datetime 必须是无时区的本地时间")
        self.assertEqual(dt, datetime(2026, 11, 26, 15, 43, 16))

    def test_offset_iso_is_converted_to_local(self):
        self.assertEqual(parse_iso_local("2026-11-26T07:43:16+00:00"), datetime(2026, 11, 26, 15, 43, 16))
        # 本身就是 +08:00 的入参不应再被平移
        self.assertEqual(parse_iso_local("2026-11-26T15:43:16+08:00"), datetime(2026, 11, 26, 15, 43, 16))

    def test_naive_input_kept_as_is(self):
        self.assertEqual(to_local_naive(datetime(2026, 11, 26, 15, 43, 16)), datetime(2026, 11, 26, 15, 43, 16))

    def test_empty_and_none(self):
        self.assertIsNone(parse_iso_local(""))
        self.assertIsNone(to_local_naive(None))


if __name__ == "__main__":
    unittest.main()
