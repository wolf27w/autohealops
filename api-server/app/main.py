# -*- coding: utf-8 -*-
"""AutoHealOps 平台入口（唯一对外 API 入口）"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.api import (
    agent, ai_proxy, alert_policies, alert_silences, alerts, approvals, approval_flows, audit, auth, batch, clusters, custom_tasks, db_connections,
    db_diagnosis, decisions, diagnostic, executions, external_access, fault_diagnosis, im_groups, in_app_messages, inspections, knowledge, log_alert_rules, log_sources, logs, mcp, monitoring, notifications, on_duty, permissions,
    projects, repair_records, report_schedules, resource_alert_rules, resource_governance, resources, self_heal_rules, stats, tasks, terminal, tickets, users, watch, webhooks,
)
from app.api import hosts, jvm
from app.api import settings as settings_api
from app.core.config import settings
from app.core.database import async_session
from app.core.logging import get_logger
from app.core import orm_hooks  # noqa: F401  注册仪表盘缓存即时失效钩子（导入即生效）
from app.core.security import generate_trace_id, hash_password
from app.models import User

logger = get_logger("api-server.main")


async def seed_default_users() -> None:
    """初始化默认用户 admin/operator/viewer

    安全：不再使用硬编码弱口令。admin 密码优先取 ADMIN_INITIAL_PASSWORD（环境变量），
    否则生成随机强密码；operator/viewer 一律随机密码。
    初始密码仅打印到日志一次（仅当用户为新创建时），提示首次登录后立即修改。
    """
    import secrets

    def _random_pw() -> str:
        return secrets.token_urlsafe(12)

    defaults = [
        ("admin", (settings.ADMIN_INITIAL_PASSWORD or _random_pw()), "admin"),
        ("operator", _random_pw(), "operator"),
        ("viewer", _random_pw(), "viewer"),
    ]
    async with async_session() as session:
        result = await session.execute(select(User))
        existing = {u.username for u in result.scalars()}
        for username, password, role in defaults:
            if username in existing:
                continue
            session.add(User(username=username, password_hash=hash_password(password), role=role))
            logger.warning("已创建默认用户 %s，初始密码: %s（请首次登录后立即修改）", username, password)
        await session.commit()


async def _interval_worker(name: str, interval: float, fn, timeout: float | None) -> None:
    """独立后台任务：按固定间隔调度 fn，单次执行加超时隔离，异常自愈（不阻塞其他任务）"""
    import asyncio
    while True:
        await asyncio.sleep(interval)
        try:
            if timeout:
                await asyncio.wait_for(fn(), timeout=timeout)
            else:
                await fn()
        except asyncio.TimeoutError:
            logger.warning("%s 执行超时（>%.0fs），中断本轮，下轮继续", name, timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning("%s 异常: %s", name, e)


async def _maintenance_loop() -> None:
    """周期维护任务：慢 SQL 采集（每 5 分钟）+ 资源快照清理（每日）"""
    import asyncio
    from app.services.db_collector import scan_all_connections
    loop_count = 0
    while True:
        await asyncio.sleep(30)
        loop_count += 1
        # 慢 SQL + 健康指标采集：每 1 分钟（2 个 30s 周期）
        if loop_count % 2 == 0:
            try:
                n = await scan_all_connections()
                if n:
                    logger.info("慢 SQL 采集完成：%d 条", n)
            except Exception as e:  # noqa: BLE001
                logger.warning("慢SQL采集 异常: %s", e)
        # 资源快照清理：首次启动清理一次 + 每天一次（2880 个 30s 周期）
        if loop_count == 1 or loop_count % 2880 == 0:
            try:
                from app.services.resource_service import cleanup_old_snapshots, maintain_snapshot_partitions
                from app.services.system_setting_service import get_setting
                try:
                    _days = int(await get_setting("snapshot_retention_days", "7") or 7)
                except (TypeError, ValueError):
                    _days = 7
                # 分区维护：优先 DROP 过期分区（秒级 + 回收磁盘）+ 补齐未来分区
                try:
                    part = await maintain_snapshot_partitions(max(1, _days))
                    if part.get("partitioned"):
                        logger.info("资源快照分区维护：新增 %d 个 / DROP %d 个过期分区 %s",
                                    part.get("added", 0), len(part.get("dropped") or []), part.get("dropped") or [])
                except Exception as e:  # noqa: BLE001
                    logger.warning("资源快照分区维护 异常: %s", e)
                n = await cleanup_old_snapshots(max(1, _days))
                if n:
                    logger.info("资源快照清理完成（保留 %d 天）：删除 %d 条历史记录", _days, n)
            except Exception as e:  # noqa: BLE001
                logger.warning("资源快照清理 异常: %s", e)
            # 巡检/诊断报告保留期（可配，默认 30 天）
            try:
                from app.services.inspection_service import cleanup_old_inspection_reports
                from app.services.system_setting_service import get_setting
                try:
                    _inspect_days = int(await get_setting("inspection_retention_days", "30") or 30)
                except (TypeError, ValueError):
                    _inspect_days = 30
                n = await cleanup_old_inspection_reports(max(1, _inspect_days))
                if n:
                    logger.info("巡检报告清理完成：删除 %d 条超过保留期的报告", n)
            except Exception as e:  # noqa: BLE001
                logger.warning("巡检报告清理 异常: %s", e)
            # 日志类数据保留期（可配，默认 30 天；审计永久保留不删）
            try:
                from app.services.data_retention_service import cleanup_logs
                from app.services.system_setting_service import get_setting
                try:
                    _log_days = int(await get_setting("log_retention_days", "30") or 30)
                except (TypeError, ValueError):
                    _log_days = 30
                r = await cleanup_logs(max(1, _log_days))
                if any(r.values()):
                    logger.info("日志保留清理完成：%s", r)
            except Exception as e:  # noqa: BLE001
                logger.warning("日志保留清理 异常: %s", e)


# 后台循环 task（模块级引用，防 GC）
_background_tasks = []


async def _auto_setup_rbac_on_startup() -> None:
    """启动时自动接入存量集群的云原生 RBAC（未接入的自动补上，无需人工触发）"""
    import asyncio
    from app.models import Cluster
    from app.services import rbac_service
    await asyncio.sleep(3)  # 等 executor 客户端池就绪
    try:
        async with async_session() as session:
            result = await session.execute(select(Cluster).where(Cluster.rbac_mode == "off"))
            clusters = list(result.scalars())
        for c in clusters:
            try:
                await rbac_service.setup_cluster_rbac(c.id)
                logger.info("存量集群 %d 已自动接入 RBAC", c.id)
            except Exception as e:  # noqa: BLE001
                logger.warning("存量集群 %d 自动接入 RBAC 失败: %s", c.id, e)
    except Exception as e:  # noqa: BLE001
        logger.warning("启动时 RBAC 自动接入失败: %s", e)


async def startup_tasks() -> None:
    """启动时的异步初始化任务"""
    global _background_task
    from app.services import scheduler_service

    await seed_default_users()
    from app.api.alert_policies import seed_default_policies as seed_alert_policies
    await seed_alert_policies()
    await scheduler_service.load_all_tasks()
    scheduler_service.start()

    # 报表定时推送（C4）
    from app.services import report_service as _report_svc
    await _report_svc.load_all_schedules()

    # 定时巡检（集群 + 数据库，可设置定期，默认不定时）
    from app.services import inspection_service as _inspect_svc
    from app.services import db_diagnosis_service as _db_svc
    await _inspect_svc.load_all_cluster_inspects()
    await _db_svc.load_all_db_inspects()

    # 加载存量定时诊断任务
    from app.services import diagnostic_service as _diag_svc
    await _diag_svc.load_diagnostic_cron()

    # 启动长连接客户端（钉钉 Stream / 飞书长连接，transport_mode=long_conn 的渠道）
    try:
        from app.services import long_conn_client
        await long_conn_client.start_long_conn_clients()
        # 全局飞书长连接（凭证取系统设置，审批闭环：群内回复/按钮 → 事件回调）
        await long_conn_client.start_feishu_long_conn_global()
    except Exception as e:  # noqa: BLE001
        logger.warning("长连接客户端启动失败: %s", e)

    # 后台定时任务拆分：审批超时 / 告警自动处理 / 日志告警扫描 各自独立循环（互不阻塞），
    # 慢 SQL 采集 + 快照清理 走独立维护循环
    import asyncio
    from app.services.approval_service import timeout_handler
    from app.services.alert_auto_service import auto_process_alerts
    from app.services.log_alert_service import scan_log_alerts
    from app.services.ticket_service import auto_close_tickets_for_resolved_alerts
    from app.services.repair_plan_service import close_stale_repair_plans
    for name, fn, timeout in (
        ("审批超时", timeout_handler, 60.0),
        ("告警自动处理", auto_process_alerts, 150.0),
        ("日志告警扫描", scan_log_alerts, 120.0),
        ("告警恢复自动关单", auto_close_tickets_for_resolved_alerts, 60.0),
        # 僵尸自愈计划收尾：告警已结束但计划还挂在 pending/executing → cancelled（每 5 秒兜底；
        # ORM 写入路径已由 orm_hooks 的告警状态钩子即时取消，这里只覆盖批量 update() 等绕过 ORM 的路径）
        ("僵尸计划收尾", close_stale_repair_plans, 120.0),
    ):
        _background_tasks.append(asyncio.create_task(_interval_worker(name, 5 if name == "僵尸计划收尾" else 30, fn, timeout)))
    _background_tasks.append(asyncio.create_task(_maintenance_loop()))
    # 仪表盘预热（每 10 秒）：把默认「实时」视图预先算好写缓存，前端轮询不再踩冷路径
    from app.api.stats import prewarm_dashboard
    _background_tasks.append(asyncio.create_task(_interval_worker("仪表盘预热", 10, prewarm_dashboard, 60.0)))
    logger.info("后台定时任务已拆分启动：审批超时/告警自动处理/日志告警扫描/告警恢复自动关单（每 30 秒，独立隔离）+ 僵尸自愈计划收尾（每 5 秒兜底 + 告警变更即触发）+ 周期维护 + 仪表盘预热（每 10 秒）")

    # 云原生 RBAC：启动时自动接入存量集群（未接入的自动补上，无需人工触发）
    asyncio.create_task(_auto_setup_rbac_on_startup())


    # 容器资源阈值告警扫描（Prometheus，每 60 秒）
    try:
        from app.services.resource_alert_service import background_loop as resource_alert_loop
        _resource_alert_task = asyncio.create_task(resource_alert_loop())
        logger.info("容器资源告警扫描已启动（每 60 秒，Prometheus 数据源）")
    except Exception as e:  # noqa: BLE001
        logger.warning("容器资源告警扫描启动失败: %s", e)

    # 节点恢复自动 uncordon（每 5 分钟：只解平台自己 cordon 的节点，Ready 且无压力条件才解）
    try:
        from app.services.node_diagnosis_service import uncordon_ready_nodes
        _background_tasks.append(asyncio.create_task(_interval_worker("节点恢复 uncordon", 300, uncordon_ready_nodes, 120.0)))
        logger.info("节点恢复自动 uncordon 已启动（每 5 分钟，仅处理平台 cordon 的节点）")
    except Exception as e:  # noqa: BLE001
        logger.warning("节点 uncordon 扫描启动失败: %s", e)

    # 恢复收敛扫描（每 60 秒：待处理/审批中的 Pod 告警，资源恢复即关闭）
    try:
        from app.services.alert_auto_service import recovery_sweep_loop
        _recovery_task = asyncio.create_task(recovery_sweep_loop())
        logger.info("恢复收敛扫描已启动（每 60 秒：资源恢复 → 待处理/审批中告警自动关闭）")
    except Exception as e:  # noqa: BLE001
        logger.warning("恢复收敛扫描启动失败: %s", e)

    # 告警爆发汇总（每 60 秒：未解决告警 ≥ 10 条时推汇总到全局告警群，无告警不发）
    try:
        from app.services.notification_service import alert_digest_sweep_loop
        _alert_digest_task = asyncio.create_task(alert_digest_sweep_loop())
        logger.info("告警爆发汇总扫描已启动（每 60 秒：未解决 ≥ 10 条时推送汇总）")
    except Exception as e:  # noqa: BLE001
        logger.warning("告警爆发汇总扫描启动失败: %s", e)

    # 数据库连接故障告警扫描（每 60 秒：连接失败/认证失败 → 告警 → 统一告警策略）
    try:
        from app.services.db_alert_service import background_loop as db_alert_loop
        _db_alert_task = asyncio.create_task(db_alert_loop())
        logger.info("数据库连接告警扫描已启动（每 60 秒，只读探活，只给方案不执行修改）")
    except Exception as e:  # noqa: BLE001
        logger.warning("数据库连接告警扫描启动失败: %s", e)

    # Pod 重启原因 AI 诊断告警扫描（N5：每 60 秒，重启增量 → OOM/探针 → 诊断+通知，不审批）
    try:
        from app.services.pod_restart_service import background_loop as pod_restart_loop
        _pod_restart_task = asyncio.create_task(pod_restart_loop())
        logger.info("Pod 重启原因诊断扫描已启动（每 60 秒，OOM/探针失败 → AI 诊断 → 通知，不审批）")
    except Exception as e:  # noqa: BLE001
        logger.warning("Pod 重启原因诊断扫描启动失败: %s", e)

    # 主动诊断（应用巡检）：持续高水位/频繁重启但无告警 → 自动 AI 全面诊断（不建告警）
    try:
        from app.services.proactive_diagnosis_service import background_loop as proactive_loop
        _proactive_task = asyncio.create_task(proactive_loop())
        logger.info("主动诊断（应用巡检）已启动（默认每 30 分钟，可配开关/间隔）")
    except Exception as e:  # noqa: BLE001
        logger.warning("主动诊断巡检启动失败: %s", e)

    # executor 存活探测 + 掉线告警（M2；每 60 秒，状态翻转才通知）
    try:
        from app.services.executor_health_service import background_loop as executor_health_loop
        _exec_health_task = asyncio.create_task(executor_health_loop())
        logger.info("executor 掉线探测已启动（每 60 秒，状态翻转才通知）")
    except Exception as e:  # noqa: BLE001
        logger.warning("executor 掉线探测启动失败: %s", e)

    # AI 用量异常告警（M12；每小时巡检，单日成本/调用量突增才通知）
    try:
        from app.services.ai_usage_service import background_loop as ai_usage_loop
        _ai_usage_task = asyncio.create_task(ai_usage_loop())
        logger.info("AI 用量异常巡检已启动（每小时，成本/调用量突增告警）")
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 用量异常巡检启动失败: %s", e)

    # 主机/虚拟机进程监控（N11；每 60 秒，进程掉线/恢复通知闭环）
    try:
        from app.services.host_monitor_service import background_loop as host_monitor_loop
        _host_monitor_task = asyncio.create_task(host_monitor_loop())
        logger.info("主机/虚拟机进程监控已启动（每 60 秒）")
    except Exception as e:  # noqa: BLE001
        logger.warning("主机/虚拟机进程监控启动失败: %s", e)

    # 会话定时采集（应用诊断：针对绑定的容器周期采集信息追加到会话；默认全关，按会话开关）
    try:
        from app.services.session_collect_service import background_loop as session_collect_loop
        _session_collect_task = asyncio.create_task(session_collect_loop())
        logger.info("会话定时采集已启动（每 60 秒巡检到期会话）")
    except Exception as e:  # noqa: BLE001
        logger.warning("会话定时采集启动失败: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭事件"""
    logger.info("AutoHealOps 平台启动中...")
    # 数据库表结构由 alembic 管理（部署时执行 alembic upgrade head），启动时只做初始化
    await startup_tasks()
    # R12：启动 Agent 反向隧道网关（agent 主动外连接入）
    try:
        from app import agent_gateway
        agent_gateway.start_server(getattr(settings, "AGENT_GATEWAY_ADDR", "0.0.0.0:9095"))
    except Exception as e:  # noqa: BLE001
        logger.warning("Agent 网关启动失败: %s", e)
    logger.info("AutoHealOps 平台启动完成")
    yield
    # 关闭共享 HTTP 连接池
    from app.core.http_client import close_http_clients
    await close_http_clients()
    try:
        from app import agent_gateway
        agent_gateway.stop_server()
    except Exception:  # noqa: BLE001
        pass
    logger.info("AutoHealOps 平台已停止")


app = FastAPI(title="AutoHealOps平台", version="1.0.0", lifespan=lifespan)

# CORS（显式白名单；不可 allow_origins=* 且 credentials=True）
_cors_origins = [o.strip() for o in (settings.CORS_ORIGINS or "").split(",") if o.strip()]
if not _cors_origins:
    _cors_origins = ["http://localhost", "http://localhost:5173", "http://127.0.0.1", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_log_middleware(request: Request, call_next):
    """请求日志中间件：trace_id 贯穿"""
    trace_id = request.headers.get("X-Trace-Id") or generate_trace_id()
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Trace-Id"] = trace_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """统一异常处理：内部细节只进日志，不回显给客户端"""
    logger.error("unhandled error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误", "data": None},
    )


@app.get("/health")
async def health_check():
    """存活探针（liveness）：只判进程存活，不查任何外部依赖，永远快速返回"""
    return {"code": 200, "message": "success", "data": {"status": "ok", "version": "1.0.0"}}


@app.get("/ready")
async def readiness_check():
    """就绪探针（readiness）：依赖（数据库 / Redis）可用才 200，否则 503（不接流量、不重启）

    依赖探测逻辑与 /stats/platform-health 共用 platform_health_service.check_dependencies()（去重）。
    """
    from app.services.platform_health_service import check_dependencies

    dep = await check_dependencies()
    ok = dep["database"] and dep["redis"]
    return JSONResponse(status_code=200 if ok else 503, content={
        "code": 200 if ok else 503,
        "message": "success" if ok else "dependencies unavailable",
        "data": {"status": "ready" if ok else "not_ready", "database": dep["database"], "redis": dep["redis"]},
    })


@app.get("/metrics")
async def metrics():
    """平台自监控 /metrics（Prometheus 文本格式，无鉴权，供监控系统抓取）

    输出平台自身探活指标：集群健康数、待处理告警、待审批、AI 成本、告警状态分布。
    """
    from fastapi.responses import PlainTextResponse
    from sqlalchemy import func, select
    from app.models import Alert, Approval, Cluster

    async with async_session() as session:
        cluster_total = (await session.execute(select(func.count()).select_from(Cluster))).scalar() or 0
        cluster_healthy = (await session.execute(select(func.count()).select_from(Cluster).where(Cluster.status == "healthy"))).scalar() or 0
        alert_pending = (await session.execute(select(func.count()).select_from(Alert).where(Alert.status == "pending"))).scalar() or 0
        alert_awaiting = (await session.execute(select(func.count()).select_from(Alert).where(Alert.status == "awaiting_approval"))).scalar() or 0
        approval_pending = (await session.execute(select(func.count()).select_from(Approval).where(Approval.status == "pending"))).scalar() or 0

    lines = [
        "# HELP apiops_cluster_total 集群总数",
        "# TYPE apiops_cluster_total gauge",
        f"apiops_cluster_total {cluster_total}",
        "# HELP apiops_cluster_healthy 健康集群数",
        "# TYPE apiops_cluster_healthy gauge",
        f"apiops_cluster_healthy {cluster_healthy}",
        "# HELP apiops_alert_pending 待处理告警数",
        "# TYPE apiops_alert_pending gauge",
        f"apiops_alert_pending {alert_pending}",
        "# HELP apiops_alert_awaiting 待审批告警数",
        "# TYPE apiops_alert_awaiting gauge",
        f"apiops_alert_awaiting {alert_awaiting}",
        "# HELP apiops_approval_pending 待审批单数",
        "# TYPE apiops_approval_pending gauge",
        f"apiops_approval_pending {approval_pending}",
        "# HELP apiops_up 平台存活",
        "# TYPE apiops_up gauge",
        "apiops_up 1",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


# 注册所有 API 路由
app.include_router(auth.router)
app.include_router(ai_proxy.router)  # AI 服务代理（需在具体路由之后注册，避免覆盖）
app.include_router(clusters.router)
app.include_router(monitoring.router)
app.include_router(alerts.router)
app.include_router(alert_policies.router)
app.include_router(alert_silences.router)
app.include_router(self_heal_rules.router)
app.include_router(tasks.router)
app.include_router(approvals.router)
app.include_router(approval_flows.router)
app.include_router(im_groups.router)
app.include_router(webhooks.router)
app.include_router(decisions.router)
app.include_router(batch.router)
app.include_router(executions.router)
app.include_router(custom_tasks.router)
app.include_router(resources.router)
app.include_router(jvm.router)
app.include_router(resource_alert_rules.router)
app.include_router(resource_governance.router)
app.include_router(knowledge.router)
app.include_router(audit.router)
app.include_router(stats.router)
app.include_router(report_schedules.router)
app.include_router(users.router)
app.include_router(notifications.router)
app.include_router(in_app_messages.router)
app.include_router(on_duty.router)
app.include_router(logs.router)
app.include_router(watch.router)
app.include_router(inspections.router)
app.include_router(repair_records.router)
app.include_router(log_alert_rules.router)
app.include_router(log_sources.router)
app.include_router(tickets.router)
app.include_router(external_access.router)
app.include_router(settings_api.router)
app.include_router(permissions.router)
app.include_router(projects.router)
app.include_router(db_connections.router)
app.include_router(db_diagnosis.router)
app.include_router(diagnostic.router)
app.include_router(fault_diagnosis.router)
app.include_router(agent.router)
app.include_router(terminal.router)
app.include_router(hosts.router)
app.include_router(mcp.router)
