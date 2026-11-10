# -*- coding: utf-8 -*-
"""R13 回归：脱敏 + 故障类型登记判断。

运行：cd ai-service && ./.venv/bin/python -m unittest tests.test_prompt_suggestion -v
"""
import unittest

from app.services.prompt_suggestion_service import _mask


class TestMask(unittest.TestCase):
    def test_ip(self):
        self.assertIn("<IP>", _mask("connect to 10.0.0.1:3306 failed"))
        self.assertNotIn("10.0.0.1", _mask("connect to 10.0.0.1:3306 failed"))

    def test_secret(self):
        self.assertIn("token=***", _mask("token=abc123"))
        self.assertIn("password=***", _mask("password=supersecret"))

    def test_email_phone(self):
        out = _mask("admin@example.com 13800138000")
        self.assertIn("<EMAIL>", out)
        self.assertIn("<PHONE>", out)

    def test_jdbc(self):
        out = _mask("jdbc:mysql://user:pw@1.2.3.4:3306/db")
        self.assertIn("jdbc:mysql://***", out)

    def test_empty(self):
        self.assertEqual(_mask(""), "")


if __name__ == "__main__":
    unittest.main()
