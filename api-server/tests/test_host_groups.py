# -*- coding: utf-8 -*-
"""R25 回归：主机/虚拟机分组权限矩阵 + 角色层级（2026-11-09）

覆盖：
- 角色层级 admin > operator > viewer；需求层级 manage > operate > view
- 平台 admin 全权限；组 viewer 只读；组 operator 可操作不可管理；组 admin 全权限
- 无组角色：只有 view（能进列表即可见），无操作/管理/删除

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_host_groups -v
"""
import unittest
from types import SimpleNamespace

from app.services import host_group_service as hgs


def _user(role="viewer", uid=1):
    return SimpleNamespace(id=uid, role=role)


def _host(group_id=1, kind="host"):
    return SimpleNamespace(group_id=group_id, kind=kind)


class TestRoleLevels(unittest.TestCase):
    def test_role_level_ordering(self):
        self.assertLess(hgs.ROLE_LEVEL["viewer"], hgs.ROLE_LEVEL["operator"])
        self.assertLess(hgs.ROLE_LEVEL["operator"], hgs.ROLE_LEVEL["admin"])

    def test_need_level_ordering(self):
        self.assertLess(hgs.NEED_LEVEL["view"], hgs.NEED_LEVEL["operate"])
        self.assertLess(hgs.NEED_LEVEL["operate"], hgs.NEED_LEVEL["manage"])

    def test_is_platform_admin(self):
        self.assertTrue(hgs.is_platform_admin(_user("admin")))
        self.assertFalse(hgs.is_platform_admin(_user("operator")))
        self.assertFalse(hgs.is_platform_admin(_user("viewer")))


class TestHostCan(unittest.TestCase):
    def test_platform_admin_all_true(self):
        can = hgs.host_can(_user("admin"), _host(), True, None)
        self.assertTrue(all(can.values()))

    def test_viewer_readonly(self):
        can = hgs.host_can(_user("viewer"), _host(), False, "viewer")
        self.assertTrue(can["view"])
        self.assertFalse(can["operate"])
        self.assertFalse(can["manage"])
        self.assertFalse(can["delete"])

    def test_operator_can_operate_not_manage(self):
        can = hgs.host_can(_user("operator"), _host(), False, "operator")
        self.assertTrue(can["view"])
        self.assertTrue(can["operate"])
        self.assertFalse(can["manage"])
        self.assertFalse(can["delete"])

    def test_group_admin_full(self):
        can = hgs.host_can(_user("viewer"), _host(), False, "admin")
        self.assertTrue(all(can.values()))

    def test_no_group_role_view_only(self):
        can = hgs.host_can(_user("viewer"), _host(), False, None)
        self.assertTrue(can["view"])
        self.assertFalse(can["operate"])
        self.assertFalse(can["manage"])
        self.assertFalse(can["delete"])


if __name__ == "__main__":
    unittest.main()
