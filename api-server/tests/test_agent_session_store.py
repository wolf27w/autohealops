# -*- coding: utf-8 -*-
"""会话历史文件存储单测（2026-11-11）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_agent_session_store -v
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from app.core.config import settings
from app.services import agent_session_store as store


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-sessions-"))
        self._old = getattr(settings, "AGENT_SESSION_DIR", "")
        settings.AGENT_SESSION_DIR = str(self.tmp)

    def tearDown(self):
        settings.AGENT_SESSION_DIR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_append_and_read_roundtrip(self):
        store.append_message(1, {"id": 11, "role": "user", "content": "内存高"})
        store.append_message(1, {"id": 12, "role": "tool", "tool_name": "search_logs",
                                 "tool_output": "OOMKilled", "content": ""})
        msgs = store.read_messages(1)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["content"], "内存高")
        self.assertEqual(msgs[1]["tool_name"], "search_logs")
        self.assertEqual(msgs[1]["tool_output"], "OOMKilled")

    def test_missing_file_returns_none(self):
        self.assertIsNone(store.read_messages(999))

    def test_append_separates_sessions(self):
        store.append_message(1, {"role": "user", "content": "a"})
        store.append_message(2, {"role": "user", "content": "b"})
        self.assertEqual(len(store.read_messages(1)), 1)
        self.assertEqual(store.read_messages(2)[0]["content"], "b")

    def test_delete_session_file(self):
        store.append_message(3, {"role": "user", "content": "x"})
        self.assertIsNotNone(store.read_messages(3))
        store.delete_session_file(3)
        self.assertIsNone(store.read_messages(3))

    def test_file_is_jsonl(self):
        store.append_message(4, {"role": "user", "content": "第一行\n第二行"})
        raw = store.session_file(4).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(raw), 1)  # 换行被 json 转义，不破坏一行一消息
        self.assertEqual(json.loads(raw[0])["content"], "第一行\n第二行")
