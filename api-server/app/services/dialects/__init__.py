# -*- coding: utf-8 -*-
"""直连库执行方言注册表（mysql / oracle）。

`db_direct_service` 只做分派，SQL 语义在各方言模块内实现。
新增一种数据库时：实现 `Dialect` 子类并在此注册即可。
"""
from .base import Dialect, UnsupportedError, split_sql_statements
from .mysql import MySQLDialect
from .oracle import OracleDialect

_REGISTRY: dict[str, type[Dialect]] = {
    "mysql": MySQLDialect,
    "oracle": OracleDialect,
}


def get_dialect(db_type: str) -> Dialect:
    """按 db_type 返回方言实例；未知类型抛 UnsupportedError（业务错误，非堆栈）。"""
    key = (db_type or "mysql").lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        raise UnsupportedError(
            f"直连执行暂不支持 {db_type or '未知'}（当前支持 mysql/oracle）"
        )
    return cls()


def supports(db_type: str, capability: str) -> bool:
    """能力检测：该 db_type 是否支持某能力（query/exec/schema/ddl/...）。"""
    key = (db_type or "mysql").lower()
    cls = _REGISTRY.get(key)
    if cls is None:
        return False
    return capability in cls.CAPABILITIES


__all__ = [
    "Dialect",
    "UnsupportedError",
    "split_sql_statements",
    "get_dialect",
    "supports",
    "MySQLDialect",
    "OracleDialect",
]
