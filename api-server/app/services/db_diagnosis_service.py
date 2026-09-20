# -*- coding: utf-8 -*-
"""数据库诊断服务：采集健康指标→落趋势表→AI 分析→报告→沉淀知识库（只给方案不执行）

流程：
1. 只读账号实时采集（连接数/锁等待/表空间）+ 慢SQL
2. 落 db_metric_snapshots 趋势表（多次诊断形成趋势）
3. 汇总上下文 → ai-service /db-diagnose 分析（红线：只给方案）
4. 落 DiagnosticReport（task_id=0，诊断中心按需触发，非定时任务）
5. 结论沉淀知识库（fault_type=db_ 前缀，source=ai 锁定）
"""
import json
import time

from sqlalchemy import select

from app.clients.ai_client import ai_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.models import DBConnection, DiagnosticReport, SlowSQLRecord
from app.services import knowledge_service

logger = get_logger("api-server.db_diagnosis")


def _format_health(metrics: list[dict]) -> str:
    """健康指标 → 可读文本（供 AI 分析）"""
    lines = []
    for m in metrics:
        detail = ""
        if m.get("detail"):
            try:
                detail = f" 明细:{m['detail'][:300]}"
            except (TypeError, KeyError):
                detail = ""
        lines.append(f"- {m['metric_name']}={m['value']}{detail}")
    return "\n".join(lines) or "（无健康指标采集到）"


async def _format_slow_sql(conn_id: int, limit: int = 10) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(SlowSQLRecord)
            .where(SlowSQLRecord.db_connection_id == conn_id)
            .order_by(SlowSQLRecord.avg_latency_ms.desc())
            .limit(limit)
        )
        records = list(result.scalars())
    if not records:
        return "（无慢SQL记录）"
    return "\n".join(
        f"- {r.schema_name or '-'} | exec={r.exec_count} avg={r.avg_latency_ms}ms max={r.max_latency_ms}ms | {r.sql_text[:200]}"
        for r in records
    )


async def diagnose_db(conn_id: int, user=None) -> dict:
    """执行数据库诊断（按需触发）"""
    async with async_session() as session:
        c = await session.get(DBConnection, conn_id)
        if not c:
            return {"code": 404, "message": "数据库连接不存在", "data": None}

    # 巡检开关（按库）：关闭则不执行诊断
    if not c.inspect_enabled:
        return {"code": 400, "message": "该数据库巡检开关已关闭", "data": None}

    # 1. 采集健康指标（只读账号，落趋势表）
    from app.services.db_collector import collect_db_health, collect_connection_slow_sql
    metrics = await collect_db_health(conn_id)
    try:
        await collect_connection_slow_sql(conn_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %d 慢SQL采集异常: %s", conn_id, e)

    health_text = _format_health(metrics)
    slow_sql_text = await _format_slow_sql(conn_id)

    # 2. AI 分析（只给方案）
    try:
        data = await ai_client._post("/api/v1/ai/db-diagnose", {
            "db_type": c.db_type, "db_name": c.database,
            "health": health_text, "slow_sql": slow_sql_text,
            "severity": "medium",
        })
        conclusion = data.get("conclusion", "")
        root_cause = data.get("root_cause", "")
        suggestions = data.get("suggestions", [])
        raw = data.get("raw", "")
    except Exception as e:  # noqa: BLE001
        logger.error("数据库 %d AI 诊断失败: %s", conn_id, e)
        return {"code": 500, "message": f"AI 诊断失败: {e}", "data": None}

    # 3. 落报告（task_id=0：按需诊断，非定时任务）
    summary = f"db:{c.name} {c.db_type}"
    async with async_session() as session:
        report = DiagnosticReport(
            task_id=0,
            task_type="db",
            conclusion=conclusion or "诊断完成（无明确结论）",
            root_cause=root_cause,
            suggestion="\n".join(suggestions) if suggestions else "",
            raw_output=json.dumps({
                "db_name": c.name, "db_type": c.db_type,
                "health": health_text, "slow_sql": slow_sql_text[:2000],
                "ai_raw": raw[:2000], "summary": summary,
            }, ensure_ascii=False),
        )
        session.add(report)
        await session.commit()
        await session.refresh(report)
        report_id = report.id

    # 4. 沉淀知识库（db_ 前缀，AI 生成锁定；只存结论+方案，不含执行）
    try:
        fault_type = f"db_{c.db_type}"
        await knowledge_service.add_knowledge(
            fault_type=fault_type,
            root_cause=root_cause or "数据库健康问题",
            repair_steps=conclusion or "",
            tags=f"db,{c.db_type}",
            execution_steps="",
            source="ai", is_locked=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库诊断结论沉淀知识库失败: %s", e)

    return {"code": 200, "message": "success", "data": {
        "report_id": report_id, "conclusion": conclusion,
        "root_cause": root_cause, "suggestions": suggestions,
        "health": metrics,
    }}


# ============ 定时巡检调度（APScheduler，可设置定期，默认不定时） ============

async def _run_db_inspect_job(conn_id: int) -> None:
    """定时巡检执行入口（Redis 锁防多副本重复）"""
    from app.core.redis_client import acquire_lock, redis_pool, release_lock
    lock_key = f"lock:db_inspect:{conn_id}"
    val = await acquire_lock(redis_pool, lock_key, 180000)
    if not val:
        return
    try:
        async with async_session() as session:
            c = await session.get(DBConnection, conn_id)
            if not c or not c.inspect_enabled or not (c.inspect_cron or "").strip():
                return
        await diagnose_db(conn_id, None)  # 系统定时触发
        logger.info("数据库 %d 定时巡检完成", conn_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %d 定时巡检失败: %s", conn_id, e)
    finally:
        await release_lock(redis_pool, lock_key, val)


def register_db_inspect(c: DBConnection) -> None:
    """注册/刷新数据库定时巡检任务（inspect_cron 为空则注销）"""
    from apscheduler.triggers.cron import CronTrigger
    from app.services.scheduler_service import scheduler
    job_id = f"db_inspect_{c.id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    if not c.inspect_enabled or not (c.inspect_cron or "").strip():
        return
    try:
        trigger = CronTrigger.from_crontab(c.inspect_cron.strip())
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库 %d 巡检 cron 非法: %s", c.id, e)
        return
    scheduler.add_job(_run_db_inspect_job, trigger, args=[c.id], id=job_id,
                      coalesce=True, max_instances=1, misfire_grace_time=120)


def unregister_db_inspect(conn_id: int) -> None:
    from app.services.scheduler_service import scheduler
    job_id = f"db_inspect_{conn_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def load_all_db_inspects() -> None:
    """启动时加载所有定时巡检任务"""
    async with async_session() as session:
        rows = list((await session.execute(
            select(DBConnection).where(DBConnection.inspect_enabled.is_(True))
        )).scalars())
    n = 0
    for c in rows:
        if (c.inspect_cron or "").strip():
            register_db_inspect(c)
            n += 1
    logger.info("已加载 %d 个数据库定时巡检任务", n)
