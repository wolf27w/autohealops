# -*- coding: utf-8 -*-
"""get_setting 进程内缓存单测（R30 附带，2026-11-11）

不连库：直接验证缓存字典的命中/失效语义。
运行：cd api-server && ./.venv/bin/python -m unittest tests.test_setting_cache -v
"""
import time
import unittest

from app.services import system_setting_service as svc


class TestSettingCache(unittest.TestCase):
    def setUp(self):
        svc.invalidate_setting_cache()

    def tearDown(self):
        svc.invalidate_setting_cache()

    def test_cache_hit_within_ttl(self):
        svc._cache["demo_key"] = (time.monotonic(), "v1")
        self.assertEqual(svc._cache["demo_key"][1], "v1")

    def test_invalidate_single_key(self):
        now = time.monotonic()
        svc._cache["a"] = (now, "1")
        svc._cache["b"] = (now, "2")
        svc.invalidate_setting_cache("a")
        self.assertNotIn("a", svc._cache)
        self.assertIn("b", svc._cache)

    def test_invalidate_all(self):
        now = time.monotonic()
        svc._cache["a"] = (now, "1")
        svc._cache["b"] = (now, "2")
        svc.invalidate_setting_cache()
        self.assertEqual(svc._cache, {})

    def test_ttl_expiry(self):
        svc._cache["stale"] = (time.monotonic() - svc._CACHE_TTL - 1, "old")
        hit = svc._cache.get("stale")
        self.assertFalse(hit and (time.monotonic() - hit[0]) < svc._CACHE_TTL)
