# -*- coding: utf-8 -*-
"""H10：agent 接入令牌固定 266 字符（urlsafe base64，无填充）。

运行：
    cd api-server && ./.venv/bin/python -m unittest tests.test_agent_token -v
"""
import os
import re
import sys
import unittest

# 允许从 api-server 目录直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.api.clusters import _new_agent_token  # noqa: E402

# 需求口径：固定 266 字符；字符集限定为 urlsafe base64（A-Za-z0-9_-），无填充（末尾不出现 '='）。
EXPECTED_LEN = 266
_URLSAFE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class TestAgentToken(unittest.TestCase):
    """agent 接入令牌：长度恒为 266 + 字符集 urlsafe + 无填充 + 唯一性。"""

    def test_length_is_exactly_266(self):
        for _ in range(50):
            self.assertEqual(len(_new_agent_token()), EXPECTED_LEN)

    def test_charset_urlsafe_and_no_padding(self):
        for _ in range(50):
            token = _new_agent_token()
            self.assertRegex(token, _URLSAFE_RE, "令牌字符集必须限定为 urlsafe base64")
            self.assertNotIn("=", token, "无填充：末尾不应出现 '='")

    def test_uniqueness(self):
        tokens = {_new_agent_token() for _ in range(100)}
        self.assertEqual(len(tokens), 100, "100 次生成不应出现重复")


if __name__ == "__main__":
    unittest.main()
