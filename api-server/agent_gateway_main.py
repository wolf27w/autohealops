# -*- coding: utf-8 -*-
"""agent-gateway 独立启动入口（R2 拆网关阶段 1）。

只启动网关（不启动 FastAPI）：
- 9095：agent 反向隧道网关（agent_gateway.start_server，复用 main.py 的调用方式）
- 9096：内部 gRPC server（注册 AgentGatewayInternalServicer，供 api-server 调用）

在事件循环内启动隧道网关，确保 agent_gateway._loop 可用（agent 注册/事件回写依赖）。
"""
import asyncio

from app import agent_gateway, agent_gateway_service
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("api-server.agent_gateway_main")

GATEWAY_ADDR = getattr(settings, "AGENT_GATEWAY_ADDR", "0.0.0.0:9095")
INTERNAL_ADDR = "0.0.0.0:9096"


async def _serve_forever() -> None:
    # 1) agent 反向隧道网关（9095）；须在事件循环内启动，否则 _loop=None 导致 agent 注册失败
    agent_gateway.start_server(GATEWAY_ADDR)
    # 2) 内部 gRPC server（9096）
    internal = agent_gateway_service.start_internal_server(INTERNAL_ADDR)
    logger.info(
        "agent-gateway 独立服务已启动（隧道 %s / 内部 gRPC %s）", GATEWAY_ADDR, INTERNAL_ADDR
    )
    try:
        await asyncio.Event().wait()
    finally:
        internal.stop(0)
        agent_gateway.stop_server()
        logger.info("agent-gateway 独立服务已停止")


def main() -> None:
    asyncio.run(_serve_forever())


if __name__ == "__main__":
    main()
