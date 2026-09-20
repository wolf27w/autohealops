# -*- coding: utf-8 -*-
"""交互式终端 WebSocket：浏览器 ↔ api-server ↔ executor gRPC 双向流（专项 D 终端链路）"""
import asyncio
import json
import queue
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import decode_jwt
from app.proto import executor_pb2, executor_pb2_grpc
from app.services import user_permission_service

router = APIRouter(prefix="/api/v1")

logger = get_logger("api-server.terminal")

# 懒初始化 gRPC channel/stub
_channel = None
_stub = None


def _get_stub():
    global _channel, _stub
    if _stub is None:
        import grpc
        from app.core.config import settings
        _channel = grpc.insecure_channel(settings.K8S_EXECUTOR_GRPC_ADDR)
        _stub = executor_pb2_grpc.ExecutorServiceStub(_channel)
    return _stub


async def _resolve_user(token: str):
    """从 token 解析用户（WebSocket 无 Authorization 头，token 走 query param）"""
    from app.models import User
    try:
        payload = decode_jwt(token)
    except JWTError:
        return None
    user_id = int(payload.get("sub", 0))
    async with async_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    return user if user and user.is_active else None


@router.websocket("/ws/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/exec")
async def ws_exec_pod(websocket: WebSocket, cluster_id: int, namespace: str, pod_name: str):
    """交互式终端 WebSocket（终端默认开，走平台 RBAC 鉴权）。

    浏览器 → WebSocket → executor gRPC 双向流 → k8s exec（TTY）
    """
    token = websocket.query_params.get("token", "")
    container = websocket.query_params.get("container", "")

    user = await _resolve_user(token)
    if user is None:
        await websocket.close(code=4001, reason="未认证")
        return
    perm = await user_permission_service.check_cluster_permission(user, cluster_id, namespace, "operate")
    if not perm:
        await websocket.close(code=4003, reason="无权限")
        return
    await websocket.accept()

    # 线程安全队列：WebSocket 主循环 → gRPC 请求流
    req_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()

    def _grpc_generator():
        yield executor_pb2.ExecPodStreamFrame(
            cluster_id=cluster_id, namespace=namespace, pod_name=pod_name,
            container=container, tty=True,
        )
        while True:
            frame = req_queue.get()  # 阻塞等待 stdin（WebSocket 主循环 put）
            yield frame

    def _run_grpc():
        """独立线程跑 gRPC 双向流：generator 阻塞等待 stdin，响应循环独立收 stdout"""
        try:
            stub = _get_stub()
            stream = stub.ExecPodStream(_grpc_generator())
            for resp in stream:
                if resp.closed:
                    if resp.error:
                        asyncio.run_coroutine_threadsafe(_send_error(resp.error), loop)
                    asyncio.run_coroutine_threadsafe(_send_exit(resp.exit_code), loop)
                    return
                if resp.stdout:
                    asyncio.run_coroutine_threadsafe(_ws_send_bytes(resp.stdout), loop)
                if resp.stderr:
                    asyncio.run_coroutine_threadsafe(_ws_send_bytes(resp.stderr), loop)
        except Exception as e:  # noqa: BLE001
            logger.warning("terminal gRPC 流异常: %s", e)
            asyncio.run_coroutine_threadsafe(_send_error(str(e)), loop)
            asyncio.run_coroutine_threadsafe(_send_exit(-1), loop)

    async def _ws_send_bytes(data: bytes):
        try:
            await websocket.send_bytes(bytes(data))
        except Exception:  # noqa: BLE001
            pass

    async def _send_exit(code: int):
        try:
            await websocket.send_json({"type": "exit", "code": code})
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass

    async def _send_error(message: str):
        try:
            await websocket.send_json({"type": "error", "message": message})
        except Exception:  # noqa: BLE001
            pass

    loop = asyncio.get_running_loop()
    grpc_thread = threading.Thread(target=_run_grpc, daemon=True)
    grpc_thread.start()

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg["type"] == "websocket.receive":
                if msg.get("bytes"):
                    req_queue.put(executor_pb2.ExecPodStreamFrame(stdin=bytes(msg["bytes"])))
                elif msg.get("text"):
                    text = msg["text"]
                    try:
                        j = json.loads(text)
                        if j.get("type") == "resize":
                            req_queue.put(executor_pb2.ExecPodStreamFrame(
                                rows=int(j.get("rows", 0)), cols=int(j.get("cols", 0))))
                    except (json.JSONDecodeError, ValueError):
                        # 非 JSON 文本 = stdin 键盘输入（xterm.js onData 发的是字符串）
                        req_queue.put(executor_pb2.ExecPodStreamFrame(stdin=text.encode("utf-8")))
    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
