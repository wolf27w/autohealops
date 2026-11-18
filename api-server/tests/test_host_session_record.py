# -*- coding: utf-8 -*-
"""R43 H4 会话录制（asciinema cast v2）服务单测。

覆盖：
- cast 头部生成（cast_header_line）
- 输出/输入事件写入（format_event）
- 行格式合法性（json.loads 逐行校验）
- 时长统计（相对秒数递增）
- CastRecorder 端到端（落盘 + 回填 record_path/record_bytes）
- 清理逻辑（purge_old_recordings：删文件 + 清 record_path）
- 文件不存在时的读取行为（cast_exists / read_cast_header / resolve_cast_path）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_host_session_record -v
"""
import json
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import host_session_record_service as rec_svc


class _FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *a):
        return False


class TestCastHeader(unittest.TestCase):
    def test_header_fields(self):
        line = rec_svc.cast_header_line(cols=120, rows=32, started_epoch=1731900000)
        hdr = json.loads(line)
        self.assertEqual(hdr["version"], 2)
        self.assertEqual(hdr["width"], 120)
        self.assertEqual(hdr["height"], 32)
        self.assertEqual(hdr["timestamp"], 1731900000)
        self.assertEqual(hdr["env"]["SHELL"], "/bin/bash")
        self.assertEqual(hdr["env"]["TERM"], "xterm-256color")

    def test_header_defaults_and_trailing_newline(self):
        line = rec_svc.cast_header_line()
        self.assertTrue(line.endswith("\n"))
        hdr = json.loads(line)
        self.assertIn("version", hdr)
        self.assertIn("timestamp", hdr)


class TestFormatEvent(unittest.TestCase):
    def test_output_event(self):
        line = rec_svc.format_event(0.123456, "o", "hello\nworld")
        ev = json.loads(line)
        self.assertEqual(ev[0], 0.123456)
        self.assertEqual(ev[1], "o")
        self.assertEqual(ev[2], "hello\nworld")

    def test_input_event(self):
        ev = json.loads(rec_svc.format_event(1.5, "i", "ls -la"))
        self.assertEqual(ev[1], "i")
        self.assertEqual(ev[2], "ls -la")

    def test_escaping_quotes_and_unicode(self):
        line = rec_svc.format_event(2.0, "o", 'a"b\\c\u4e2d\u6587')
        ev = json.loads(line)
        self.assertEqual(ev[2], 'a"b\\c\u4e2d\u6587')

    def test_trailing_newline(self):
        self.assertTrue(rec_svc.format_event(0.0, "o", "x").endswith("\n"))


class TestCastRecorder(unittest.IsolatedAsyncioTestCase):
    def _fake_session_for_close(self):
        sess = SimpleNamespace(id=999, record_path=None, record_bytes=0)
        session = MagicMock()
        session.get = AsyncMock(return_value=sess)
        session.commit = AsyncMock()
        return session, sess

    async def test_end_to_end_write_and_backfill(self):
        with tempfile.TemporaryDirectory() as d:
            rec = rec_svc.CastRecorder(999, cols=100, rows=30,
                                       base_dir=Path(d), started_epoch=1731900000)
            rec.add("output", b"prompt$ ")
            time.sleep(0.01)
            rec.add("output", b"total 4\n")
            time.sleep(0.01)
            rec.add("input", b"ls\n")
            session, sess = self._fake_session_for_close()
            with patch.object(rec_svc, "async_session", lambda: _FakeSessionCtx(session)):
                await rec.close("finished", "")

            # 文件内容：头 + 3 行事件
            path = rec._path
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 4)

            hdr = json.loads(lines[0])
            self.assertEqual(hdr["version"], 2)
            self.assertEqual(hdr["width"], 100)
            self.assertEqual(hdr["height"], 30)

            events = [json.loads(ln) for ln in lines[1:]]
            self.assertEqual([e[1] for e in events], ["o", "o", "i"])
            self.assertEqual(events[2][2], "ls\n")
            # 时长递增（相对秒数严格递增）
            ts = [e[0] for e in events]
            self.assertTrue(all(ts[i] < ts[i + 1] for i in range(len(ts) - 1)))

            # 回填元数据
            self.assertEqual(sess.record_path, str(path))
            self.assertGreater(sess.record_bytes, 0)

    async def test_resize_updates_cols_rows(self):
        with tempfile.TemporaryDirectory() as d:
            rec = rec_svc.CastRecorder(1, cols=80, rows=24, base_dir=Path(d))
            try:
                rec.resize(200, 50)
                self.assertEqual(rec.cols, 200)
                self.assertEqual(rec.rows, 50)
            finally:
                session, sess = self._fake_session_for_close()
                with patch.object(rec_svc, "async_session", lambda: _FakeSessionCtx(session)):
                    await rec.close("finished", "")

    async def test_open_failure_does_not_raise(self):
        # 用一个「目录」占位文件，使 open() 失败 → _failed=True，add/flush/close 全部安全
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "1.cast").mkdir()  # 目录占了文件路径
            rec = rec_svc.CastRecorder(1, base_dir=base)
            self.assertTrue(rec._failed)
            rec.add("output", b"x")
            await rec.flush()
            session, sess = self._fake_session_for_close()
            with patch.object(rec_svc, "async_session", lambda: _FakeSessionCtx(session)):
                await rec.close("finished", "")
            self.assertEqual(sess.record_path, None)  # 失败不写元数据


class TestReadBehavior(unittest.TestCase):
    def test_missing_file(self):
        self.assertFalse(rec_svc.cast_exists(None))
        self.assertFalse(rec_svc.cast_exists("/nonexistent/xx.cast"))
        self.assertIsNone(rec_svc.read_cast_header(None))
        self.assertIsNone(rec_svc.resolve_cast_path(None))
        self.assertIsNone(rec_svc.resolve_cast_path("/nonexistent/xx.cast"))

    def test_read_header_existing_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "1.cast"
            p.write_text(rec_svc.cast_header_line(120, 32, 1731900000) + "[0.1, \"o\", \"x\"]\n")
            self.assertTrue(rec_svc.cast_exists(str(p)))
            hdr = rec_svc.read_cast_header(str(p))
            self.assertEqual(hdr["width"], 120)
            self.assertEqual(rec_svc.resolve_cast_path(str(p)), p)


class TestRemoveRecordFile(unittest.TestCase):
    def test_remove_existing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "1.cast"
            p.write_text("x")
            self.assertTrue(rec_svc._remove_record_file(str(p)))
            self.assertFalse(p.exists())

    def test_remove_missing_or_none(self):
        self.assertFalse(rec_svc._remove_record_file(None))
        self.assertFalse(rec_svc._remove_record_file("/nonexistent/xx.cast"))


class TestPurge(unittest.IsolatedAsyncioTestCase):
    async def test_purge_deletes_file_and_clears_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "1.cast"
            p.write_text("{}")
            sess = SimpleNamespace(id=1, record_path=str(p), record_bytes=100,
                                  started_at=datetime(2026, 1, 1))
            result = MagicMock()
            result.scalars.return_value = [sess]
            session = MagicMock()
            session.execute = AsyncMock(return_value=result)
            session.get = AsyncMock(return_value=sess)
            session.commit = AsyncMock()
            with patch.object(rec_svc, "async_session", lambda: _FakeSessionCtx(session)):
                n = await rec_svc.purge_old_recordings(days=30)
            self.assertEqual(n, 1)
            self.assertFalse(p.exists())
            self.assertIsNone(sess.record_path)
            self.assertEqual(sess.record_bytes, 0)

    async def test_purge_no_rows(self):
        result = MagicMock()
        result.scalars.return_value = []
        session = MagicMock()
        session.execute = AsyncMock(return_value=result)
        with patch.object(rec_svc, "async_session", lambda: _FakeSessionCtx(session)):
            n = await rec_svc.purge_old_recordings(days=30)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
