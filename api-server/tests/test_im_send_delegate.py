# -*- coding: utf-8 -*-
"""WS4：IM 发送层收敛单测 —— notification_service 的 6 个发送函数委托 messaging。

验证：返回 {"status","body"}；钉钉 text 的 payload 含正确 msgtype / at 字段。
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import messaging, notification_service


class _FakeResp:
    def __init__(self, status_code=200, text='{"errcode":0,"errmsg":"ok"}'):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResp()


class TestImSendDelegate(unittest.TestCase):
    def setUp(self):
        self.client = _FakeClient()
        patcher = mock.patch.object(messaging, "get_http_client", return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _send(self, coro):
        return asyncio.run(coro)

    def _last(self):
        return self.client.calls[-1]["json"]

    def test_dingtalk_text_delegates(self):
        r = self._send(notification_service.send_dingtalk_text(
            "https://oapi.dingtalk.com/robot/send?access_token=x",
            "hello", at_mobiles=["13800000000"], at_all=False))
        self.assertEqual(set(r), {"status", "body"})
        self.assertEqual(r["status"], 200)
        p = self._last()
        self.assertEqual(p["msgtype"], "text")
        self.assertEqual(p["text"]["content"], "hello")
        self.assertEqual(p["at"], {"atMobiles": ["13800000000"], "isAtAll": False})

    def test_dingtalk_text_at_all(self):
        r = self._send(notification_service.send_dingtalk_text("u", "c", at_all=True))
        self.assertEqual(set(r), {"status", "body"})
        p = self._last()
        self.assertEqual(p["msgtype"], "text")
        self.assertEqual(p["at"], {"isAtAll": True})

    def test_dingtalk_markdown_delegates(self):
        r = self._send(notification_service.send_dingtalk_markdown("u", "标题", "内容"))
        self.assertEqual(set(r), {"status", "body"})
        p = self._last()
        self.assertEqual(p["msgtype"], "markdown")
        self.assertEqual(p["markdown"]["title"], "标题")
        self.assertEqual(p["markdown"]["text"], "内容")

    def test_dingtalk_action_card_delegates(self):
        buttons = [{"text": "批准", "value": "approve_1"}]
        r = self._send(notification_service.send_dingtalk_action_card("u", "t", "x", buttons))
        self.assertEqual(set(r), {"status", "body"})
        p = self._last()
        self.assertEqual(p["msgtype"], "actionCard")
        self.assertEqual(p["actionCard"]["btns"], buttons)

    def test_wechat_text_delegates(self):
        r = self._send(notification_service.send_wechat_text("u", "c", mentioned_list=["a"], at_all=False))
        self.assertEqual(set(r), {"status", "body"})
        p = self._last()
        self.assertEqual(p["msgtype"], "text")
        self.assertEqual(p["text"]["mentioned_list"], ["a"])

    def test_wechat_text_at_all(self):
        self._send(notification_service.send_wechat_text("u", "c", at_all=True))
        p = self._last()
        self.assertEqual(p["text"]["mentioned_list"], ["@all"])

    def test_wechat_markdown_delegates(self):
        r = self._send(notification_service.send_wechat_markdown("u", "c"))
        self.assertEqual(set(r), {"status", "body"})
        p = self._last()
        self.assertEqual(p["msgtype"], "markdown")
        self.assertEqual(p["markdown"]["content"], "c")

    def test_feishu_webhook_text_delegates(self):
        r = self._send(notification_service.send_feishu_webhook_text("u", "c"))
        self.assertEqual(set(r), {"status", "body"})
        p = self._last()
        self.assertEqual(p["msg_type"], "text")
        self.assertEqual(p["content"]["text"], "c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
