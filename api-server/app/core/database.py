# -*- coding: utf-8 -*-
"""数据库连接（SQLAlchemy 2.0 异步，MySQL + aiomysql 驱动）"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""


# 异步引擎：连接池 20，溢出 40
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_size=20,
    max_overflow=40,
    pool_recycle=1800,  # 30 分钟回收，防止 MySQL 空闲断连后复用失效连接
    # 注：aiomysql 的 ping() 缺 reconnect 参数，与 SQLAlchemy pool_pre_ping 不兼容，故不开启
)

# 会话工厂
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：请求级数据库会话，用完自动关闭"""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
