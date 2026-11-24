# -*- coding: utf-8 -*-
"""Oracle 直连执行方言（oracledb，async thin 模式）。

连接写法与 `app.services.db_collector.test_db_connection` 里的 Oracle 分支保持一致：
service_name / sid / tns 三选一 + SYSDBA/SYSOPER 的 AUTH_MODE 处理，勿另起炉灶。

能力覆盖：connect / query / exec_sql / schema / ddl / list_databases / probe，以及逻辑导出
（export）/ 导入（import）/ 脚本导入（import_sql）/ 逻辑备份（backup，method=logical）。
建库 / 删库仍不支持（Oracle 无「库」概念，由 base.Dialect 的 require() 明确报错）。
"""
import asyncio
import csv
from datetime import datetime
from decimal import Decimal
import io
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

# 导出/导入/备份的边界（与 mysql 侧对齐）
_MAX_STMT_LEN = 10 * 1024 * 1024     # 单条语句超过 10MB 跳过
_BATCH_SIZE = 200                    # import_sql 每批最多处理的语句数
_CSV_BATCH = 1000                    # CSV/批量 INSERT 每批行数
_PAGE_SIZE = 1000                    # 导出/备份分页每页行数
_MAX_EXPORT_BYTES = 5 * 1024 * 1024  # 导出 SQL 总大小上限（与 mysql 一致）

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


# ============ 导出 / 导入 / 备份（逻辑备份）辅助 ============

def _q(v) -> str:
    """Oracle SQL 字面量：NULL / 数字 / TIMESTAMP / HEXTORAW / 字符串（单引号双写转义）。

    Oracle 字符串转义与 MySQL 不同：单引号用 `''` 双写，反斜杠是普通字符（不转义）。
    """
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime):
        # DATE/TIMESTAMP 统一用 TIMESTAMP 字面量（DATE 列可隐式转换）
        return "TIMESTAMP '" + v.strftime("%Y-%m-%d %H:%M:%S.%f") + "'"
    if isinstance(v, (bytes, bytearray)):
        return "HEXTORAW('" + bytes(v).hex().upper() + "')"
    s = str(v)
    return "'" + s.replace("'", "''") + "'"


def _paginated_select(owner: str, table: str, offset: int, limit: int,
                      columns: list[str] | None = None) -> str:
    """生成 Oracle 分页查询（ROWNUM 包裹，兼容 11g+）。

    - offset 从 0 起，返回第 [offset, offset+limit) 行；limit 上限控制单页行数。
    - columns 提供时按列清单投影（外层不泄露 ROWNUM 别名列）；未提供时退化为 `*`。
    - 12c+ 可用 `OFFSET n ROWS FETCH NEXT m ROWS ONLY`，此处统一 ROWNUM 包裹以兼容旧版本。
    """
    check_ident(owner, "库名")
    check_ident(table, "表名")
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    sel = ", ".join(f'"{x}"' for x in columns) if columns else "*"
    base = f'SELECT {sel} FROM "{owner}"."{table}"'
    if offset <= 0:
        return f"SELECT {sel} FROM ({base}) WHERE ROWNUM <= {limit}"
    end = offset + limit
    if columns:
        out = ", ".join(f'a."{x}"' for x in columns)
        return (f"SELECT {out} FROM ("
                f"SELECT {sel}, ROWNUM AS rn__ FROM ({base}) WHERE ROWNUM <= {end}"
                f") a WHERE rn__ > {offset}")
    return (f"SELECT * FROM (SELECT a.*, ROWNUM AS rn__ FROM ({base}) a "
            f"WHERE ROWNUM <= {end}) WHERE rn__ > {offset}")


def _split_oracle_sql(text: str) -> list[str]:
    """Oracle 脚本语句切分（在 base.split_sql_statements 基础上补 DECLARE 声明段）。

    规则：
    - 单引号字符串 / 行注释(`-- `、`#`) / 块注释(`/* */`) 内的分号不切分。
    - `BEGIN ... END;` / `DECLARE ... BEGIN ... END;` PL/SQL 块作为单条语句：
      块内（含 DECLARE 声明段，如 `v_x NUMBER;`）的分号不切分；独立一行的 `/`
      （SQL*Plus 执行符）作为块终止符被丢弃。
    - 其余按 `;` 切分。

    局限（刻意不完整解析）：不处理 `CREATE OR REPLACE PROCEDURE/FUNCTION/PACKAGE/TRIGGER`
    这类带 `IS/AS ... BEGIN ... END` 的存储程序体，也不处理 MySQL 的 DELIMITER。
    """
    stmts: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    depth = 0          # PL/SQL 块嵌套深度（DECLARE/BEGIN 各 +1，END -1；>0 时 `;` 不切分）
    declared = False   # 刚见过 DECLARE、尚未见到其 BEGIN（其 BEGIN 不再 +1）
    saw_block = False
    line_start = True

    def _flush() -> None:
        nonlocal cur, depth, declared, saw_block, line_start
        s = "".join(cur).strip()
        if s:
            stmts.append(s)
        cur = []
        depth = 0
        declared = False
        saw_block = False
        line_start = True

    def _word_at(idx: int) -> str | None:
        j = idx
        while j < n and (text[j].isalnum() or text[j] in "_$#"):
            j += 1
        return text[idx:j] if j > idx else None

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "#":
            line_start = False
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue
        if ch == "-" and nxt == "-" and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            line_start = False
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue
        if ch == "/" and nxt == "*":
            line_start = False
            cur.append(ch)
            cur.append(nxt)
            i += 2
            while i < n:
                cur.append(text[i])
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    cur.append(text[i + 1])
                    i += 2
                    break
                i += 1
            continue
        if ch in ("'", '"', "`"):
            line_start = False
            quote = ch
            cur.append(ch)
            i += 1
            while i < n:
                cur.append(text[i])
                if text[i] == "\\" and quote != "`" and i + 1 < n:
                    cur.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == quote:
                    if i + 1 < n and text[i + 1] == quote:
                        cur.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and line_start and nxt in ("\r", "\n", ""):
            if saw_block and not "".join(cur).rstrip().endswith(";"):
                cur.append(";")
            _flush()
            i += 1
            continue
        if ch.isalpha():
            w = _word_at(i)
            uw = (w or "").upper()
            if uw in ("BEGIN", "END", "DECLARE", "IF", "LOOP", "CASE"):
                j = i + len(w)
                while j < n and text[j] in " \t\r\n":
                    j += 1
                w2 = _word_at(j)
                uw2 = (w2 or "").upper()
                if uw == "DECLARE":
                    depth += 1
                    declared = True
                    saw_block = True
                elif uw == "BEGIN":
                    if declared:
                        declared = False
                    else:
                        depth += 1
                    saw_block = True
                elif uw == "END":
                    if uw2 in ("IF", "LOOP", "CASE"):
                        pass  # END IF / END LOOP / END CASE：不改变块深度
                    elif depth > 0:
                        depth -= 1
                for _ in range(len(w)):
                    cur.append(text[i])
                    i += 1
                line_start = False
                continue
            cur.append(ch)
            i += 1
            line_start = False
            continue
        if ch == ";":
            line_start = False
            if depth > 0:
                cur.append(ch)
                i += 1
                continue
            if saw_block:
                cur.append(ch)
            _flush()
            i += 1
            continue
        if ch in "\r\n":
            line_start = True
            cur.append(ch)
            i += 1
            continue
        if ch in " \t":
            cur.append(ch)
            i += 1
            continue
        line_start = False
        cur.append(ch)
        i += 1
    _flush()
    return stmts


def _sql_blank(s: str) -> bool:
    """去掉注释后是否无实际内容（纯注释语句跳过用）。"""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "#":
            while i < n and s[i] not in "\r\n":
                i += 1
            continue
        if ch == "-" and i + 1 < n and s[i + 1] == "-" and (i + 2 >= n or s[i + 2] in " \t\r\n"):
            while i < n and s[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and s[i + 1] == "*":
            i += 2
            while i < n and not (s[i] == "*" and i + 1 < n and s[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out).strip() == ""


def _executemany_batched(conn, stmt: str, args: list[list], batch_size: int = _CSV_BATCH) -> int:
    """分批 executemany（单事务）：任一批失败即整体回滚并报错行号区间（1-based 数据行）。"""
    cur = conn.cursor()
    executed = 0
    start = 0
    total = len(args)
    try:
        while start < total:
            batch = args[start:start + batch_size]
            cur.executemany(stmt, batch)
            executed += len(batch)
            start += batch_size
        conn.commit()
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        first = start + 1
        last = min(start + batch_size, total)
        raise RuntimeError(f"第 {first}~{last} 行批量插入失败：{e}") from e
    return executed


def _list_tables_sync(conn, owner: str) -> list[str]:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM (SELECT table_name FROM all_tables "
            "WHERE owner = :1 ORDER BY table_name) WHERE ROWNUM <= 3000", [owner])
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close()


def _table_columns_sync(conn, owner: str, table: str) -> list[str]:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT column_name FROM all_tab_columns "
            "WHERE owner = :1 AND table_name = :2 ORDER BY column_id", [owner, table])
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close()


def _get_table_ddl_sync(conn, owner: str, table: str) -> str:
    """DBMS_METADATA.GET_DDL 取建表语句；无权限/对象不存在时返回空串（调用方标注跳过）。"""
    cur = conn.cursor()
    try:
        cur.execute("SELECT DBMS_METADATA.GET_DDL('TABLE', :1, :2) FROM DUAL", [table, owner])
        row = cur.fetchone()
        val = row[0] if row and row[0] is not None else ""
        if hasattr(val, "read"):  # 老版本驱动可能返回 LOB 对象
            val = val.read()
        text = str(val).strip()
        return (text + ("" if text.endswith(";") else ";")) if text else ""
    finally:
        cur.close()


def _table_backup_sync(conn, owner: str, table: str, schema_only: bool) -> str:
    """单表逻辑备份文本：结构 DDL（DBMS_METADATA，尽力）+ 数据 INSERT（分页 SELECT 拼接）。"""
    out = io.StringIO()
    out.write(f"-- ---------- table: {table} ----------\n")
    ddl = _get_table_ddl_sync(conn, owner, table)
    if ddl:
        out.write(ddl + "\n")
    else:
        out.write("-- (结构 DDL 不可用：DBMS_METADATA 无权限或对象不存在)\n")
    if schema_only:
        out.write("\n")
        return out.getvalue()
    cols = _table_columns_sync(conn, owner, table)
    if not cols:
        out.write("-- (无列信息，跳过数据)\n\n")
        return out.getvalue()
    col_list = ", ".join(f'"{x}"' for x in cols)
    prefix = f'INSERT INTO "{owner}"."{table}" ({col_list}) VALUES '
    offset = 0
    while True:
        sql = _paginated_select(owner, table, offset, _PAGE_SIZE, cols)
        cur = conn.cursor()
        try:
            cur.execute(sql)
            batch = cur.fetchall()
        finally:
            cur.close()
        if not batch:
            break
        for r in batch:
            out.write(prefix + "(" + ", ".join(_q(v) for v in r) + ");\n")
        if len(batch) < _PAGE_SIZE:
            break
        offset += len(batch)
    out.write("\n")
    return out.getvalue()


class OracleDialect(Dialect):
    db_type = "oracle"
    PROBE_SQL = "select 1 from dual"
    CAPABILITIES = frozenset({"query", "exec", "schema", "ddl", "list_databases",
                              "export", "import", "backup"})

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

    # ---- 导出 / 导入 / 备份（同步实现，异步包装丢线程池）----

    def _export_sync(self, c, db: str, table: str, rows: int, mode: str) -> dict:
        """逻辑导出：分页 SELECT 拼 INSERT（Oracle 无 mysqldump 对等物）。

        - mode：structure（DBMS_METADATA DDL）/ data（INSERT）/ both。
        - db=owner/schema；分页用 ROWNUM 包裹（兼容 11g+）；单表 ≤rows 行、总 ≤5MB。
        """
        mode = (mode or "both").lower()
        if mode not in ("structure", "data", "both"):
            raise RuntimeError(f"mode 仅支持 structure/data/both（当前 {mode}）")
        rows = max(1, min(int(rows or 1000), MAX_ROWS))
        db = (db or c.database or "").strip()
        if not db:
            raise RuntimeError("db 不能为空（Oracle 中 db=owner/schema）")
        check_ident(db, "库名")
        conn = self._connect_sync(c, readonly=True)
        try:
            tables = [check_ident(table, "表名")] if table else _list_tables_sync(conn, db)
            buf = io.StringIO()
            buf.write("-- AutoHealOps export (Oracle logical)\n")
            buf.write(f"-- schema: {db}  time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            total, truncated = 0, False
            for t in tables:
                buf.write(f"-- ---------- table {t} ----------\n")
                if mode != "data":
                    ddl = _get_table_ddl_sync(conn, db, t)
                    buf.write(ddl if ddl else "-- (结构 DDL 不可用：DBMS_METADATA 无权限或对象不存在)\n")
                    buf.write("\n")
                if mode == "structure":
                    continue
                cols = _table_columns_sync(conn, db, t)
                if not cols:
                    buf.write("-- (无列信息，跳过数据)\n\n")
                    continue
                col_list = ", ".join(f'"{x}"' for x in cols)
                prefix = f'INSERT INTO "{db}"."{t}" ({col_list}) VALUES '
                offset = 0
                while offset < rows and not truncated:
                    limit = min(_PAGE_SIZE, rows - offset)
                    sql = _paginated_select(db, t, offset, limit, cols)
                    cur = conn.cursor()
                    try:
                        cur.execute(sql)
                        batch = cur.fetchall()
                    finally:
                        cur.close()
                    if not batch:
                        break
                    for r in batch:
                        buf.write(prefix + "(" + ", ".join(_q(v) for v in r) + ");\n")
                        total += 1
                    if len(batch) < limit:
                        break
                    offset += len(batch)
                    if buf.tell() > _MAX_EXPORT_BYTES:
                        truncated = True
                buf.write("\n")
                if truncated or buf.tell() > _MAX_EXPORT_BYTES:
                    truncated = True
                    break
            return {"db": db, "table": table, "tables": len(tables), "rows": total,
                    "bytes": buf.tell(), "truncated": truncated, "sql": buf.getvalue()}
        finally:
            conn.close()

    def _import_content_sync(self, c, db: str, table: str, fmt: str, content: str) -> dict:
        """导入（≤5MB）：CSV 走批量 INSERT（executemany 1000 行/批，失败回滚报错行）；SQL 走块切分逐条执行。

        Oracle 无 USE 语义：CSV 必须指定 schema（db），按 `schema.table` 拼接；SQL 脚本
        语句需自带 schema 前缀或依赖登录默认 schema（本实现不切换 CURRENT_SCHEMA）。
        """
        if len(content.encode("utf-8")) > _MAX_EXPORT_BYTES:
            raise RuntimeError("文件超过 5MB，请拆分后导入")
        db = (db or c.database or "").strip()
        conn = self._connect_sync(c, readonly=False)
        conn.autocommit = False
        try:
            executed = skipped = 0
            if (fmt or "sql").lower() == "csv":
                if not table:
                    raise RuntimeError("CSV 导入必须指定表名")
                check_ident(db, "库名")
                check_ident(table, "表名")
                rd = csv.reader(io.StringIO(content.lstrip("\ufeff")))
                records = list(rd)
                if len(records) < 2:
                    raise RuntimeError("CSV 至少需要表头 + 1 行数据")
                header = [h.strip() for h in records[0]]
                for h in header:
                    check_ident(h, "字段名")
                cols = ", ".join(f'"{h}"' for h in header)
                ph = ", ".join(f":{i + 1}" for i in range(len(header)))
                stmt = f'INSERT INTO "{db}"."{table}" ({cols}) VALUES ({ph})'
                args: list[list] = []
                for rec in records[1:]:
                    if not rec or all((v or "").strip() == "" for v in rec):
                        skipped += 1
                        continue
                    args.append([rec[i] if i < len(rec) else "" for i in range(len(header))])
                executed = _executemany_batched(conn, stmt, args, _CSV_BATCH)
            else:
                cur = conn.cursor()
                try:
                    for raw in _split_oracle_sql(content):
                        s = raw.strip()
                        if not s or _sql_blank(s):
                            skipped += 1
                            continue
                        cur.execute(s)
                        executed += 1
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    cur.close()
            return {"format": fmt, "executed": executed, "skipped": skipped,
                    "bytes": len(content.encode("utf-8"))}
        finally:
            conn.close()

    def _import_sql_sync(self, c, db: str, sql: str, mode: str) -> dict:
        """导入 SQL 脚本（不限大小）：Oracle 一次只执行一条语句，按块切分逐条执行。

        - 用 `_split_oracle_sql` 切分（BEGIN...END / DECLARE...BEGIN...END 整体为一条）。
        - mode=stop 遇错即停；skip 跳过错误继续；空/纯注释/超长语句跳过。
        - 返回 {total, executed, skipped, failed, errors, stopped}。
        """
        mode = (mode or "stop").lower()
        conn = self._connect_sync(c, readonly=False)
        try:
            stmts = _split_oracle_sql(sql or "")
            executed = skipped = 0
            errors: list[dict] = []
            stopped = False
            total = len(stmts)
            cur = conn.cursor()
            try:
                for start in range(0, total, _BATCH_SIZE):
                    batch = stmts[start:start + _BATCH_SIZE]
                    for j, raw in enumerate(batch):
                        idx = start + j
                        s = raw.strip()
                        if not s or _sql_blank(s):
                            skipped += 1
                            continue
                        if len(s) > _MAX_STMT_LEN:
                            skipped += 1
                            errors.append({"index": idx, "statement_head": s[:120],
                                           "error": "语句过长（>10MB），已跳过"})
                            continue
                        try:
                            cur.execute(s)
                            executed += 1
                        except Exception as e:  # noqa: BLE001
                            errors.append({"index": idx, "statement_head": s[:120],
                                           "error": str(e)[:300]})
                            if mode == "stop":
                                stopped = True
                                break
                    if stopped:
                        break
            finally:
                cur.close()
            return {"total": total, "executed": executed, "skipped": skipped,
                    "failed": len(errors), "errors": errors, "stopped": stopped}
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

    async def export_sql(self, c, db: str = "", table: str = "", rows: int = 1000,
                         mode: str = "both") -> dict:
        return await asyncio.to_thread(self._export_sync, c, db, table, rows, mode)

    async def import_content(self, c, db: str, table: str, fmt: str, content: str) -> dict:
        return await asyncio.to_thread(self._import_content_sync, c, db, table, fmt, content)

    async def import_sql(self, c, db: str, sql: str, mode: str = "stop") -> dict:
        return await asyncio.to_thread(self._import_sql_sync, c, db, sql, mode)

    async def backup_sql(self, c, databases: list[str], schema_only: bool = False):
        """Oracle 逻辑备份（异步生成器，逐表 yield bytes，不落盘）。

        Oracle 无 mysqldump 对等物（expdp 需数据库主机权限），故实现为逻辑备份：
        按 schema 生成结构 DDL（DBMS_METADATA，尽力）+ 数据 INSERT（分页 SELECT）。
        首块标注 `method=logical`；连接/查询失败会抛异常中断流，不假装成功。
        同步驱动阻塞经 asyncio.to_thread 隔离（逐表），避免整库塞内存。
        """
        yield ("-- AutoHealOps backup (Oracle)\n"
               "-- method: logical（INSERT 语句；非 expdp / Data Pump）\n"
               f"-- time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n").encode("utf-8")
        conn = self._connect_sync(c, readonly=False)
        try:
            for schema in databases:
                check_ident(schema, "库名")
                tables = await asyncio.to_thread(_list_tables_sync, conn, schema)
                yield (f"\n-- ==================== schema: {schema} ({len(tables)} tables) ====================\n"
                       ).encode("utf-8")
                for t in tables:
                    chunk = await asyncio.to_thread(_table_backup_sync, conn, schema, t, schema_only)
                    if chunk:
                        yield chunk.encode("utf-8")
        finally:
            conn.close()

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
