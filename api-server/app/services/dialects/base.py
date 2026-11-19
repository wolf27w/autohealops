# -*- coding: utf-8 -*-
"""直连执行方言抽象基类 + 通用 SQL 拆分器。

方言 = 一种数据库（mysql/oracle）的连接与执行实现。`db_direct_service` 只做分派，
具体 SQL 语义在各方言里实现，避免一类库一套 if/else 散落各处。

能力集合（CAPABILITIES）用于「能力检测」：入口层（API 拦截点 / 定时任务）据此判断
该 db_type 是否放行，而不是写死 `== "mysql"`。
"""
from __future__ import annotations

import re


# ============ DDL 生成侧共享校验（与 api 层白名单一致，MySQL/Oracle 复用） ============
_IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_TYPE_RE = re.compile(r"^[A-Za-z0-9_ ,()'\".+-]{1,200}$")
_EXPR_DEFAULT_RE = re.compile(r"^-?\d+(\.\d+)?$")


def check_ident(v: str, what: str = "标识符") -> str:
    """标识符校验（库名/表名/字段名/索引名），仅字母数字下划线，长度 1~64。"""
    v = (v or "").strip()
    if not _IDENT_RE.match(v):
        raise ValueError(f"{what}不合法：{v}")
    return v


def check_type_spec(t: str) -> str:
    """字段类型串校验：白名单字符 + 禁止注释/反引号，防注入。"""
    t = (t or "").strip()
    if not t or not _TYPE_RE.match(t) or ";" in t or "--" in t or "/*" in t or "`" in t:
        raise ValueError(f"字段类型不合法：{t}")
    return t


def sql_literal(v) -> str:
    """SQL 字符串字面量（单引号包裹 + 反斜杠/单引号转义），非法字符直接报错。"""
    s = str(v)
    if ";" in s or "--" in s or "/*" in s:
        raise ValueError("注释/默认值包含非法字符")
    return "'" + s.replace("\\", "\\\\").replace("'", "''") + "'"


def default_value_sql(dv) -> str | None:
    """默认值 → DEFAULT 子句片段（MySQL / Oracle 通用语义一致）。

    返回 None 表示无默认值（不加 DEFAULT）；否则返回 `DEFAULT ...` 片段。
    """
    if dv is None or str(dv) == "":
        return None
    s = str(dv)
    up = s.upper()
    if up == "NULL":
        return "DEFAULT NULL"
    if _EXPR_DEFAULT_RE.match(s) or up in ("CURRENT_TIMESTAMP", "CURRENT_TIMESTAMP()"):
        return f"DEFAULT {s}"
    return f"DEFAULT {sql_literal(s)}"


class UnsupportedError(RuntimeError):
    """该方言不支持某能力时抛出（业务错误，可安全转成用户可见文案，非 500 堆栈）。"""


class Dialect:
    db_type: str = ""
    # 该方言支持的能力集合。可能值：
    #   query / exec / schema / ddl / list_databases /
    #   create_database / drop_database / export / import / backup
    CAPABILITIES: frozenset[str] = frozenset()

    # 探测用的存活 SQL（Oracle 需带 FROM DUAL）
    PROBE_SQL: str = "select 1"

    def supports(self, capability: str) -> bool:
        return capability in self.CAPABILITIES

    def require(self, capability: str, what: str) -> None:
        if not self.supports(capability):
            raise UnsupportedError(f"直连执行暂不支持 {self.db_type} 的{what}")

    # ---- 各能力入口（子类按需实现）----

    async def connect(self, c, readonly: bool = True, database: str | None = None):
        raise NotImplementedError

    async def query(self, c, sql: str, limit: int = 200, database: str | None = None) -> dict:
        raise NotImplementedError

    async def exec_sql(self, c, sql: str, database: str | None = None) -> dict:
        raise NotImplementedError

    async def schema(self, c) -> dict:
        raise NotImplementedError

    async def ddl(self, c, kind: str, db: str, name: str) -> dict:
        raise NotImplementedError

    async def list_databases(self, c) -> set[str]:
        raise NotImplementedError

    async def create_database(self, c, name: str) -> None:
        self.require("create_database", "建库")

    async def drop_database(self, c, name: str) -> None:
        self.require("drop_database", "删库")

    async def export_sql(self, c, db: str, table: str = "", rows: int = 1000, mode: str = "both") -> dict:
        # TODO(oracle): 后续支持时实现 Oracle 数据导出（DBMS_METADATA / 分页 SELECT）
        self.require("export", "导出")

    async def import_content(self, c, db: str, table: str, fmt: str, content: str) -> dict:
        # TODO(oracle): 后续支持时实现 Oracle 导入（sqlldr / INSERT 批量）
        self.require("import", "导入")

    async def import_sql(self, c, db: str, sql: str, mode: str = "stop") -> dict:
        # TODO(oracle): 后续支持时实现 Oracle 脚本导入（PL/SQL 块按 / 拆分执行）
        self.require("import", "导入")

    async def backup_sql(self, c, databases: list[str], schema_only: bool = False):
        # TODO(oracle): 后续支持时实现 Oracle 备份（expdp / Data Pump）
        self.require("backup", "备份")
        if False:  # pragma: no cover - 仅保证本方法为异步生成器
            yield b""

    # ---- DDL 生成（纯函数，不连库）----

    def build_table_alter(self, database: str, table: str, changes: dict) -> list[str]:
        """结构化改动 → DDL 语句列表（纯函数，不连库，由 API 层 dry_run/执行复用）。

        - MySQL：返回单元素列表（一条 ALTER TABLE 含全部子句）。
        - Oracle：一次只能执行一条语句，返回多条按顺序执行/预览。
        """
        self.require("ddl", "改表")
        raise NotImplementedError

    def build_database_alter(self, name: str, payload: dict) -> str:
        """编辑库（字符集/排序规则）→ 单条 ALTER DATABASE。

        Oracle 无「库字符集/排序规则」概念，子类抛 UnsupportedError（前端据此优雅禁用）。
        """
        self.require("ddl", "编辑库")
        raise NotImplementedError

    async def table_schema(self, c, db: str, table: str) -> dict:
        """表结构详情（字段/索引/表选项），返回结构与 MySQL 同形。"""
        self.require("schema", "查看表结构")
        raise NotImplementedError

    async def probe(self, c) -> tuple[bool, str]:
        try:
            await self.query(c, self.PROBE_SQL, 1)
            return True, ""
        except Exception as e:  # noqa: BLE001
            return False, str(e)


# ============ 通用 SQL 拆分器 ============

def split_sql_statements(text: str) -> list[str]:
    """按 `;` 切分 SQL 语句，忽略字符串 / 行注释 / 块注释里的分号，且不误拆 PL/SQL 块。

    处理：
    - 单引号/双引号/反引号字符串（含反斜杠转义、双写转义）
    - `-- ` 与 `#` 行注释、`/* */` 块注释
    - Oracle PL/SQL 匿名块 `BEGIN ... END;`：块内部的分号不当作语句分隔符；
      独立一行的 `/`（SQL*Plus 执行符）作为块终止符，被丢弃（不进入语句文本）。

    局限（刻意不完整解析，够用即止）：
    - `DECLARE ... BEGIN ... END;` 的声明段（BEGIN 之前）仍会按 `;` 切分；
    - 不完整处理 `CREATE [OR REPLACE] PROCEDURE/FUNCTION/PACKAGE/TRIGGER` 这类带
      `IS/AS ... BEGIN ... END` 的存储程序体（体内部嵌套的 BEGIN...END 靠深度粗略保护）；
    - 不处理 MySQL 的 DELIMITER 自定义分隔符。
    """
    stmts: list[str] = []
    cur: list[str] = []
    i, n = 0, len(text)
    depth = 0        # PL/SQL BEGIN...END 嵌套深度（>0 表示处于块内，分号不切分）
    saw_block = False  # 本语句是否包含 PL/SQL 块（用于保留块闭合的 `;`）
    line_start = True  # 当前是否处于行首（仅空白），用于识别独立一行的 `/`

    def _flush() -> None:
        nonlocal cur, depth, saw_block, line_start
        s = "".join(cur).strip()
        if s:
            stmts.append(s)
        cur = []
        depth = 0
        saw_block = False
        line_start = True

    def _word_at(idx: int) -> str | None:
        """取 idx 处开始的「单词」（字母开头，后跟字母/数字/_/$/#）。"""
        j = idx
        while j < n and (text[j].isalnum() or text[j] in "_$#"):
            j += 1
        return text[idx:j] if j > idx else None

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        # 行注释：#
        if ch == "#":
            line_start = False
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue
        # 行注释：--（后跟空白才视为注释，避免减法 `1--2` 误判）
        if ch == "-" and nxt == "-" and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            line_start = False
            while i < n and text[i] not in "\r\n":
                cur.append(text[i])
                i += 1
            continue
        # 块注释 /* */
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
        # 字符串：' " `
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
        # 独立一行的 `/`：SQL*Plus 块执行符，作为语句终止（`/` 本身丢弃）
        if ch == "/" and line_start and nxt in ("\r", "\n", ""):
            if saw_block and not "".join(cur).rstrip().endswith(";"):
                cur.append(";")
            _flush()
            i += 1
            continue
        # 关键字识别（BEGIN / END / DECLARE 等，仅当处于单词边界）
        if ch.isalpha():
            w = _word_at(i)
            uw = (w or "").upper()
            if uw in ("BEGIN", "END", "DECLARE", "IF", "LOOP", "CASE"):
                j = i + len(w)
                while j < n and text[j] in " \t\r\n":
                    j += 1
                w2 = _word_at(j)
                uw2 = (w2 or "").upper()
                if uw == "BEGIN":
                    depth += 1
                    saw_block = True
                elif uw == "END":
                    if uw2 in ("IF", "LOOP", "CASE"):
                        pass  # 控制结构闭合（END IF / END LOOP / END CASE），不改变 BEGIN 深度
                    elif depth > 0:
                        depth -= 1
                # DECLARE / IF / LOOP / CASE 不改变深度
                for _ in range(len(w)):
                    cur.append(text[i])
                    i += 1
                line_start = False
                continue
            cur.append(ch)
            i += 1
            line_start = False
            continue
        # 分号：块内不切分；块闭合的 `;` 保留（PL/SQL 语法需要）
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
        # 换行：重置 line_start
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
