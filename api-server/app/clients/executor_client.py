# -*- coding: utf-8 -*-
"""k8s-executor gRPC 客户端（api-server → executor 通信）

注意：gRPC 同步调用会**阻塞事件循环**，在 async 代码里必须用 `acall(...)` 包一层
（内部走 asyncio.to_thread），否则一个慢调用（最长 30s）会把整个 API 锁死。
"""
import asyncio
import grpc

from app.core.config import settings
from app.core.logging import get_logger
from app.proto import executor_pb2, executor_pb2_grpc
from app.proto import agent_gateway_internal_pb2, agent_gateway_internal_pb2_grpc

logger = get_logger("api-server.executor_client")

# 全局 channel（懒初始化）
_channel = None
_stub = None

# A4：gRPC 并发上限。asyncio.to_thread 用默认线程池（worker 有限），
# 后台循环若同时发起大量慢调用（最长 30s）会耗尽线程池 → 用信号量限制并发。
_grpc_semaphore = asyncio.Semaphore(max(1, int(getattr(settings, "EXECUTOR_MAX_CONCURRENCY", 8))))


async def acall(fn, *args, **kwargs):
    """在 async 上下文调用同步 gRPC 方法：丢到线程池，避免阻塞事件循环。

    用法：await acall(executor_client.query_prometheus, cluster_id, promql)
    """
    async with _grpc_semaphore:
        return await asyncio.to_thread(fn, *args, **kwargs)


def _conv_mount(m) -> dict:
    """gRPC VolumeMountInfo → dict"""
    return {"name": m.name, "mount_path": m.mount_path, "sub_path": m.sub_path,
            "read_only": m.read_only, "sub_path_expr": m.sub_path_expr,
            "propagation": m.propagation}


def _conv_vol(v) -> dict:
    """gRPC VolumeInfo → dict"""
    return {"name": v.name, "type": v.type, "source": v.source, "read_only": v.read_only}


def _get_stub():
    """懒初始化 gRPC stub（隧道感知：agent 集群的方法调用自动改走反向隧道，见 _TunnelAwareStub）"""
    global _channel, _stub
    if _stub is None:
        _channel = grpc.insecure_channel(settings.K8S_EXECUTOR_GRPC_ADDR)
        _stub = _TunnelAwareStub(executor_pb2_grpc.ExecutorServiceStub(_channel))
    return _stub


# ==================== R2 拆网关阶段 1：AGENT_GATEWAY_MODE 开关 ====================
# 网关接入分两种模式：
#   inproc（默认）：进程内直调 app.agent_gateway 模块（现有行为，api-server 单副本部署）。
#   remote：agent 隧道连接表迁到独立 agent-gateway 服务后，经内部 gRPC（AgentGatewayInternal）
#           远程下发隧道调用，api-server 可多副本。
# remote 客户端懒初始化（同 _channel 惰性建 stub），仅 remote 模式且首次调用时建连。
_GW_CHANNEL = None
_GW_STUB = None


def _gw_mode() -> str:
    """当前网关模式：inproc / remote"""
    return getattr(settings, "AGENT_GATEWAY_MODE", "inproc")


def _get_gw_stub():
    """懒初始化内部网关 gRPC stub（仅 remote 模式使用）"""
    global _GW_CHANNEL, _GW_STUB
    if _GW_STUB is None:
        addr = getattr(settings, "AGENT_GATEWAY_INTERNAL_ADDR", "127.0.0.1:9096")
        _GW_CHANNEL = grpc.insecure_channel(addr)
        _GW_STUB = agent_gateway_internal_pb2_grpc.AgentGatewayInternalStub(_GW_CHANNEL)
    return _GW_STUB


def _remote_is_connected(cluster_id: int) -> bool:
    """remote 模式在线判断：走内部网关 IsConnected"""
    try:
        resp = _get_gw_stub().IsConnected(
            agent_gateway_internal_pb2.IsConnectedRequest(cluster_id=int(cluster_id)),
            timeout=5.0,
        )
        return bool(resp.connected)
    except Exception as e:  # noqa: BLE001
        logger.warning("remote 网关 IsConnected 失败（cluster=%s）: %s", cluster_id, e)
        return False


def _remote_call(cluster_id: int, method: str, payload: bytes, timeout: float = 30.0) -> bytes:
    """remote 模式单发调用：走内部网关 TunnelCall，返回响应 payload 字节"""
    t = float(timeout or 30.0)
    resp = _get_gw_stub().TunnelCall(
        agent_gateway_internal_pb2.TunnelCallRequest(
            cluster_id=int(cluster_id), method=method, payload=payload, timeout=t,
        ),
        timeout=t + 5.0,
    )
    if not resp.ok:
        raise RuntimeError(resp.error or "remote 网关 TunnelCall 失败")
    return resp.payload


def _remote_stream_call(cluster_id: int, method: str, payload: bytes, timeout: float | None = 300.0):
    """remote 模式服务端流式：走内部网关 TunnelStreamCall，逐条 yield payload 字节"""
    t = float(timeout or 300.0)
    for chunk in _get_gw_stub().TunnelStreamCall(
        agent_gateway_internal_pb2.TunnelCallRequest(
            cluster_id=int(cluster_id), method=method, payload=payload, timeout=t,
        ),
        timeout=t + 5.0,
    ):
        if chunk.error:
            raise RuntimeError(chunk.error)
        yield chunk.payload


# ---- 模式感知的网关接入层：_TunnelAwareStub / _agent_call 统一走这里，不再直调 agent_gateway ----

def _gateway_is_connected(cluster_id: int) -> bool:
    if _gw_mode() == "remote":
        return _remote_is_connected(cluster_id)
    from app import agent_gateway
    return agent_gateway.is_connected(cluster_id)


def _gateway_call(cluster_id: int, method: str, payload: bytes, timeout: float = 30.0) -> bytes:
    if _gw_mode() == "remote":
        return _remote_call(cluster_id, method, payload, timeout)
    from app import agent_gateway
    return agent_gateway.call(cluster_id, method, payload, timeout=timeout)


def _gateway_stream_call(cluster_id: int, method: str, payload: bytes, timeout: float | None = 300.0):
    if _gw_mode() == "remote":
        yield from _remote_stream_call(cluster_id, method, payload, timeout)
        return
    from app import agent_gateway
    yield from agent_gateway.stream_call(cluster_id, method, payload, timeout=timeout)


def _gateway_bidi_call(cluster_id: int, method: str, request_iterator, timeout: float | None = None):
    if _gw_mode() == "remote":
        raise RuntimeError(
            f"AGENT_GATEWAY_MODE=remote 暂不支持双向流（{method} 交互终端），R2 阶段 1 未实现"
        )
    from app import agent_gateway
    yield from agent_gateway.bidi_call(cluster_id, method, request_iterator, timeout=timeout)


# ==================== P1：全方法反向代理（agent 隧道） ====================
# 原先只有 ExecuteCommand / GetPodLogs 显式走 _agent_call，其余 30+ 个方法一律直连平台侧 executor，
# agent 接入的集群「连得上但用不了」。这里在 stub 层做一次代理：
#   请求体里的 cluster_id 对应集群有在线 agent → 序列化后经隧道下发，把响应字节反序列化返回；
#   否则原样透传给直连 stub（平台侧 executor，行为完全不变）。
# 好处：30+ 个调用点一行代码都不用改，将来新增 RPC 也自动支持。

_SERVICE_DESC = executor_pb2.DESCRIPTOR.services_by_name["ExecutorService"]
# 方法名 → 响应消息类（隧道回来的是响应字节，需要按类型反序列化）
_RESP_CLASSES = {
    m.name: getattr(executor_pb2, m.output_type.name) for m in _SERVICE_DESC.methods
}
# 流式方法：隧道目前是请求/响应模型，不代理（保持直连语义）
_STREAMING_METHODS = {
    m.name for m in _SERVICE_DESC.methods if m.server_streaming or m.client_streaming
}


class _TunnelAwareStub:
    """gRPC stub 代理：agent 在线的集群走反向隧道，其余直连 executor。"""

    __slots__ = ("_real",)

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def __getattr__(self, name):
        fn = getattr(self._real, name)
        resp_cls = _RESP_CLASSES.get(name)
        if resp_cls is None or name in _STREAMING_METHODS:
            return fn  # 流式 / 未知方法：保持直连

        def _call(req, timeout=None, metadata=None, **kwargs):
            cid = int(getattr(req, "cluster_id", 0) or 0)
            if cid:
                if _gateway_is_connected(cid):
                    data = _gateway_call(
                        cid, name, req.SerializeToString(),
                        timeout=timeout or ExecutorClient.TIMEOUT,
                    )
                    return resp_cls.FromString(data)
                # R14 pull 模式：平台直连 agent
                out = _pull_call(cid, name, req, resp_cls, timeout or ExecutorClient.TIMEOUT)
                if out is not None:
                    return out
            return fn(req, timeout=timeout, metadata=metadata, **kwargs)

        return _call

    # ---------- 流式方法（2026-11-04 起可走 agent 隧道） ----------

    def _stream_via_agent(self, req, method: str, resp_cls, timeout=None):
        """返回 (是否走隧道, 迭代器)"""
        cid = int(getattr(req, "cluster_id", 0) or 0)
        if not cid:
            return False, None
        if not _gateway_is_connected(cid):
            return False, None
        payload = req.SerializeToString()

        def gen():
            for data in _gateway_stream_call(cid, method, payload, timeout):
                yield resp_cls.FromString(data)

        return True, gen()

    def ExecutePlan(self, req, timeout=None, metadata=None, **kwargs):  # noqa: N802 (grpc 命名)
        ok, it = self._stream_via_agent(req, "ExecutePlan", executor_pb2.ExecutePlanResponse, timeout)
        if ok:
            return it
        pull_it = _pull_stream(int(getattr(req, "cluster_id", 0) or 0), "ExecutePlan",
                               req, executor_pb2.ExecutePlanResponse, timeout)
        if pull_it is not None:
            return pull_it
        return self._real.ExecutePlan(req, timeout=timeout, metadata=metadata, **kwargs)

    def BatchExecute(self, req, timeout=None, metadata=None, **kwargs):  # noqa: N802
        """批量执行是「多集群」请求：逐集群判断走隧道还是直连，保持流式语义"""
        ids = list(getattr(req, "cluster_ids", []) or [])
        if not ids:
            return self._real.BatchExecute(req, timeout=timeout, metadata=metadata, **kwargs)
        agent_ids = {i for i in ids if _gateway_is_connected(i)}
        if not agent_ids:
            return self._real.BatchExecute(req, timeout=timeout, metadata=metadata, **kwargs)

        def gen():
            for cid in ids:
                sub = executor_pb2.BatchExecuteRequest(
                    cluster_ids=[cid], namespace=req.namespace, command=req.command, trace_id=req.trace_id,
                )
                if cid in agent_ids:
                    for data in _gateway_stream_call(cid, "BatchExecute", sub.SerializeToString(), timeout):
                        yield executor_pb2.BatchExecuteResponse.FromString(data)
                else:
                    for resp in self._real.BatchExecute(sub, timeout=timeout, metadata=metadata):
                        yield resp

        return gen()

    def ExecPodStream(self, request_iterator, timeout=None, metadata=None, **kwargs):  # noqa: N802
        """双向流（交互终端）：需要先窥探首帧的 cluster_id 判断是否走隧道"""
        it = iter(request_iterator)
        try:
            first = next(it)
        except StopIteration:
            return iter(())

        def chain():
            yield first
            yield from it

        cid = int(getattr(first, "cluster_id", 0) or 0)
        if cid:
            if _gateway_is_connected(cid):
                def gen():
                    for data in _gateway_bidi_call(cid, "ExecPodStream", chain(), timeout):
                        yield executor_pb2.ExecPodStreamFrame.FromString(data)
                return gen()
            pull_gen = _pull_bidi(cid, "ExecPodStream", chain(), executor_pb2.ExecPodStreamFrame, timeout)
            if pull_gen is not None:
                return pull_gen
        return self._real.ExecPodStream(chain(), timeout=timeout, metadata=metadata, **kwargs)


# ==================== R14：pull 模式（平台直连 agent） ====================
# agent 出网受限时，平台直接连 agent 监听的 gRPC 端口（-mode=pull）。
# 目标表由后台循环每 30s 从 clusters 表刷新（agent_mode='pull'），集群增删时立即同步，
# 这样同步的 stub 里不需要查库（stub 跑在线程池，拿不到 async session）。
_PULL_TARGETS: dict[int, tuple[str, str]] = {}
_PULL_CHANNELS: dict[tuple[str, str], grpc.Channel] = {}


def set_pull_target(cluster_id: int, url: str, token: str = "") -> None:
    """设置/清除某集群的 pull 目标（url 为空=移除）"""
    cid = int(cluster_id)
    if not url:
        _PULL_TARGETS.pop(cid, None)
        return
    _PULL_TARGETS[cid] = (url.strip(), token or "")


def clear_pull_targets() -> None:
    _PULL_TARGETS.clear()


def pull_target(cluster_id: int) -> tuple[str, str] | None:
    return _PULL_TARGETS.get(int(cluster_id or 0))


def _pull_channel(url: str, timeout: float = 30.0) -> grpc.Channel:
    """按 URL 复用 channel（同一 agent 只建一个连接）"""
    key = url
    ch = _PULL_CHANNELS.get(key)
    if ch is None:
        ch = grpc.insecure_channel(url)
        _PULL_CHANNELS[key] = ch
    return ch


def _pull_md(token: str):
    return (("authorization", f"Bearer {token}"),) if token else None


def _pull_call(cluster_id: int, method: str, req, resp_cls, timeout: float):
    """pull 模式 unary 调用；非 pull 集群返回 None（调用方走原路径）"""
    tgt = pull_target(cluster_id)
    if not tgt:
        return None
    url, token = tgt
    fn = _pull_channel(url).unary_unary(
        f"/executor.ExecutorService/{method}",
        request_serializer=lambda m: m.SerializeToString(),
        response_deserializer=resp_cls.FromString,
    )
    return fn(req, timeout=timeout or ExecutorClient.TIMEOUT, metadata=_pull_md(token))


def _pull_stream(cluster_id: int, method: str, req, resp_cls, timeout: float):
    """pull 模式服务端流式；非 pull 集群返回 None"""
    tgt = pull_target(cluster_id)
    if not tgt:
        return None
    url, token = tgt
    fn = _pull_channel(url).unary_stream(
        f"/executor.ExecutorService/{method}",
        request_serializer=lambda m: m.SerializeToString(),
        response_deserializer=resp_cls.FromString,
    )

    def gen():
        for resp in fn(req, timeout=timeout, metadata=_pull_md(token)):
            yield resp

    return gen()


def _pull_bidi(cluster_id: int, method: str, frames, resp_cls, timeout: float):
    """pull 模式双向流（交互终端）；非 pull 集群返回 None"""
    tgt = pull_target(cluster_id)
    if not tgt:
        return None
    url, token = tgt
    fn = _pull_channel(url).stream_stream(
        f"/executor.ExecutorService/{method}",
        request_serializer=lambda m: m.SerializeToString(),
        response_deserializer=resp_cls.FromString,
    )

    def gen():
        call = fn(iter(frames), timeout=timeout, metadata=_pull_md(token))
        for resp in call:
            yield resp

    return gen()


def _agent_call(cluster_id: int, method: str, req, resp_cls, timeout: float):
    """R12：若该集群有在线 agent，则经反向隧道下发并反序列化响应；否则返回 None（走直连）。"""
    if not _gateway_is_connected(cluster_id):
        return None
    data = _gateway_call(cluster_id, method, req.SerializeToString(), timeout=timeout)
    return resp_cls.FromString(data)


def _metadata(trace_id: str = ""):
    """构造 gRPC metadata（携带 trace_id）"""
    return (("trace-id", trace_id),) if trace_id else None


class ExecutorClient:
    """k8s-executor gRPC 客户端封装（含超时控制 + 错误处理 + 日志）"""

    TIMEOUT = 30  # 默认超时秒

    async def acall(self, fn, *args, **kwargs):
        """在 async 上下文调用本客户端的同步 gRPC 方法（走线程池，不阻塞事件循环）

        用法：await executor_client.acall(executor_client.query_prometheus, cluster_id, promql)
        """
        async with _grpc_semaphore:
            return await asyncio.to_thread(fn, *args, **kwargs)

    def start_watch(self, cluster_id: int, trace_id: str = "") -> bool:
        try:
            resp = _get_stub().StartWatch(
                executor_pb2.StartWatchRequest(cluster_id=cluster_id),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return resp.success
        except Exception as e:  # noqa: BLE001
            logger.error("start_watch 失败: %s", e)
            return False

    def stop_watch(self, cluster_id: int, trace_id: str = "") -> bool:
        try:
            resp = _get_stub().StopWatch(
                executor_pb2.StopWatchRequest(cluster_id=cluster_id),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return resp.success
        except Exception as e:  # noqa: BLE001
            logger.error("stop_watch 失败: %s", e)
            return False

    def execute_command(self, cluster_id: int, namespace: str, command: str, trace_id: str = "") -> dict:
        """执行单条命令，返回 {success, output, error}"""
        try:
            req = executor_pb2.ExecuteCommandRequest(
                cluster_id=cluster_id, namespace=namespace, command=command, trace_id=trace_id
            )
            resp = _agent_call(cluster_id, "ExecuteCommand", req, executor_pb2.ExecuteCommandResponse, self.TIMEOUT)
            if resp is None:
                resp = _get_stub().ExecuteCommand(req, timeout=self.TIMEOUT, metadata=_metadata(trace_id))
            return {"success": resp.success, "output": resp.output, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("execute_command 失败: %s", e)
            return {"success": False, "output": "", "error": str(e)}

    def execute_plan(self, cluster_id: int, plan_id: int, plan_json: str, trace_id: str = ""):
        """流式执行修复计划，yield 每步进度"""
        try:
            for resp in _get_stub().ExecutePlan(
                executor_pb2.ExecutePlanRequest(
                    cluster_id=cluster_id, plan_id=plan_id, plan_json=plan_json, trace_id=trace_id
                ),
                timeout=600,
                metadata=_metadata(trace_id),
            ):
                yield {
                    "step_index": resp.step_index,
                    "command": resp.step_command,
                    "success": resp.success,
                    "output": resp.output,
                    "message": resp.message,
                    "finished": resp.finished,
                    "circuit_triggered": resp.circuit_triggered,
                    "rollback_command": resp.rollback_command,
                }
        except Exception as e:  # noqa: BLE001
            logger.error("execute_plan 失败: %s", e)
            yield {"step_index": -1, "success": False, "message": f"执行失败: {e}", "finished": True}

    def rollback_plan(self, cluster_id: int, plan_id: int, plan_json: str, trace_id: str = "") -> dict:
        try:
            resp = _get_stub().RollbackPlan(
                executor_pb2.RollbackPlanRequest(
                    cluster_id=cluster_id, plan_id=plan_id, plan_json=plan_json, trace_id=trace_id
                ),
                timeout=600,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "executed": list(resp.executed_rollbacks)}
        except Exception as e:  # noqa: BLE001
            logger.error("rollback_plan 失败: %s", e)
            return {"success": False, "message": str(e), "executed": []}

    def health_check(self, cluster_id: int, trace_id: str = "") -> dict:
        try:
            resp = _get_stub().HealthCheck(
                executor_pb2.HealthCheckRequest(cluster_id=cluster_id, trace_id=trace_id),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "status": resp.status, "version": resp.version, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("health_check 失败: %s", e)
            return {"success": False, "status": "unreachable", "version": "", "error": str(e)}

    def get_pod_status(self, cluster_id: int, namespace: str, pod_name: str, trace_id: str = "") -> dict:
        """查询 Pod 结构化状态（恢复判定：phase/ready/restartCount/容器状态）"""
        try:
            resp = _get_stub().GetPodStatus(
                executor_pb2.GetPodStatusRequest(
                    cluster_id=cluster_id, namespace=namespace, pod_name=pod_name
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {
                "success": resp.success,
                "phase": resp.phase,
                "ready": resp.ready,
                "restart_count": resp.restart_count,
                "message": resp.message,
                "containers": [
                    {
                        "name": c.name, "state": c.state, "ready": c.ready,
                        "restart_count": c.restart_count, "reason": c.reason,
                    }
                    for c in resp.container_statuses
                ],
            }
        except Exception as e:  # noqa: BLE001
            logger.error("get_pod_status 失败: %s", e)
            return {"success": False, "phase": "", "ready": False, "restart_count": 0, "message": str(e), "containers": []}

    def repair_image_pull(self, cluster_id: int, namespace: str, pod_name: str, trace_id: str = "") -> dict:
        """修复镜像拉取失败：定位 Deployment → 补 imagePullSecrets"""
        try:
            resp = _get_stub().RepairImagePull(
                executor_pb2.RepairImagePullRequest(
                    cluster_id=cluster_id, namespace=namespace, pod_name=pod_name
                ),
                timeout=60,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "fixed": resp.fixed}
        except Exception as e:  # noqa: BLE001
            logger.error("repair_image_pull 失败: %s", e)
            return {"success": False, "message": str(e), "fixed": False}

    def get_deployment_status(self, cluster_id: int, namespace: str, deployment_name: str, trace_id: str = "") -> dict:
        """查询 Deployment 副本状态（Pod 重建后判断是否恢复）"""
        try:
            resp = _get_stub().GetDeploymentStatus(
                executor_pb2.GetDeploymentStatusRequest(
                    cluster_id=cluster_id, namespace=namespace, deployment_name=deployment_name
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "ready": resp.ready, "replicas": resp.replicas,
                    "ready_replicas": resp.ready_replicas, "message": resp.message}
        except Exception as e:  # noqa: BLE001
            logger.error("get_deployment_status 失败: %s", e)
            return {"success": False, "ready": False, "replicas": 0, "ready_replicas": 0, "message": str(e)}

    def exec_in_pod(self, cluster_id: int, namespace: str, pod_name: str, command: str,
                    container: str = "", trace_id: str = "") -> dict:
        """容器内只读诊断命令执行（应用诊断）"""
        try:
            resp = _get_stub().ExecInPod(
                executor_pb2.ExecInPodRequest(
                    cluster_id=cluster_id, namespace=namespace, pod_name=pod_name,
                    container=container, command=command, trace_id=trace_id,
                ),
                timeout=120,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "output": resp.output, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("exec_in_pod 失败: %s", e)
            return {"success": False, "output": "", "error": str(e)}

    def list_deployment_pods(self, cluster_id: int, namespace: str, deployment_name: str) -> dict:
        """列出 Deployment 所有 Pod 状态（含新旧版本，验证修复是否真正恢复）"""
        try:
            resp = _get_stub().ListDeploymentPods(
                executor_pb2.ListDeploymentPodsRequest(
                    cluster_id=cluster_id, namespace=namespace, deployment_name=deployment_name
                ),
                timeout=self.TIMEOUT,
            )
            pods = [{"name": p.name, "phase": p.phase, "ready": p.ready, "restart_count": p.restart_count}
                    for p in resp.pods]
            return {"success": resp.success, "pods": pods, "all_ready": resp.all_ready, "message": resp.message}
        except Exception as e:  # noqa: BLE001
            logger.error("list_deployment_pods 失败: %s", e)
            return {"success": False, "pods": [], "all_ready": False, "message": str(e)}

    def list_pod_restart_info(self, cluster_id: int, trace_id: str = "") -> dict:
        """列出集群全部 Pod 的重启信息（N5 重启原因 AI 诊断检测数据源）

        返回 {success, pods: [{namespace, name, restart_count, restarted_at, phase, ready,
                               containers: [{name, restart_count, last_terminated_reason,
                                             last_terminated_exit_code, last_terminated_message}]}], error}
        """
        try:
            resp = _get_stub().ListPodRestartInfo(
                executor_pb2.ListPodRestartInfoRequest(cluster_id=cluster_id),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            pods = [{
                "namespace": p.namespace, "name": p.name,
                "restart_count": p.restart_count, "restarted_at": p.restarted_at,
                "phase": p.phase, "ready": p.ready,
                "containers": [{
                    "name": c.name, "restart_count": c.restart_count,
                    "last_terminated_reason": c.last_terminated_reason,
                    "last_terminated_exit_code": c.last_terminated_exit_code,
                    "last_terminated_message": c.last_terminated_message,
                } for c in p.containers],
            } for p in resp.pods]
            return {"success": resp.success, "pods": pods, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("list_pod_restart_info 失败: %s", e)
            return {"success": False, "pods": [], "error": str(e)}

    def collect_quota(self, cluster_id: int, namespace: str = "", trace_id: str = "") -> bool:
        try:
            resp = _get_stub().CollectQuota(
                executor_pb2.CollectQuotaRequest(cluster_id=cluster_id, namespace=namespace, trace_id=trace_id),
                timeout=120,
                metadata=_metadata(trace_id),
            )
            return resp.success
        except Exception as e:  # noqa: BLE001
            logger.error("collect_quota 失败: %s", e)
            return False

    def run_script(self, cluster_id: int, script_content: str, parameters: str, task_id: int, trace_id: str = "") -> dict:
        try:
            resp = _get_stub().RunScript(
                executor_pb2.RunScriptRequest(
                    cluster_id=cluster_id, script_content=script_content, parameters=parameters,
                    task_id=task_id, trace_id=trace_id,
                ),
                timeout=360,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "output": resp.output, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("run_script 失败: %s", e)
            return {"success": False, "output": "", "error": str(e)}

    def batch_execute(self, cluster_ids: list[int], namespace: str, command: str, trace_id: str = ""):
        """流式批量执行，yield 每集群结果"""
        try:
            for resp in _get_stub().BatchExecute(
                executor_pb2.BatchExecuteRequest(
                    cluster_ids=cluster_ids, namespace=namespace, command=command, trace_id=trace_id
                ),
                timeout=600,
                metadata=_metadata(trace_id),
            ):
                yield {"cluster_id": resp.cluster_id, "success": resp.success, "output": resp.output, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("batch_execute 失败: %s", e)

    def query_prometheus(self, cluster_id: int, promql: str, trace_id: str = "") -> dict:
        """查询集群内 Prometheus（executor port-forward 代理）→ 返回 {success, data(JSON), error}"""
        try:
            resp = _get_stub().QueryPrometheus(
                executor_pb2.QueryPrometheusRequest(cluster_id=cluster_id, promql=promql, trace_id=trace_id),
                timeout=60,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "data": resp.data, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("query_prometheus 失败: %s", e)
            return {"success": False, "data": "", "error": str(e)}

    def get_pod_logs(self, cluster_id: int, namespace: str, pod_name: str,
                     container: str = "", tail_lines: int = 200, previous: bool = False, trace_id: str = "") -> dict:
        """查看 Pod 容器日志 → 返回 {success, logs, error}"""
        try:
            req = executor_pb2.GetPodLogsRequest(
                cluster_id=cluster_id, namespace=namespace, pod_name=pod_name,
                container=container, tail_lines=tail_lines, previous=previous,
            )
            resp = _agent_call(cluster_id, "GetPodLogs", req, executor_pb2.GetPodLogsResponse, self.TIMEOUT)
            if resp is None:
                resp = _get_stub().GetPodLogs(req, timeout=self.TIMEOUT, metadata=_metadata(trace_id))
            return {"success": resp.success, "logs": resp.logs, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("get_pod_logs 失败: %s", e)
            return {"success": False, "logs": "", "error": str(e)}

    def get_pod_yaml(self, cluster_id: int, namespace: str, pod_name: str, trace_id: str = "") -> dict:
        """获取 Pod YAML → 返回 {success, yaml, error}"""
        try:
            resp = _get_stub().GetPodYaml(
                executor_pb2.GetPodYamlRequest(cluster_id=cluster_id, namespace=namespace, pod_name=pod_name),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "yaml": resp.yaml, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("get_pod_yaml 失败: %s", e)
            return {"success": False, "yaml": "", "error": str(e)}

    def apply_pod_yaml(self, cluster_id: int, namespace: str, yaml_content: str, trace_id: str = "") -> dict:
        """应用 Pod YAML → 返回 {success, message, error}"""
        try:
            resp = _get_stub().ApplyPodYaml(
                executor_pb2.ApplyPodYamlRequest(
                    cluster_id=cluster_id, namespace=namespace, yaml_content=yaml_content, trace_id=trace_id,
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("apply_pod_yaml 失败: %s", e)
            return {"success": False, "message": "", "error": str(e)}

    def list_pods(self, cluster_id: int, namespace: str = "", trace_id: str = "") -> dict:
        """命名空间 Pod 列表（含创建时间/就绪/重启）→ 返回 {success, pods, error}"""
        try:
            resp = _get_stub().ListPods(
                executor_pb2.ListPodsRequest(cluster_id=cluster_id, namespace=namespace),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "pods": [
                {"namespace": p.namespace, "name": p.name, "phase": p.phase, "ready": p.ready,
                 "restart_count": p.restart_count, "created_at": p.created_at,
                 "node_name": p.node_name, "pod_ip": p.pod_ip,
                 "containers": list(p.containers), "init_containers": list(p.init_containers),
                 "status": p.status, "ready_containers": p.ready_containers,
                 "total_containers": p.total_containers} for p in resp.pods], "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("list_pods 失败: %s", e)
            return {"success": False, "pods": [], "error": str(e)}

    def list_namespaces(self, cluster_id: int, trace_id: str = "") -> dict:
        """命名空间列表（name + labels）→ 返回 {success, namespaces:[{name,labels}], error}。R19 发布标签门控。"""
        try:
            resp = _get_stub().ListNamespaces(
                executor_pb2.ListNamespacesRequest(cluster_id=cluster_id),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "namespaces": [
                {"name": n.name, "labels": n.labels} for n in resp.namespaces], "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("list_namespaces 失败: %s", e)
            return {"success": False, "namespaces": [], "error": str(e)}

    def get_pod_events(self, cluster_id: int, namespace: str, pod_name: str, trace_id: str = "") -> dict:
        """单个 Pod 的 k8s Event → 返回 {success, events, error}"""
        try:
            resp = _get_stub().GetPodEvents(
                executor_pb2.GetPodEventsRequest(cluster_id=cluster_id, namespace=namespace, pod_name=pod_name),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "events": [
                {"type": e.type, "reason": e.reason, "message": e.message, "count": e.count,
                 "first_timestamp": e.first_timestamp, "last_timestamp": e.last_timestamp,
                 "source": e.source} for e in resp.events], "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("get_pod_events 失败: %s", e)
            return {"success": False, "events": [], "error": str(e)}

    def cleanup_evicted(self, cluster_id: int, trace_id: str = "") -> dict:
        """清理该集群已驱逐（Evicted）的 Pod，返回 {success, deleted, message, error}"""
        try:
            resp = _get_stub().CleanupEvicted(
                executor_pb2.CleanupEvictedRequest(cluster_id=cluster_id),
                timeout=self.TIMEOUT * 3,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "deleted": int(resp.deleted),
                    "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("cleanup_evicted 失败: %s", e)
            return {"success": False, "deleted": 0, "message": "", "error": str(e)}

    def get_pod_detail(self, cluster_id: int, namespace: str, pod_name: str, trace_id: str = "") -> dict:
        """Pod 详情（含容器组内容）→ 返回 {success, detail, error}"""
        try:
            resp = _get_stub().GetPodDetail(
                executor_pb2.GetPodDetailRequest(cluster_id=cluster_id, namespace=namespace, pod_name=pod_name),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            if not resp.success:
                return {"success": False, "detail": None, "error": resp.error}

            def _conv(cs):
                return {"name": cs.name, "image": cs.image, "state": cs.state, "ready": cs.ready,
                        "restart_count": cs.restart_count, "reason": cs.reason,
                        "started_at": cs.started_at, "finished_at": cs.finished_at, "is_init": cs.is_init,
                        "mounts": [_conv_mount(m) for m in cs.mounts]}

            return {"success": True, "detail": {
                "name": resp.name, "namespace": resp.namespace, "phase": resp.phase,
                "ready": resp.ready, "restart_count": resp.restart_count,
                "created_at": resp.created_at, "node_name": resp.node_name,
                "pod_ip": resp.pod_ip, "host_ip": resp.host_ip,
                "containers": [_conv(c) for c in resp.containers],
                "init_containers": [_conv(c) for c in resp.init_containers],
                "volumes": [_conv_vol(v) for v in resp.volumes],
                "owner_kind": resp.owner_kind, "owner_name": resp.owner_name,
                "owner_namespace": resp.owner_namespace,
            }, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("get_pod_detail 失败: %s", e)
            return {"success": False, "detail": None, "error": str(e)}

    def get_workload_mounts(self, cluster_id: int, kind: str, namespace: str, name: str, trace_id: str = "") -> dict:
        """工作负载 Pod 模板挂载配置（volumes + 容器 volumeMounts）→ 返回 {success, volumes, containers, init_containers, error}"""
        try:
            resp = _get_stub().GetWorkloadMounts(
                executor_pb2.GetWorkloadMountsRequest(cluster_id=cluster_id, kind=kind, namespace=namespace, name=name),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            if not resp.success:
                return {"success": False, "volumes": [], "containers": [], "init_containers": [], "error": resp.error}

            def _conv_cm(c):
                return {"name": c.name, "mounts": [_conv_mount(m) for m in c.mounts]}

            return {"success": True, "error": resp.error,
                    "volumes": [_conv_vol(v) for v in resp.volumes],
                    "containers": [_conv_cm(c) for c in resp.containers],
                    "init_containers": [_conv_cm(c) for c in resp.init_containers]}
        except Exception as e:  # noqa: BLE001
            logger.error("get_workload_mounts 失败: %s", e)
            return {"success": False, "volumes": [], "containers": [], "init_containers": [], "error": str(e)}

    def list_misc_resources(self, cluster_id: int, namespace: str = "", trace_id: str = "") -> dict:
        """集群管理其它资源（服务/路由/ConfigMap/Secret/PVC）→ 返回 {success, services, ingresses, configmaps, secrets, pvcs, error}"""
        try:
            resp = _get_stub().ListMiscResources(
                executor_pb2.ListMiscResourcesRequest(cluster_id=cluster_id, namespace=namespace),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "error": resp.error,
                    "services": [{"namespace": x.namespace, "name": x.name, "type": x.type,
                                   "cluster_ip": x.cluster_ip, "ports": x.ports, "external_ip": x.external_ip}
                                  for x in resp.services],
                    "ingresses": [{"namespace": x.namespace, "name": x.name, "hosts": x.hosts,
                                    "class": getattr(x, "class", ""), "address": x.address} for x in resp.ingresses],
                    "configmaps": [{"namespace": x.namespace, "name": x.name, "data_count": x.data_count}
                                    for x in resp.configmaps],
                    "secrets": [{"namespace": x.namespace, "name": x.name, "type": x.type,
                                  "data_count": x.data_count} for x in resp.secrets],
                    "pvcs": [{"namespace": x.namespace, "name": x.name, "status": x.status,
                               "capacity": x.capacity, "storage_class": x.storage_class,
                               "volume_name": x.volume_name, "access_modes": x.access_modes} for x in resp.pvcs],
                    "storage_classes": [{"name": x.name, "provisioner": x.provisioner,
                                         "is_default": x.is_default} for x in resp.storage_classes]}
        except Exception as e:  # noqa: BLE001
            logger.error("list_misc_resources 失败: %s", e)
            return {"success": False, "error": str(e)}

    def get_workload_yaml(self, cluster_id: int, kind: str, namespace: str, name: str, trace_id: str = "") -> dict:
        """获取工作负载（整个服务）YAML → 返回 {success, yaml, error}"""
        try:
            resp = _get_stub().GetWorkloadYaml(
                executor_pb2.GetWorkloadYamlRequest(cluster_id=cluster_id, kind=kind, namespace=namespace, name=name),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "yaml": resp.yaml, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("get_workload_yaml 失败: %s", e)
            return {"success": False, "yaml": "", "error": str(e)}

    def apply_workload_yaml(self, cluster_id: int, kind: str, namespace: str, name: str,
                            yaml_content: str, trace_id: str = "") -> dict:
        """应用工作负载（整个服务）YAML → 返回 {success, message, error}"""
        try:
            resp = _get_stub().ApplyWorkloadYaml(
                executor_pb2.ApplyWorkloadYamlRequest(
                    cluster_id=cluster_id, kind=kind, namespace=namespace, name=name,
                    yaml_content=yaml_content, trace_id=trace_id,
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("apply_workload_yaml 失败: %s", e)
            return {"success": False, "message": "", "error": str(e)}

    def delete_resource(self, cluster_id: int, kind: str, namespace: str, name: str, trace_id: str = "") -> dict:
        """删除配置类对象（ConfigMap/Secret）→ 返回 {success, message, error}"""
        try:
            resp = _get_stub().DeleteResource(
                executor_pb2.DeleteResourceRequest(
                    cluster_id=cluster_id, kind=kind, namespace=namespace, name=name, trace_id=trace_id,
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("delete_resource 失败: %s", e)
            return {"success": False, "message": "", "error": str(e)}

    def setup_rbac(self, cluster_id: int, trace_id: str = "") -> dict:
        """云原生 RBAC 接入：能力探测 + 下发 ClusterRole + 自举 automation SA
        → 返回 {success, rbac_capable, impersonate_capable, cluster_roles, sa_created, error}
        """
        try:
            resp = _get_stub().SetupRBAC(
                executor_pb2.SetupRBACRequest(cluster_id=cluster_id, trace_id=trace_id),
                timeout=60,
                metadata=_metadata(trace_id),
            )
            return {
                "success": resp.success,
                "rbac_capable": resp.rbac_capable,
                "impersonate_capable": resp.impersonate_capable,
                "cluster_roles": list(resp.cluster_roles),
                "sa_created": resp.sa_created,
                "error": resp.error,
            }
        except Exception as e:  # noqa: BLE001
            logger.error("setup_rbac 失败: %s", e)
            return {"success": False, "rbac_capable": False, "impersonate_capable": False,
                    "cluster_roles": [], "sa_created": False, "error": str(e)}

    def reconcile_role_bindings(self, cluster_id: int, bindings: list[dict], trace_id: str = "") -> dict:
        """项目/成员变更 → reconcile RoleBinding（绑组）

        bindings: [{"namespace": "qa-eft", "group": "autohealops:proj:1:read", "cluster_role": "autohealops-read"}, ...]
        → 返回 {success, created, updated, deleted, error}
        """
        try:
            specs = [
                executor_pb2.RoleBindingSpec(
                    namespace=b.get("namespace", ""),
                    group=b.get("group", ""),
                    cluster_role=b.get("cluster_role", ""),
                )
                for b in bindings
            ]
            resp = _get_stub().ReconcileRoleBindings(
                executor_pb2.ReconcileRoleBindingsRequest(cluster_id=cluster_id, bindings=specs, trace_id=trace_id),
                timeout=60,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "created": resp.created, "updated": resp.updated,
                    "deleted": resp.deleted, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("reconcile_role_bindings 失败: %s", e)
            return {"success": False, "created": 0, "updated": 0, "deleted": 0, "error": str(e)}

    # ============ 工作负载运维（专项 D） ============

    def list_workloads(self, cluster_id: int, namespace: str = "", trace_id: str = "") -> dict:
        """列出工作负载（无状态/有状态/守护集/定时任务）→ {success, workloads, error}"""
        try:
            resp = _get_stub().ListWorkloads(
                executor_pb2.ListWorkloadsRequest(cluster_id=cluster_id, namespace=namespace),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "error": resp.error, "workloads": [
                {"kind": w.kind, "name": w.name, "namespace": w.namespace,
                 "replicas": w.replicas, "ready_replicas": w.ready_replicas}
                for w in resp.workloads
            ]}
        except Exception as e:  # noqa: BLE001
            logger.error("list_workloads 失败: %s", e)
            return {"success": False, "error": str(e), "workloads": []}

    def scale_workload(self, cluster_id: int, kind: str, namespace: str, name: str, replicas: int, trace_id: str = "") -> dict:
        try:
            resp = _get_stub().ScaleWorkload(
                executor_pb2.ScaleWorkloadRequest(
                    cluster_id=cluster_id, kind=kind, namespace=namespace, name=name, replicas=replicas, trace_id=trace_id,
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("scale_workload 失败: %s", e)
            return {"success": False, "message": "", "error": str(e)}

    def rollout_workload(self, cluster_id: int, kind: str, namespace: str, name: str, action: str, revision: int = 0, trace_id: str = "") -> dict:
        try:
            resp = _get_stub().RolloutWorkload(
                executor_pb2.RolloutWorkloadRequest(
                    cluster_id=cluster_id, kind=kind, namespace=namespace, name=name,
                    action=action, revision=revision, trace_id=trace_id,
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("rollout_workload 失败: %s", e)
            return {"success": False, "message": "", "error": str(e)}

    def rollout_history(self, cluster_id: int, namespace: str, name: str) -> dict:
        try:
            resp = _get_stub().RolloutHistory(
                executor_pb2.RolloutHistoryRequest(cluster_id=cluster_id, namespace=namespace, name=name),
                timeout=self.TIMEOUT,
            )
            return {"success": resp.success, "revisions": list(resp.revisions), "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("rollout_history 失败: %s", e)
            return {"success": False, "revisions": [], "error": str(e)}

    def update_probe(self, cluster_id: int, namespace: str, name: str, probe: dict, trace_id: str = "") -> dict:
        try:
            resp = _get_stub().UpdateProbe(
                executor_pb2.UpdateProbeRequest(
                    cluster_id=cluster_id, namespace=namespace, name=name,
                    container=probe.get("container", ""),
                    probe_type=probe.get("probe_type", ""),
                    probe_kind=probe.get("probe_kind", ""),
                    path=probe.get("path", ""),
                    port=probe.get("port", 0),
                    command=probe.get("command", []),
                    initial_delay_seconds=probe.get("initial_delay_seconds", 0),
                    period_seconds=probe.get("period_seconds", 0),
                    timeout_seconds=probe.get("timeout_seconds", 0),
                    failure_threshold=probe.get("failure_threshold", 0),
                    success_threshold=probe.get("success_threshold", 0),
                    trace_id=trace_id,
                ),
                timeout=self.TIMEOUT,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("update_probe 失败: %s", e)
            return {"success": False, "message": "", "error": str(e)}

    def recreate_workload(self, cluster_id: int, kind: str, namespace: str, name: str, trace_id: str = "") -> dict:
        try:
            resp = _get_stub().RecreateWorkload(
                executor_pb2.RecreateWorkloadRequest(
                    cluster_id=cluster_id, kind=kind, namespace=namespace, name=name, trace_id=trace_id,
                ),
                timeout=180,
                metadata=_metadata(trace_id),
            )
            return {"success": resp.success, "message": resp.message, "error": resp.error}
        except Exception as e:  # noqa: BLE001
            logger.error("recreate_workload 失败: %s", e)
            return {"success": False, "message": "", "error": str(e)}


# ============ 进程级单例（去重：原 14 处各自实例化，导致 14 条 gRPC 通道） ============
executor_client = ExecutorClient()
