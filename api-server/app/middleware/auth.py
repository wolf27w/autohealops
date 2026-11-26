# -*- coding: utf-8 -*-
"""认证中间件：JWT 验证"""
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.core.security import decode_jwt
from app.models import User

logger = get_logger("api-server.auth")

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> User:
    """从 Authorization Bearer Token 解析当前用户"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="未认证")
    token = credentials.credentials
    # 登出黑名单校验（2026-11-26：Redis 抖动不能把整站打成 500——JWT 签名仍会校验，
    # 黑名单只是「提前失效」的加速器；Redis 不可用时降级放行并留日志）
    try:
        if await redis_pool.get(f"token_blacklist:{token}"):
            raise HTTPException(status_code=401, detail="token 已失效")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("token 黑名单校验失败（Redis 不可用，降级放行）: %s", e)
    try:
        payload = decode_jwt(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的 token")
    user_id = int(payload.get("sub", 0))
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    request.state.trace_id = payload.get("trace_id", getattr(request.state, "trace_id", ""))
    return user


async def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    """检查用户是否激活"""
    if not user.is_active:
        raise HTTPException(status_code=403, detail="用户已停用")
    return user
