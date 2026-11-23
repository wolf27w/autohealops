# -*- coding: utf-8 -*-
"""Agent 反向隧道网关（R12：agent 主动外连接入模式）。

agent（集群内单 Deployment）主动连本网关，建立 gRPC 双向流 Tunnel；
平台把 executor 请求（method + 序列化 payload）经隧道下发，agent 在集群内执行后回传响应。

- 接入判定：某 cluster 的 agent 当前已连接 → 该集群走隧道（无需额外开关）。
- 传输：同步 grpc.server（跑在后台线程），与现有同步 executor_client 兼容（经 acall 走线程池）。
"""
import json
import queue
import threading
import time
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor

import grpc

from app.core.config import settings
from app.core.logging import get_logger
from app.proto import agent_pb2, agent_pb2_grpc

logger = get_logger("api-server.agent_gateway")

_agents: dict[int, "_AgentConn"] = {}
_agents_lock = threading.Lock()
_server: grpc.Server | None = None
_loop = None


async def _verify_and_mark(cluster_id: int, token: str, version: str = "", capabilities: list | None = None) -> str:
    """校验 agent 令牌（按集群 sha256 哈希），通过则标记 connected + 心跳 + 版本 + 能力；返回空串=成功

    capabilities 为上报的能力列表（protobuf repeated 字段，老 agent 未上报时为空列表）。
    空列表 = 老 agent / 无能力 → 平台按能力缺失处理（不阻断现有功能）。
    """
    import hashlib
    from datetime import datetime
    from app.core.database import async_session
    from app.models import Cluster
    try:
        async with async_session() as s:
            c = await s.get(Cluster, int(cluster_id))
            if c is None:
                return "集群不存在"
            h = c.agent_token_hash or ""
            if not h or hashlib.sha256((token or "").encode()).hexdigest() != h:
                return "token 无效"
            c.agent_status = "connected"
            c.agent_last_seen_at = datetime.now()
            if version:
                c.agent_version = version[:50]
            if capabilities is not None:
                c.agent_capabilities = json.dumps(list(capabilities), ensure_ascii=False)
            need_rbac = (c.rbac_mode or "off") == "off"
            await s.commit()
        if need_rbac:
            asyncio.create_task(_post_connect_setup(int(cluster_id)))
        return ""
    except Exception as e:  # noqa: BLE001
        return f"验证异常: {e}"


async def _post_connect_setup(cluster_id: int) -> None:
    """agent 首次连上后自动接入云原生 RBAC（agent 集群没有 kubeconfig，必须在连上后做）"""
    try:
        from app.services import rbac_service
        await rbac_service.setup_cluster_rbac(cluster_id)
        logger.info("agent 集群 %s 已自动接入 RBAC", cluster_id)
    except Exception as e:  # noqa: BLE001 失败不影响隧道
        logger.warning("agent 集群 %s 自动接入 RBAC 失败: %s", cluster_id, e)


async def _mark_disconnected(cluster_id: int) -> None:
    """agent 断开 → 标记 disconnected（best-effort）"""
    from app.core.database import async_session
    from app.models import Cluster
    try:
        async with async_session() as s:
            c = await s.get(Cluster, int(cluster_id))
            if c and c.agent_status == "connected":
                c.agent_status = "disconnected"
                await s.commit()
    except Exception:  # noqa: BLE001
        pass


class _AgentConn:
    def __init__(self, cluster_id: int):
        self.cluster_id = int(cluster_id)
        self.out: "queue.Queue" = queue.Queue()
        self.pending: dict[str, dict] = {}
        # 流式会话：req_id → queue.Queue（stream_msg 帧投递到此；结束时投 _STREAM_END）
        self.streams: dict[str, "queue.Queue"] = {}
        self.alive = True
        # R59：agent 上报的版本与能力（内存态，供能力协商用）
        self.version: str = ""
        self.capabilities: frozenset = frozenset()

    def fail_streams(self, msg: str) -> None:
        for q in list(self.streams.values()):
            q.put(RuntimeError(msg))


def is_connected(cluster_id: int) -> bool:
    """该集群是否有 agent 在线（在线=走反向隧道）。"""
    with _agents_lock:
        a = _agents.get(int(cluster_id))
    return bool(a and a.alive)


def agent_capabilities(cluster_id: int):
    """R59：该集群在线 agent 的能力集合（frozenset[str]）；未连接返回 None。

    注意区分：未连接= None（不该做能力拦截，走直连/错误兑底）；
    已连接但老 agent 未上报能力 = frozenset()（空集合 → 视为能力全缺 → 提示升级）。
    """
    with _agents_lock:
        a = _agents.get(int(cluster_id))
    if not a or not a.alive:
        return None
    return a.capabilities


def agent_version(cluster_id: int) -> str:
    """R59：该集群在线 agent 版本（未连接/未上报返回空串）"""
    with _agents_lock:
        a = _agents.get(int(cluster_id))
    if not a or not a.alive:
        return ""
    return a.version or ""


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


_STREAM_END = object()


def stream_call(cluster_id: int, method: str, payload: bytes, timeout: float | None = 300.0):
    """服务端流式调用：逐条 yield 响应 payload 字节（ExecutePlan / BatchExecute）"""
    with _agents_lock:
        a = _agents.get(int(cluster_id))
    if not a or not a.alive:
        raise RuntimeError(f"集群 {cluster_id} 无在线 agent")
    req_id = uuid.uuid4().hex
    q: "queue.Queue" = queue.Queue()
    a.streams[req_id] = q
    logger.info("隧道流式下发 cluster=%s method=%s", cluster_id, method)
    a.out.put(agent_pb2.AgentFrame(type="stream_open", req_id=req_id, method=method, payload=payload))
    deadline = (time.time() + timeout) if timeout else None
    try:
        while True:
            remain = None if deadline is None else max(0.1, deadline - time.time())
            if deadline is not None and remain <= 0.1:
                raise TimeoutError(f"agent 流式超时（{method}）")
            item = q.get(timeout=remain)
            if item is _STREAM_END:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        a.streams.pop(req_id, None)


def bidi_call(cluster_id: int, method: str, request_iterator, timeout: float | None = None):
    """双向流调用（ExecPodStream 交互终端）：平台→agent 发消息由后台线程泵送"""
    with _agents_lock:
        a = _agents.get(int(cluster_id))
    if not a or not a.alive:
        raise RuntimeError(f"集群 {cluster_id} 无在线 agent")
    req_id = uuid.uuid4().hex
    q: "queue.Queue" = queue.Queue()
    a.streams[req_id] = q
    logger.info("隧道双向流下发 cluster=%s method=%s", cluster_id, method)
    a.out.put(agent_pb2.AgentFrame(type="stream_open", req_id=req_id, method=method))

    def pump():
        try:
            for req in request_iterator:
                data = req if isinstance(req, (bytes, bytearray)) else req.SerializeToString()
                a.out.put(agent_pb2.AgentFrame(type="stream_msg", req_id=req_id, payload=bytes(data)))
        except Exception as e:  # noqa: BLE001 上游迭代器异常（如 WS 断开）
            logger.info("终端上行流结束: %s", e)
        finally:
            a.out.put(agent_pb2.AgentFrame(type="stream_end", req_id=req_id))

    threading.Thread(target=pump, daemon=True).start()
    deadline = (time.time() + timeout) if timeout else None
    try:
        while True:
            remain = None if deadline is None else max(0.1, deadline - time.time())
            if deadline is not None and remain <= 0.1:
                raise TimeoutError(f"agent 双向流超时（{method}）")
            item = q.get(timeout=remain)
            if item is _STREAM_END:
                return
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        a.streams.pop(req_id, None)


def _push_config(a: "_AgentConn") -> None:
    """把平台配置（告警策略）推给刚注册的 agent（best-effort，失败不影响隧道）"""
    if not _loop:
        return

    async def _build():
        from app.services.agent_alert_service import build_agent_config
        return await build_agent_config(a.cluster_id)

    try:
        cfg = asyncio.run_coroutine_threadsafe(_build(), _loop).result(timeout=8)
        payload = json.dumps(cfg, ensure_ascii=False).encode()
        a.out.put(agent_pb2.AgentFrame(type="config", method="agent_config", payload=payload))
        logger.info("已向 agent 下发配置 cluster=%s 策略=%d 条", a.cluster_id, len(cfg.get("policies") or []))
    except Exception as e:  # noqa: BLE001
        logger.warning("下发 agent 配置失败 cluster=%s: %s", a.cluster_id, e)


async def _handle_event(cluster_id: int, method: str, payload: bytes) -> None:
    """处理 agent 上行事件（异常不外抛，避免打断隧道读循环）"""
    from app.services.agent_alert_service import handle_event
    try:
        await handle_event(cluster_id, method, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("处理 agent 事件失败 cluster=%s method=%s: %s", cluster_id, method, e)


class _Servicer(agent_pb2_grpc.AgentServiceServicer):
    def Tunnel(self, request_iterator, context):  # noqa: N802 (grpc 命名)
        state = {"a": None}

        def reader():
            try:
                for f in request_iterator:
                    if f.type == "register":
                        err = ""
                        if _loop:
                            try:
                                err = asyncio.run_coroutine_threadsafe(
                                    _verify_and_mark(f.cluster_id, f.token, f.version, list(f.capabilities)), _loop
                                ).result(timeout=8)
                            except Exception as e:  # noqa: BLE001
                                err = f"验证异常: {e}"
                        else:
                            err = "网关未就绪"
                        if err:
                            logger.warning("agent 注册失败 cluster=%s: %s", f.cluster_id, err)
                            context.abort(grpc.StatusCode.UNAUTHENTICATED, err)
                            return
                        a = _AgentConn(f.cluster_id)
                        a.version = f.version or ""
                        a.capabilities = frozenset(f.capabilities or [])
                        state["a"] = a
                        with _agents_lock:
                            _agents[a.cluster_id] = a
                        a.out.put(agent_pb2.AgentFrame(type="register_ack"))
                        logger.info("agent 已接入 cluster=%s version=%s 能力=%d 项",
                                    a.cluster_id, f.version or "-", len(a.capabilities))
                        # P2：把告警策略下发给 agent（agent 内 watcher 用；agent 连不到平台库）
                        _push_config(a)
                    elif f.type == "stream_msg":
                        a = state["a"]
                        if a:
                            q = a.streams.get(f.req_id)
                            if q:
                                q.put(bytes(f.payload))
                    elif f.type == "stream_end":
                        a = state["a"]
                        if a:
                            q = a.streams.get(f.req_id)
                            if q:
                                q.put(_STREAM_END)
                    elif f.type == "stream_error":
                        a = state["a"]
                        if a:
                            q = a.streams.get(f.req_id)
                            if q:
                                q.put(RuntimeError(f.error or "agent 流式调用失败"))
                    elif f.type == "response":
                        a = state["a"]
                        if a:
                            slot = a.pending.get(f.req_id)
                            if slot:
                                slot["payload"] = bytes(f.payload)
                                slot["error"] = f.error
                                slot["event"].set()
                    elif f.type == "event":
                        # P2：agent 内 watcher 检测到的故障/恢复事件 → 平台统一写库 + 触发处理
                        a = state["a"]
                        if a and _loop:
                            asyncio.run_coroutine_threadsafe(
                                _handle_event(a.cluster_id, f.method, bytes(f.payload)), _loop
                            )
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
            a.fail_streams("agent 隧道断开")
            with _agents_lock:
                still = _agents.get(a.cluster_id) is not a
                if not still:
                    _agents.pop(a.cluster_id, None)
            # 滚动升级期间同一集群可能短时存在两条隧道：只有「确实没有别的连接」才标离线
            if not still and _loop:
                asyncio.run_coroutine_threadsafe(_mark_disconnected(a.cluster_id), _loop)
            logger.info("agent 断开 cluster=%s（仍有其他连接=%s）", a.cluster_id, still)


def start_server(addr: str) -> None:
    global _server, _loop
    if _server is not None:
        return
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = None
    # 2026-11-23：允许 agent 侧 keepalive ping（无活跃流也允许），
    # 否则默认策略会把客户端心跳判为“too many pings”直接断开 → agent 反复掉线。
    _server = grpc.server(
        ThreadPoolExecutor(max_workers=20),
        options=[
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.min_time_between_pings_ms", 10000),
            ("grpc.http2.max_pings_without_data", 0),
            ("grpc.keepalive_time_ms", 30000),
            ("grpc.keepalive_timeout_ms", 10000),
        ],
    )
    agent_pb2_grpc.add_AgentServiceServicer_to_server(_Servicer(), _server)
    tls_enabled = bool(getattr(settings, "AGENT_TLS_ENABLED", True))
    if tls_enabled:
        # 平台自建 CA 签发服务端证书；要求 agent 出示客户端证书（mTLS）
        from urllib.parse import urlparse
        from app.services import agent_pki
        hosts = []
        public = (getattr(settings, "AGENT_GATEWAY_PUBLIC_ADDR", "") or "").strip()
        if public:
            hosts.append(public.rsplit(":", 1)[0] if ":" in public else public)
        plat = (getattr(settings, "PLATFORM_PUBLIC_URL", "") or "").strip()
        if plat:
            h = urlparse(plat).hostname
            if h:
                hosts.append(h)
        ca_existed = (agent_pki.ca_dir() / "ca.crt").exists()
        crt, key = agent_pki.ensure_server_cert(hosts)
        if not ca_existed:
            logger.error("平台 CA 是本次新建的：所有已下发 agent 的客户端证书都会失效，"
                         "请确认 CA 目录（%s）已持久化，并给每个集群重新下发 install.yaml", agent_pki.ca_dir())
        with open(crt, "rb") as f:
            cert_bytes = f.read()
        with open(key, "rb") as f:
            key_bytes = f.read()
        ca_bytes = agent_pki.ca_cert_pem().encode()
        creds = grpc.ssl_server_credentials([(key_bytes, cert_bytes)],
                                            root_certificates=ca_bytes,
                                            require_client_auth=True)
        bound = _server.add_secure_port(addr, creds)
        if not bound:
            logger.error("Agent 网关端口绑定失败（%s 已被占用）：本进程将收不到 agent 隧道，"
                         "通常是旧 api-server 进程残留，请先杀掉旧进程再重启", addr)
        else:
            logger.info("Agent 反向隧道网关已启动（mTLS）: %s（CA 目录 %s）", addr, agent_pki.ca_dir())
    else:
        bound = _server.add_insecure_port(addr)
        if not bound:
            logger.error("Agent 网关端口绑定失败（%s 已被占用）：本进程将收不到 agent 隧道，"
                         "通常是旧 api-server 进程残留，请先杀掉旧进程再重启", addr)
        else:
            logger.warning("Agent 反向隧道网关已启动（明文，仅调试用）: %s", addr)
    _server.start()


def stop_server() -> None:
    """停止隧道网关：先踢掉在线 agent 连接，再停 server。

    背景（2026-11-18）：旧进程 graceful shutdown 时若隧道还有在线 agent，
    gRPC 的 Tunnel 双向流不会结束 → 进程退不掉但已释放 8080，变成“占着 9095、
    却没有 API” 的僵尸进程；新起的 api-server 拿不到 agent 隧道（bind 失败只 warn），
    表现为「agent 接入的集群报错 / unreachable」。
    """
    global _server
    if _server is None:
        return
    with _agents_lock:
        conns = list(_agents.values())
        _agents.clear()
    for a in conns:
        a.alive = False
        try:
            a.fail_streams("平台重启/停止，隧道关闭")
        except Exception:  # noqa: BLE001
            pass
    try:
        _server.stop(1)
    except Exception as e:  # noqa: BLE001
        logger.warning("停止 Agent 网关失败: %s", e)
    _server = None
    logger.info("Agent 反向隧道网关已停止（断开 %d 条 agent 连接）", len(conns))
