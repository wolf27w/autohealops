# -*- coding: utf-8 -*-
"""告警正文分段渲染（2026-11-24）。

背景：`alerts.message` 被多处协程各自 append（发布变更、自愈结论、回滚留痕、恢复关闭…），
并发时后写覆盖前写 → 前端看到的信息时有时无。老板要求「正文只由审计渲染，避免抢写」。

做法（不新增表、不迁移）：
- 正文按【段】组织：每个来源只负责自己的那一段，**幂等覆盖**而不是追加；
- 写入走 `set_section()`，内部按 alert 行加锁（SELECT ... FOR UPDATE）串行化，
  避免并发丢更新；
- 段名固定（发布变更 / 处置留痕 / 处置结果），同段重复调用结果一致。

审计记录（audit_records）仍是完整事实源；正文只是它的可读渲染。
"""
from __future__ import annotations

import re

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Alert

logger = get_logger("api-server.alert_message")

# 段名 → 正则（匹配「【段名】…」整行，行内不跨行）
def _section_re(name: str) -> re.Pattern:
    return re.compile(rf"^【{re.escape(name)}】.*$", re.MULTILINE)


def upsert_section(message: str, name: str, content: str) -> str:
    """幂等设置正文中的某个【段】：存在则整行替换，不存在则追加到末尾。"""
    msg = message or ""
    line = f"【{name}】{content}"
    rx = _section_re(name)
    if rx.search(msg):
        return rx.sub(lambda _m: line, msg, count=1)
    return (msg.rstrip() + "\n" + line) if msg.strip() else line


def get_section(message: str, name: str) -> str:
    """读取某段内容（不含段名前缀）；不存在返回空串"""
    m = _section_re(name).search(message or "")
    if not m:
        return ""
    return m.group(0)[len(f"【{name}】"):].strip()


def strip_sections(message: str, names: list[str]) -> str:
    """去掉指定段（用于取「基础描述」）"""
    msg = message or ""
    for name in names:
        msg = _section_re(name).sub("", msg)
    return msg.strip()


async def set_section(alert_id: int, name: str, content: str) -> str:
    """带行锁地写入一段正文（并发安全），返回写入后的完整正文（失败返回空串）。

    行锁（with_for_update）保证同一告警的多处写入串行，避免后写覆盖前写。
    """
    if not alert_id:
        return ""
    try:
        async with async_session() as session:
            a = await session.get(Alert, int(alert_id), with_for_update=True)
            if a is None:
                return ""
            a.message = upsert_section(a.message or "", name, content)
            new_msg = a.message
            await session.commit()
            return new_msg
    except Exception as e:  # noqa: BLE001
        logger.warning("告警 %s 正文段落【%s】写入失败: %s", alert_id, name, e)
        return ""
