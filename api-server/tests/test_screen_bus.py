# -*- coding: utf-8 -*-
"""大屏推送 ScreenBus 单测。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_screen_bus -q
"""
import asyncio
import json
import unittest

from app.services.screen_bus import (
    ScreenBus,
    build_frame,
    dedup_key,
    encode_frame,
)


class TestFrameAssembly(unittest.TestCase):
    """纯函数：帧组装 + 去重键。"""

    def test_build_frame_structure(self):
        frame = build_frame("alert_created", {"id": 1, "name": "x"}, ts="2026-11-12T00:00:00")
        self.assertEqual(frame["event_type"], "alert_created")
        self.assertEqual(frame["payload"], {"id": 1, "name": "x"})
        self.assertEqual(frame["ts"], "2026-11-12T00:00:00")

    def test_build_frame_defaults(self):
        frame = build_frame("ticket_closed", None)
        self.assertEqual(frame["payload"], {})
        self.assertTrue(frame["ts"])

    def test_encode_frame_wire_format(self):
        s = encode_frame("selfheal_done", {"id": 7}, ts="T")
        self.assertTrue(s.startswith("event: selfheal_done\n"))
        self.assertTrue(s.endswith("\n\n"))
        data_line = [ln for ln in s.splitlines() if ln.startswith("data: ")][0]
        obj = json.loads(data_line[len("data: "):])
        self.assertEqual(obj["event_type"], "selfheal_done")
        self.assertEqual(obj["payload"], {"id": 7})
        self.assertEqual(obj["ts"], "T")

    def test_dedup_key_prefers_id(self):
        self.assertEqual(dedup_key("alert_created", {"id": 3}), "alert_created:3")
        self.assertEqual(dedup_key("alert_created", {"id": 3, "extra": 1}), "alert_created:3")

    def test_dedup_key_fallback_payload(self):
        k1 = dedup_key("alert_merged", {"a": 1})
        k2 = dedup_key("alert_merged", {"a": 1})
        k3 = dedup_key("alert_merged", {"a": 2})
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)
        self.assertNotEqual(dedup_key("alert_created", {"a": 1}),
                            dedup_key("alert_merged", {"a": 1}))


class TestScreenBusDelivery(unittest.IsolatedAsyncioTestCase):
    """publish → subscribe 队列的投递与去重。"""

    async def asyncSetUp(self):
        self.bus = ScreenBus(dedup_window=2.0)

    async def test_publish_delivers_to_subscriber(self):
        q = self.bus.subscribe()
        self.assertTrue(self.bus.publish("alert_created", {"id": 1}))
        frame = q.get_nowait()
        self.assertIn("event: alert_created", frame)
        self.bus.unsubscribe(q)

    async def test_publish_dedup_same_identity(self):
        q = self.bus.subscribe()
        self.assertTrue(self.bus.publish("alert_created", {"id": 5}))
        # 同 id → 去重；带额外字段的同 id → 也去重
        self.assertFalse(self.bus.publish("alert_created", {"id": 5}))
        self.assertFalse(self.bus.publish("alert_created", {"id": 5, "extra": 1}))
        self.assertEqual(q.qsize(), 1)
        self.bus.unsubscribe(q)

    async def test_publish_different_keys_not_deduped(self):
        q = self.bus.subscribe()
        self.assertTrue(self.bus.publish("alert_created", {"id": 1}))
        self.assertTrue(self.bus.publish("alert_created", {"id": 2}))
        self.assertTrue(self.bus.publish("alert_merged", {"id": 1}))
        self.assertEqual(q.qsize(), 3)
        self.bus.unsubscribe(q)

    async def test_dedup_window_expiry(self):
        bus = ScreenBus(dedup_window=0.0)
        q = bus.subscribe()
        self.assertTrue(bus.publish("ticket_closed", {"id": 2}))
        self.assertTrue(bus.publish("ticket_closed", {"id": 2}))  # 窗口 0 → 不去重
        self.assertEqual(q.qsize(), 2)
        bus.unsubscribe(q)

    async def test_unsubscribe_stops_delivery(self):
        q = self.bus.subscribe()
        self.bus.unsubscribe(q)
        self.assertTrue(self.bus.publish("ticket_created", {"id": 9}))
        self.assertTrue(q.empty())

    async def test_queue_full_drops_oldest(self):
        bus = ScreenBus(dedup_window=0.0, queue_size=2)
        q = bus.subscribe()
        bus.publish("ticket_created", {"id": 1})
        bus.publish("ticket_created", {"id": 2})
        bus.publish("ticket_created", {"id": 3})  # 满 → 丢最旧（id=1）
        self.assertEqual(q.qsize(), 2)
        frames = [q.get_nowait() for _ in range(2)]
        ids = [json.loads(f.split("data: ", 1)[1])["payload"]["id"] for f in frames]
        self.assertEqual(ids, [2, 3])
        bus.unsubscribe(q)
