# -*- coding: utf-8 -*-
"""MCP 标准接口（P2-R9）：把平台诊断能力以 Model Context Protocol 暴露给外部 AI 客户端（Cursor/Claude Code 等）。

- 传输：Streamable HTTP（POST /mcp），JSON-RPC 2.0。
- 鉴权：复用平台 JWT（Authorization: Bearer <token>，require_any_user）。
- 工具：来自统一工具注册表 TOOL_REGISTRY（exec_in_pod/describe_pod/search_logs/get_pod_metrics/
        query_db_health/rag_search/get_hpa/get_deployment）。
- 写类工具（permission=operate）经 MCP 调用同样执行；如需审批控制，后续可在 tools/call 加拦截。
"""
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.middleware.permission import require_any_user
from app.services.tool_registry import TOOL_REGISTRY, coerce_args, validate_args

router = APIRouter(prefix="/mcp")

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_INFO = {"name": "autohealops", "version": "1.0.0"}

# 上下文参数：注入到每个工具的 inputSchema，调用时从 arguments 提取到 context
_CONTEXT_PARAMS = {
    "cluster_id": {"type": "integer", "description": "集群 ID（操作目标集群）"},
    "namespace": {"type": "string", "description": "命名空间"},
    "pod_name": {"type": "string", "description": "Pod 名"},
}


def _tool_schema(td: dict) -> dict:
    props: dict = {}
    required: list = []
    for k, spec in (td.get("args_schema") or {}).items():
        props[k] = {"type": spec.get("type", "string"), "description": spec.get("description", "")}
        if spec.get("required"):
            required.append(k)
    for k, spec in _CONTEXT_PARAMS.items():
        if k not in props:
            props[k] = {"type": spec["type"], "description": spec["description"]}
    return {"type": "object", "properties": props, "required": required}


def _resp(req_id, result=None, error=None):
    body = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        body["error"] = error
    else:
        body["result"] = result
    return JSONResponse(body)


@router.post("")
async def mcp_endpoint(request: Request, user=Depends(require_any_user)):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "invalid JSON"}}, status_code=400)

    method = body.get("method")
    req_id = body.get("id")
    params = body.get("params") or {}

    # 通知类（无 id）：返回 202，不带 body
    if method in ("notifications/initialized", "notifications/cancelled") or req_id is None:
        return JSONResponse(None, status_code=202)

    if method == "initialize":
        return _resp(req_id, result={
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": _SERVER_INFO,
        })

    if method == "ping":
        return _resp(req_id, result={})

    if method == "tools/list":
        tools = [{
            "name": name,
            "description": td.get("description", ""),
            "inputSchema": _tool_schema(td),
        } for name, td in TOOL_REGISTRY.items()]
        return _resp(req_id, result={"tools": tools})

    if method == "tools/call":
        name = params.get("name", "")
        arguments = dict(params.get("arguments") or {})
        td = TOOL_REGISTRY.get(name)
        if not td:
            return _resp(req_id, error={"code": -32602, "message": f"未知工具：{name}"})

        context = {
            "cluster_id": arguments.get("cluster_id"),
            "namespace": arguments.get("namespace", ""),
            "pod_name": arguments.get("pod_name", ""),
            "user_id": getattr(user, "id", None),
            "session_id": None,
            "db_conn_id": arguments.get("conn_id"),
        }
        args = coerce_args(td, arguments)
        ok, msg = validate_args(td, args)
        if not ok:
            return _resp(req_id, error={"code": -32602, "message": msg})

        try:
            r = await td["handler"](args, context)
        except Exception as e:  # noqa: BLE001
            r = {"success": False, "output": "", "error": str(e)}

        text = r.get("output") or r.get("error") or "(无输出)"
        return _resp(req_id, result={
            "content": [{"type": "text", "text": text}],
            "isError": not bool(r.get("success", False)),
        })

    return _resp(req_id, error={"code": -32601, "message": f"方法不支持：{method}"})


@router.get("")
async def mcp_info():
    """供探测：返回 MCP 服务信息。"""
    return {"protocol": "mcp", "transport": "streamable-http", "version": _PROTOCOL_VERSION,
            "tools": [t for t in TOOL_REGISTRY]}
