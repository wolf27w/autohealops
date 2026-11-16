# -*- coding: utf-8 -*-
"""告警屏蔽服务（R11 收尾）：匹配器校验、重复时间窗、生效状态、预览 SQL 过滤条件。

纯函数为主，便于单测；不依赖 DB 会话。
`matcher_filters` 只构造 SQLAlchemy 表达式（供预览接口做只读聚合，不把行拉到 Python）。
"""
import json
import re
from datetime import datetime, time, timedelta, timezone

# Asia/Shanghai：中国自 1991 年起无夏令时，恒为 UTC+8，用固定偏移即可，不依赖 tzdata。
SHANGHAI_TZ = timezone(timedelta(hours=8))

SILENCE_KEYS = ("cluster_id", "namespace", "resource_type", "resource_name", "fault_type", "severity")
SILENCE_OPS = ("=", "!=", "=~", "!~")
RECURRENCE_TYPES = ("once", "daily", "weekly")


def parse_recurrence(recurrence) -> dict:
    """把 recurrence（dict 或 JSON 字符串）解析为规范 dict；解析失败返回 {}。"""
    if recurrence is None:
        return {}
    if isinstance(recurrence, dict):
        return dict(recurrence)
    if isinstance(recurrence, str):
        try:
            r = json.loads(recurrence)
        except (json.JSONDecodeError, TypeError):
            return {}
        return r if isinstance(r, dict) else {}
    return {}


def validate_matchers(matchers) -> list[str]:
    """校验 matchers 结构，返回错误信息列表（空列表 = 合法）。

    规则：key ∈ SILENCE_KEYS，op ∈ SILENCE_OPS，=~ / !~ 的 value 必须是合法正则。
    """
    errors: list[str] = []
    if matchers is None:
        return errors
    if not isinstance(matchers, list):
        return ["matchers 必须是数组"]
    for i, m in enumerate(matchers):
        if not isinstance(m, dict):
            errors.append(f"matchers[{i}] 必须是对象")
            continue
        key = m.get("key", "")
        op = m.get("op", "=")
        if key not in SILENCE_KEYS:
            errors.append(f"matchers[{i}].key 非法：{key}")
        if op not in SILENCE_OPS:
            errors.append(f"matchers[{i}].op 非法：{op}")
        if op in ("=~", "!~"):
            try:
                re.compile(str(m.get("value", "")))
            except re.error as e:
                errors.append(f"matchers[{i}].value 正则非法：{e}")
    return errors


def _parse_hhmm(s) -> int | None:
    """'HH:MM' → 分钟数；非法返回 None。"""
    if s is None:
        return None
    try:
        h, m = (int(x) for x in str(s).split(":"))
    except (ValueError, AttributeError):
        return None
    if 0 <= h < 24 and 0 <= m < 60:
        return h * 60 + m
    return None


def _days_list(days) -> set[int]:
    """days（list[int|str]）→ 合法星期集合（1=周一…7=周日），非法值忽略。"""
    out: set[int] = set()
    for d in (days or []):
        try:
            n = int(d)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 7:
            out.add(n)
    return out


def validate_recurrence(recurrence) -> str | None:
    """校验 recurrence，返回错误信息；合法返回 None。"""
    rec = parse_recurrence(recurrence)
    rtype = (rec.get("type") or "once").lower()
    if rtype not in RECURRENCE_TYPES:
        return f"recurrence.type 非法：{rec.get('type')}"
    if rtype in ("daily", "weekly"):
        if _parse_hhmm(rec.get("start")) is None or _parse_hhmm(rec.get("end")) is None:
            return "recurrence.start/end 时间格式非法（需 HH:MM）"
    raw_days = rec.get("days")
    if raw_days is not None and not isinstance(raw_days, list):
        return "recurrence.days 必须是数组"
    for d in (raw_days or []):
        try:
            n = int(d)
        except (TypeError, ValueError):
            return "recurrence.days 含非法值"
        if not (1 <= n <= 7):
            return "recurrence.days 含非法值（1~7）"
    return None


def _as_sh(dt: datetime | None) -> datetime | None:
    """把 datetime 归一化为 Asia/Shanghai 时区；naive 视为 SH 本地时间。"""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI_TZ)
    return dt.astimezone(SHANGHAI_TZ)


def _minutes_to_time(minutes: int) -> time:
    return time(minutes // 60, minutes % 60)


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None


def _once_state(sl, now: datetime) -> dict:
    start = _as_sh(getattr(sl, "starts_at", None))
    end = _as_sh(getattr(sl, "ends_at", None))
    if not start or not end:
        return {"active": False, "start": None, "end": None,
                "next_start": None, "next_end": None, "reason": "once_no_window"}
    if start >= end:
        return {"active": False, "start": None, "end": None,
                "next_start": None, "next_end": None, "reason": "once_invalid"}
    if now < start:
        return {"active": False, "start": None, "end": None,
                "next_start": start, "next_end": end, "reason": "once_not_started"}
    if now >= end:
        return {"active": False, "start": None, "end": None,
                "next_start": None, "next_end": None, "reason": "once_ended"}
    return {"active": True, "start": start, "end": end,
            "next_start": start, "next_end": end, "reason": "once_active"}


def _recurring_state(rec: dict, rtype: str, now: datetime) -> dict:
    start_min = _parse_hhmm(rec.get("start"))
    end_min = _parse_hhmm(rec.get("end"))
    days = _days_list(rec.get("days"))
    if start_min is None or end_min is None or start_min == end_min:
        return {"active": False, "start": None, "end": None,
                "next_start": None, "next_end": None, "reason": f"{rtype}_invalid"}
    cross = start_min > end_min  # 跨零点（如 23:00 ~ 02:00）
    start_t = _minutes_to_time(start_min)
    end_t = _minutes_to_time(end_min)
    today = now.date()

    def _day_ok(d):
        if rtype == "weekly" and days:
            return d.isoweekday() in days
        return True

    def _window_for(d):
        s = datetime.combine(d, start_t, tzinfo=SHANGHAI_TZ)
        e_day = d + (timedelta(days=1) if cross else timedelta(0))
        e = datetime.combine(e_day, end_t, tzinfo=SHANGHAI_TZ)
        return s, e

    # 1) 当前窗口：跨零点需回看昨天（昨天开始、今天结束）
    for offset in range(-1, 2):
        d = today + timedelta(days=offset)
        if not _day_ok(d):
            continue
        s, e = _window_for(d)
        if s <= now < e:
            return {"active": True, "start": s, "end": e,
                    "next_start": s, "next_end": e, "reason": f"{rtype}_active"}

    # 2) 未生效：找下一个（含今天尚未开始）
    for offset in range(0, 8):
        d = today + timedelta(days=offset)
        if not _day_ok(d):
            continue
        s, e = _window_for(d)
        if s >= now:
            return {"active": False, "start": None, "end": None,
                    "next_start": s, "next_end": e, "reason": f"{rtype}_inactive"}
    return {"active": False, "start": None, "end": None,
            "next_start": None, "next_end": None, "reason": f"{rtype}_no_upcoming"}


def window_state(silence_like, now: datetime | None = None) -> dict:
    """计算屏蔽条目的生效窗口状态。

    返回 dict：{active, start, end, next_start, next_end, reason}
    - active 时 start/end = 当前窗口；否则 start/end = None，next_start/next_end = 下一个窗口。
    """
    now = _as_sh(now if now is not None else datetime.now())
    rec = parse_recurrence(getattr(silence_like, "recurrence", "{}"))
    rtype = (rec.get("type") or "once").lower()
    if rtype == "once":
        return _once_state(silence_like, now)
    if rtype in ("daily", "weekly"):
        return _recurring_state(rec, rtype, now)
    return _once_state(silence_like, now)


def is_in_window(silence_like, now: datetime | None = None) -> bool:
    """纯函数：当前是否处于生效窗口。"""
    return bool(window_state(silence_like, now)["active"])


def silence_status(silence_like, now: datetime | None = None) -> dict:
    """状态接口返回体：{active, next_start, next_end, reason}。"""
    st = window_state(silence_like, now)
    return {
        "active": st["active"],
        "next_start": _iso(st.get("next_start")),
        "next_end": _iso(st.get("next_end")),
        "reason": st.get("reason", ""),
    }


def matcher_filters(alert_model, matchers) -> list:
    """把 matchers 转成 SQLAlchemy filter 表达式列表（AND 连接，只构造不查询）。

    供预览接口做近 7 天只读聚合；= / != 走普通比较，=~ / !~ 走 MySQL REGEXP。
    cluster_id 为整型列：= / != 按整数比较，正则时 cast 为字符串。
    """
    import sqlalchemy as sa

    conds: list = []
    for m in (matchers or []):
        if not isinstance(m, dict):
            continue
        key = m.get("key", "")
        op = m.get("op", "=")
        val = str(m.get("value", ""))
        col = getattr(alert_model, key, None)
        if col is None:
            continue
        if key == "cluster_id":
            try:
                ival = int(val)
            except ValueError:
                conds.append(sa.false())
                continue
            if op == "=":
                conds.append(col == ival)
            elif op == "!=":
                conds.append(col != ival)
            elif op == "=~":
                conds.append(sa.cast(col, sa.String).op("REGEXP")(val))
            elif op == "!~":
                conds.append(sa.not_(sa.cast(col, sa.String).op("REGEXP")(val)))
            continue
        if op == "=":
            conds.append(col == val)
        elif op == "!=":
            conds.append(col != val)
        elif op == "=~":
            conds.append(col.op("REGEXP")(val))
        elif op == "!~":
            conds.append(sa.not_(col.op("REGEXP")(val)))
    return conds
