# -*- coding: utf-8 -*-
"""AI-Service 入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.aux_api import router as aux_router
from app.api.core_api import router as core_router
from app.core.config import settings
from app.core.http_client import close_http_clients
from app.services.embedding_service import EmbeddingConfigError
from app.services.prompt_engine import seed_default_templates


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动事件：预置默认提示词模板（不建表，表由 api-server alembic 管理）"""
    await seed_default_templates()
    yield
    await close_http_clients()


app = FastAPI(title="AI-Service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def internal_auth_middleware(request: Request, call_next):
    """内部服务鉴权：仅接受带正确 X-Internal-Token 的请求（来自 api-server）

    ai-service 不直接对外，只被 api-server 调用（ai_client 内部直连 / ai_proxy 转发）。
    /health 豁免（容器探活）。
    """
    if request.url.path in ("/health", "/live", "/ready", "/docs", "/openapi.json"):
        return await call_next(request)
    token = request.headers.get("X-Internal-Token", "")
    if not token or token != settings.JWT_SECRET_KEY:
        return JSONResponse(status_code=401, content={"code": 401, "message": "未认证", "data": None})
    return await call_next(request)


@app.exception_handler(EmbeddingConfigError)
async def embedding_config_error_handler(request: Request, exc: EmbeddingConfigError):
    """Embedding 未配置/调用失败：返回明确错误，前端可读"""
    return JSONResponse(status_code=503, content={"code": 503, "message": str(exc), "data": None})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_router)
app.include_router(aux_router)


@app.get("/health")
async def health():
    return {"code": 200, "message": "success", "data": {"status": "ok"}}


@app.get("/live")
async def live():
    """存活探针（liveness）：只判进程存活，不查依赖"""
    return {"code": 200, "message": "success", "data": {"status": "ok"}}


@app.get("/ready")
async def ready():
    """就绪探针（readiness）：数据库（以及配置了 REDIS_URL 时的 Redis）可用才 200"""
    from sqlalchemy import text as _text

    from app.core.database import async_session

    db_ok = True
    redis_ok = True
    try:
        async with async_session() as s:
            await s.execute(_text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    if getattr(settings, "REDIS_URL", ""):
        try:
            import redis.asyncio as aioredis  # noqa: PLC0415

            _r = aioredis.from_url(settings.REDIS_URL)
            await _r.ping()
            await _r.aclose()
        except Exception:  # noqa: BLE001
            redis_ok = False
    ok = db_ok and redis_ok
    return JSONResponse(status_code=200 if ok else 503, content={
        "code": 200 if ok else 503,
        "message": "success" if ok else "dependencies unavailable",
        "data": {"status": "ready" if ok else "not_ready", "database": db_ok, "redis": redis_ok},
    })
