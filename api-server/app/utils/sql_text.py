# -*- coding: utf-8 -*-
"""SQL 文本工具（2026-11-26）：判断采集到的 SQL 是否被截断、给出长度。

背景：MySQL performance_schema 的 DIGEST_TEXT 只保留 `performance_schema_max_sql_text_length`
字符（默认 1024），更长的 SQL 会被硬截断。前端必须能看出「这条是不是完整的」，
否则展开后看到没结尾的语句会误以为平台丢了内容。
"""
import re

# 结尾停在半个词/逗号/运算符上，也视为截断
_TAIL_CUT_RE = re.compile(r"[,.( \t\n=+*/<>-]$")


def looks_truncated(sql: str, hard_limit: int = 1000) -> bool:
    """SQL 文本是否疑似被截断。

    判定（任一命中即视为截断）：
    - 长度 >= hard_limit（performance_schema 默认上限 1024）
    - 括号不配对
    - 单引号数为奇数
    - 结尾停在逗号/点号/运算符/左括号等「半截」位置
    """
    s = (sql or "").strip()
    if len(s) >= hard_limit:
        return True
    if s.count("(") != s.count(")"):
        return True
    if s.count("'") % 2 == 1:
        return True
    return bool(_TAIL_CUT_RE.search(s)) and not s.endswith(")")
