# -*- coding: utf-8 -*-
"""AI 用量异常告警（M12）：单日成本突增 / 调用量异常

每日巡检 AICallLog：把「今日成本 / 调用量」与近 N 日均值对比，超过倍数或绝对阈值 → 通知平台。

阈值与开关（system_settings，改完即时生效）：
- ai_usage_alert_enabled（默认 true）
- ai_usage_baseline_days（默认 7，对比基线天数）
- ai_cost_spike_ratio（默认 3，成本突增倍数；基线>0 才生效）
- ai_calls_spike_ratio（默认 3，调用量突增倍数）
- ai_cost_daily_abs（默认 0=不启用，绝对金额阈值）
- ai_calls_daily_abs（默认 0=不启用，绝对调用数阈值）

同一天同类只告警一次（Redis 去重）。
"""
import asyncio
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.models import AICallLog
from app.services.system_setting_service import get_setting

logger = get_logger("api-server.ai-usage")

SCAN_INTERVAL_SEC = 3600  # 每小时巡检一次（按天告警，去重防刷）


async def _s_int(key: str, default: int) -> int:
    try:
        return int((await get_setting(key, str(default))).strip())
    except Exception:  # noqa: BLE001
        return default


async def _s_float(key: str, default: float) -> float:
    try:
        return float((await get_setting(key, str(default))).strip())
    except Exception:  # noqa: BLE001
        return default


async def _s_bool(key: str, default: bool) -> bool:
    try:
        v = (await get_setting(key, "true" if default else "false")).strip().lower()
    except Exception:  # noqa: BLE001
        return default
    return v in ("1", "true", "yes", "on")


async def _config() -> dict:
    return {
        "enabled": await _s_bool("ai_usage_alert_enabled", True),
        "baseline_days": max(1, min(await _s_int("ai_usage_baseline_days", 7), 30)),
        "cost_ratio": await _s_float("ai_cost_spike_ratio", 3.0),
        "calls_ratio": await _s_float("ai_calls_spike_ratio", 3.0),
        "cost_abs": await _s_float("ai_cost_daily_abs", 0.0),
        "calls_abs": await _s_float("ai_calls_daily_abs", 0.0),
    }


def evaluate_usage(today_cost: float, today_calls: int, base_cost: float, base_calls: float,
                   cfg: dict) -> list[str]:
    """纯函数：返回异常描述列表（空=正常）。便于单测。"""
    alerts: list[str] = []
    if base_cost > 0 and cfg["cost_ratio"] > 0 and today_cost >= cfg["cost_ratio"] * base_cost and today_cost > 0:
        alerts.append(f"AI 成本突增：今日 {today_cost:.2f}（近基线日均 {base_cost:.2f}，"
                      f"{today_cost / base_cost:.1f}×，阈值 {cfg['cost_ratio']:.1f}×）")
    if cfg["cost_abs"] > 0 and today_cost >= cfg["cost_abs"]:
        alerts.append(f"AI 成本超绝对阈值：今日 {today_cost:.2f} ≥ {cfg['cost_abs']:.2f}")
    if base_calls > 0 and cfg["calls_ratio"] > 0 and today_calls >= cfg["calls_ratio"] * base_calls and today_calls > 0:
        alerts.append(f"AI 调用量异常：今日 {today_calls} 次（近基线日均 {base_calls:.0f}，"
                      f"{today_calls / base_calls:.1f}×，阈值 {cfg['calls_ratio']:.1f}×）")
    if cfg["calls_abs"] > 0 and today_calls >= cfg["calls_abs"]:
        alerts.append(f"AI 调用量超绝对阈值：今日 {today_calls} ≥ {cfg['calls_abs']:.0f}")
    return alerts


async def get_usage_snapshot() -> dict:
    """今日 vs 基线（成本 / 调用数）。"""
    cfg = await _config()
    today = date.today()
    since = datetime.combine(today - timedelta(days=cfg["baseline_days"]), datetime.min.time())
    async with async_session() as s:
        rows = (await s.execute(
            select(func.date(AICallLog.created_at).label("d"),
                   func.count().label("cnt"),
                   func.coalesce(func.sum(AICallLog.cost), 0.0).label("cost"))
            .where(AICallLog.created_at >= since)
            .group_by(func.date(AICallLog.created_at))
        )).all()
    today_cost = 0.0
    today_calls = 0
    base_costs: list[float] = []
    base_calls: list[int] = []
    for r in rows:
        d = str(r.d)
        if d == today.isoformat():
            today_cost = float(r.cost or 0.0)
            today_calls = int(r.cnt or 0)
        else:
            base_costs.append(float(r.cost or 0.0))
            base_calls.append(int(r.cnt or 0))
    base_cost = sum(base_costs) / len(base_costs) if base_costs else 0.0
    base_calls = sum(base_calls) / len(base_calls) if base_calls else 0.0
    alerts = evaluate_usage(today_cost, today_calls, base_cost, base_calls, cfg)
    return {"today": {"date": today.isoformat(), "cost": round(today_cost, 4), "calls": today_calls},
            "baseline": {"days": cfg["baseline_days"], "avg_cost": round(base_cost, 4), "avg_calls": round(base_calls, 1)},
            "alerts": alerts, "enabled": cfg["enabled"]}


async def check_once() -> dict:
    """巡检一次；有异常且当日未告警过 → 通知平台。"""
    snap = await get_usage_snapshot()
    if not snap["enabled"] or not snap["alerts"]:
        return snap
    today = snap["today"]["date"]
    key = f"ai_usage:alerted:{today}"
    try:
        already = await redis_pool.get(key)
    except Exception:  # noqa: BLE001
        already = None
    if already:
        return snap
    try:
        from app.services.notification_service import notify_platform
        await notify_platform("⚠️ AI 用量异常（M12）：\n- " + "\n- ".join(snap["alerts"]), at_all=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 用量告警通知失败: %s", e)
    try:
        await redis_pool.set(key, "1", ex=24 * 3600)
    except Exception:  # noqa: BLE001
        pass
    logger.info("AI 用量异常告警已发：%s", snap["alerts"])
    return snap


async def background_loop() -> None:
    while True:
        try:
            await check_once()
        except Exception as e:  # noqa: BLE001
            logger.warning("AI 用量巡检异常: %s", e)
        await asyncio.sleep(SCAN_INTERVAL_SEC)
