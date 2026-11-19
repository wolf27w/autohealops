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
import re
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib.parse import quote

from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm

from .base import (
    Dialect,
    UnsupportedError,
    check_ident,
    check_type_spec,
    default_value_sql,
    sql_literal,
)

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

    # ---- DDL 生成（表结构设计器，R48）----

    def build_table_alter(self, database: str, table: str, changes: dict) -> list[str]:
        return _build_table_alter_pg(database, table, changes)

    def build_database_alter(self, name: str, payload: dict) -> str:
        # PG 无「ALTER DATABASE 改字符集/排序规则」的等价轻量语句（需转储重建），明确不支持
        raise UnsupportedError("PostgreSQL 不支持编辑库字符集/排序规则")

    async def table_schema(self, c, db: str, table: str) -> dict:
        """表结构详情：字段/索引/表注释/行数估计，与 MySQL 同形（extra/key/charset/collation 恒空）。

        PG 中 `db` = schema；列注释/表注释从 pg_description（col_description/obj_description）取，
        索引从 pg_index + pg_attribute 取（按 indkey 顺序展开列，unique/method 同形映射）。
        """
        conn = await self.connect(c, readonly=True)
        try:
            col_rows = await conn.fetch(
                "SELECT column_name, data_type, udt_name, character_maximum_length, "
                "numeric_precision, numeric_scale, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position", db, table)
            comment_rows = await conn.fetch(
                "SELECT a.attname, col_description(c.oid, a.attnum) AS comment "
                "FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid "
                "  AND a.attnum > 0 AND NOT a.attisdropped "
                "WHERE n.nspname = $1 AND c.relname = $2", db, table)
            tinfo_row = await conn.fetchrow(
                "SELECT obj_description(c.oid, 'pg_class') AS comment, "
                "c.reltuples::bigint AS rows "
                "FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = $1 AND c.relname = $2 AND c.relkind = 'r'", db, table)
            idx_rows = await conn.fetch(
                "SELECT i.relname AS index_name, ix.indisunique AS is_unique, "
                "am.amname AS access_method, a.attname AS column_name "
                "FROM pg_catalog.pg_class t "
                "JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace "
                "JOIN pg_catalog.pg_index ix ON ix.indrelid = t.oid "
                "JOIN pg_catalog.pg_class i ON i.oid = ix.indexrelid "
                "JOIN pg_catalog.pg_am am ON am.oid = i.relam "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid "
                "  AND a.attnum = ANY(ix.indkey) "
                "WHERE n.nspname = $1 AND t.relname = $2 AND t.relkind = 'r' "
                "ORDER BY i.relname, array_position(ix.indkey::int2[], a.attnum)", db, table)
        finally:
            await conn.close()

        comments = {r["attname"]: (r["comment"] or "") for r in comment_rows}
        columns = [{
            "name": r["column_name"],
            "type": _fmt_pg_type(r),
            "nullable": (r["is_nullable"] or "YES") == "YES",
            "default": _norm_pg_default(r["column_default"]),
            "extra": "",
            "comment": comments.get(r["column_name"], ""),
            "key": "",
            "charset": "",
            "collation": "",
        } for r in col_rows]

        idx_map: dict[str, dict] = {}
        for r in idx_rows:
            name = r["index_name"]
            item = idx_map.setdefault(name, {
                "name": name,
                "unique": bool(r["is_unique"]),
                "type": (r["access_method"] or "btree").upper(),
                "columns": [],
            })
            item["columns"].append(r["column_name"])

        return {
            "database": db, "table": table,
            "engine": "", "charset": "", "collation": "",
            "comment": (tinfo_row["comment"] if tinfo_row else "") or "",
            "rows": int(tinfo_row["rows"] or 0) if tinfo_row else 0,
            "columns": columns, "indexes": list(idx_map.values()),
        }


def _fmt_pg_type(r) -> str:
    """information_schema.columns 一行 → 可读类型串（用于重建 CREATE TABLE / 回填设计器）。"""
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


def _norm_pg_default(d):
    """information_schema.columns.column_default → 与 MySQL COLUMN_DEFAULT 同形（去 ::类型 后缀/引号）。

    PG 默认值常带类型后缀（`'foo'::character varying`、`0::integer`、`nextval('seq'::regclass)`），
    这里去掉 `::类型` 后缀并把字符串引号剥掉，使设计器回填可读、且能经 default_value_sql 往返。
    """
    if d is None:
        return None
    d = str(d).strip()
    if d.upper() == "NULL":
        return None
    # 去类型后缀：::character varying / ::integer / ::regclass / ::double precision 等
    d = re.sub(r"::[A-Za-z0-9_\" ]+(\[\])?$", "", d)
    if len(d) >= 2 and d[0] == "'" and d[-1] == "'":
        return d[1:-1].replace("''", "'")
    return d


# ============ DDL 生成（表结构设计器，R48） ============

# MySQL 类型 → PG 类型（PG 无 tinyint/mediumint/longtext/datetime/double/blob，映射到最近等价）
_PG_TYPE_MAP = {
    "tinyint": "smallint", "mediumint": "integer",
    "tinytext": "text", "mediumtext": "text", "longtext": "text",
    "datetime": "timestamp",
    "double": "double precision",
    "tinyblob": "bytea", "mediumblob": "bytea", "longblob": "bytea", "blob": "bytea",
    "bool": "boolean",
}


def _pg_type(t: str) -> str:
    """类型映射：MySQL 类型 → PG 类型；已是 PG 类型（含空格/参数）原样返回。

    int/bigint/smallint/varchar/char/text/numeric/decimal/float/json/boolean 等 PG 直接支持，
    原样返回（类型串已过 check_type_spec 白名单，无注入面）。
    """
    t = check_type_spec(t)
    m = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*(?:\(.*\))?$", t)
    if not m:
        return t  # 多词类型（timestamp without time zone / double precision / character varying 等）
    base = m.group(1).lower()
    mapped = _PG_TYPE_MAP.get(base)
    return mapped if mapped else t


def _pg_default_sql(dv):
    """默认值 → DEFAULT 片段；PG 不接受 CURRENT_TIMESTAMP()（MySQL 写法），规整为 CURRENT_TIMESTAMP。"""
    if dv is None or str(dv) == "":
        return None
    s = str(dv)
    if s.upper() == "CURRENT_TIMESTAMP()":
        s = "CURRENT_TIMESTAMP"
    return default_value_sql(s)


def _pg_column_def(col: dict) -> str:
    """单列定义（ADD COLUMN 用）：`"name" type [NOT NULL] [DEFAULT ...]`"""
    name = check_ident(col.get("name") or "", "字段名")
    typ = _pg_type(col.get("type") or "")
    parts = [f'"{name}" {typ}']
    if not bool(col.get("nullable", True)):
        parts.append("NOT NULL")
    d = _pg_default_sql(col.get("default"))
    if d:
        parts.append(d)
    return " ".join(parts)


def _pg_modify_clauses(qual: str, name: str, col: dict) -> list[str]:
    """修改列 → 多条语句（PG 无 MODIFY COLUMN，TYPE/NOT NULL/DEFAULT/COMMENT 逐项拆开）。"""
    name = check_ident(name or "", "字段名")
    stmts: list[str] = []
    typ = (col.get("type") or "").strip()
    if typ:
        stmts.append(f'ALTER TABLE {qual} ALTER COLUMN "{name}" TYPE {_pg_type(typ)}')
    if col.get("nullable") is not None:
        stmts.append(
            f'ALTER TABLE {qual} ALTER COLUMN "{name}" '
            f'{"DROP" if col.get("nullable") else "SET"} NOT NULL')
    if col.get("default") is not None:
        d = _pg_default_sql(col.get("default"))
        if d:
            stmts.append(f'ALTER TABLE {qual} ALTER COLUMN "{name}" SET {d}')
        else:
            stmts.append(f'ALTER TABLE {qual} ALTER COLUMN "{name}" DROP DEFAULT')
    comment = (col.get("comment") or "").strip()
    if comment:
        stmts.append(f'COMMENT ON COLUMN {qual}."{name}" IS {sql_literal(comment)}')
    return stmts


def _build_table_alter_pg(database: str, table: str, ch: dict) -> list[str]:
    """结构化改动 → PG DDL 语句列表（一次一条，按顺序执行/预览）。

    - 类型映射见 `_pg_type`；改名 → `RENAME COLUMN`；注释 → `COMMENT ON COLUMN/TABLE`
    - 修改列拆成 TYPE / SET|DROP NOT NULL / SET|DROP DEFAULT 多条
    - 不支持：存储引擎 / 表级字符集 / 排序规则 / AFTER 列位置（明确报错，不静默）
    """
    check_ident(database, "库名")  # PG 中 database=schema
    check_ident(table, "表名")
    ch = ch or {}
    stmts: list[str] = []
    qual = f'"{database}"."{table}"'

    if (ch.get("engine") or "").strip():
        raise UnsupportedError("PostgreSQL 无存储引擎概念，不支持 ENGINE")
    if (ch.get("charset") or "").strip() or (ch.get("collation") or "").strip():
        raise UnsupportedError("PostgreSQL 无表级字符集/排序规则概念（可在建库/建列时指定）")

    # 1) 新增列（含列注释）
    for c in ch.get("add_columns") or []:
        if (c.get("after") or "").strip():
            raise UnsupportedError("PostgreSQL 不支持 AFTER 指定列位置（列顺序由新增顺序决定）")
        stmts.append(f"ALTER TABLE {qual} ADD COLUMN {_pg_column_def(c)}")
        comment = (c.get("comment") or "").strip()
        if comment:
            name = check_ident(c.get("name") or "", "字段名")
            stmts.append(f'COMMENT ON COLUMN {qual}."{name}" IS {sql_literal(comment)}')

    # 2) 修改列
    for c in ch.get("modify_columns") or []:
        stmts.extend(_pg_modify_clauses(qual, c.get("name") or "", c))

    # 3) 改列名（RENAME COLUMN）→ 再按新名走修改
    for c in ch.get("change_columns") or []:
        old = check_ident(c.get("from") or "", "字段名")
        new = check_ident(c.get("name") or "", "字段名")
        if old != new:
            stmts.append(f'ALTER TABLE {qual} RENAME COLUMN "{old}" TO "{new}"')
        stmts.extend(_pg_modify_clauses(qual, new, c))

    # 4) 删列
    for n in ch.get("drop_columns") or []:
        stmts.append(f'ALTER TABLE {qual} DROP COLUMN "{check_ident(n, "字段名")}"')

    # 5) 加索引
    for i in ch.get("add_indexes") or []:
        cols = [check_ident(x, "索引字段") for x in (i.get("columns") or [])]
        if not cols:
            raise ValueError("索引至少需要一个字段")
        uniq = "UNIQUE " if i.get("unique") else ""
        idx_name = (i.get("name") or "").strip()
        if not idx_name:
            idx_name = f"idx_{table}_{'_'.join(cols)}"
        check_ident(idx_name, "索引名")
        collist = ", ".join(f'"{x}"' for x in cols)
        stmts.append(f"CREATE {uniq}INDEX \"{idx_name}\" ON {qual} ({collist})")

    # 6) 删索引（PG 索引名在 schema 内唯一，DROP INDEX 带 schema 前缀避免歧义）
    for n in ch.get("drop_indexes") or []:
        stmts.append(f'DROP INDEX "{database}"."{check_ident(n, "索引名")}"')

    # 7) 表注释
    if ch.get("comment") is not None:
        stmts.append(f"COMMENT ON TABLE {qual} IS {sql_literal(ch.get('comment') or '')}")

    # 8) 重命名表
    new_name = (ch.get("rename_table") or "").strip()
    if new_name:
        stmts.append(f'ALTER TABLE {qual} RENAME TO "{check_ident(new_name, "新表名")}"')

    if not stmts:
        raise ValueError("没有任何改动")
    return stmts
