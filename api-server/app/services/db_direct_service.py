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


async def _connect(c, readonly: bool = True):
    if (c.db_type or "mysql").lower() != "mysql":
        raise RuntimeError(f"直连执行暂只支持 mysql（当前 {c.db_type}）")
    info = _info(c, readonly)
    return await aiomysql.connect(
        host=info["host"], port=info["port"], user=info["user"], password=info["password"],
        db=info["database"], charset="utf8mb4", autocommit=True, connect_timeout=8,
    )


def _norm(v):
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return v


async def query(c, sql: str, limit: int = 200) -> dict:
    limit = max(1, min(int(limit or 200), MAX_ROWS))
    conn = await _connect(c, readonly=True)
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql)
            rows = await cur.fetchmany(limit)
            columns = [d[0] for d in (cur.description or [])]
            data = [[_norm(r.get(col)) for col in columns] for r in rows]
            return {"columns": columns, "rows": data, "truncated": len(data) >= limit}
    finally:
        conn.close()


async def exec_sql(c, sql: str) -> dict:
    conn = await _connect(c, readonly=False)
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


async def export_sql(c, db: str, table: str = "", rows: int = 1000) -> dict:
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
                await cur.execute(f"SHOW CREATE TABLE `{db}`.`{t}`")
                row = await cur.fetchone()
                buf.write(f"-- ---------- table {t} ----------\n{row[1]};\n\n")
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
