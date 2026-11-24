# -*- coding: utf-8 -*-
"""数据库连接故障告警扫描：连接失败/认证失败 → 生成告警，统一走现有告警策略

流程（每 60s 扫描一次）：
1. 遍历受监控的 DBConnection（collect_enabled 或 inspect_enabled 开启）
2. 只读账号探活（connect + SELECT 1，最小权限，只读不修改）
3. 失败 → 分类 fault_type（DBConnFailed 连接失败 / DBAuthFailed 认证失败）
       → DBConnection.status = failed + 建/聚合告警（manual_required=True）+ 通知
4. 成功 → DBConnection.status = connected + 关闭该库在途 DB 告警（pending/awaiting_approval/manual_required）
       + 复用 alert_auto_service._cancel_related_approvals 解除关联待审批单

安全红线：数据库诊断只给方案、绝不执行修改。DB 告警一律 manual_required=True 转人工，
不进入自动修复链路（alert_auto_service 跳过 resource_type='db' 的告警）。

统一复用：
- 通知：notification_service.notify_alert（与集群/资源告警同一入口，
  统一策略：分级渠道/静默窗口/模板/限频）
- 去重：同库同 fault_type 在途不重复建单（pending/awaiting_approval 命中则聚合 event_count）
- 恢复关闭：复用 alert_auto_service._cancel_related_approvals（告警已恢复→取消审批）
"""
import asyncio
import os
from datetime import datetime, timedelta

import aiomysql
from sqlalchemy import or_, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import acquire_lock, redis_pool, release_lock
from app.models import Alert, DBConnection
from app.services import notification_service
from app.services.db_collector import _build_conn_info

logger = get_logger("api-server.db_alert")

# 扫描周期（秒）
SCAN_INTERVAL = 60

# DB 告警 fault_type（统一走告警策略；auto_process_alerts 跳过，不自动修复）
DB_ALERT_FAULT_TYPES = ("DBConnFailed", "DBAuthFailed")

# 配置缺失类错误特征（agent 侧没下发/不存在该库的凭据）：这类不是「数据库故障」，
# 而是平台与 agent 配置不一致 —— 人工解决后下一轮必然重建，体验上像「关不掉」（2026-11-11 老板反馈）。
# 处理：同库同故障「最近一次已解决且在冷却期内」→ 不再新建告警（只保留原单，等真正恢复/人工处置）。
CONFIG_ERROR_MARKERS = (
    "agent 配置里没有 key",
    "未下发",
    "agent 未配置",
)
# 配置类告警冷却时长（分钟），可用环境变量覆盖
CONFIG_ALERT_COOLDOWN_MIN = int(os.getenv("DB_CONFIG_ALERT_COOLDOWN_MIN", "60") or 60)

# 认证失败特征（连接失败/认证失败分流，仅影响 fault_type 展示与策略匹配）
AUTH_MARKERS = (
    "access denied",                    # MySQL 1045
    "password authentication failed",   # PG 28P01
    "authentication failed",
    "invalid password",
    "invalid username",
    "ora-01017",                        # Oracle 用户名/密码无效
    "ora-28000",                        # Oracle 账号锁定
    "error 1045",                       # MySQL 访问拒绝
)


def _classify_failure(err: str) -> str:
    """错误信息 → fault_type（认证失败优先，其余按连接失败）"""
    err_l = (err or "").lower()
    if any(m in err_l for m in AUTH_MARKERS):
        return "DBAuthFailed"
    return "DBConnFailed"


def _is_config_error(err: str) -> bool:
    """是否为「平台与 agent 配置不一致」类错误（非数据库真实故障）"""
    err_l = (err or "").lower()
    return any(m.lower() in err_l for m in CONFIG_ERROR_MARKERS)


def _build_message(c: DBConnection, fault_type: str, err: str) -> str:
    reason = "认证失败" if fault_type == "DBAuthFailed" else "连接失败"
    return (
        f"数据库【{c.name}】{reason}（类型 {c.db_type}，地址 {c.host}:{c.port}，"
        f"库 {c.database or '-'}）：{(err or '')[:300]}"
    )


async def _probe_connection(c: DBConnection) -> tuple[bool, str]:
    """只读账号探活（connect + SELECT 1），返回 (成功, 原始错误)

    agent 库：平台没有账密 → 经隧道由 agent 连库。
    """
    if (getattr(c, "source", "direct") or "direct") == "agent":
        from app.services.db_agent_service import agent_probe
        return await agent_probe(c)
    info = _build_conn_info(c, readonly=True)
    try:
        if c.db_type == "mysql":
            conn = await aiomysql.connect(
                host=info["host"], port=info["port"], user=info["username"],
                password=info["password"], db=info["database"] or None, connect_timeout=5,
            )
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
            conn.close()
        elif c.db_type == "pg":
            import asyncpg  # noqa: PLC0415  # 可选依赖
            conn = await asyncpg.connect(
                host=info["host"], port=info["port"], user=info["username"],
                password=info["password"], database=info["database"] or "postgres", timeout=5,
            )
            await conn.execute("SELECT 1")
            await conn.close()
        elif c.db_type == "oracle":
            import oracledb  # noqa: PLC0415  # 可选依赖
            from app.services.db_collector import _maybe_oracle_thick, _oracle_connect_sync

            _maybe_oracle_thick()

            def _probe_oracle():
                conn = _oracle_connect_sync(info, oracledb)
                try:
                    cur = conn.cursor()
                    cur.execute("SELECT 1 FROM DUAL")
                    cur.fetchone()
                finally:
                    conn.close()

            await asyncio.to_thread(_probe_oracle)
        else:
            return False, f"不支持的数据库类型 {c.db_type}"
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


async def _upsert_db_alert(c: DBConnection, fault_type: str, err: str) -> int:
    """建单/聚合：同库同故障在途不重复建单；返回新建告警 id（0=在途聚合未新建）"""
    now = datetime.now()
    async with async_session() as session:
        # 合并（2026-11-24）：原来只查 pending/awaiting，而 DB 告警大多 30~60s 被自动解决，
        # 一 resolved dedup_key 就变 NULL → 下一轮又插一行（实测同一库同一故障 2.5h 落 113 行）。
        # 统一走 alert_merge：活跃（含 ticketed）累加、窗口内已解决的复用 reopen。
        from app.services import alert_merge
        row, reopen = await alert_merge.find_merge_target(
            session, cluster_id=0, resource_type="db", fault_type=fault_type,
            namespace="", deployment_name=c.name, resource_name=c.name)
        if row:
            # 配置类告警冷却：人工解决后不要下一分钟就重建（老板 2026-11-11 反馈「关不掉」）
            if reopen and _is_config_error(err):
                closed_at = row.resolved_at or row.detected_at
                age_s = (now - closed_at).total_seconds() if closed_at else CONFIG_ALERT_COOLDOWN_MIN * 60
                if age_s < CONFIG_ALERT_COOLDOWN_MIN * 60:
                    logger.info(
                        "数据库【%s】配置类错误在 %d 分钟冷却期内（上次告警 #%s 已解决），本轮不重开：%s",
                        c.name, CONFIG_ALERT_COOLDOWN_MIN, row.id, (err or "")[:120],
                    )
                    return 0
            alert_merge.apply_merge(row, message=_build_message(c, fault_type, err),
                                    severity="high", reopen=reopen, now=now)
            await session.commit()
            if reopen:
                logger.info("数据库【%s】%s 复发合并：复用 #%d（累计 %d 次）",
                            c.name, fault_type, row.id, row.event_count)
            return 0
        env_val = (getattr(c, "env_tag", "") or "").strip()
        if not env_val and (getattr(c, "source", "direct") or "direct") == "agent" and c.cluster_id:
            try:
                from app.models import Cluster
                async with async_session() as _s:
                    _cl = await _s.get(Cluster, int(c.cluster_id))
                if _cl:
                    env_val = (getattr(_cl, "env_tag", "") or _cl.name or "")
            except Exception:  # noqa: BLE001
                pass

        alert = Alert(
            cluster_id=0,                 # 无集群：用 0 兜底（迁移后 cluster_id 可空，代码仍兼容）
            env=env_val,
            resource_type="db",
            resource_name=c.name,
            # deployment_name 复用为连接名，使现有 dedup_key 生成列按库去重；
            # 迁移后 dedup_key 直接按 resource_type='db' 取 resource_name，此值不影响。
            deployment_name=c.name,
            namespace="",
            fault_type=fault_type,
            severity="high",
            status="pending",
            message=_build_message(c, fault_type, err),
            event_count=1,
            manual_required=True,
            manual_reason="数据库连接故障，平台不自动修复（诊断只给方案、绝不执行修改），需人工/DBA 排查",
            occurred_at=now.strftime("%Y-%m-%d %H:%M:%S"),
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        alert_id = alert.id
        from app.services.screen_bus import screen_bus
        screen_bus.publish("alert_created", {"alert_id": alert.id, "cluster_id": getattr(alert, "cluster_id", None), "fault_type": alert.fault_type, "resource_name": alert.resource_name, "severity": alert.severity})

    # 通知：与集群/资源告警同一入口（统一策略：分级/静默/渠道/模板/限频）
    try:
        await notification_service.notify_alert(alert)
    except Exception as e:  # noqa: BLE001
        logger.warning("DB 告警 %d 通知失败: %s", alert_id, e)
    return alert_id


async def _resolve_db_alerts(c: DBConnection) -> int:
    """连接恢复 → 关闭该库在途 DB 告警（resolved_by=system）+ 解除关联待审批单"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with async_session() as session:
        result = await session.execute(
            select(Alert).where(
                Alert.resource_type == "db",
                Alert.resource_name == c.name,
                # 在途告警：pending / awaiting_approval（含 manual_required=True 者，它们同属这两态）
                Alert.status.in_(["pending", "awaiting_approval"]),
            )
        )
        alerts = list(result.scalars())
        for a in alerts:
            a.status = "resolved"
            a.resolved_at = now
            a.resolved_by = "system"
            if "已恢复" not in (a.message or ""):
                a.message = (a.message or "") + "（数据库连接已恢复，系统自动关闭）"
        await session.commit()
        ids = [a.id for a in alerts]

    # 复用现有「关联告警已恢复 → 取消审批」逻辑（覆盖 manual_assist / risk_approval 两类）
    from app.services.alert_auto_service import _cancel_related_approvals
    for aid in ids:
        try:
            await _cancel_related_approvals(aid)
        except Exception as e:  # noqa: BLE001
            logger.warning("DB 告警 %d 解除关联审批单失败: %s", aid, e)
    return len(ids)


async def scan_db_connections() -> int:
    """扫描全部受监控数据库连接，更新状态 + 建单/关闭告警；返回新建告警数"""
    value = await acquire_lock(redis_pool, "lock:db_alert", 30000)
    if value is None:
        return 0
    new_alerts = 0
    try:
        async with async_session() as session:
            result = await session.execute(
                select(DBConnection).where(
                    or_(DBConnection.collect_enabled.is_(True), DBConnection.inspect_enabled.is_(True))
                )
            )
            conns = list(result.scalars())

        for c in conns:
            try:
                # agent 库：agent 不在线 ≠ 数据库故障（重启窗口/隧道抖动时不告警）
                if (getattr(c, "source", "direct") or "direct") == "agent":
                    from app import agent_gateway
                    if not (c.cluster_id and agent_gateway.is_connected(int(c.cluster_id))):
                        continue

                ok, err = await _probe_connection(c)
                # 连续 2 次失败才告警（避免瞬时抖动刷单）；成功即清零
                fkey = f"dbprobe:fail:{c.id}"
                if ok:
                    if await redis_pool.get(fkey):
                        await redis_pool.delete(fkey)
                else:
                    if "无在线 agent" in (err or ""):
                        continue  # 隧道刚断，不算数据库故障
                    n = int(await redis_pool.incr(fkey))
                    await redis_pool.expire(fkey, 1800)
                    if n < 2:
                        logger.info("数据库 %s 首次探活失败（%s），等下一轮确认", c.name, err[:60])
                        continue
                if ok:
                    async with async_session() as session:
                        c2 = await session.get(DBConnection, c.id)
                        if c2 and c2.status != "connected":
                            c2.status = "connected"
                            await session.commit()
                    await _resolve_db_alerts(c)
                else:
                    fault_type = _classify_failure(err)
                    async with async_session() as session:
                        c2 = await session.get(DBConnection, c.id)
                        if c2:
                            c2.status = "failed"
                            await session.commit()
                    logger.warning("数据库【%s】%s: %s", c.name, fault_type, err[:200])
                    if await _upsert_db_alert(c, fault_type, err):
                        new_alerts += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("连接 %d 探活异常: %s", c.id, e)
    finally:
        await release_lock(redis_pool, "lock:db_alert", value)
    return new_alerts


async def background_loop() -> None:
    """后台循环（main 调用，每 60s 一次）"""
    while True:
        try:
            await scan_db_connections()
        except Exception as e:  # noqa: BLE001
            logger.warning("数据库连接告警扫描异常: %s", e)
        await asyncio.sleep(SCAN_INTERVAL)
