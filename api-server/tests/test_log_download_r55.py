# -*- coding: utf-8 -*-
"""R55：agent 接入集群日志下载走 gRPC 隧道 —— 纯函数 + helper 单测。

覆盖：
1) has_timestamps / filter_logs_by_time_strict（无时间戳报错 + 时间段过滤边界）
2) _agent_pod_logs_all / _agent_pod_logs_timed（agent 集群走 gRPC 隧道；旧 agent 降级报错）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_log_download_r55 -v
"""
import unittest
from unittest import mock

from app.services.logstream_client import (
    _parse_time,
    filter_logs_by_time_strict,
    has_timestamps,
)


def _line(ts: str, content: str) -> str:
    return f"{ts} {content}"


class TestHasTimestamps(unittest.TestCase):
    def test_true_with_timestamps(self):
        text = "\n".join([
            _line("2026-11-18T11:04:05+08:00", "hello"),
            _line("2026-11-18T11:05:00+08:00", "world"),
        ])
        self.assertTrue(has_timestamps(text))

    def test_false_without_timestamps(self):
        self.assertFalse(has_timestamps("hello\nworld"))

    def test_false_on_empty(self):
        self.assertFalse(has_timestamps(""))

    def test_true_if_any_line_has_timestamp(self):
        # 续行（无时间戳）跟随带时间戳的首行 → 仍视为有时间戳
        self.assertTrue(has_timestamps("2026-11-18T11:04:05+08:00 a\n    continuation"))


class TestFilterLogsByTimeStrict(unittest.TestCase):
    def test_normal_filter_strips_timestamp(self):
        text = "\n".join([
            _line("2026-11-18T11:04:05.000000000+08:00", "hello"),
            _line("2026-11-18T11:06:00.000000000+08:00", "later"),
        ])
        out, err = filter_logs_by_time_strict(text, "2026-11-18T11:04:00+08:00", "2026-11-18T11:05:30+08:00")
        self.assertEqual(err, "")
        self.assertEqual(out.splitlines(), ["hello"])

    def test_no_timestamp_reports_error(self):
        """旧 agent 未透传 timestamps → 明确报错，不静默空。"""
        out, err = filter_logs_by_time_strict("hello\nworld", "1", "2")
        self.assertEqual(out, "")
        self.assertIn("agent 版本过低", err)

    def test_empty_text_no_error(self):
        out, err = filter_logs_by_time_strict("", "1", "2")
        self.assertEqual((out, err), ("", ""))

    def test_boundary_inclusive(self):
        text = "\n".join([
            _line("2026-11-18T11:04:00.000000000+08:00", "lo"),
            _line("2026-11-18T11:05:30.000000000+08:00", "hi"),
            _line("2026-11-18T11:05:31.000000000+08:00", "out"),
        ])
        out, err = filter_logs_by_time_strict(text, "2026-11-18T11:04:00+08:00", "2026-11-18T11:05:30+08:00")
        self.assertEqual(err, "")
        self.assertEqual(out.splitlines(), ["lo", "hi"])


class TestAgentPodLogsHelpers(unittest.IsolatedAsyncioTestCase):
    """agent 集群的 gRPC 隧道路径（helpers 直接走 executor_client.get_pod_logs）。"""

    async def test_all_via_grpc_strips_timestamps(self):
        from app.api import resources
        raw = "\n".join([
            _line("2026-11-18T11:04:05.000000000+08:00", "hello"),
            _line("2026-11-18T11:05:00.000000000+08:00", "world"),
        ]) + "\n"
        with mock.patch.object(resources.executor_client, "acall",
                               new=mock.AsyncMock(return_value={"success": True, "logs": raw})):
            text, err = await resources._agent_pod_logs_all(1, "ns", "pod", "c", False)
        self.assertEqual(err, "")
        self.assertEqual(text.splitlines(), ["hello", "world"])

    async def test_all_old_agent_reports_error(self):
        from app.api import resources
        with mock.patch.object(resources.executor_client, "acall",
                               new=mock.AsyncMock(return_value={"success": True, "logs": "no-timestamp-line"})):
            text, err = await resources._agent_pod_logs_all(1, "ns", "pod", "c", False)
        self.assertEqual(text, "")
        self.assertIn("agent 版本过低", err)

    async def test_all_calls_grpc_with_timestamps_flag(self):
        from app.api import resources
        acall = mock.AsyncMock(return_value={"success": True, "logs": ""})
        with mock.patch.object(resources.executor_client, "acall", acall):
            await resources._agent_pod_logs_all(1, "ns", "pod", "c", False)
        args, kwargs = acall.call_args
        self.assertEqual(args[0], resources.executor_client.get_pod_logs)
        self.assertEqual(args[1:5], (1, "ns", "pod", "c"))
        self.assertEqual(args[5], 0)                       # tail_lines=0 → 整份
        self.assertIs(kwargs.get("timestamps"), True)
        self.assertEqual(kwargs.get("since_seconds"), 0)

    async def test_timed_uses_since_seconds_and_filters(self):
        from app.api import resources
        raw = _line("2026-11-18T11:04:05.000000000+08:00", "hello") + "\n"
        acall = mock.AsyncMock(return_value={"success": True, "logs": raw})
        with mock.patch.object(resources.executor_client, "acall", acall), \
             mock.patch.object(resources.time, "time", return_value=2000000000.0):
            text, err = await resources._agent_pod_logs_timed(
                1, "ns", "pod", "c", "2026-11-18T11:04:00+08:00", "2026-11-18T11:05:30+08:00", False)
        self.assertEqual(err, "")
        self.assertEqual(text.splitlines(), ["hello"])
        args, kwargs = acall.call_args
        self.assertIs(kwargs.get("timestamps"), True)
        self.assertGreater(kwargs.get("since_seconds", 0), 0)

    async def test_timed_old_agent_reports_error(self):
        from app.api import resources
        acall = mock.AsyncMock(return_value={"success": True, "logs": "no-ts-line"})
        with mock.patch.object(resources.executor_client, "acall", acall), \
             mock.patch.object(resources.time, "time", return_value=2000000000.0):
            text, err = await resources._agent_pod_logs_timed(
                1, "ns", "pod", "c", "2026-11-18T11:04:00+08:00", "2026-11-18T11:05:30+08:00", False)
        self.assertEqual(text, "")
        self.assertIn("agent 版本过低", err)

    async def test_timed_invalid_start_reports_error(self):
        from app.api import resources
        acall = mock.AsyncMock(return_value={"success": True, "logs": ""})
        with mock.patch.object(resources.executor_client, "acall", acall):
            text, err = await resources._agent_pod_logs_timed(1, "ns", "pod", "c", "not-a-time", "x", False)
        self.assertEqual(text, "")
        self.assertIn("开始时间无法解析", err)
        acall.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
