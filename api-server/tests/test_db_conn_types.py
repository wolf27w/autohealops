# -*- coding: utf-8 -*-
"""R18 回归：数据库连接按类型校验/字段落库（2026-11-09）

覆盖：
- mysql：database 可空、host/username 必填
- pg：database 必填、默认端口 5432
- oracle：Service Name / SID / TNS 三选一；TNS 括号配对校验；SYSDBA/SYSOPER 角色
- 切换类型时清空上一类型的专属字段（host/port/username 保留）

运行：cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import unittest
from types import SimpleNamespace

from app.api.db_connections import (
    _apply_type_fields,
    _tns_descriptor_ok,
    _validate_conn_payload,
)

TNS_OK = "(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=10.0.0.1)(PORT=1521))(CONNECT_DATA=(SERVICE_NAME=ORCL)))"


def _fake_conn(**kw):
    base = dict(
        db_type="", host="", port=0, database="", username="",
        connect_mode="", service_name="", sid="", tns_descriptor="", oracle_role="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestMysqlPgValidation(unittest.TestCase):
    def test_mysql_database_optional(self):
        self.assertIsNone(_validate_conn_payload("mysql", {"name": "a", "host": "h", "username": "u"}))

    def test_mysql_requires_username(self):
        msg = _validate_conn_payload("mysql", {"name": "a", "host": "h", "username": ""})
        self.assertIn("用户名", msg or "")

    def test_pg_requires_database(self):
        msg = _validate_conn_payload("pg", {"name": "a", "host": "h", "username": "u", "database": ""})
        self.assertIn("数据库名", msg or "")

    def test_pg_ok(self):
        self.assertIsNone(_validate_conn_payload("pg", {"name": "a", "host": "h", "username": "u", "database": "d"}))

    def test_default_ports(self):
        for t, port in (("mysql", 3306), ("pg", 5432), ("oracle", 1521)):
            c = _fake_conn()
            data = {"name": "a", "host": "h", "username": "u", "database": "d",
                    "connect_mode": "service_name", "service_name": "ORCL"}
            _apply_type_fields(c, t, data)
            self.assertEqual(c.port, port)


class TestOracleValidation(unittest.TestCase):
    def test_service_name_requires_value(self):
        msg = _validate_conn_payload("oracle", {
            "name": "a", "username": "u", "host": "h", "connect_mode": "service_name", "service_name": ""})
        self.assertIn("服务名", msg or "")

    def test_sid_requires_value(self):
        msg = _validate_conn_payload("oracle", {
            "name": "a", "username": "u", "host": "h", "connect_mode": "sid", "sid": ""})
        self.assertIn("SID", msg or "")

    def test_tns_ok(self):
        self.assertIsNone(_validate_conn_payload("oracle", {
            "name": "a", "username": "u", "connect_mode": "tns", "tns_descriptor": TNS_OK}))

    def test_tns_rejects_bad_prefix_and_unbalanced(self):
        for bad in ("(ADDRESS=(HOST=x))", "(DESCRIPTION=(ADDRESS=(HOST=x))"):
            self.assertTrue(_validate_conn_payload("oracle", {
                "name": "a", "username": "u", "connect_mode": "tns", "tns_descriptor": bad}))
        self.assertFalse(_tns_descriptor_ok("(ADDRESS=(HOST=x))"))
        self.assertTrue(_tns_descriptor_ok(TNS_OK))

    def test_oracle_role_enum(self):
        base = {"name": "a", "username": "u", "host": "h", "connect_mode": "service_name", "service_name": "ORCL"}
        self.assertIsNone(_validate_conn_payload("oracle", {**base, "oracle_role": "sysdba"}))
        self.assertIsNone(_validate_conn_payload("oracle", {**base, "oracle_role": "sysoper"}))
        self.assertIn("角色", _validate_conn_payload("oracle", {**base, "oracle_role": "superuser"}) or "")


class TestApplyTypeFields(unittest.TestCase):
    def test_oracle_service_name_clears_other_modes(self):
        c = _fake_conn(database="old", sid="OLDSID", tns_descriptor="old")
        _apply_type_fields(c, "oracle", {
            "host": "h", "username": "u", "connect_mode": "service_name", "service_name": "ORCL"})
        self.assertEqual(c.service_name, "ORCL")
        self.assertEqual(c.sid, "")
        self.assertEqual(c.tns_descriptor, "")
        self.assertEqual(c.database, "")

    def test_switch_oracle_to_mysql_clears_oracle_fields(self):
        c = _fake_conn(db_type="oracle", connect_mode="sid", sid="ORCL", oracle_role="sysdba")
        _apply_type_fields(c, "mysql", {"host": "h", "username": "u", "database": "db"})
        self.assertEqual(c.connect_mode, "")
        self.assertEqual(c.sid, "")
        self.assertEqual(c.oracle_role, "")
        self.assertEqual(c.database, "db")


if __name__ == "__main__":
    unittest.main()
