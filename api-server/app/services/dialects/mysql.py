# -*- coding: utf-8 -*-
"""MySQL 直连执行方言。

⚠️ 本模块由原 `app.services.db_direct_service.py` 的 MySQL 逻辑原样迁移而来，
行为（SQL 语义 / 返回结构 / 边界处理）逐条等价，请勿改动 SQL 语义。
"""
import asyncio
import csv
import io
import re
from datetime import datetime

import aiomysql

from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm

from .base import (
    Dialect,
    check_ident,
    check_type_spec,
    default_value_sql,
    split_sql_statements,
    sql_literal,
)

logger = get_logger("api-server.db_direct.mysql")

SYS_DBS = {"information_schema", "performance_schema", "mysql", "sys"}
READ_HEADS = ("select", "show", "desc", "describe", "explain", "with")
MAX_ROWS = 5000

_MAX_STMT_LEN = 10 * 1024 * 1024  # 单条语句超过 10MB 跳过，避免卡死
_STMT_TIMEOUT = 60.0             # 单条语句执行超时（秒）
_BATCH_SIZE = 200                # 每批最多处理的语句数


def _info(c, readonly: bool = True) -> dict:
    """连接信息；readonly=True 优先用只读账号（查系统视图/performance_schema 足够）"""
    return {
        "host": c.host,
        "port": int(c.port or 3306),
        "user": (c.readonly_username or c.username) if readonly else (c.username or c.readonly_username),
        "password": decrypt_aes256_gcm(
            (c.readonly_password_encrypted or c.password_encrypted)
            if readonly else (c.password_encrypted or c.readonly_password_encrypted)
        ) if (c.password_encrypted or c.readonly_password_encrypted) else "",
        "database": c.database or None,
    }


def _norm(v):
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


def _q(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


_DELIM_LINE_RE = re.compile(r"[ \t]*DELIMITER[ \t]+(\S+)", re.IGNORECASE)


def _split_sql(s: str) -> list[str]:
    """遗留的简单 `;` 切分器（不处理 DELIMITER，不 strip 语句）。

    已被 `split_sql_delimited` 取代（导入路径已切换），保留仅为兼容旧测试/旧调用方。
    """
    out, cur, quote = [], [], None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
            cur.append(ch)
        elif ch == ";":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return out


def split_sql_delimited(text: str) -> list[str]:
    """DELIMITER 感知的 MySQL 语句切分（纯函数，不连库）。

    识别行首 `DELIMITER xxx` 指令（大小写不敏感、可带前导空白）并切换语句分隔符
    （默认 `;`），指令行本身不进入任何语句；`DELIMITER ;` 切回分号。

    分隔符只在字符串 / 行注释 / 块注释之外生效：字符串、注释里的分隔符与
    `DELIMITER` 关键字均不识别（避免误拆存储过程体 / 误判注释里的指令）。
    """
    stmts: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    delim = ";"

    def _at_line_start(idx: int) -> bool:
        return idx == 0 or text[idx - 1] in "\r\n"

    def _flush() -> None:
        s = "".join(cur).strip()
        if s:
            stmts.append(s)
        cur.clear()

    while i < n:
        # 行首 DELIMITER 指令（仅顶层：字符串/注释在各自分支里已原子吞掉，不会落在行首）
        if _at_line_start(i):
            m = _DELIM_LINE_RE.match(text, i)
            if m:
                delim = m.group(1)
                j = text.find("\n", i)
                i = (j + 1) if j != -1 else n
                continue

        ch = text[i]

        # 字符串 ' " `（含反斜杠转义、双写转义）
        if ch in ("'", '"', "`"):
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

        # 行注释 #
        if ch == "#":
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue

        # 行注释 --（后跟空白才视为注释）
        if ch == "-" and i + 1 < n and text[i + 1] == "-" and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue

        # 块注释 /* */
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            cur.append(ch)
            cur.append(text[i + 1])
            i += 2
            while i < n:
                cur.append(text[i])
                if text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    cur.append(text[i + 1])
                    i += 2
                    break
                i += 1
            continue

        # 当前分隔符（在字符串/注释之外才切分）
        if text.startswith(delim, i):
            _flush()
            i += len(delim)
            continue

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


# ============ DDL 生成（表结构设计器，R37 §6.6；由 api 层 `_build_table_alter` 原样迁入） ============

def _column_def(col: dict) -> str:
    """单列定义：`name` type [NOT NULL] [DEFAULT x] [AUTO_INCREMENT] [COMMENT '...']"""
    name = check_ident(col.get("name") or "", "字段名")
    typ = check_type_spec(col.get("type") or "")
    parts = [f"`{name}` {typ}"]
    if not bool(col.get("nullable", True)):
        parts.append("NOT NULL")
    d = default_value_sql(col.get("default"))
    if d:
        parts.append(d)
    # 自增：兼容布尔 `auto_increment` 与字符串 `extra='auto_increment'` 两种入参
    if col.get("auto_increment") or "auto_increment" in ((col.get("extra") or "").lower()):
        parts.append("AUTO_INCREMENT")
    if (col.get("comment") or "") != "":
        parts.append(f"COMMENT {sql_literal(col.get('comment'))}")
    return " ".join(parts)


def _build_table_alter(database: str, table: str, ch: dict) -> str:
    """结构化改动 → 单条 ALTER TABLE（RENAME TO 作为子句并入，便于审核单一次执行）。"""
    check_ident(database, "库名")
    check_ident(table, "表名")
    ch = ch or {}
    clauses: list[str] = []
    for c in ch.get("add_columns") or []:
        clause = "ADD COLUMN " + _column_def(c)
        after = (c.get("after") or "").strip()
        if after:
            clause += f" AFTER `{check_ident(after, 'after 字段')}`"
        clauses.append(clause)
    for name in ch.get("drop_columns") or []:
        clauses.append(f"DROP COLUMN `{check_ident(name, '字段名')}`")
    for c in ch.get("modify_columns") or []:
        clauses.append("MODIFY COLUMN " + _column_def(c))
    for c in ch.get("change_columns") or []:
        old = check_ident(c.get("from") or "", "字段名")
        clauses.append(f"CHANGE COLUMN `{old}` " + _column_def(c))
    for i in ch.get("add_indexes") or []:
        cols = ", ".join(f"`{check_ident(x, '索引字段')}`" for x in (i.get("columns") or []))
        if not cols:
            raise ValueError("索引至少需要一个字段")
        uniq = "UNIQUE " if i.get("unique") else ""
        idx_name = (i.get("name") or "").strip()
        name_sql = f"`{check_ident(idx_name, '索引名')}` " if idx_name else ""
        clauses.append(f"ADD {uniq}INDEX {name_sql}({cols})")
    for name in ch.get("drop_indexes") or []:
        clauses.append(f"DROP INDEX `{check_ident(name, '索引名')}`")
    if ch.get("comment") is not None:
        clauses.append(f"COMMENT={sql_literal(ch.get('comment') or '')}")
    if (ch.get("engine") or "").strip():
        eng = str(ch["engine"]).strip()
        if not re.match(r"^[A-Za-z0-9_]{1,32}$", eng):
            raise ValueError(f"存储引擎不合法：{eng}")
        clauses.append(f"ENGINE={eng}")
    charset = (ch.get("charset") or "").strip()
    if charset:
        if not re.match(r"^[A-Za-z0-9_]{1,32}$", charset):
            raise ValueError(f"字符集不合法：{charset}")
        clause = f"CONVERT TO CHARACTER SET {charset}"
        collation = (ch.get("collation") or "").strip()
        if collation:
            if not re.match(r"^[A-Za-z0-9_]{1,64}$", collation):
                raise ValueError(f"排序规则不合法：{collation}")
            clause += f" COLLATE {collation}"
        clauses.append(clause)
    if not clauses:
        raise ValueError("没有任何改动")
    new_name = (ch.get("rename_table") or "").strip()
    if new_name:
        clauses.append(f"RENAME TO `{check_ident(new_name, '新表名')}`")
    return f"ALTER TABLE `{database}`.`{table}`\n  " + ",\n  ".join(clauses)


def _build_database_alter(name: str, payload: dict) -> str:
    """编辑库：字符集/排序规则 → 单条 ALTER DATABASE。"""
    db = check_ident(name, "库名")
    charset = (payload.get("charset") or "").strip()
    collation = (payload.get("collation") or "").strip()
    if not charset and not collation:
        raise ValueError("至少指定字符集或排序规则")
    if charset and not re.match(r"^[A-Za-z0-9_]{1,32}$", charset):
        raise ValueError(f"字符集不合法：{charset}")
    if collation and not re.match(r"^[A-Za-z0-9_]{1,64}$", collation):
        raise ValueError(f"排序规则不合法：{collation}")
    parts = []
    if charset:
        parts.append(f"CHARACTER SET {charset}")
    if collation:
        parts.append(f"COLLATE {collation}")
    return f"ALTER DATABASE `{db}` " + " ".join(parts)


def _mysql_schema_to_dict(db: str, tbl: str, cols: list[dict], idx_rows: list[dict],
                          meta: list[dict]) -> dict:
    """把 information_schema 的三组行转成表结构详情 dict（与 api 层同形）。"""
    columns = [{
        "name": r.get("COLUMN_NAME"),
        "type": r.get("COLUMN_TYPE") or "",
        "nullable": (r.get("IS_NULLABLE") or "YES") == "YES",
        "default": r.get("COLUMN_DEFAULT"),
        "extra": r.get("EXTRA") or "",
        "comment": r.get("COLUMN_COMMENT") or "",
        "key": r.get("COLUMN_KEY") or "",
        "charset": r.get("CHARACTER_SET_NAME") or "",
        "collation": r.get("COLLATION_NAME") or "",
    } for r in cols]

    def _sint(v, default=1):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    idx_map: dict[str, dict] = {}
    for r in idx_rows:
        name = r.get("INDEX_NAME") or ""
        item = idx_map.setdefault(name, {
            "name": name, "unique": _sint(r.get("NON_UNIQUE"), 1) == 0,
            "type": r.get("INDEX_TYPE") or "", "columns": [],
        })
        item["columns"].append(r.get("COLUMN_NAME") or "")

    tinfo = meta[0] if meta else {}
    collation = tinfo.get("TABLE_COLLATION") or ""
    try:
        rows = int(tinfo.get("TABLE_ROWS") or 0)
    except (TypeError, ValueError):
        rows = 0
    return {
        "database": db, "table": tbl,
        "engine": tinfo.get("ENGINE") or "",
        "charset": collation.split("_")[0] if collation else "",
        "collation": collation,
        "comment": tinfo.get("TABLE_COMMENT") or "",
        "rows": rows,
        "columns": columns,
        "indexes": list(idx_map.values()),
    }


class MySQLDialect(Dialect):
    db_type = "mysql"
    PROBE_SQL = "select 1"
    CAPABILITIES = frozenset({
        "query", "exec", "schema", "ddl", "list_databases",
        "create_database", "drop_database", "export", "import", "backup",
    })

    async def connect(self, c, readonly: bool = True, database: str | None = None):
        info = _info(c, readonly)
        return await aiomysql.connect(
            host=info["host"], port=info["port"], user=info["user"], password=info["password"],
            db=(database or info["database"]), charset="utf8mb4", autocommit=True, connect_timeout=8,
        )

    async def query(self, c, sql: str, limit: int = 200, database: str | None = None) -> dict:
        limit = max(1, min(int(limit or 200), MAX_ROWS))
        conn = await self.connect(c, readonly=True, database=database)
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql)
                rows = await cur.fetchmany(limit)
                columns = [d[0] for d in (cur.description or [])]
                data = [[_norm(r.get(col)) for col in columns] for r in rows]
                return {"columns": columns, "rows": data, "truncated": len(data) >= limit}
        finally:
            conn.close()

    async def exec_sql(self, c, sql: str, database: str | None = None) -> dict:
        conn = await self.connect(c, readonly=False, database=database)
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql)
                affected = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
                last_id = cur.lastrowid or 0
                return {"columns": [], "rows": [], "truncated": False,
                        "affected_rows": affected, "last_insert_id": last_id, "write": True}
        finally:
            conn.close()

    async def schema(self, c) -> dict:
        conn = await self.connect(c, readonly=True)
        try:
            out = {"databases": [], "tables": [], "views": [], "routines": [], "events": []}
            async with conn.cursor() as cur:
                await cur.execute("SHOW DATABASES")
                out["databases"] = [r[0] for r in await cur.fetchall()]

                await cur.execute("""
                    SELECT table_schema, table_name, table_type FROM information_schema.tables
                    ORDER BY table_schema, table_name LIMIT 3000""")
                for db, name, typ in await cur.fetchall():
                    if db in SYS_DBS:
                        continue
                    if (typ or "").upper() == "VIEW":
                        out["views"].append({"db": db, "name": name})
                    else:
                        out["tables"].append({"db": db, "name": name, "type": typ})

                await cur.execute("""
                    SELECT routine_schema, routine_name, routine_type FROM information_schema.routines
                    ORDER BY routine_schema, routine_type, routine_name LIMIT 500""")
                for db, name, typ in await cur.fetchall():
                    if db in SYS_DBS:
                        continue
                    out["routines"].append({"db": db, "name": name, "type": typ})

                await cur.execute("""
                    SELECT event_schema, event_name, IFNULL(status,'') FROM information_schema.events
                    ORDER BY event_schema, event_name LIMIT 500""")
                for db, name, st in await cur.fetchall():
                    if db in SYS_DBS:
                        continue
                    out["events"].append({"db": db, "name": name, "extra": st})
            return out
        finally:
            conn.close()

    async def ddl(self, c, kind: str, db: str, name: str) -> dict:
        kind_map = {
            "table": "SHOW CREATE TABLE", "view": "SHOW CREATE VIEW",
            "function": "SHOW CREATE FUNCTION", "procedure": "SHOW CREATE PROCEDURE",
            "event": "SHOW CREATE EVENT",
        }
        stmt = kind_map.get((kind or "").lower())
        if not stmt:
            raise RuntimeError(f"不支持的 kind={kind}")
        full = f"`{name}`" if not db else f"`{db}`.`{name}`"
        conn = await self.connect(c, readonly=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"{stmt} {full}")
                row = await cur.fetchone()
                cols = [d[0] for d in (cur.description or [])]
                text = ""
                for i, col in enumerate(cols):
                    if col.lower().startswith("create "):
                        text = str(row[i])
                        break
                if not text and len(row) > 1:
                    text = str(row[1])
                return {"kind": kind, "name": name, "ddl": text + ";"}
        finally:
            conn.close()

    # ---- DDL 生成（纯函数，不连库）----

    def build_table_alter(self, database: str, table: str, changes: dict) -> list[str]:
        return [_build_table_alter(database, table, changes)]

    def build_database_alter(self, name: str, payload: dict) -> str:
        return _build_database_alter(name, payload)

    async def table_schema(self, c, db: str, table: str) -> dict:
        def _rows(data: dict) -> list[dict]:
            cols = data.get("columns") or []
            return [dict(zip(cols, r)) for r in (data.get("rows") or [])]

        cols = _rows(await self.query(c, (
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA, COLUMN_COMMENT, "
            "CHARACTER_SET_NAME, COLLATION_NAME, COLUMN_KEY FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{table}' ORDER BY ORDINAL_POSITION"), limit=500))
        idx_rows = _rows(await self.query(c, (
            "SELECT INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, INDEX_TYPE "
            "FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{table}' ORDER BY INDEX_NAME, SEQ_IN_INDEX"), limit=500))
        meta = _rows(await self.query(c, (
            "SELECT ENGINE, TABLE_COLLATION, TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA='{db}' AND TABLE_NAME='{table}'"), limit=500))
        return _mysql_schema_to_dict(db, table, cols, idx_rows, meta)

    async def export_sql(self, c, db: str, table: str = "", rows: int = 1000, mode: str = "both") -> dict:
        mode = (mode or "both").lower()
        if mode not in ("structure", "data", "both"):
            raise RuntimeError(f"mode 仅支持 structure/data/both（当前 {mode}）")
        rows = max(1, min(int(rows or 1000), MAX_ROWS))
        db = db or c.database
        if not db:
            raise RuntimeError("db 不能为空")
        conn = await self.connect(c, readonly=True)
        try:
            tables = [table] if table else []
            async with conn.cursor() as cur:
                if not tables:
                    await cur.execute("""
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name""", (db,))
                    tables = [r[0] for r in await cur.fetchall()]

            buf = io.StringIO()
            buf.write("-- AutoHealOps export\n")
            buf.write(f"-- database: {db}  time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            buf.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")
            total, truncated = 0, False
            for t in tables:
                async with conn.cursor() as cur:
                    if mode != "data":
                        await cur.execute(f"SHOW CREATE TABLE `{db}`.`{t}`")
                        row = await cur.fetchone()
                        buf.write(f"-- ---------- table {t} ----------\n{row[1]};\n\n")
                    if mode == "structure":
                        continue
                    await cur.execute(f"SELECT * FROM `{db}`.`{t}` LIMIT {rows}")
                    cols = [d[0] for d in (cur.description or [])]
                    col_list = ",".join(f"`{x}`" for x in cols)
                    prefix = f"INSERT INTO `{db}`.`{t}` ({col_list}) VALUES "
                    for r in await cur.fetchall():
                        buf.write(prefix + "(" + ",".join(_q(v) for v in r) + ");\n")
                        total += 1
                        if buf.tell() > 5 * 1024 * 1024:
                            truncated = True
                            break
                buf.write("\n")
                if truncated or buf.tell() > 5 * 1024 * 1024:
                    truncated = True
                    break
            buf.write("SET FOREIGN_KEY_CHECKS=1;\n")
            return {"db": db, "table": table, "tables": len(tables), "rows": total,
                    "bytes": buf.tell(), "truncated": truncated, "sql": buf.getvalue()}
        finally:
            conn.close()

    async def import_content(self, c, db: str, table: str, fmt: str, content: str) -> dict:
        if len(content.encode("utf-8")) > 5 * 1024 * 1024:
            raise RuntimeError("文件超过 5MB，请拆分后导入")
        db = db or c.database
        conn = await self.connect(c, readonly=False)
        try:
            if db:
                async with conn.cursor() as cur:
                    await cur.execute(f"USE `{db}`")
            executed, skipped = 0, 0
            if (fmt or "sql").lower() == "csv":
                if not table:
                    raise RuntimeError("CSV 导入必须指定表名")
                rd = csv.reader(io.StringIO(content.lstrip("\ufeff")))
                records = list(rd)
                if len(records) < 2:
                    raise RuntimeError("CSV 至少需要表头 + 1 行数据")
                header = [h.strip() for h in records[0]]
                cols = ",".join(f"`{h}`" for h in header)
                ph = ",".join(["%s"] * len(header))
                stmt = f"INSERT INTO `{table}` ({cols}) VALUES ({ph})"
                async with conn.cursor() as cur:
                    for rec in records[1:]:
                        if not rec:
                            continue
                        args = [rec[i] if i < len(rec) else "" for i in range(len(header))]
                        await cur.execute(stmt, args)
                        executed += 1
            else:
                for stmt in split_sql_delimited(content):
                    s = stmt.strip()
                    if not s or _sql_blank(s):
                        skipped += 1
                        continue
                    async with conn.cursor() as cur:
                        await cur.execute(s)
                    executed += 1
            return {"format": fmt, "executed": executed, "skipped": skipped, "bytes": len(content.encode("utf-8"))}
        finally:
            conn.close()

    async def list_databases(self, c) -> set[str]:
        """当前账号可见的库名集合（建库/删库/备份预检用）。"""
        conn = await self.connect(c, readonly=True)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SHOW DATABASES")
                return {r[0] for r in await cur.fetchall()}
        finally:
            conn.close()

    async def create_database(self, c, name: str) -> None:
        conn = await self.connect(c, readonly=False)
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE DATABASE `{name}` DEFAULT CHARACTER SET utf8mb4")
        finally:
            conn.close()

    async def drop_database(self, c, name: str) -> None:
        conn = await self.connect(c, readonly=False)
        try:
            async with conn.cursor() as cur:
                await cur.execute(f"DROP DATABASE `{name}`")
        finally:
            conn.close()

    async def import_sql(self, c, db: str, sql: str, mode: str = "stop") -> dict:
        """导入 SQL 脚本（不限大小）：按 `;` 切分逐条执行，支持 stop/skip 两种错误模式。

        - stop：遇第一条错误立即停止；skip：跳过错误继续执行。
        - 空语句/纯注释语句/超长语句跳过；单条语句带超时，避免卡死。
        - 返回 {total, executed, skipped, failed, errors, stopped}。
        """
        mode = (mode or "stop").lower()
        db = db or c.database
        conn = await self.connect(c, readonly=False)
        try:
            if db:
                async with conn.cursor() as cur:
                    await cur.execute(f"USE `{db}`")
            stmts = split_sql_delimited(sql or "")
            executed = skipped = 0
            errors: list[dict] = []
            stopped = False
            total = len(stmts)
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
                        async with conn.cursor() as cur:
                            await asyncio.wait_for(cur.execute(s), timeout=_STMT_TIMEOUT)
                        executed += 1
                    except Exception as e:  # noqa: BLE001
                        errors.append({"index": idx, "statement_head": s[:120], "error": str(e)[:300]})
                        if mode == "stop":
                            stopped = True
                            break
                if stopped:
                    break
            return {"total": total, "executed": executed, "skipped": skipped,
                    "failed": len(errors), "errors": errors, "stopped": stopped}
        finally:
            conn.close()

    async def backup_sql(self, c, databases: list[str], schema_only: bool = False):
        """生成库备份 SQL（异步生成器，逐段 yield 字节，不落盘）。

        用主账号（readonly=False）保证能读到全部表/数据；全程流式，大库也不会整体载入内存。
        """
        conn = await self.connect(c, readonly=False)
        try:
            yield ("-- AutoHealOps backup\n"
                   f"-- host: {c.host}:{c.port}  time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                   "SET NAMES utf8mb4;\n"
                   "SET FOREIGN_KEY_CHECKS=0;\n"
                   "SET UNIQUE_CHECKS=0;\n\n").encode("utf-8")
            for db in databases:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name""", (db,))
                    tables = [r[0] for r in await cur.fetchall()]
                yield (f"\n-- ==================== database: {db} ({len(tables)} tables) ====================\n"
                       ).encode("utf-8")
                yield (f"CREATE DATABASE IF NOT EXISTS `{db}` DEFAULT CHARACTER SET utf8mb4;\n"
                       f"USE `{db}`;\n\n").encode("utf-8")
                for t in tables:
                    async with conn.cursor() as cur:
                        await cur.execute(f"SHOW CREATE TABLE `{db}`.`{t}`")
                        row = await cur.fetchone()
                        if row is None or len(row) < 2:
                            continue
                        yield f"-- ---------- table: {t} ----------\n{row[1]};\n\n".encode("utf-8")
                        if schema_only:
                            continue
                        await cur.execute(f"SELECT * FROM `{db}`.`{t}`")
                        cols = [d[0] for d in (cur.description or [])]
                        if not cols:
                            continue
                        col_list = ",".join(f"`{x}`" for x in cols)
                        prefix = f"INSERT INTO `{db}`.`{t}` ({col_list}) VALUES "
                        batch: list[str] = []
                        while True:
                            rows = await cur.fetchmany(500)
                            if not rows:
                                break
                            for r in rows:
                                batch.append("(" + ",".join(_q(v) for v in r) + ")")
                            if len(batch) >= 500:
                                yield (prefix + ",".join(batch) + ";\n").encode("utf-8")
                                batch = []
                        if batch:
                            yield (prefix + ",".join(batch) + ";\n").encode("utf-8")
                    yield "\n".encode("utf-8")
            yield "SET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n".encode("utf-8")
        finally:
            conn.close()
