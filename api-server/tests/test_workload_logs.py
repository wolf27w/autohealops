# -*- coding: utf-8 -*-
"""R5 工作负载聚合日志：纯函数单测（行合并 / 前缀 / 增量行数计算）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_workload_logs -v
"""
import unittest

from app.api.workload_logs import incremental_new_lines, merge_log_lines, prefix_line


class TestPrefixLine(unittest.TestCase):
    def test_basic_prefix(self):
        self.assertEqual(prefix_line("web-0", "hello"), "[web-0] hello")

    def test_empty_line(self):
        self.assertEqual(prefix_line("web-0", ""), "[web-0] ")

    def test_prefix_preserves_line_content(self):
        self.assertEqual(prefix_line("pod", "a[b]c d"), "[pod] a[b]c d")


class TestMergeLogLines(unittest.TestCase):
    def test_empty_entries(self):
        self.assertEqual(merge_log_lines([]), [])

    def test_order_follows_entries(self):
        # entries 顺序即最终顺序（pod 起始时间 → 行序）
        entries = [("pod-a", ["1", "2"]), ("pod-b", ["3"])]
        rows = merge_log_lines(entries)
        self.assertEqual([r["pod"] for r in rows], ["pod-a", "pod-a", "pod-b"])
        self.assertEqual([r["line"] for r in rows], ["1", "2", "3"])

    def test_prefix_true_adds_pod_prefix(self):
        entries = [("pod-a", ["1", "2"])]
        rows = merge_log_lines(entries, prefix=True)
        self.assertEqual(rows, [{"pod": "pod-a", "line": "[pod-a] 1"},
                                {"pod": "pod-a", "line": "[pod-a] 2"}])

    def test_prefix_false_keeps_raw_line(self):
        entries = [("pod-a", ["1"])]
        rows = merge_log_lines(entries, prefix=False)
        self.assertEqual(rows[0]["line"], "1")

    def test_empty_lines_skipped_none(self):
        # 空 pod 行列表不产生任何行
        entries = [("pod-a", [])]
        self.assertEqual(merge_log_lines(entries), [])


class TestIncrementalNewLines(unittest.TestCase):
    def test_first_snapshot_all_new(self):
        lines = ["a", "b", "c"]
        new, pushed = incremental_new_lines(lines, 0)
        self.assertEqual(new, ["a", "b", "c"])
        self.assertEqual(pushed, 3)

    def test_only_new_appended(self):
        lines = ["a", "b", "c", "d"]
        new, pushed = incremental_new_lines(lines, 3)
        self.assertEqual(new, ["d"])
        self.assertEqual(pushed, 4)

    def test_no_change(self):
        lines = ["a", "b"]
        new, pushed = incremental_new_lines(lines, 2)
        self.assertEqual(new, [])
        self.assertEqual(pushed, 2)

    def test_shrunk_resets_from_start(self):
        # 日志被截断/轮转（行数变小）→ 从头重推
        lines = ["x", "y"]
        new, pushed = incremental_new_lines(lines, 5)
        self.assertEqual(new, ["x", "y"])
        self.assertEqual(pushed, 2)

    def test_empty_lines(self):
        new, pushed = incremental_new_lines([], 0)
        self.assertEqual(new, [])
        self.assertEqual(pushed, 0)


if __name__ == "__main__":
    unittest.main()
