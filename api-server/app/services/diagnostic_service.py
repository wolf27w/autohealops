# -*- coding: utf-8 -*-
"""诊断中心执行服务：应用诊断（ExecInPod 采集 → AI 诊断 → 报告）+ 调度"""
import asyncio
import json
from datetime import datetime

from sqlalchemy import select

from app.clients.ai_client import ai_client
from app.clients.executor_client import executor_client
from app.core.database import async_session
from app.core.logging import get_logger
from app.models import DiagnosticReport, DiagnosticTask

logger = get_logger("api-server.diagnostic")


# 应用诊断采集命令（只读白名单内）
DIAG_COMMANDS = [
    "ps aux",
    "env",
    "df -h",
    "free -m",
    "ss -tlnp",
]


async def run_db_diagnostic(task: DiagnosticTask) -> None:
    """数据库诊断：采集慢 SQL → 对 top 慢 SQL 生成 AI 优化建议 → 报告"""
    from app.models import SlowSQLRecord
    from app.services import db_collector

    target = json.loads(task.target or "{}")
    conn_id = target.get("db_connection_id")
    if not conn_id:
        return

    # 1. 采集慢 SQL
    await db_collector.collect_connection_slow_sql(conn_id)

    # 2. 取 top 慢 SQL
    async with async_session() as session:
        result = await session.execute(
            select(SlowSQLRecord).where(SlowSQLRecord.db_connection_id == conn_id)
            .order_by(SlowSQLRecord.avg_latency_ms.desc()).limit(5)
        )
        records = list(result.scalars())

    if not records:
        async with async_session() as session:
            session.add(DiagnosticReport(task_id=task.id, task_type="db",
                conclusion="未采集到慢 SQL（可能无超阈值 SQL 或连接不可用）",
                root_cause="", suggestion="", raw_output=""))
            await session.commit()
        return

    # 3. 对每条慢 SQL 调 AI 生成建议
    analyses = []
    for r in records:
        alert_data = {
            "alert_id": 0,
            "fault_type": "sql_optimization",
            "severity": "high",
            "sql_text": (r.sql_text or "")[:2000],
            "status_info": f"慢SQL 执行{r.exec_count}次 平均{r.avg_latency_ms}ms 最大{r.max_latency_ms}ms",
        }
        context = {"exec_output": f"执行次数:{r.exec_count}, 平均耗时:{r.avg_latency_ms}ms, 最大耗时:{r.max_latency_ms}ms, schema:{r.schema_name}"}
        try:
            diag = await ai_client.diagnose(0, alert_data, context, "")
            analyses.append({
                "sql": (r.sql_text or "")[:300],
                "avg_ms": r.avg_latency_ms,
                "root_cause": diag.get("root_cause", ""),
                "suggestions": diag.get("suggestions", []),
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("慢 SQL %d 优化建议生成失败: %s", r.id, e)
            analyses.append({"sql": (r.sql_text or "")[:300], "avg_ms": r.avg_latency_ms, "error": str(e)})

    # 4. 汇总报告
    conclusion = f"共分析 {len(analyses)} 条慢 SQL"
    root_cause = "\n".join(f"[{a.get('avg_ms', 0)}ms] {a.get('root_cause', '')}" for a in analyses)
    suggestion = "\n".join(
        f"[{a.get('avg_ms', 0)}ms] {a.get('sql', '')[:100]}\n  → {'; '.join(a.get('suggestions', []) or ['无'])}"
        for a in analyses
    )
    raw = json.dumps(analyses, ensure_ascii=False)[:4000]
    async with async_session() as session:
        session.add(DiagnosticReport(task_id=task.id, task_type="db",
            conclusion=conclusion, root_cause=root_cause, suggestion=suggestion, raw_output=raw))
        await session.commit()


async def run_app_diagnostic(task: DiagnosticTask) -> None:
    """应用诊断：ExecInPod 采集 + describe events → AI 诊断 → 报告"""
    target = json.loads(task.target or "{}")
    cluster_id = target.get("cluster_id")
    namespace = target.get("namespace", "")
    pod_name = target.get("pod_name", "")

    # 1. 采集容器内只读命令输出
    exec_outputs = []
    for cmd in DIAG_COMMANDS:
        r = await executor_client.acall(executor_client.exec_in_pod, cluster_id, namespace, pod_name, cmd)
        if r.get("success"):
            exec_outputs.append(f"$ {cmd}\n{r.get('output', '')[:2000]}")
    exec_text = "\n\n".join(exec_outputs) if exec_outputs else "(容器内采集失败或命令无输出)"

    # 2. describe events
    describe = await executor_client.acall(executor_client.execute_command, cluster_id, namespace, f"describe pod {pod_name}")
    events = describe.get("output", "")[:3000] if describe.get("success") else ""

    # 3. 调 AI 诊断（复用 diagnose 接口，通用诊断上下文）
    alert_data = {
        "alert_id": 0,
        "cluster_name": str(cluster_id),
        "namespace": namespace,
        "pod_name": pod_name,
        "fault_type": "application_diagnosis",
        "severity": "high",
        "status_info": f"手动诊断任务 #{task.id}，时间窗 {task.time_window_minutes} 分钟",
        "recent_events": events,
    }
    conclusion, root_cause, suggestion, raw = "", "", "", ""
    try:
        diag = await ai_client.diagnose(0, alert_data, {"exec_output": exec_text}, "")
        conclusion = diag.get("root_cause", "") or diag.get("summary", "")
        root_cause = diag.get("root_cause", "")
        suggestion = "; ".join(diag.get("suggestions", []))
        raw = json.dumps(diag, ensure_ascii=False)[:4000]
    except Exception as e:  # noqa: BLE001
        logger.warning("诊断任务 %d AI 诊断失败: %s", task.id, e)
        raw = f"AI 诊断失败: {e}\n\n采集数据:\n{exec_text}"
        conclusion = "AI 诊断暂不可用（embedding/AI 未配置或诊断接口异常），以下是采集的原始数据"

    async with async_session() as session:
        report = DiagnosticReport(
            task_id=task.id, task_type="app",
            conclusion=conclusion, root_cause=root_cause, suggestion=suggestion,
            raw_output=raw,
        )
        session.add(report)
        await session.commit()


async def run_diagnostic_task(task_id: int) -> None:
    """执行单个诊断任务"""
    async with async_session() as session:
        task = await session.get(DiagnosticTask, task_id)
        if not task:
            return
        task.status = "running"
        task.last_run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await session.commit()
    logger.info("诊断任务 %d 开始执行（类型 %s）", task_id, task.task_type)

    try:
        if task.task_type == "app":
            await run_app_diagnostic(task)
        else:
            await run_db_diagnostic(task)
        status = "success"
    except Exception as e:  # noqa: BLE001
        logger.error("诊断任务 %d 失败: %s", task_id, e)
        status = "failed"

    async with async_session() as session:
        task = await session.get(DiagnosticTask, task_id)
        if task:
            task.status = status
            await session.commit()
    logger.info("诊断任务 %d 完成（%s）", task_id, status)


# 后台诊断任务引用集合（防 GC）
_background_diagnostic_tasks: set = set()


def schedule_run(task_id: int) -> None:
    """异步触发任务执行（不阻塞 API，保持引用防 GC）"""
    try:
        loop = asyncio.get_event_loop()
        t = loop.create_task(run_diagnostic_task(task_id))
        _background_diagnostic_tasks.add(t)
        t.add_done_callback(_background_diagnostic_tasks.discard)
    except RuntimeError:
        asyncio.run(run_diagnostic_task(task_id))


def register_diagnostic_cron(task) -> None:
    """注册诊断任务 cron 调度（复用 apscheduler）"""
    if not task.cron:
        return
    try:
        from apscheduler.triggers.cron import CronTrigger
        from app.services.scheduler_service import scheduler
        trigger = CronTrigger.from_crontab(task.cron)
        job_id = f"diagnostic_task_{task.id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        scheduler.add_job(run_diagnostic_task, trigger, args=[task.id], id=job_id,
                          coalesce=True, max_instances=1, misfire_grace_time=300)
    except Exception as e:  # noqa: BLE001
        logger.warning("诊断任务 %d cron 注册失败: %s", task.id, e)


def unregister_diagnostic_cron(task_id: int) -> None:
    """注销诊断任务 cron 调度"""
    try:
        from app.services.scheduler_service import scheduler
        job_id = f"diagnostic_task_{task_id}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("诊断任务 %d cron 注销失败: %s", task_id, e)


async def load_diagnostic_cron() -> None:
    """启动时加载存量定时诊断任务到 apscheduler"""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(DiagnosticTask).where(DiagnosticTask.cron != "")
            )
            tasks = list(result.scalars())
        for t in tasks:
            register_diagnostic_cron(t)
        if tasks:
            logger.info("加载 %d 个定时诊断任务", len(tasks))
    except Exception as e:  # noqa: BLE001
        logger.warning("加载诊断 cron 任务失败: %s", e)
