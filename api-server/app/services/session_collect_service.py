# -*- coding: utf-8 -*-
"""会话「定时采集」后台服务（老板 2026-10-16）

针对诊断会话绑定的容器，按会话配置的间隔周期采集信息（当前支持 JVM 综合诊断），
把结果作为 assistant 消息追加到会话里，便于在应用诊断页持续观察。

- 开关/间隔/采集项存在 diagnosis_sessions（collect_enabled/collect_interval_min/collect_items）。
- 每 60s 巡检一次到期会话；先推进 collect_last_at 再采集，避免重复触发。
- 采集项 collect_items 为 CSV，目前实现 jvm（JVM 综合采集+AI），预留 logs/metrics 等。
"""
import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import DiagnosisMessage, DiagnosisSession

logger = get_logger("api-server.session_collect")

_MAX_CONCURRENCY = 2  # 同一轮最多并发采集的会话数（避免同时 exec 过多 Pod）


async def _collect_one(sess_id: int) -> None:
    """采集单个会话并追加消息。"""
    async with async_session() as s:
        sess = await s.get(DiagnosisSession, sess_id)
        if not sess or not sess.collect_enabled:
            return
        cluster_id, namespace, pod_name = sess.cluster_id, sess.namespace, sess.pod_name
        container = sess.container or ""
    ts = datetime.now().strftime("%m-%d %H:%M")
    content: str
    try:
        from app.api.jvm import collect_and_analyze  # 延迟导入，避免加载顺序耦合
        res = await collect_and_analyze(cluster_id, namespace, pod_name, container)
        a = res.get("analysis")
        if a:
            content = json.dumps({
                "root_cause": f"[定时采集 {ts}] " + (a.get("root_cause") or ""),
                "suggestions": a.get("suggestions") or [],
                "risk_level": a.get("risk_level"), "confidence": a.get("confidence"),
                "model_used": a.get("model_used"), "handling_advice": "", "kb_hit": False,
            }, ensure_ascii=False)
        else:
            content = json.dumps({
                "root_cause": f"[定时采集 {ts}] 本次采集未得到结论：{res.get('message') or '无数据'}",
                "suggestions": [], "risk_level": "low", "confidence": 0.0,
            }, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("定时采集失败 session=%s: %s", sess_id, e)
        content = json.dumps({
            "root_cause": f"[定时采集 {ts}] 采集异常：{e}", "suggestions": [],
            "risk_level": "low", "confidence": 0.0,
        }, ensure_ascii=False)
    async with async_session() as s:
        s.add(DiagnosisMessage(session_id=sess_id, role="assistant", content=content))
        await s.commit()


async def _sweep() -> None:
    now = datetime.now()
    async with async_session() as s:
        rows = (await s.execute(
            select(DiagnosisSession).where(DiagnosisSession.collect_enabled.is_(True))
        )).scalars().all()
    due: list[int] = []
    for sess in rows:
        interval = max(1, sess.collect_interval_min or 5)
        last = sess.collect_last_at
        if last and (now - last) < timedelta(minutes=interval):
            continue
        due.append(sess.id)
    if not due:
        return
    # 先推进 last_at（防重）
    async with async_session() as s:
        for sid in due:
            obj = await s.get(DiagnosisSession, sid)
            if obj:
                obj.collect_last_at = now
        await s.commit()
    logger.info("定时采集：本轮 %d 个会话到期", len(due))
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _run(sid: int) -> None:
        async with sem:
            await _collect_one(sid)

    await asyncio.gather(*[_run(sid) for sid in due])


async def background_loop() -> None:
    """每 60 秒巡检一次到期的定时采集会话。"""
    await asyncio.sleep(45)
    while True:
        try:
            await _sweep()
        except Exception as e:  # noqa: BLE001
            logger.warning("定时采集循环异常: %s", e)
        await asyncio.sleep(60)
