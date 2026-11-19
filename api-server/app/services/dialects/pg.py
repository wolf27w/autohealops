# -*- coding: utf-8 -*-
"""PostgreSQL 直连执行方言（asyncpg，异步原生）。

能力覆盖（对齐 Oracle 的 R18 前例）：connect / query / exec_sql / schema / ddl /
list_databases / probe。导出 / 导入 / 备份 / 建库 / 删库 暂不支持（由 base.Dialect
的 require() 明确报 UnsupportedError，与 Oracle 口径一致）。

语义约定（与 Oracle 对等，注意区分「库」的两个层次）：
- 连接串里的 database = 实例上的真实数据库（`c.database`，创建连接时必填）；
- 方法参数 `database` 在 PG 语义下是 **schema**（`search_path`），来自对象树/下拉的
  schema 名——连接仍连到 `c.database`，只是把 search_path 指到该 schema；
- `schema()` 顶层 `databases` = 当前库可见的 schema 列表（浏览树按 schema 分组，
  tables/views/routines 的 `db` 字段 = schema 名）；
- `list_databases()` = 实例上的真实数据库名（pg_database），仅供建库/删库/备份预检用
  （这些能力 PG 本身不支持，预检后仍会按 require() 报 UnsupportedError）。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote

from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm

from .base import Dialect

try:
    import asyncpg
except ImportError:  # pragma: no cover - asyncpg 已在 requirements.txt
    asyncpg = None

logger = get_logger("api-server.db_direct.pg")

MAX_ROWS = 5000

# 浏览树里排除的系统 schema（pg_catalog / information_schema 恒排除，pg_toast/pg_temp 前缀排除）
PG_SYS_SCHEMAS = {"pg_catalog", "information_schema"}


def _dsn(info: dict) -> str:
    """拼 asyncpg 连接串（URL 形式）。

    user/password/database 做百分号编码（`quote(..., safe="")`），
    避免密码里的 `@`/`:`/`/`/`#` 等特殊字符把 URL 截断。
    """
    host = (info.get("host") or "").strip()
    port = int(info.get("port") or 5432)
    db = info.get("database") or ""
    user = info.get("user") or ""
    password = info.get("password") or ""
    auth = ""
    if user:
        auth = quote(user, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        auth += "@"
    path = "/" + quote(db, safe="") if db else ""
    return f"postgresql://{auth}{host}:{port}{path}"


def _info(c, readonly: bool = True) -> dict:
    """连接信息；readonly=True 优先用只读诊断账号（最小权限）"""
    return {
        "host": c.host,
        "port": int(c.port or 5432),
        "user": (c.readonly_username or c.username) if readonly else (c.username or c.readonly_username),
        "password": decrypt_aes256_gcm(
            (c.readonly_password_encrypted or c.password_encrypted)
            if readonly else (c.password_encrypted or c.readonly_password_encrypted)
        ) if (c.password_encrypted or c.readonly_password_encrypted) else "",
        "database": c.database or "",
    }


def _norm(v):
    """结果归一化：与 MySQL/Oracle 同形，并处理 PG 特有类型（bytes/datetime/Decimal/date/time/uuid/数组）。"""
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, Decimal):
        if v == v.to_integral_value():
            return int(v)
        return float(v)
    if isinstance(v, (date, time, timedelta, uuid.UUID)):
        return str(v)
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def _parse_affected(status: str) -> int:
    """asyncpg 命令状态串（如 `UPDATE 3` / `INSERT 0 1` / `DELETE 2`）→ 影响行数。"""
    if not status:
        return 0
    parts = str(status).split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


class PGDialect(Dialect):
    db_type = "pg"
    PROBE_SQL = "select 1"
    CAPABILITIES = frozenset({"query", "exec", "schema", "ddl", "list_databases"})

    async def connect(self, c, readonly: bool = True, database: str | None = None):
        if asyncpg is None:
            from .base import UnsupportedError
            raise UnsupportedError("asyncpg 未安装，无法直连 PostgreSQL")
        info = _info(c, readonly)
        conn = await asyncpg.connect(dsn=_dsn(info), timeout=8)
        if database:
            # PG 语义下 database 参数 = schema：把 search_path 指过去（参数化，无注入面）
            await conn.execute("SELECT set_config('search_path', $1, false)", database)
        return conn

    async def query(self, c, sql: str, limit: int = 200, database: str | None = None) -> dict:
        limit = max(1, min(int(limit or 200), MAX_ROWS))
        conn = await self.connect(c, readonly=True, database=database)
        try:
            # 有界读取：portal cursor 只在事务内有效，forward/fetch 只取 limit 行，
            # 大结果集不会整表拉进内存（对齐 MySQL/Oracle 的 fetchmany 口径）
            async with conn.transaction():
                cur = await conn.cursor(sql)
                await cur.forward(limit)
                rows = await cur.fetch(limit)
            columns = list(rows[0].keys()) if rows else []
            if not columns:
                # 空结果集：用 prepared statement 拿列名（SHOW/EXPLAIN 等不能 prepare 则列留空）
                try:
                    stmt = await conn.prepare(sql)
                    columns = [a.name for a in stmt.get_attributes()]
                except Exception:  # noqa: BLE001
                    columns = []
            data = [[_norm(r.get(col)) for col in columns] for r in rows]
            return {"columns": columns, "rows": data, "truncated": len(data) >= limit}
        finally:
            await conn.close()

    async def exec_sql(self, c, sql: str, database: str | None = None) -> dict:
        conn = await self.connect(c, readonly=False, database=database)
        try:
            status = await conn.execute(sql)
            affected = _parse_affected(status)
            return {"columns": [], "rows": [], "truncated": False,
                    "affected_rows": affected, "last_insert_id": 0, "write": True}
        finally:
            await conn.close()

    async def schema(self, c) -> dict:
        """结构树：顶层 databases = schema 列表；tables/views/routines 按 schema 分组；events 恒空。"""
        conn = await self.connect(c, readonly=True)
        try:
            out = {"databases": [], "tables": [], "views": [], "routines": [], "events": []}

            rows = await conn.fetch(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT IN ('pg_catalog','information_schema') "
                "AND schema_name NOT LIKE 'pg\\_%' ORDER BY schema_name")
            out["databases"] = [r[0] for r in rows]

            rows = await conn.fetch(
                "SELECT table_schema, table_name, table_type FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog','information_schema') "
                "AND table_schema NOT LIKE 'pg\\_%' "
                "ORDER BY table_schema, table_name LIMIT 3000")
            for db, name, typ in rows:
                if (typ or "").upper() == "VIEW":
                    out["views"].append({"db": db, "name": name})
                else:
                    out["tables"].append({"db": db, "name": name, "type": typ or "TABLE"})

            rows = await conn.fetch(
                "SELECT routine_schema, routine_name, routine_type FROM information_schema.routines "
                "WHERE routine_schema NOT IN ('pg_catalog','information_schema') "
                "AND routine_schema NOT LIKE 'pg\\_%' "
                "ORDER BY routine_schema, routine_type, routine_name LIMIT 500")
            for db, name, typ in rows:
                out["routines"].append({"db": db, "name": name, "type": typ or "FUNCTION"})

            return out
        finally:
            await conn.close()

    async def ddl(self, c, kind: str, db: str, name: str) -> dict:
        kind = (kind or "").lower()
        conn = await self.connect(c, readonly=True)
        try:
            if kind == "function":
                row = await conn.fetchrow(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = $1 AND p.proname = $2 AND p.prokind = 'f' "
                    "ORDER BY p.oid LIMIT 1", db, name)
                if not row or row[0] is None:
                    raise RuntimeError(f"无法获取函数 `{db}.{name}` 的定义（对象不存在或权限不足）")
                return {"kind": kind, "name": name, "ddl": str(row[0]).rstrip() + ";"}
            if kind == "procedure":
                row = await conn.fetchrow(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = $1 AND p.proname = $2 AND p.prokind = 'p' "
                    "ORDER BY p.oid LIMIT 1", db, name)
                if not row or row[0] is None:
                    raise RuntimeError(f"无法获取存储过程 `{db}.{name}` 的定义（对象不存在或权限不足）")
                return {"kind": kind, "name": name, "ddl": str(row[0]).rstrip() + ";"}
            if kind == "view":
                row = await conn.fetchrow(
                    "SELECT pg_get_viewdef(c.oid, true) FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind = 'v'", db, name)
                if not row or row[0] is None:
                    raise RuntimeError(f"无法获取视图 `{db}.{name}` 的定义（对象不存在或权限不足）")
                ddl = f'CREATE OR REPLACE VIEW "{db}"."{name}" AS\n{row[0]};'
                return {"kind": kind, "name": name, "ddl": ddl}
            if kind == "table":
                return await self._table_ddl(conn, db, name)
            raise RuntimeError(
                f"PG 不支持的 kind={kind}（支持 table/view/function/procedure）")
        finally:
            await conn.close()

    async def _table_ddl(self, conn, schema_name: str, table: str) -> dict:
        """表定义重建：PG 无 SHOW CREATE TABLE，用 information_schema.columns 拼 CREATE TABLE。"""
        rows = await conn.fetch(
            "SELECT column_name, data_type, udt_name, character_maximum_length, "
            "numeric_precision, numeric_scale, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position",
            schema_name, table)
        if not rows:
            raise RuntimeError(f"无法获取表 `{schema_name}.{table}` 的定义（对象不存在或权限不足）")
        cols = []
        for r in rows:
            name = r["column_name"]
            typ = _fmt_pg_type(r)
            parts = [f'"{name}" {typ}']
            if (r["is_nullable"] or "YES") != "YES":
                parts.append("NOT NULL")
            if r["column_default"]:
                parts.append("DEFAULT " + str(r["column_default"]))
            cols.append("  " + " ".join(parts))
        ddl = f'CREATE TABLE "{schema_name}"."{table}" (\n' + ",\n".join(cols) + "\n);"
        return {"kind": "table", "name": table, "ddl": ddl}

    async def list_databases(self, c) -> set[str]:
        """实例上的真实数据库名（pg_database，排除模板库），供建库/删库/备份预检。"""
        conn = await self.connect(c, readonly=True)
        try:
            rows = await conn.fetch(
                "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname")
            return {r[0] for r in rows}
        finally:
            await conn.close()

    # ---- DDL 生成（表结构设计器，R37）：PG 本批不实现（与 Oracle 的 R18 能力对齐）----

    def build_database_alter(self, name: str, payload: dict) -> str:
        # PG 无「ALTER DATABASE 改字符集/排序规则」的等价轻量语句（需转储重建），明确不支持
        from .base import UnsupportedError
        raise UnsupportedError("PostgreSQL 不支持编辑库字符集/排序规则")


def _fmt_pg_type(r) -> str:
    """information_schema.columns 一行 → 可读类型串（用于重建 CREATE TABLE）。"""
    dt = (r["data_type"] or "").lower()
    if dt == "character varying":
        n = r["character_maximum_length"]
        return f"varchar({n})" if n else "varchar"
    if dt == "character":
        n = r["character_maximum_length"]
        return f"char({n})" if n else "char"
    if dt == "numeric":
        p, s = r["numeric_precision"], r["numeric_scale"]
        if p is not None and s is not None:
            return f"numeric({p},{s})"
        return f"numeric({p})" if p is not None else "numeric"
    if dt == "user-defined":
        return f'"{r["udt_name"]}"'
    return r["data_type"] or r["udt_name"] or "text"
