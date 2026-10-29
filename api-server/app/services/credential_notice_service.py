# -*- coding: utf-8 -*-
"""凭据到期主动通知（D1，2026-10-29）

现状：集群 kubeconfig 的证书/Token 到期时间已解析入 `clusters.cred_expires_at`，但只在列表里标红。
需求：30 / 7 / 1 天三档主动通知（站内 + IM 按统一通知策略），到期后也要喊一次。

去重：每档只通知一次（Redis `cred_notice:{cluster_id}:{threshold}`，TTL 到该档结束），
换新 kubeconfig 后 `cred_expires_at` 变化 → 用「到期时间戳」进 key，自然重新通知。
"""
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.models import Cluster

logger = get_logger("api-server.credential_notice")

THRESHOLDS = (30, 7, 1)


async def check_credentials() -> int:
    """扫描全部集群凭据，按档位发提醒；返回本轮发出的通知条数"""
    sent = 0
    now = datetime.now()
    async with async_session() as session:
        clusters = list((await session.execute(select(Cluster))).scalars())

    for c in clusters:
        exp = c.cred_expires_at
        if not exp:
            continue
        days_left = (exp - now).total_seconds() / 86400
        for th in THRESHOLDS:
            if days_left > th:
                continue
            key = f"cred_notice:{c.id}:{th}:{int(exp.timestamp())}"
            try:
                if await redis_pool.get(key):
                    continue
            except Exception:  # noqa: BLE001
                pass
            title = f"集群凭据 {th} 天内到期：{c.name}"
            if days_left <= 0:
                title = f"集群凭据已过期：{c.name}"
            content = (
                f"集群「{c.name}」的接入凭据（{c.cred_type or 'kubeconfig'}）"
                f"{'已过期' if days_left <= 0 else f'剩余 {days_left:.1f} 天'}（到期时间 {exp:%Y-%m-%d %H:%M}）。"
                f"请在「集群管理」里一键替换 kubeconfig；改用 Agent 接入可免维护。"
            )
            from app.services import notification_service as ns
            # notify_platform 会发 IM（按已启用渠道）+ 给管理员写站内消息，一次搞完
            try:
                await ns.notify_platform(f"【凭据到期】{content}", at_all=False)
            except Exception as e:  # noqa: BLE001
                logger.debug("凭据提醒推送失败: %s", e)
            try:
                # TTL：到下一档（或过期后 7 天）再重新提醒
                ttl = int(max(3600, (days_left - (th - 1)) * 86400)) if days_left > 1 else 7 * 86400
                await redis_pool.set(key, "1", ex=min(ttl, 30 * 86400))
            except Exception:  # noqa: BLE001
                pass
            sent += 1
            logger.info("凭据到期提醒：集群 %s（%s）≤%d 天", c.name, exp.date(), th)
            break  # 同一集群本轮只发最紧急的一档
    return sent
