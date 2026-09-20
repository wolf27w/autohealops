# -*- coding: utf-8 -*-
"""日志源管理 API（自定义 ES 多实例；密码加密存储；admin 维护，全员可读/测试）"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.middleware.permission import require_admin, require_any_user
from app.models import LogSource
from app.services.es_client import es_client
from app.services.system_setting_service import get_setting

router = APIRouter(prefix="/api/v1/log-sources")

logger = get_logger("api-server.log_sources")


async def _import_default_source() -> bool:
    """自动导入：log_sources 为空且全局 system_settings 有 es_url 时，生成一条默认日志源。

    迁移后不丢存量配置；幂等（仅当表为空时执行）。返回是否导入。
    """
    es_url = (await get_setting("es_url", "")).strip()
    if not es_url:
        return False
    async with async_session() as session:
        count = (await session.execute(select(func.count()).select_from(LogSource))).scalar() or 0
        if count > 0:
            return False
        password_plain = await get_setting("es_password", "")
        s = LogSource(
            name="默认日志源",
            es_url=es_url.rstrip("/"),
            index_pattern=(await get_setting("es_index_pattern", "k8s-*")).strip() or "k8s-*",
            username=(await get_setting("es_username", "")).strip(),
            password_encrypted=encrypt_aes256_gcm(password_plain) if password_plain else "",
            is_default=True,
            enabled=True,
            created_by=0,
        )
        session.add(s)
        await session.commit()
    return True


def _serialize(s: LogSource) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "es_url": s.es_url,
        "index_pattern": s.index_pattern or "",
        "username": s.username or "",
        "has_password": bool(s.password_encrypted),
        "is_default": bool(s.is_default),
        "enabled": bool(s.enabled),
        "created_by": s.created_by,
        "created_at": str(s.created_at),
        "updated_at": str(s.updated_at),
    }


@router.get("")
async def list_sources(user=Depends(require_any_user)):
    """日志源列表（密码不返回，仅返回 has_password 标记）；首次访问自动导入全局 ES 为默认源"""
    try:
        await _import_default_source()
    except Exception as e:  # noqa: BLE001
        logger.warning("自动导入默认日志源失败: %s", e)
    async with async_session() as session:
        result = await session.execute(select(LogSource).order_by(LogSource.id.desc()))
        sources = list(result.scalars())
    return {"code": 200, "message": "success", "data": [_serialize(s) for s in sources]}


@router.post("")
async def create_source(payload: dict, user=Depends(require_admin)):
    """创建日志源（仅 admin；密码加密存储）"""
    name = (payload.get("name") or "").strip()
    es_url = (payload.get("es_url") or "").strip().rstrip("/")
    if not name:
        return {"code": 400, "message": "名称不能为空", "data": None}
    if not es_url:
        return {"code": 400, "message": "ES 地址不能为空", "data": None}
    password = payload.get("password") or ""
    is_default = bool(payload.get("is_default", False))
    async with async_session() as session:
        if is_default:
            # 唯一默认：清除其它源的默认标记
            await session.execute(update(LogSource).values(is_default=False))
        s = LogSource(
            name=name,
            es_url=es_url,
            index_pattern=(payload.get("index_pattern") or "").strip() or "k8s-*",
            username=(payload.get("username") or "").strip(),
            password_encrypted=encrypt_aes256_gcm(password) if password else "",
            is_default=is_default,
            enabled=bool(payload.get("enabled", True)),
            created_by=user.id,
        )
        session.add(s)
        await session.commit()
        await session.refresh(s)
        return {"code": 200, "message": "success", "data": _serialize(s)}


@router.put("/{source_id}")
async def update_source(source_id: int, payload: dict, user=Depends(require_admin)):
    """更新日志源（仅 admin；空密码不覆盖，传入非空密码才重新加密）"""
    async with async_session() as session:
        s = await session.get(LogSource, source_id)
        if not s:
            return {"code": 404, "message": "日志源不存在", "data": None}
        if "name" in payload:
            s.name = (payload.get("name") or "").strip()
        if "es_url" in payload:
            s.es_url = (payload.get("es_url") or "").strip().rstrip("/")
        if "index_pattern" in payload:
            s.index_pattern = (payload.get("index_pattern") or "").strip() or "k8s-*"
        if "username" in payload:
            s.username = (payload.get("username") or "").strip()
        if payload.get("password"):
            s.password_encrypted = encrypt_aes256_gcm(payload["password"])
        if "is_default" in payload:
            is_default = bool(payload.get("is_default", False))
            if is_default:
                # 唯一默认：清除其它源的默认标记
                await session.execute(update(LogSource).where(LogSource.id != source_id).values(is_default=False))
            s.is_default = is_default
        if "enabled" in payload:
            s.enabled = bool(payload.get("enabled", True))
        await session.commit()
        await session.refresh(s)
    return {"code": 200, "message": "success", "data": _serialize(s)}


@router.delete("/{source_id}")
async def delete_source(source_id: int, user=Depends(require_admin)):
    """删除日志源（仅 admin）"""
    async with async_session() as session:
        s = await session.get(LogSource, source_id)
        if s:
            await session.delete(s)
            await session.commit()
    return {"code": 200, "message": "success", "data": None}


@router.post("/{source_id}/test")
async def test_saved_source(source_id: int, user=Depends(require_any_user)):
    """测试已保存日志源的连通性（用库内解密后的密码）"""
    async with async_session() as session:
        s = await session.get(LogSource, source_id)
    if not s:
        return {"code": 404, "message": "日志源不存在", "data": None}
    password = ""
    if s.password_encrypted:
        try:
            password = decrypt_aes256_gcm(s.password_encrypted)
        except Exception:  # noqa: BLE001
            password = ""
    result = await es_client.test_connection(s.es_url, s.index_pattern, s.username or "", password)
    return {"code": 200 if result["success"] else 500, "message": result["message"], "data": result}


@router.post("/test")
async def test_source(payload: dict, user=Depends(require_any_user)):
    """测试临时日志源连通性（新增/编辑弹窗用，无需先保存）"""
    result = await es_client.test_connection(
        payload.get("es_url") or "",
        payload.get("index_pattern") or "",
        payload.get("username") or "",
        payload.get("password") or "",
    )
    return {"code": 200 if result["success"] else 500, "message": result["message"], "data": result}
