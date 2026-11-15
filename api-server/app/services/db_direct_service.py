# -*- coding: utf-8 -*-
"""直连库的平台侧执行（source=direct）。

与 agent 库的区别：agent 库账密在集群里、经隧道执行；直连库账密在平台（加密），
由 api-server 直接连库执行。前端体验保持一致（同一套 SQL 控制台/对象树/导入导出）。

当前实现：MySQL（aiomysql）；PG/Oracle 待方言接口统一后再接。
"""
import asyncio
import csv
import io
import json
import time
from datetime import datetime

import aiomysql

from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm

logger = get_logger("api-server.db_direct")

SYS_DBS = {"information_schema", "performance_schema", "mysql", "sys"}
READ_HEADS = ("select", "show", "desc", "describe", "explain", "with")
MAX_ROWS = 5000


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


async def _connect(c, readonly: bool = True, database: str | None = None):
    if (c.db_type or "mysql").lower() != "mysql":
        raise RuntimeError(f"直连执行暂只支持 mysql（当前 {c.db_type}）")
    info = _info(c, readonly)
    return await aiomysql.connect(
        host=info["host"], port=info["port"], user=info["user"], password=info["password"],
        db=(database or info["database"]), charset="utf8mb4", autocommit=True, connect_timeout=8,
    )


def _norm(v):
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


async def query(c, sql: str, limit: int = 200, database: str | None = None) -> dict:
    limit = max(1, min(int(limit or 200), MAX_ROWS))
    conn = await _connect(c, readonly=True, database=database)
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql)
            rows = await cur.fetchmany(limit)
            columns = [d[0] for d in (cur.description or [])]
            data = [[_norm(r.get(col)) for col in columns] for r in rows]
            return {"columns": columns, "rows": data, "truncated": len(data) >= limit}
    finally:
        conn.close()


async def exec_sql(c, sql: str, database: str | None = None) -> dict:
    conn = await _connect(c, readonly=False, database=database)
    try:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            affected = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            last_id = cur.lastrowid or 0
            return {"columns": [], "rows": [], "truncated": False,
                    "affected_rows": affected, "last_insert_id": last_id, "write": True}
    finally:
        conn.close()


async def schema(c) -> dict:
    conn = await _connect(c, readonly=True)
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


async def ddl(c, kind: str, db: str, name: str) -> dict:
    kind_map = {
        "table": "SHOW CREATE TABLE", "view": "SHOW CREATE VIEW",
        "function": "SHOW CREATE FUNCTION", "procedure": "SHOW CREATE PROCEDURE",
        "event": "SHOW CREATE EVENT",
    }
    stmt = kind_map.get((kind or "").lower())
    if not stmt:
        raise RuntimeError(f"不支持的 kind={kind}")
    full = f"`{name}`" if not db else f"`{db}`.`{name}`"
    conn = await _connect(c, readonly=True)
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


def _q(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


async def export_sql(c, db: str, table: str = "", rows: int = 1000, mode: str = "both") -> dict:
    mode = (mode or "both").lower()
    if mode not in ("structure", "data", "both"):
        raise RuntimeError(f"mode 仅支持 structure/data/both（当前 {mode}）")
    rows = max(1, min(int(rows or 1000), MAX_ROWS))
    db = db or c.database
    if not db:
        raise RuntimeError("db 不能为空")
    conn = await _connect(c, readonly=True)
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


async def import_content(c, db: str, table: str, fmt: str, content: str) -> dict:
    if len(content.encode("utf-8")) > 5 * 1024 * 1024:
        raise RuntimeError("文件超过 5MB，请拆分后导入")
    db = db or c.database
    conn = await _connect(c, readonly=False)
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
            for stmt in _split_sql(content):
                s = stmt.strip()
                if not s or s.startswith("--"):
                    skipped += 1
                    continue
                async with conn.cursor() as cur:
                    await cur.execute(s)
                executed += 1
        return {"format": fmt, "executed": executed, "skipped": skipped, "bytes": len(content.encode("utf-8"))}
    finally:
        conn.close()


def _split_sql(s: str) -> list[str]:
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


async def probe(c) -> tuple[bool, str]:
    try:
        await query(c, "select 1", 1)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# ============ SQL 终端：库管理 / 导入 / 备份（source=direct，MySQL） ============

_MAX_STMT_LEN = 10 * 1024 * 1024  # 单条语句超过 10MB 跳过，避免卡死
_STMT_TIMEOUT = 60.0             # 单条语句执行超时（秒）
_BATCH_SIZE = 200                # 每批最多处理的语句数


def split_sql_statements(text: str) -> list[str]:
    """按 `;` 切分 SQL 语句，忽略字符串 / 行注释 / 块注释里的分号。

    处理：单引号/双引号/反引号字符串（含反斜杠转义、双写转义）、`-- ` 与 `#` 行注释、`/* */` 块注释。
    不引入第三方依赖，纯状态机实现。
    """
    stmts: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        # 行注释：# 或 --（后跟空白才视为注释，避免把减法 `1--2` 误判）
        if ch == "#":
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue
        if ch == "-" and nxt == "-" and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue
        # 块注释 /* */
        if ch == "/" and nxt == "*":
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
        # 字符串：' " `
        if ch in ("'", '"', "`"):
            quote = ch
            cur.append(ch)
            i += 1
            while i < n:
                cur.append(text[i])
                if text[i] == "\\" and quote != "`" and i + 1 < n:
                    # 反斜杠转义（MySQL 字符串内 \' 不算结束）
                    cur.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == quote:
                    # 双写转义（'' 或 "" 或 ``）
                    if i + 1 < n and text[i + 1] == quote:
                        cur.append(text[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == ";":
            stmts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        stmts.append(tail)
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


async def list_databases(c) -> set[str]:
    """当前账号可见的库名集合（建库/删库/备份预检用）。"""
    conn = await _connect(c, readonly=True)
    try:
        async with conn.cursor() as cur:
            await cur.execute("SHOW DATABASES")
            return {r[0] for r in await cur.fetchall()}
    finally:
        conn.close()


async def create_database(c, name: str) -> None:
    conn = await _connect(c, readonly=False)
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"CREATE DATABASE `{name}` DEFAULT CHARACTER SET utf8mb4")
    finally:
        conn.close()


async def drop_database(c, name: str) -> None:
    conn = await _connect(c, readonly=False)
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE `{name}`")
    finally:
        conn.close()


async def import_sql(c, db: str, sql: str, mode: str = "stop") -> dict:
    """导入 SQL 脚本（不限大小）：按 `;` 切分逐条执行，支持 stop/skip 两种错误模式。

    - stop：遇第一条错误立即停止；skip：跳过错误继续执行。
    - 空语句/纯注释语句/超长语句跳过；单条语句带超时，避免卡死。
    - 返回 {total, executed, skipped, failed, errors, stopped}。
    """
    mode = (mode or "stop").lower()
    db = db or c.database
    conn = await _connect(c, readonly=False)
    try:
        if db:
            async with conn.cursor() as cur:
                await cur.execute(f"USE `{db}`")
        stmts = split_sql_statements(sql or "")
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


async def backup_sql(c, databases: list[str], schema_only: bool = False):
    """生成库备份 SQL（异步生成器，逐段 yield 字节，不落盘）。

    用主账号（readonly=False）保证能读到全部表/数据；全程流式，大库也不会整体载入内存。
    """
    conn = await _connect(c, readonly=False)
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
