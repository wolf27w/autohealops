# -*- coding: utf-8 -*-
"""Agent 反向隧道网关（R12：agent 主动外连接入模式）。

agent（集群内单 Deployment）主动连本网关，建立 gRPC 双向流 Tunnel；
平台把 executor 请求（method + 序列化 payload）经隧道下发，agent 在集群内执行后回传响应。

- 接入判定：某 cluster 的 agent 当前已连接 → 该集群走隧道（无需额外开关）。
- 传输：同步 grpc.server（跑在后台线程），与现有同步 executor_client 兼容（经 acall 走线程池）。
"""
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import grpc

from app.core.config import settings
from app.core.logging import get_logger
from app.proto import agent_pb2, agent_pb2_grpc

logger = get_logger("api-server.agent_gateway")

_agents: dict[int, "_AgentConn"] = {}
_agents_lock = threading.Lock()
_server: grpc.Server | None = None


class _AgentConn:
    def __init__(self, cluster_id: int):
        self.cluster_id = int(cluster_id)
        self.out: "queue.Queue" = queue.Queue()
        self.pending: dict[str, dict] = {}
        self.alive = True


def is_connected(cluster_id: int) -> bool:
    """该集群是否有 agent 在线（在线=走反向隧道）。"""
    with _agents_lock:
        a = _agents.get(int(cluster_id))
    return bool(a and a.alive)


def call(cluster_id: int, method: str, payload: bytes, timeout: float = 30.0) -> bytes:
    """经隧道下发一个 executor 调用，返回响应 payload 字节（同步阻塞）。"""
    with _agents_lock:
        a = _agents.get(int(cluster_id))
    if not a:
        raise RuntimeError(f"集群 {cluster_id} 无在线 agent")
    req_id = uuid.uuid4().hex
    logger.info("隧道下发 cluster=%s method=%s", cluster_id, method)
    slot = {"event": threading.Event(), "payload": b"", "error": ""}
    a.pending[req_id] = slot
    a.out.put(agent_pb2.AgentFrame(type="request", req_id=req_id, method=method, payload=payload))
    try:
        if not slot["event"].wait(timeout):
            raise TimeoutError(f"agent 隧道超时（{method}）")
    finally:
        a.pending.pop(req_id, None)
    if slot["error"]:
        raise RuntimeError(slot["error"])
    return slot["payload"]


class _Servicer(agent_pb2_grpc.AgentServiceServicer):
    def Tunnel(self, request_iterator, context):  # noqa: N802 (grpc 命名)
        expected = (getattr(settings, "AGENT_ACCESS_TOKEN", "") or "").strip()
        state = {"a": None}

        def reader():
            try:
                for f in request_iterator:
                    if f.type == "register":
                        if expected and f.token != expected:
                            logger.warning("agent 注册令牌无效 cluster=%s", f.cluster_id)
                            context.abort(grpc.StatusCode.UNAUTHENTICATED, "token 无效")
                            return
                        a = _AgentConn(f.cluster_id)
                        state["a"] = a
                        with _agents_lock:
                            _agents[a.cluster_id] = a
                        a.out.put(agent_pb2.AgentFrame(type="register_ack"))
                        logger.info("agent 已接入 cluster=%s", a.cluster_id)
                    elif f.type == "response":
                        a = state["a"]
                        if a:
                            slot = a.pending.get(f.req_id)
                            if slot:
                                slot["payload"] = bytes(f.payload)
                                slot["error"] = f.error
                                slot["event"].set()
                    elif f.type == "pong":
                        pass
            except Exception as e:  # noqa: BLE001
                logger.info("agent 隧道读取结束: %s", e)

        threading.Thread(target=reader, daemon=True).start()
        # 等待注册
        deadline = time.time() + 30
        while state["a"] is None and context.is_active() and time.time() < deadline:
            time.sleep(0.1)
        a = state["a"]
        if a is None:
            return
        try:
            while context.is_active():
                try:
                    frame = a.out.get(timeout=1.0)
                except queue.Empty:
                    continue
                yield frame
        finally:
            a.alive = False
            with _agents_lock:
                if _agents.get(a.cluster_id) is a:
                    _agents.pop(a.cluster_id, None)
            logger.info("agent 断开 cluster=%s", a.cluster_id)


def start_server(addr: str) -> None:
    global _server
    if _server is not None:
        return
    _server = grpc.server(ThreadPoolExecutor(max_workers=20))
    agent_pb2_grpc.add_AgentServiceServicer_to_server(_Servicer(), _server)
    _server.add_insecure_port(addr)
    _server.start()
    logger.info("Agent 反向隧道网关已启动: %s", addr)


def stop_server() -> None:
    global _server
    if _server is not None:
        _server.stop(0)
        _server = None
