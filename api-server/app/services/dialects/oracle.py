# -*- coding: utf-8 -*-
"""Oracle 直连执行方言（oracledb，async thin 模式）。

连接写法与 `app.services.db_collector.test_db_connection` 里的 Oracle 分支保持一致：
service_name / sid / tns 三选一 + SYSDBA/SYSOPER 的 AUTH_MODE 处理，勿另起炉灶。

能力覆盖（最小可用）：connect / query / exec_sql / schema / ddl / list_databases / probe。
导出 / 导入 / 备份 / 建库 / 删库 暂不支持（由 base.Dialect 的 require() 明确报错）。
"""
import asyncio
from datetime import datetime
from decimal import Decimal
import re

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
    import oracledb
    # LOB（CLOB/BLOB）直接取成 str/bytes；True 会返回 LOB 对象（还要 read），会导致 .strip() 报错
    oracledb.defaults.fetch_lobs = False
except ImportError:  # pragma: no cover - oracledb 已在 requirements.txt
    oracledb = None

logger = get_logger("api-server.db_direct.oracle")

def _dsn(info: dict) -> str:
    """按连接方式构造 DSN（thick/thin 通用的 Easy Connect / 描述符）。"""
    mode = (info.get("connect_mode") or "service_name").lower()
    if mode == "tns":
        return (info.get("tns_descriptor") or "").strip()
    host = (info.get("host") or "").strip()
    port = int(info.get("port") or 1521)
    if mode == "sid":
        sid = (info.get("sid") or "").strip()
        if oracledb is not None:
            return oracledb.makedsn(host, port, sid=sid)
        return f"{host}:{port}:{sid}"
    svc = (info.get("service_name") or info.get("database") or "").strip()
    return f"{host}:{port}/{svc}"


MAX_ROWS = 5000

# 数据字典里跳过明显的系统 schema（避免 all_tables 里 SYS/CTXSYS 等淹没业务表）
# APEX/ORDS 等内置组件 schema 前缀（业务上无关，列表里滤掉）
ORA_SYS_PREFIXES = ("APEX_", "FLOWS_", "ORDS_", "APEX_PUBLIC_")

ORA_SYS_SCHEMAS = {
    "SYS", "SYSTEM", "XDB", "MDSYS", "CTXSYS", "ORDSYS", "ORDDATA", "WMSYS",
    "OUTLN", "DBSNMP", "APPQOSSYS", "OLAPSYS", "LBACSYS", "DVSYS", "OJVMSYS",
    "AUDSYS", "SYSBACKUP", "SYSDG", "SYSKM", "SYSRAC", "DBSFWUSER", "GGSYS",
    "ANONYMOUS", "REMOTE_SCHEDULER_AGENT", "SYS$UMF", "XS$NULL", "GSMADMIN_INTERNAL",
}


def _info(c, readonly: bool = True) -> dict:
    """连接信息；readonly=True 优先用只读诊断账号（最小权限）"""
    return {
        "host": c.host,
        "port": int(c.port or 1521),
        "username": (c.readonly_username or c.username) if readonly else (c.username or c.readonly_username),
        "password": decrypt_aes256_gcm(
            (c.readonly_password_encrypted or c.password_encrypted)
            if readonly else (c.password_encrypted or c.readonly_password_encrypted)
        ) if (c.password_encrypted or c.readonly_password_encrypted) else "",
        "database": c.database or "",
        "connect_mode": getattr(c, "connect_mode", "") or "",
        "service_name": getattr(c, "service_name", "") or "",
        "sid": getattr(c, "sid", "") or "",
        "tns_descriptor": getattr(c, "tns_descriptor", "") or "",
        "oracle_role": getattr(c, "oracle_role", "") or "",
    }


def _norm(v):
    """结果归一化：与 MySQL 同形（bytes→str、datetime→str），并处理 Oracle 特有类型。"""
    if v is None:
        return None
    # fetch_lobs=True 时 CLOB/BLOB 已返回 str/bytes；此处兜底处理 LOB 句柄
    if oracledb is not None and isinstance(v, oracledb.LOB):
        try:
            v = v.read()
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    # NUMBER 由 oracledb 返回 Decimal；转 int/float 保证 JSON 可序列化（对齐 MySQL 口径）
    if isinstance(v, Decimal):
        if v == v.to_integral_value():
            return int(v)
        return float(v)
    return v


# ============ DDL 生成（表结构设计器，R37 §6.6；Oracle 方言版） ============

# MySQL 类型 → Oracle 类型（无参数映射，直接查表）
_MYSQL_TYPE_MAP = {
    "int": "NUMBER(10)", "integer": "NUMBER(10)", "mediumint": "NUMBER(7)",
    "bigint": "NUMBER(19)", "smallint": "NUMBER(5)", "tinyint": "NUMBER(3)",
    "boolean": "NUMBER(1)", "bool": "NUMBER(1)",
    "double": "FLOAT", "real": "FLOAT",
    "text": "CLOB", "tinytext": "CLOB", "mediumtext": "CLOB", "longtext": "CLOB",
    "blob": "BLOB", "tinyblob": "BLOB", "mediumblob": "BLOB", "longblob": "BLOB",
    "datetime": "TIMESTAMP", "json": "CLOB",
}


def _oracle_type(t: str) -> str:
    """类型映射：MySQL 类型 → Oracle 类型；已是 Oracle 类型则规整大写返回。

    int/bigint→NUMBER(10)/NUMBER(19)、tinyint→NUMBER(3)、varchar(n)→VARCHAR2(n)、
    text→CLOB、datetime→TIMESTAMP、decimal(p,s)→NUMBER(p,s)、char(n)→CHAR(n)、
    binary/varbinary/raw→RAW(n)、float/double→FLOAT。
    """
    t = (t or "").strip()
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*)\s*(?:\(\s*([0-9]+)\s*(?:,\s*([0-9]+)\s*)?\))?$", t)
    if not m:
        # 非标准形（如 TIMESTAMP(6) WITH TIME ZONE 等），已过白名单校验，原样返回
        return t
    base = m.group(1).lower()
    p = m.group(2)
    s = m.group(3)
    if base in ("varchar", "varchar2", "nvarchar", "nvarchar2"):
        return f"VARCHAR2({p or 255})"
    if base in ("char", "nchar"):
        return f"CHAR({p or 1})"
    if base in ("decimal", "numeric", "dec", "number"):
        if p and s:
            return f"NUMBER({p},{s})"
        return f"NUMBER({p})" if p else "NUMBER"
    if base in ("raw", "binary", "varbinary"):
        return f"RAW({p or 1})"
    if base in ("float", "double", "real"):
        return f"FLOAT({p})" if (base == "float" and p) else "FLOAT"
    if base in ("clob", "nclob", "blob", "date", "timestamp", "bfile"):
        return t.upper()
    mapped = _MYSQL_TYPE_MAP.get(base)
    return mapped if mapped else t.upper()


def _oracle_column_def(col: dict) -> str:
    """单列定义：`"name" type [GENERATED ... AS IDENTITY] [NOT NULL] [DEFAULT ...]`"""
    name = check_ident(col.get("name") or "", "字段名")
    typ = _oracle_type(check_type_spec(col.get("type") or ""))
    parts = [f'"{name}" {typ}']
    auto = bool(col.get("auto_increment"))
    if auto:
        parts.append("GENERATED BY DEFAULT AS IDENTITY")
    if not bool(col.get("nullable", True)):
        parts.append("NOT NULL")
    d = default_value_sql(col.get("default"))
    if d and not auto:
        parts.append(d)
    return " ".join(parts)


def _build_table_alter_oracle(database: str, table: str, ch: dict) -> list[str]:
    """结构化改动 → Oracle DDL 语句列表（一次只能执行一条，按顺序执行/预览）。

    - 类型映射见 `_oracle_type`；自增 → 12c+ `GENERATED BY DEFAULT AS IDENTITY`
    - 注释 → 独立 `COMMENT ON COLUMN / TABLE`；改列名 → `RENAME COLUMN`
    - 不支持：存储引擎 / 字符集 / 排序规则 / AFTER 列位置（明确报错，不静默）
    """
    check_ident(database, "库名")  # Oracle 中 database=owner/schema
    check_ident(table, "表名")
    ch = ch or {}
    stmts: list[str] = []
    qual = f'"{database}"."{table}"'

    if (ch.get("engine") or "").strip():
        raise UnsupportedError("Oracle 无存储引擎概念，不支持 ENGINE")
    if (ch.get("charset") or "").strip() or (ch.get("collation") or "").strip():
        raise UnsupportedError("Oracle 不支持修改表级字符集/排序规则")

    # 1) 新增列（一条 ADD 括号语句）
    add_cols = ch.get("add_columns") or []
    if add_cols:
        for c in add_cols:
            if (c.get("after") or "").strip():
                raise UnsupportedError("Oracle 不支持 AFTER 指定列位置（列顺序由新增顺序决定）")
        defs = ",\n  ".join(_oracle_column_def(c) for c in add_cols)
        stmts.append(f"ALTER TABLE {qual} ADD (\n  {defs}\n)")

    # 2) 修改列（MODIFY）
    mod_cols = ch.get("modify_columns") or []
    if mod_cols:
        defs = ",\n  ".join(_oracle_column_def(c) for c in mod_cols)
        stmts.append(f"ALTER TABLE {qual} MODIFY (\n  {defs}\n)")

    # 3) 改列名 / 改定义（CHANGE → RENAME COLUMN + MODIFY）
    for c in ch.get("change_columns") or []:
        old = check_ident(c.get("from") or "", "字段名")
        new = check_ident(c.get("name") or "", "字段名")
        if old != new:
            stmts.append(f'ALTER TABLE {qual} RENAME COLUMN "{old}" TO "{new}"')
        stmts.append(f"ALTER TABLE {qual} MODIFY (\n  {_oracle_column_def(c)}\n)")

    # 4) 删列
    drop_cols = ch.get("drop_columns") or []
    if drop_cols:
        defs = ",\n  ".join(f'"{check_ident(n, "字段名")}"' for n in drop_cols)
        stmts.append(f"ALTER TABLE {qual} DROP (\n  {defs}\n)")

    # 5) 加索引（CREATE INDEX；索引名留空时自动生成 idx_<表>_<列>）
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

    # 6) 删索引（DROP INDEX 只接索引名）
    for n in ch.get("drop_indexes") or []:
        stmts.append(f'DROP INDEX "{check_ident(n, "索引名")}"')

    # 7) 列注释（COMMENT ON COLUMN）
    for c in add_cols + mod_cols + (ch.get("change_columns") or []):
        comment = (c.get("comment") or "").strip()
        if comment:
            name = check_ident(c.get("name") or "", "字段名")
            stmts.append(f'COMMENT ON COLUMN {qual}."{name}" IS {sql_literal(comment)}')

    # 8) 表注释（COMMENT ON TABLE）
    if ch.get("comment") is not None:
        stmts.append(f"COMMENT ON TABLE {qual} IS {sql_literal(ch.get('comment') or '')}")

    # 9) 重命名表
    new_name = (ch.get("rename_table") or "").strip()
    if new_name:
        stmts.append(f'ALTER TABLE {qual} RENAME TO "{check_ident(new_name, "新表名")}"')

    if not stmts:
        raise ValueError("没有任何改动")
    return stmts


def _fmt_oracle_type(dt, length, precision, scale, char_length) -> str:
    """all_tab_columns 的元数据 → 可回填设计器的类型串（能经 `_oracle_type` 往返）。"""
    dt = (dt or "").upper()
    if dt == "NUMBER":
        if precision is not None:
            return f"NUMBER({precision},{scale})" if scale not in (None, 0) else f"NUMBER({precision})"
        return "NUMBER"
    if dt == "FLOAT":
        return f"FLOAT({precision})" if precision else "FLOAT"
    if dt in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "RAW"):
        ln = int(char_length or length or 0)
        return f"{dt}({ln})" if ln else dt
    if dt.startswith("TIMESTAMP"):
        return f"TIMESTAMP({scale})" if scale else "TIMESTAMP"
    return dt


def _norm_oracle_default(d):
    """all_tab_columns.DATA_DEFAULT → 与 MySQL COLUMN_DEFAULT 同形（不含引号）。"""
    if d is None:
        return None
    d = str(d).strip()
    if d.upper() == "NULL":
        return None
    up = d.upper()
    if up in ("SYSTIMESTAMP", "SYSDATE", "CURRENT_TIMESTAMP", "CURRENT_DATE"):
        return "CURRENT_TIMESTAMP"
    if len(d) >= 2 and d[0] == "'" and d[-1] == "'":
        return d[1:-1].replace("''", "'")
    return d


class OracleDialect(Dialect):
    db_type = "oracle"
    PROBE_SQL = "select 1 from dual"
    CAPABILITIES = frozenset({"query", "exec", "schema", "ddl", "list_databases"})

    def _connect_sync(self, c, readonly: bool = True, database: str | None = None):
        if oracledb is None:
            from .base import UnsupportedError
            raise UnsupportedError("oracledb 未安装，无法直连 Oracle")
        info = _info(c, readonly)
        role = (info["oracle_role"] or "").lower()
        auth_mode = oracledb.AUTH_MODE_DEFAULT
        if role == "sysdba":
            auth_mode = oracledb.AUTH_MODE_SYSDBA
        elif role == "sysoper":
            auth_mode = oracledb.AUTH_MODE_SYSOPER
        conn = oracledb.connect(
            user=info["username"], password=info["password"],
            dsn=_dsn(info), mode=auth_mode,
        )
        conn.autocommit = True  # 对齐 MySQL 的 autocommit 语义
        return conn

    def _query_sync(self, c, sql: str, limit: int = 200, database: str | None = None) -> dict:
        limit = max(1, min(int(limit or 200), MAX_ROWS))
        conn = self._connect_sync(c, readonly=True)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchmany(limit)
            columns = [d[0] for d in (cur.description or [])]
            data = [[_norm(r[i]) for i in range(len(columns))] for r in rows]
            return {"columns": columns, "rows": data, "truncated": len(data) >= limit}
        finally:
            conn.close()

    def _exec_sync(self, c, sql: str, database: str | None = None) -> dict:
        conn = self._connect_sync(c, readonly=False)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            affected = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            last_id = cur.lastrowid or 0
            return {"columns": [], "rows": [], "truncated": False,
                    "affected_rows": affected, "last_insert_id": last_id, "write": True}
        finally:
            conn.close()

    def _schema_sync(self, c) -> dict:
        """结构树：与 MySQL 同形。databases = 当前可见 owner/schema 列表；
        tables/views/routines 来自 all_tables / all_views / all_objects；events 恒为空。"""
        conn = self._connect_sync(c, readonly=True)
        try:
            out = {"databases": [], "tables": [], "views": [], "routines": [], "events": []}
            cur = conn.cursor()

            cur.execute("SELECT username FROM all_users ORDER BY username")
            out["databases"] = [r[0] for r in cur.fetchall()]

            # Oracle 11g 不支持 FETCH FIRST（12c+），统一用 ROWNUM 包一层
            cur.execute(
                "SELECT * FROM (SELECT owner, table_name FROM all_tables "
                "ORDER BY owner, table_name) WHERE ROWNUM <= 3000")
            for owner, name in cur.fetchall():
                if owner in ORA_SYS_SCHEMAS or str(owner).upper().startswith(ORA_SYS_PREFIXES):
                    continue
                out["tables"].append({"db": owner, "name": name, "type": "TABLE"})

            cur.execute(
                "SELECT * FROM (SELECT owner, view_name FROM all_views "
                "ORDER BY owner, view_name) WHERE ROWNUM <= 3000")
            for owner, name in cur.fetchall():
                if owner in ORA_SYS_SCHEMAS or str(owner).upper().startswith(ORA_SYS_PREFIXES):
                    continue
                out["views"].append({"db": owner, "name": name})

            cur.execute(
                "SELECT * FROM (SELECT owner, object_name, object_type FROM all_objects "
                "WHERE object_type IN ('PROCEDURE','FUNCTION','PACKAGE') "
                "ORDER BY owner, object_type, object_name) WHERE ROWNUM <= 500")
            for owner, name, typ in cur.fetchall():
                if owner in ORA_SYS_SCHEMAS or str(owner).upper().startswith(ORA_SYS_PREFIXES):
                    continue
                out["routines"].append({"db": owner, "name": name, "type": typ})

            # Oracle 无 MySQL 的 event（调度作业），恒为空
            return out
        finally:
            conn.close()

    def _ddl_sync(self, c, kind: str, db: str, name: str) -> dict:
        kind = (kind or "").lower()
        obj_type = {
            "table": "TABLE", "view": "VIEW", "function": "FUNCTION",
            "procedure": "PROCEDURE", "package": "PACKAGE",
        }.get(kind)
        if not obj_type:
            raise RuntimeError(
                f"Oracle 不支持的 kind={kind}（支持 table/view/function/procedure/package）")
        conn = self._connect_sync(c, readonly=True)
        try:
            cur = conn.cursor()
            owner = db or None
            cur.execute(
                "SELECT DBMS_METADATA.GET_DDL(:1, :2, :3) FROM DUAL",
                [obj_type, name, owner],
            )
            row = cur.fetchone()
            val = row[0] if row and row[0] is not None else ""
            if hasattr(val, "read"):  # 老版本驱动可能返回 LOB 对象
                val = val.read()
            text = str(val).strip()
            if not text:
                raise RuntimeError(f"无法获取 {kind} `{name}` 的 DDL（对象不存在或权限不足）")
            return {"kind": kind, "name": name, "ddl": text.rstrip() + ";"}
        finally:
            conn.close()

    def _list_dbs_sync(self, c) -> set[str]:
        """Oracle 无「库」概念：返回当前账号可见的 owner/schema 集合。"""
        conn = self._connect_sync(c, readonly=True)
        try:
            cur = conn.cursor()
            cur.execute("SELECT username FROM all_users ORDER BY username")
            return {r[0] for r in cur.fetchall()}
        finally:
            conn.close()


    # ---- 异步包装：oracledb 是同步驱动（thick 模式不支持 connect_async），统一丢线程池 ----
    async def query(self, c, sql: str, limit: int = 200, database: str | None = None) -> dict:
        return await asyncio.to_thread(self._query_sync, c, sql, limit, database)

    async def exec_sql(self, c, sql: str, database: str | None = None) -> dict:
        return await asyncio.to_thread(self._exec_sync, c, sql, database)

    async def schema(self, c) -> dict:
        return await asyncio.to_thread(self._schema_sync, c)

    async def ddl(self, c, kind: str, db: str, name: str) -> dict:
        return await asyncio.to_thread(self._ddl_sync, c, kind, db, name)

    async def list_databases(self, c) -> set[str]:
        return await asyncio.to_thread(self._list_dbs_sync, c)

    async def table_schema(self, c, db: str, table: str) -> dict:
        return await asyncio.to_thread(self._table_schema_sync, c, db, table)

    # ---- DDL 生成（纯函数，不连库）----

    def build_table_alter(self, database: str, table: str, changes: dict) -> list[str]:
        return _build_table_alter_oracle(database, table, changes)

    def build_database_alter(self, name: str, payload: dict) -> str:
        raise UnsupportedError("Oracle 无「库字符集/排序规则」概念，不支持编辑库")

    def _table_schema_sync(self, c, db: str, table: str) -> dict:
        """表结构详情：字段/索引/注释/行数，与 MySQL 同形（extra/key/charset/collation 恒为空）。"""
        conn = self._connect_sync(c, readonly=True)
        try:
            cur = conn.cursor()

            cur.execute(
                "SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, DATA_PRECISION, DATA_SCALE, "
                "NULLABLE, DATA_DEFAULT, CHAR_LENGTH FROM all_tab_columns "
                "WHERE owner=:1 AND table_name=:2 ORDER BY COLUMN_ID", [db, table])
            col_rows = cur.fetchall()

            cur.execute(
                "SELECT COLUMN_NAME, COMMENTS FROM all_col_comments "
                "WHERE owner=:1 AND table_name=:2 AND comments IS NOT NULL", [db, table])
            comments = {r[0]: r[1] for r in cur.fetchall()}

            cur.execute(
                "SELECT COMMENTS FROM all_tab_comments WHERE owner=:1 AND table_name=:2 "
                "AND table_type='TABLE'", [db, table])
            tcomment_row = cur.fetchone()
            tcomment = (tcomment_row[0] if tcomment_row and tcomment_row[0] else "")

            cur.execute(
                "SELECT NUM_ROWS FROM all_tables WHERE owner=:1 AND table_name=:2", [db, table])
            nrows_row = cur.fetchone()
            nrows = int(nrows_row[0] or 0) if nrows_row and nrows_row[0] else 0

            cur.execute(
                "SELECT INDEX_NAME, UNIQUENESS, INDEX_TYPE FROM all_indexes "
                "WHERE owner=:1 AND table_name=:2 ORDER BY INDEX_NAME", [db, table])
            idx_rows = cur.fetchall()

            cur.execute(
                "SELECT INDEX_NAME, COLUMN_NAME FROM all_ind_columns "
                "WHERE index_owner=:1 AND table_name=:2 ORDER BY INDEX_NAME, COLUMN_POSITION",
                [db, table])
            idx_cols = cur.fetchall()

        finally:
            conn.close()

        columns = []
        for name, dtype, length, precision, scale, nullable, default, char_len in col_rows:
            columns.append({
                "name": name,
                "type": _fmt_oracle_type(dtype, length, precision, scale, char_len),
                "nullable": (nullable or "Y") == "Y",
                "default": _norm_oracle_default(default),
                "extra": "",
                "comment": comments.get(name, ""),
                "key": "",
                "charset": "",
                "collation": "",
            })

        idx_map: dict[str, dict] = {}
        for name, uniqueness, idx_type in idx_rows:
            idx_map[name] = {
                "name": name,
                "unique": uniqueness == "UNIQUE",
                "type": "BTREE" if (idx_type or "NORMAL").upper() == "NORMAL" else (idx_type or ""),
                "columns": [],
            }
        for name, col in idx_cols:
            if name in idx_map:
                idx_map[name]["columns"].append(col)

        return {
            "database": db, "table": table,
            "engine": "", "charset": "", "collation": "",
            "comment": tcomment, "rows": nrows,
            "columns": columns, "indexes": list(idx_map.values()),
        }
