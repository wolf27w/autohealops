# -*- coding: utf-8 -*-
"""认证 API：登录/当前用户/登出"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.core.redis_client import redis_pool
from app.core.security import create_jwt, verify_password
from app.middleware.auth import get_current_active_user
from app.models import User

router = APIRouter(prefix="/api/v1/auth")

# 登录防爆破：窗口内失败次数阈值
_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW_SECONDS = 300


@router.post("/login")
async def login(payload: dict):
    """登录（支持 local / ldap），返回 token；失败防爆破"""
    username = payload.get("username", "")
    password = payload.get("password", "")
    auth_type = payload.get("auth_type", "local")

    if auth_type == "ldap":
        return await _ldap_login(username, password)

    # 登录防爆破：同一用户名窗口内失败次数超限则拒绝
    fail_key = f"login_fail:{username}"
    fail_count = await redis_pool.get(fail_key)
    if fail_count and int(fail_count) >= _LOGIN_FAIL_LIMIT:
        return {"code": 429, "message": "登录失败次数过多，请稍后再试", "data": None}

    async with async_session() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        count = await redis_pool.incr(fail_key)
        if count == 1:
            await redis_pool.expire(fail_key, _LOGIN_FAIL_WINDOW_SECONDS)
        return {"code": 401, "message": "用户名或密码错误", "data": None}
    if not user.is_active:
        return {"code": 403, "message": "用户已停用", "data": None}
    await redis_pool.delete(fail_key)  # 登录成功清零
    token = create_jwt(user.id, user.username, user.role)
    return {"code": 200, "message": "success", "data": {
        "token": token, "user": {"id": user.id, "username": user.username, "role": user.role, "email": user.email},
    }}


def _ldap_escape(value: str) -> str:
    """LDAP 过滤值转义（防注入，RFC 4515）"""
    return (
        value.replace("\\", "\\5c")
        .replace("*", "\\2a")
        .replace("(", "\\28")
        .replace(")", "\\29")
        .replace("\x00", "\\00")
    )


async def _ldap_login(username: str, password: str):
    """LDAP 认证（需安装 ldap3 依赖，并配置 LDAP 服务器）

    流程：服务账号绑定 → 按 user_filter 搜索用户 DN → 用户 DN + 密码二次绑定验证
    认证通过后：查找/自动创建平台用户（默认 viewer 角色），签发 JWT
    """
    from app.services import system_setting_service

    ldap_enabled = await system_setting_service.get_setting("ldap_enabled", "false")
    if ldap_enabled != "true":
        return {"code": 400, "message": "LDAP 登录未开启", "data": None}

    host = await system_setting_service.get_setting("ldap_host", "")
    port = int(await system_setting_service.get_setting("ldap_port", "389") or "389")
    base_dn = await system_setting_service.get_setting("ldap_base_dn", "")
    bind_dn = await system_setting_service.get_setting("ldap_bind_dn", "")
    bind_password = await system_setting_service.get_setting("ldap_bind_password", "")
    user_filter = await system_setting_service.get_setting("ldap_user_filter", "(uid={username})")

    if not host or not base_dn:
        return {"code": 500, "message": "LDAP 服务器地址或 Base DN 未配置", "data": None}

    # 懒加载 ldap3（可选依赖，未安装时明确提示）
    try:
        from ldap3 import ALL, SUBTREE, Connection, Server  # noqa: PLC0415
    except ImportError:
        return {"code": 501, "message": "ldap3 依赖未安装（pip install ldap3 后重启生效）", "data": None}

    # 过滤模板：{username} 替换为转义后的用户名，防 LDAP 注入
    filter_str = user_filter.replace("{username}", _ldap_escape(username))

    try:
        server = Server(host, port=port, get_info=ALL)
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"LDAP 服务器连接失败: {e}", "data": None}

    # 1. 服务账号（或匿名）绑定，搜索用户 DN
    try:
        search_conn = Connection(
            server,
            user=bind_dn or None,
            password=bind_password if bind_dn else None,
            auto_bind=True,
        )
        search_conn.search(
            search_base=base_dn,
            search_filter=filter_str,
            search_scope=SUBTREE,
            attributes=["dn"],
        )
        if not search_conn.entries:
            search_conn.unbind()
            return {"code": 401, "message": "LDAP 用户不存在", "data": None}
        user_dn = search_conn.entries[0].entry_dn
        search_conn.unbind()
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"LDAP 查询失败: {e}", "data": None}

    # 2. 用户 DN + 输入密码二次绑定，验证凭据
    try:
        user_conn = Connection(server, user=user_dn, password=password, auto_bind=True)
        user_conn.unbind()
    except Exception:  # noqa: BLE001
        return {"code": 401, "message": "用户名或密码错误", "data": None}

    # 3. 认证通过：查找/自动创建平台用户，签发 JWT
    default_role = await system_setting_service.get_setting("ldap_default_role", "viewer")
    if default_role not in ("admin", "operator", "viewer"):
        default_role = "viewer"

    async with async_session() as session:
        result = await session.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                username=username,
                password_hash="",  # LDAP 用户无本地密码
                email="",
                role=default_role,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        if not user.is_active:
            return {"code": 403, "message": "用户已停用", "data": None}

    token = create_jwt(user.id, user.username, user.role)
    return {"code": 200, "message": "success", "data": {
        "token": token, "user": {"id": user.id, "username": user.username, "role": user.role, "email": user.email},
    }}


@router.get("/me")
async def me(user: User = Depends(get_current_active_user)):
    return {"code": 200, "message": "success", "data": {
        "id": user.id, "username": user.username, "role": user.role, "email": user.email,
    }}


@router.post("/logout")
async def logout(request: Request, user: User = Depends(get_current_active_user)):
    """登出：token 加入 Redis 黑名单（直到过期），后续请求拒绝"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if token:
        await redis_pool.set(f"token_blacklist:{token}", "1", ex=settings.JWT_EXPIRE_HOURS * 3600)
    return {"code": 200, "message": "success", "data": None}
