# -*- coding: utf-8 -*-
"""内部网关 gRPC 服务（R2 拆网关阶段 1）。

agent-gateway 独立进程对外只暴露本服务的 RPC（9096 明文，仅内部网络）。
api-server 通过本 servicer 实现，把请求包装转发到同进程内的：
- agent_gateway（反向隧道：call / stream_call / is_connected）
- agent_pki（CA 私钥只在网关侧：issue_client_cert）
"""
from concurrent.futures import ThreadPoolExecutor

import grpc

from app import agent_gateway
from app.core.logging import get_logger
from app.proto import agent_gateway_internal_pb2 as pb
from app.proto import agent_gateway_internal_pb2_grpc as pb_grpc
from app.services import agent_pki

logger = get_logger("api-server.agent_gateway_service")


class AgentGatewayInternalServicer(pb_grpc.AgentGatewayInternalServicer):
    """内部网关服务实现：api-server 调用，agent-gateway 实现。"""

    def TunnelCall(self, request, context):  # noqa: N802 (grpc 命名)
        """单发调用：等价 agent_gateway.call(cluster_id, method, payload, timeout)。"""
        try:
            payload = agent_gateway.call(
                request.cluster_id,
                request.method,
                request.payload,
                timeout=request.timeout or 30.0,
            )
            return pb.TunnelCallResponse(ok=True, payload=payload)
        except Exception as e:  # noqa: BLE001 调用失败回给 api-server 判断
            logger.warning(
                "TunnelCall 失败 cluster=%s method=%s: %s",
                request.cluster_id, request.method, e,
            )
            return pb.TunnelCallResponse(ok=False, error=str(e))

    def TunnelStreamCall(self, request, context):  # noqa: N802 (grpc 命名)
        """服务端流式：等价 agent_gateway.stream_call（逐条回传 payload）。"""
        try:
            for chunk in agent_gateway.stream_call(
                request.cluster_id,
                request.method,
                request.payload,
                timeout=request.timeout or 300.0,
            ):
                yield pb.TunnelStreamChunk(payload=chunk)
        except Exception as e:  # noqa: BLE001 流式异常以 error 帧终止
            logger.warning(
                "TunnelStreamCall 失败 cluster=%s method=%s: %s",
                request.cluster_id, request.method, e,
            )
            yield pb.TunnelStreamChunk(error=str(e))

    def IsConnected(self, request, context):  # noqa: N802 (grpc 命名)
        """在线判断：该集群 agent 是否在线（在线=走反向隧道）。"""
        return pb.IsConnectedResponse(
            connected=agent_gateway.is_connected(request.cluster_id)
        )

    def IssueClientCert(self, request, context):  # noqa: N802 (grpc 命名)
        """签发 agent 客户端证书（CA 私钥只在网关侧，签发须经网关）。"""
        try:
            cert_pem, key_pem = agent_pki.issue_client_cert(
                request.cluster_id, request.days or 1825
            )
            ca_pem = agent_pki.ca_cert_pem()
            return pb.IssueClientCertResponse(ok=True, cert_pem=cert_pem, key_pem=key_pem, ca_pem=ca_pem)
        except Exception as e:  # noqa: BLE001
            logger.warning("IssueClientCert 失败 cluster=%s: %s", request.cluster_id, e)
            return pb.IssueClientCertResponse(ok=False, error=str(e))


def start_internal_server(addr: str) -> grpc.Server:
    """启动内部 gRPC server（明文，仅内部网络），返回 server（调用方负责 stop）。"""
    server = grpc.server(ThreadPoolExecutor(max_workers=20))
    pb_grpc.add_AgentGatewayInternalServicer_to_server(
        AgentGatewayInternalServicer(), server
    )
    server.add_insecure_port(addr)
    server.start()
    logger.info("内部网关 gRPC 服务已启动（明文，仅内部）: %s", addr)
    return server
