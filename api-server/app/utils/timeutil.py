# -*- coding: utf-8 -*-
"""时间工具：把前端传来的 ISO 时间（通常带 Z / UTC 偏移）转成「库内存储的本地 naive 时间」。

背景（2026-11-26 老板反馈「数据库管理里慢 SQL 日志没有了」）：
前端 `dayjs().toISOString()` 发的是 UTC（如 08:43Z），而库里
`slow_sql_events.start_time` / `slow_sql_records.sampled_at` 存的是**本地时间**（如 16:43），
直接拿 UTC 去比会差 8 小时 → 「最近 1 小时」查询恒为空，看起来像功能丢了。

统一口径：**DB 里的时间列都是本地 naive（Asia/Shanghai）**，API 入参若带时区一律先换算。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_NAME = "Asia/Shanghai"
LOCAL_TZ = ZoneInfo(TZ_NAME)


def to_local_naive(dt: datetime | None) -> datetime | None:
    """带时区的 datetime → 本地 naive；已经是 naive 的原样返回（视为本地时间）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(LOCAL_TZ).replace(tzinfo=None)


def parse_iso_local(v: str) -> datetime | None:
    """ISO 字符串 → 本地 naive datetime；空串返回 None；格式错误抛 ValueError。"""
    if not v:
        return None
    return to_local_naive(datetime.fromisoformat(v.replace("Z", "+00:00")))
