# -*- coding: utf-8 -*-
"""直连库的平台侧执行（source=direct）——方言分派入口。

与 agent 库的区别：agent 库账密在集群里、经隧道执行；直连库账密在平台（加密），
由 api-server 直接连库执行。前端体验保持一致（同一套 SQL 控制台/对象树/导入导出）。

具体 SQL 语义在 `app.services.dialects.*` 内实现（mysql / oracle），
本模块只做 db_type → 方言的分派，不含任何 SQL 逻辑。
"""
from app.core.logging import get_logger
from app.services.dialects import get_dialect, split_sql_statements  # noqa: F401  # 兼容旧导入

logger = get_logger("api-server.db_direct")


def _dialect(c):
    return get_dialect((c.db_type or "mysql").lower())


async def query(c, sql: str, limit: int = 200, database: str | None = None) -> dict:
    return await _dialect(c).query(c, sql, limit=limit, database=database)


async def exec_sql(c, sql: str, database: str | None = None) -> dict:
    return await _dialect(c).exec_sql(c, sql, database=database)


async def schema(c) -> dict:
    return await _dialect(c).schema(c)


async def ddl(c, kind: str, db: str, name: str) -> dict:
    return await _dialect(c).ddl(c, kind, db, name)


async def export_sql(c, db: str, table: str = "", rows: int = 1000, mode: str = "both") -> dict:
    return await _dialect(c).export_sql(c, db, table, rows, mode)


async def import_content(c, db: str, table: str, fmt: str, content: str) -> dict:
    return await _dialect(c).import_content(c, db, table, fmt, content)


async def import_csv_chunk(c, db: str, table: str, columns: list[str], content: str,
                           has_header: bool = False) -> dict:
    return await _dialect(c).import_csv_chunk(c, db, table, columns, content, has_header)


async def import_sql(c, db: str, sql: str, mode: str = "stop") -> dict:
    return await _dialect(c).import_sql(c, db, sql, mode)


async def backup_sql(c, databases: list[str], schema_only: bool = False):
    d = _dialect(c)
    async for chunk in d.backup_sql(c, databases, schema_only):
        yield chunk


async def probe(c) -> tuple[bool, str]:
    return await _dialect(c).probe(c)


async def list_databases(c) -> set[str]:
    return await _dialect(c).list_databases(c)


async def create_database(c, name: str) -> None:
    await _dialect(c).create_database(c, name)


async def drop_database(c, name: str) -> None:
    await _dialect(c).drop_database(c, name)
