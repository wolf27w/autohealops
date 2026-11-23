# -*- coding: utf-8 -*-
"""启动失败日志窗口单测（2026-11-23）

老板反馈分析不到主要原因：原来只保留关键行+末尾，Spring 的 Description/Action/Caused by 会被截掉。
"""
import unittest

from app.utils.pod_logs import extract_startup_window


class TestStartupWindow(unittest.TestCase):
    def test_captures_description_and_caused_by(self):
        lines = ["INFO starting"] + ["noise"] * 50 + [
            "APPLICATION FAILED TO START",
            "Description:",
            "Failed to bind properties under 'spring.datasource.url'",
            "Action:",
            "Update your application's configuration",
            "Caused by: java.lang.IllegalArgumentException: bad url",
        ] + ["tail"] * 10
        out = "\n".join(extract_startup_window(lines))
        self.assertIn("APPLICATION FAILED TO START", out)
        self.assertIn("Failed to bind properties", out)
        self.assertIn("Update your application's configuration", out)
        self.assertIn("Caused by", out)

    def test_no_marker_returns_empty(self):
        self.assertEqual(extract_startup_window(["INFO ok", "INFO ok"]), [])

    def test_duplicate_key_marker(self):
        lines = ["x"] * 5 + ["org.yaml.snakeyaml.constructor.DuplicateKeyException: while constructing a mapping"] + ["detail"] * 5
        self.assertIn("DuplicateKeyException", "\n".join(extract_startup_window(lines)))


if __name__ == "__main__":
    unittest.main()
