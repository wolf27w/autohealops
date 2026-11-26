# -*- coding: utf-8 -*-
"""系统设置 API：平台级配置（Prometheus/ES/LDAP）+ 密码修改"""
from fastapi import APIRouter, Depends

from app.core.security import hash_password, verify_password
from app.core.database import async_session
from app.middleware.auth import get_current_active_user
from app.middleware.permission import require_admin
from app.models import User
from app.services import system_setting_service

router = APIRouter(prefix="/api/v1")


@router.get("/settings")
async def get_settings(user=Depends(require_admin)):
    """读取全部系统设置（admin）"""
    settings = await system_setting_service.get_all_settings()
    return {"code": 200, "message": "success", "data": settings}


@router.put("/settings")
async def update_settings(payload: dict, user=Depends(require_admin)):
    """更新系统设置（admin）"""
    await system_setting_service.update_settings(payload)
    # 涉及 Embedding 配置时，通知 ai-service 清缓存（改完立即生效，不用等 60s）
    if any(k.startswith("embedding_") for k in payload.keys()):
        try:
            from app.clients.ai_client import ai_client
            await ai_client.reset_embed_cache()
        except Exception:  # noqa: BLE001
            pass
    return {"code": 200, "message": "success", "data": None}


@router.get("/settings/automation-policy")
async def get_automation_policy(user=Depends(require_admin)):
    """自动化授权策略（admin，2026-11-26）

    返回策略 + 动作库（每个动作按环境显示已配置值，未配置为空=按 risk 默认）。
    前端「自动化策略」页直接渲染成矩阵。
    """
    from app.services.action_registry import ACTIONS
    from app.services.autonomy_gate import CONFIDENCE_THRESHOLD_PROD, CONFIDENCE_THRESHOLD_TEST, load_automation_policy

    policy = await load_automation_policy()
    rows = []
    for a in ACTIONS:
        ov = policy.get("actions", {}).get(a["key"]) or {}
        rows.append({
            "key": a["key"], "name": a.get("name", a["key"]), "risk": a.get("risk", ""),
            "description": a.get("description", ""), "rollback_note": a.get("rollback_note", ""),
            "prod": ov.get("prod") or "", "test": ov.get("test") or "",
        })
    return {"code": 200, "message": "success", "data": {
        "mode": policy.get("mode", "by_env"),
        "thresholds": policy.get("thresholds") or {"prod": CONFIDENCE_THRESHOLD_PROD, "test": CONFIDENCE_THRESHOLD_TEST},
        "defaults": {"mode": "by_env", "thresholds": {"prod": CONFIDENCE_THRESHOLD_PROD, "test": CONFIDENCE_THRESHOLD_TEST}},
        "actions": rows,
    }}


@router.put("/settings/automation-policy")
async def update_automation_policy(payload: dict, user=Depends(require_admin)):
    """保存自动化授权策略（admin）

    payload：{mode, thresholds:{prod,test}, actions:{key:{prod,test}}}
    值校验：mode ∈ by_env/full/assist；阈值 ∈ [0,1]；动作授权 ∈ auto/approve/manual/forbidden/空。
    """
    import json as _json

    from app.services.action_registry import ACTIONS

    mode = str(payload.get("mode") or "by_env").strip().lower()
    if mode not in ("by_env", "full", "assist"):
        return {"code": 400, "message": "mode 只能是 by_env / full / assist", "data": None}
    th_raw = payload.get("thresholds") if isinstance(payload.get("thresholds"), dict) else {}
    thresholds = {}
    for bucket, default in (("prod", 0.8), ("test", 0.6)):
        try:
            v = float(th_raw.get(bucket, default))
        except (TypeError, ValueError):
            return {"code": 400, "message": f"{bucket} 阈值必须是 0~1 的数字", "data": None}
        if not (0 <= v <= 1):
            return {"code": 400, "message": f"{bucket} 阈值必须在 0~1 之间", "data": None}
        thresholds[bucket] = v

    valid_keys = {a["key"] for a in ACTIONS if a.get("risk") != "forbidden"}
    allowed = {"auto", "approve", "manual", "forbidden", ""}
    actions_in = payload.get("actions") if isinstance(payload.get("actions"), dict) else {}
    actions: dict = {}
    for key, ov in actions_in.items():
        if key not in valid_keys or not isinstance(ov, dict):
            continue
        clean = {}
        for bucket in ("prod", "test"):
            val = str(ov.get(bucket) or "").strip().lower()
            if val not in allowed:
                return {"code": 400, "message": f"动作 {key} 的 {bucket} 授权值非法: {val!r}", "data": None}
            if val:
                clean[bucket] = val
        if clean:
            actions[key] = clean

    policy = {"mode": mode, "thresholds": thresholds, "actions": actions}
    await system_setting_service.update_settings({
        "automation_policy": _json.dumps(policy, ensure_ascii=False),
        "automation_mode": mode,  # 兼容旧读取点（_automation_mode）
    })
    return {"code": 200, "message": "success", "data": policy}


@router.post("/settings/embedding/test")
async def test_embedding(payload: dict | None = None, user=Depends(require_admin)):
    """Embedding 连通性测试（admin）

    payload 可传 {base_url, model, api_key, provider} 做「先测再存」；不传则测当前已保存配置。
    返回 {ok, dimension, model, base_url, error}，前端可直接展示结果。
    """
    from app.clients.ai_client import ai_client
    payload = payload or {}
    cfg = {k: payload.get(k) for k in ("provider", "base_url", "model", "api_key") if payload.get(k) is not None}
    if not cfg:
        cfg = None
    try:
        # ai_client._post 已解包 data（code!=200 时抛异常）
        data = await ai_client.embed_test(cfg)
    except Exception as e:  # noqa: BLE001
        data = {"ok": False, "error": str(e)}
    ok = bool((data or {}).get("ok"))
    return {"code": 200 if ok else 400,
            "message": "连通正常" if ok else ((data or {}).get("error") or "测试失败"),
            "data": data or {}}


@router.post("/auth/change-password")
async def change_password(payload: dict, user=Depends(get_current_active_user)):
    """修改当前用户密码"""
    old_password = payload.get("old_password", "")
    new_password = payload.get("new_password", "")
    if not old_password or not new_password:
        return {"code": 400, "message": "旧密码和新密码不能为空", "data": None}
    if not verify_password(old_password, user.password_hash):
        return {"code": 400, "message": "旧密码错误", "data": None}
    async with async_session() as session:
        u = await session.get(User, user.id)
        u.password_hash = hash_password(new_password)
        await session.commit()
    return {"code": 200, "message": "success", "data": None}
