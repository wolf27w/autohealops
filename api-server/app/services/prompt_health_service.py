# -*- coding: utf-8 -*-
"""R13 提示词模板健康度与熔断（2026-11-10）

老板 2026-11-05 口径：
- 采纳率 < 40%（或标记无效 ≥ 3 次）→ 自动降级 `draft` + 通知管理员，可回滚到上一版本
- 只对 `source=auto`（AI 起草、人工采纳的）模板生效；人工手写模板不自动降级

数据来源：`ai_diagnosis_results.template_id` + `is_useful`（人工/隐式反馈都回写这里），
所以采纳率是「真实诊断结果被判定有用的比例」，不是模板被编辑的次数。
"""
from datetime import datetime, timedelta

from sqlalchemy import case, func, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import AIDiagnosisResult, PromptTemplate, PromptVersion

logger = get_logger("api-server.prompt_health")

# 熔断口径（需求：采纳率 < 40% 或无效 ≥3 次）
MIN_SAMPLES = 5           # 样本太少不判（避免 1 次差评就下线）
MIN_ADOPTION_RATE = 0.4   # 采纳率下限
MAX_INVALID = 3           # 无效标记上限
WINDOW_DAYS = 30          # 统计窗口


async def template_stats(template_id: int, days: int = WINDOW_DAYS) -> dict:
    """某模板最近 N 天的诊断采纳情况"""
    since = datetime.now() - timedelta(days=days)
    async with async_session() as session:
        row = (await session.execute(
            select(
                func.count(AIDiagnosisResult.id),
                func.sum(case((AIDiagnosisResult.is_useful == 1, 1), else_=0)),
                func.sum(case((AIDiagnosisResult.is_useful == -1, 1), else_=0)),
            ).where(
                AIDiagnosisResult.template_id == template_id,
                AIDiagnosisResult.created_at >= since,
            )
        )).one()
    total_all, useful, useless = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    rated = useful + useless
    return {
        "template_id": template_id,
        "diagnosis_total": total_all,
        "useful": useful,
        "useless": useless,
        "rated": rated,
        "adoption_rate": round(useful / rated, 3) if rated else None,
        "window_days": days,
    }


def should_trip(source: str, status: str, invalid_count: int, rated: int, rate: float | None) -> tuple[bool, str]:
    """熔断判定（纯函数，便于单测）：只对 auto 模板、仅 active 状态生效

    返回 (是否熔断, 原因文案)
    """
    if source != "auto" or (status or "active") != "active":
        return False, ""
    if int(invalid_count or 0) >= MAX_INVALID:
        return True, f"无效标记 {int(invalid_count)} 次（阈值 {MAX_INVALID}）"
    if int(rated or 0) >= MIN_SAMPLES and rate is not None and rate < MIN_ADOPTION_RATE:
        return True, f"采纳率 {rate:.0%}（样本 {int(rated)}）低于 {MIN_ADOPTION_RATE:.0%}"
    return False, ""


async def _trip_if_needed(session, tpl: PromptTemplate, stats: dict, reason: str) -> bool:
    """满足熔断条件则降级 draft + 通知（同一事务内改状态）"""
    tripped, detail = should_trip(tpl.source, tpl.status, tpl.invalid_count,
                                  stats.get("rated", 0), stats.get("adoption_rate"))
    if not tripped:
        return False
    tpl.status = "draft"
    tpl.is_active = False
    # 降级即解绑路由（避免后续诊断继续命中差模板）
    try:
        from app.models import PromptRouting
        rules = list((await session.execute(
            select(PromptRouting).where(PromptRouting.template_name == tpl.name)
        )).scalars())
        for r in rules:
            r.enabled = False
    except Exception:  # noqa: BLE001 路由表在 ai-service 侧定义，失败不影响降级
        pass
    logger.warning("模板熔断：%s → draft（%s，触发=%s）", tpl.name, detail, reason)
    return True


async def _notify(tpl_name: str, detail: str) -> None:
    try:
        from app.services import notification_service as ns
        await ns.notify_platform(
            f"【提示词模板熔断】模板 `{tpl_name}` 已自动降级为草稿（{detail}）。\n"
            f"请在「AI 配置 → 提示词模板 → 健康度」查看，可回滚到上一版本或手动恢复。",
            at_all=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("熔断通知发送失败（忽略）: %s", e)


async def record_invalid(template_id: int, reason: str = "诊断被标记无效") -> dict | None:
    """记一次「无效」并立即判定熔断。返回 {tripped, ...} 或 None（模板不存在）"""
    async with async_session() as session:
        tpl = await session.get(PromptTemplate, template_id)
        if tpl is None:
            return None
        tpl.invalid_count = int(tpl.invalid_count or 0) + 1
        stats = await template_stats(template_id)
        tripped = await _trip_if_needed(session, tpl, stats, reason)
        name, detail = tpl.name, f"无效标记 {tpl.invalid_count} 次"
        invalid_now = int(tpl.invalid_count or 0)
        await session.commit()
    if tripped:
        await _notify(name, detail)
    return {"template_id": template_id, "tripped": tripped, "invalid_count": invalid_now,
            "adoption_rate": stats.get("adoption_rate")}


async def check_template(template_id: int) -> dict:
    """单模板体检（不改计数，只看采纳率）"""
    stats = await template_stats(template_id)
    async with async_session() as session:
        tpl = await session.get(PromptTemplate, template_id)
        if tpl is None:
            return {"template_id": template_id, "exists": False}
        tripped = await _trip_if_needed(session, tpl, stats, "定时体检")
        name = tpl.name
        if tripped:
            await session.commit()
    if tripped:
        rate = stats.get("adoption_rate")
        await _notify(name, f"采纳率 {rate:.0%}" if rate is not None else f"无效标记累计过多")
    return {**stats, "exists": True, "tripped": tripped}


async def sweep() -> dict:
    """定时体检所有 active 的 auto 模板（main.py 每小时跑一次）"""
    async with async_session() as session:
        ids = list((await session.execute(
            select(PromptTemplate.id).where(
                PromptTemplate.source == "auto",
                PromptTemplate.status == "active",
            )
        )).scalars())
    tripped = []
    for tid in ids:
        try:
            r = await check_template(tid)
            if r.get("tripped"):
                tripped.append(tid)
        except Exception as e:  # noqa: BLE001
            logger.warning("模板体检失败 id=%s: %s", tid, e)
    return {"checked": len(ids), "tripped": tripped}


async def health_list() -> list[dict]:
    """模板健康度列表（前端「健康度」标签页用）"""
    async with async_session() as session:
        tpls = list((await session.execute(
            select(PromptTemplate).order_by(PromptTemplate.id)
        )).scalars())
    out = []
    for t in tpls:
        st = await template_stats(t.id)
        out.append({
            "id": t.id, "name": t.name, "scene": t.scene or "", "source": t.source or "manual",
            "status": t.status or "active", "version": t.version,
            "invalid_count": int(t.invalid_count or 0),
            "adoption_rate": st["adoption_rate"], "rated": st["rated"],
            "useful": st["useful"], "useless": st["useless"],
            "diagnosis_total": st["diagnosis_total"],
            "trip_threshold": {"min_samples": MIN_SAMPLES, "min_rate": MIN_ADOPTION_RATE,
                               "max_invalid": MAX_INVALID, "window_days": WINDOW_DAYS},
        })
    return out


async def list_versions(template_id: int) -> list[dict]:
    async with async_session() as session:
        rows = list((await session.execute(
            select(PromptVersion).where(PromptVersion.template_id == template_id)
            .order_by(PromptVersion.version.desc())
        )).scalars())
    return [{"id": r.id, "version": r.version, "changelog": r.changelog,
             "created_at": str(r.created_at)} for r in rows]


async def rollback(template_id: int, version: int | None = None) -> dict:
    """回滚到上一版本（或指定版本）：把该版本内容写回模板，version+1 保留可追溯"""
    async with async_session() as session:
        tpl = await session.get(PromptTemplate, template_id)
        if tpl is None:
            return {"ok": False, "message": "模板不存在"}
        q = select(PromptVersion).where(PromptVersion.template_id == template_id)
        if version:
            q = q.where(PromptVersion.version == version)
        else:
            q = q.where(PromptVersion.version < tpl.version).order_by(PromptVersion.version.desc())
        target = (await session.execute(q.limit(1))).scalar_one_or_none()
        if target is None and not version:
            # 兜底：历史数据版本号不规范（存档版本 ≥ 当前）时，取「非当前版本」的最新一条
            target = (await session.execute(
                select(PromptVersion).where(
                    PromptVersion.template_id == template_id,
                    PromptVersion.version != tpl.version,
                ).order_by(PromptVersion.version.desc()).limit(1)
            )).scalar_one_or_none()
        if target is None:
            return {"ok": False, "message": "没有可回滚的历史版本"}
        target_ver = target.version
        sys_p, usr_p = target.system_prompt, target.user_prompt_template

    # 归档当前版本 → 写回目标版本（version+1，可追溯）
    async with async_session() as session:
        tpl = await session.get(PromptTemplate, template_id)
        if tpl is None:
            return {"ok": False, "message": "模板不存在"}
        session.add(PromptVersion(template_id=tpl.id, version=tpl.version,
                                  system_prompt=tpl.system_prompt,
                                  user_prompt_template=tpl.user_prompt_template,
                                  changelog=f"回滚前归档（回滚到 v{target_ver}）"))
        tpl.version = int(tpl.version or 1) + 1
        tpl.system_prompt = sys_p
        tpl.user_prompt_template = usr_p
        await session.commit()
    # 回滚后恢复上线（熔断降级的模板回滚即重新启用）
    async with async_session() as session:
        tpl = await session.get(PromptTemplate, template_id)
        if tpl:
            tpl.status = "active"
            tpl.is_active = True
            tpl.invalid_count = 0
            await session.commit()
    logger.info("模板 %d 回滚到 v%s", template_id, target_ver)
    return {"ok": True, "rolled_back_to": target_ver}


async def reactivate(template_id: int) -> dict:
    """手动恢复（熔断后人工确认模板没问题）"""
    async with async_session() as session:
        tpl = await session.get(PromptTemplate, template_id)
        if tpl is None:
            return {"ok": False, "message": "模板不存在"}
        tpl.status = "active"
        tpl.is_active = True
        tpl.invalid_count = 0
        name = tpl.name
        # 重新启用该模板的路由
        try:
            from app.models import PromptRouting
            rules = list((await session.execute(
                select(PromptRouting).where(PromptRouting.template_name == name)
            )).scalars())
            for r in rules:
                r.enabled = True
        except Exception:  # noqa: BLE001
            pass
        await session.commit()
    return {"ok": True, "template_id": template_id}
