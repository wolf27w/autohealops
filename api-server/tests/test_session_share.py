# -*- coding: utf-8 -*-
"""R31 诊断会话共享：权限判定纯函数单测（2026-11-12）

覆盖列表可见性（can_view_session）与消息可读性（can_read_session）两套规则，
均为纯函数，不依赖数据库。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_session_share -q
"""
import unittest

from app.api.agent import can_view_session, can_read_session


class TestCanViewSession(unittest.TestCase):
    """会话列表可见性：自己 ∪ admin 的 proactive ∪ (共享且有权)。"""

    def test_owner_visible(self):
        self.assertTrue(can_view_session(
            user_id=1, is_admin=False, sess_user_id=1,
            sess_source="agent", sess_shared=False, has_cluster_perm=False))

    def test_non_owner_private_hidden(self):
        self.assertFalse(can_view_session(
            user_id=2, is_admin=False, sess_user_id=1,
            sess_source="agent", sess_shared=False, has_cluster_perm=True))

    def test_shared_with_perm_visible(self):
        self.assertTrue(can_view_session(
            user_id=2, is_admin=False, sess_user_id=1,
            sess_source="agent", sess_shared=True, has_cluster_perm=True))

    def test_shared_without_perm_hidden(self):
        self.assertFalse(can_view_session(
            user_id=2, is_admin=False, sess_user_id=1,
            sess_source="agent", sess_shared=True, has_cluster_perm=False))

    def test_admin_proactive_visible(self):
        self.assertTrue(can_view_session(
            user_id=2, is_admin=True, sess_user_id=0,
            sess_source="proactive", sess_shared=False, has_cluster_perm=False))

    def test_admin_other_private_hidden(self):
        self.assertFalse(can_view_session(
            user_id=2, is_admin=True, sess_user_id=1,
            sess_source="agent", sess_shared=False, has_cluster_perm=False))

    def test_admin_shared_visible(self):
        # 实际调用中 admin 的 check_cluster_permission 恒 True，故 has_cluster_perm=True
        self.assertTrue(can_view_session(
            user_id=2, is_admin=True, sess_user_id=1,
            sess_source="agent", sess_shared=True, has_cluster_perm=True))


class TestCanReadSession(unittest.TestCase):
    """会话消息可读性：owner ∪ admin ∪ (共享且有权)。"""

    def test_owner_readable(self):
        self.assertTrue(can_read_session(
            user_id=1, is_admin=False, sess_user_id=1,
            sess_shared=False, has_cluster_perm=False))

    def test_admin_readable_any(self):
        self.assertTrue(can_read_session(
            user_id=2, is_admin=True, sess_user_id=1,
            sess_shared=False, has_cluster_perm=False))

    def test_shared_with_perm_readable(self):
        self.assertTrue(can_read_session(
            user_id=2, is_admin=False, sess_user_id=1,
            sess_shared=True, has_cluster_perm=True))

    def test_shared_without_perm_forbidden(self):
        self.assertFalse(can_read_session(
            user_id=2, is_admin=False, sess_user_id=1,
            sess_shared=True, has_cluster_perm=False))

    def test_non_owner_private_forbidden(self):
        self.assertFalse(can_read_session(
            user_id=2, is_admin=False, sess_user_id=1,
            sess_shared=False, has_cluster_perm=True))


if __name__ == "__main__":
    unittest.main()
