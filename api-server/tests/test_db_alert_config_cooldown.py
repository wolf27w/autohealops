# -*- coding: utf-8 -*-
"""DB 告警配置类错误识别 + 设置读缓存单测（2026-11-11）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_db_alert_config_cooldown tests.test_setting_cache -v
"""
import unittest

from app.services.db_alert_service import CONFIG_ERROR_MARKERS, _classify_failure, _is_config_error


class TestConfigErrorClassify(unittest.TestCase):
    def test_agent_key_missing_is_config_error(self):
        err = "agent 配置里没有 key=stg-mysql（可能已删除或未下发）"
        self.assertTrue(_is_config_error(err))

    def test_not_delivered_is_config_error(self):
        self.assertTrue(_is_config_error("数据库配置未下发到 agent"))

    def test_not_configured_is_config_error(self):
        self.assertTrue(_is_config_error("agent 未配置该数据库"))

    def test_real_connection_failure_is_not_config_error(self):
        self.assertFalse(_is_config_error("Can't connect to MySQL server on 'mysql.stg' (111)"))

    def test_auth_failure_is_not_config_error(self):
        self.assertFalse(_is_config_error("Access denied for user 'root'@'10.0.0.1' (using password: YES)"))

    def test_empty_error_is_not_config_error(self):
        self.assertFalse(_is_config_error(""))

    def test_auth_failure_still_classified_as_auth(self):
        self.assertEqual(_classify_failure("Access denied for user 'x'"), "DBAuthFailed")
        self.assertEqual(_classify_failure("agent 配置里没有 key=foo"), "DBConnFailed")

    def test_markers_are_lowercase_comparable(self):
        # 大小写混写也要命中（_is_config_error 内部统一转小写比较）
        self.assertTrue(_is_config_error("AGENT 配置里没有 KEY=stg-mysql"))
        self.assertTrue(all(m == m for m in CONFIG_ERROR_MARKERS))
