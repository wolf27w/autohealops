# -*- coding: utf-8 -*-
"""大屏实时推送（SSE）：订阅进程内 ScreenBus，业务事件即时下发到 EventSource。

事件类型（由各业务 service 在对应时机调用 screen_bus.publish 产生）：
  alert_created / alert_merged / selfheal_done / ticket_created / ticket_closed

鉴权：EventSource 无法带 Authorization 头 → 用 ?token=；也兼容 Bearer 头
（与 app.api.stats.dashboard_stream 保持一致）。
"""
import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from jose import JWTError
from sqlalchemy import select

from app.core.database import async_session
from app.core.redis_client import redis_pool
from app.core.security import decode_jwt
from app.models import User
from app.services.screen_bus import screen_bus

router = APIRouter(prefix="/api/v1/stats/screen", tags=["screen"])


@router.get("/stream")
async def screen_stream(request: Request, token: str = Query("")):
    """大屏实时推送（SSE）：订阅 ScreenBus，业务事件即时下发。

    事件帧为 `event: <event_type>` + `data: <JSON>`（JSON 结构见 screen_bus.encode_frame）。
    """
    # ---- 鉴权（复用 stats.dashboard_stream 的 JWT 校验逻辑）----
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(status_code=401, detail="未认证")
    try:
        if await redis_pool.get(f"token_blacklist:{token}"):
            raise HTTPException(status_code=401, detail="token 已失效")
        payload = decode_jwt(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的 token")
    async with async_session() as session:
        user = (await session.execute(
            select(User).where(User.id == int(payload.get("sub", 0)))
        )).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    async def _event_gen():
        q = screen_bus.subscribe()
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # 心跳，防代理断连
                    continue
                yield frame
        finally:
            screen_bus.unsubscribe(q)

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )
