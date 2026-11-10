# -*- coding: utf-8 -*-
"""R13 回归：故障类型登记判断（未登记 = 未分类 → 沉淀提示词候选）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_diag_category -v
"""
import unittest

from app.services.diag_category import is_known_fault_type, category_of


class TestIsKnownFaultType(unittest.TestCase):
    def test_known(self):
        self.assertTrue(is_known_fault_type("AppOOM"))
        self.assertTrue(is_known_fault_type("LogKeyword"))
        self.assertTrue(is_known_fault_type("CrashLoopBackOff"))
        self.assertTrue(is_known_fault_type("NodeNotReady"))
        self.assertTrue(is_known_fault_type("DBConnFailed"))
        self.assertTrue(is_known_fault_type("db_mysql"))

    def test_unknown(self):
        self.assertFalse(is_known_fault_type("SomeBrandNewError"))
        self.assertFalse(is_known_fault_type(""))
        self.assertFalse(is_known_fault_type(None))


class TestCategoryOf(unittest.TestCase):
    def test_app_fault_types_map_app(self):
        for ft in ("AppOOM", "AppSQL", "AppConnRefused", "AppBusiness"):
            self.assertEqual(category_of("log", ft), "app")

    def test_unknown_log_defaults_app(self):
        self.assertEqual(category_of("log", "Whatever"), "app")


if __name__ == "__main__":
    unittest.main()
