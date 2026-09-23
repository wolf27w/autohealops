# -*- coding: utf-8 -*-
"""AI 诊断助手 API：会话 CRUD + 对话 + 闭环落盘（按 user_id 隔离 + RBAC）"""
import asyncio
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.middleware.permission import require_any_user
from app.models import Cluster, DiagnosisMessage, DiagnosisSession
from app.services import agent_service, user_permission_service

router = APIRouter(prefix="/api/v1/agent")
logger = get_logger("api-server.agent")

# 后台任务引用集合：防止 asyncio 任务被 GC 提前回收
_bg_tasks: set[asyncio.Task] = set()


async def _run_first_turn(sid: int, problem: str, cluster_name: str, deep: bool) -> None:
    """后台执行会话首轮诊断（创建接口立即返回，不阻塞 HTTP 请求）

    异常只记日志，不再抛给请求方；诊断结果由前端轮询 /messages 获取。
    """
    try:
        async with async_session() as s:
            sess = await s.get(DiagnosisSession, sid)
        if not sess:
            logger.warning("后台首轮诊断：会话 %d 不存在", sid)
            return
        await agent_service.run_agent_turn(sess, problem, cluster_name, deep=deep)
        logger.info("会话 %d 首轮诊断完成（后台）", sid)
    except Exception as e:  # noqa: BLE001
        logger.warning("会话 %d 首轮诊断失败（后台）: %s", sid, e)


async def _run_turn(sid: int, content: str, cluster_name: str, deep: bool) -> None:
    """后台执行一轮诊断（发送消息接口立即返回，诊断丢后台，前端轮询结果）"""
    try:
        async with async_session() as s:
            sess = await s.get(DiagnosisSession, sid)
        if not sess:
            logger.warning("后台诊断：会话 %d 不存在", sid)
            return
        await agent_service.run_agent_turn(sess, content, cluster_name, deep=deep)
        logger.info("会话 %d 诊断完成（后台）", sid)
    except Exception as e:  # noqa: BLE001
        logger.warning("会话 %d 诊断失败（后台）: %s", sid, e)


@router.post("/sessions")
async def create_session(payload: dict, user=Depends(require_any_user)):
    """创建诊断会话（校验集群权限 + RBAC 可见集群）"""
    cluster_id = payload.get("cluster_id")
    namespace = payload.get("namespace", "")
    pod_name = payload.get("pod_name", "")
    problem = payload.get("problem", "")
    deep = bool(payload.get("deep", False))  # 深度诊断开关（默认关 = 快速单轮）

    if not cluster_id or not pod_name:
        return {"code": 400, "message": "cluster_id 和 pod_name 必填", "data": None}

    # RBAC：校验集群/命名空间权限
    if not await user_permission_service.check_cluster_permission(user, cluster_id, namespace):
        return {"code": 403, "message": "无该集群/命名空间权限", "data": None}

    # 取集群名（诊断上下文用）
    cluster_name = ""
    async with async_session() as s:
        c = await s.get(Cluster, cluster_id)
        if c:
            cluster_name = c.name

    async with async_session() as s:
        sess = DiagnosisSession(
            user_id=user.id,
            cluster_id=cluster_id,
            namespace=namespace,
            pod_name=pod_name,
            title=(problem or pod_name)[:200],
            status="active",
        )
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        sid = sess.id

    # 首轮对话（若带 problem 则立即执行诊断）
    # 提速·方案 A：创建接口只建会话并立即返回，首轮诊断丢后台跑，
    # 避免慢诊断导致前端 axios 30s 超时 → 窗口不关 + 重复建会话。
    if problem:
        task = asyncio.create_task(_run_first_turn(sid, problem, cluster_name, deep))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    return {"code": 200, "message": "success", "data": {
        "id": sid,
        "cluster_id": cluster_id,
        "namespace": namespace,
        "pod_name": pod_name,
        "title": (problem or pod_name)[:200],
        "status": "active",
        "diagnosing": bool(problem),  # true = 首轮诊断后台执行中，前端可轮询 /messages
        "final_answer": None,
        "tool_steps": [],
    }}


@router.get("/sessions")
async def list_sessions(user=Depends(require_any_user)):
    """我的诊断会话列表（user_id 隔离）"""
    async with async_session() as s:
        result = await s.execute(
            select(DiagnosisSession).where(DiagnosisSession.user_id == user.id)
            .order_by(DiagnosisSession.id.desc())
        )
        sessions = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": x.id, "cluster_id": x.cluster_id, "namespace": x.namespace,
         "pod_name": x.pod_name, "title": x.title, "status": x.status,
         "created_at": str(x.created_at), "updated_at": str(x.updated_at)}
        for x in sessions
    ]}


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: int, user=Depends(require_any_user)):
    """会话消息列表（校验归属）"""
    async with async_session() as s:
        sess = await s.get(DiagnosisSession, session_id)
        if not sess or sess.user_id != user.id:
            return {"code": 403, "message": "无该会话权限", "data": None}
        result = await s.execute(
            select(DiagnosisMessage).where(DiagnosisMessage.session_id == session_id)
            .order_by(DiagnosisMessage.id)
        )
        msgs = list(result.scalars())
    return {"code": 200, "message": "success", "data": [
        {"id": m.id, "role": m.role, "content": m.content,
         "tool_name": m.tool_name, "tool_args": m.tool_args, "tool_output": m.tool_output,
         "created_at": str(m.created_at)}
        for m in msgs
    ]}


@router.post("/sessions/{session_id}/messages")
async def send_message(session_id: int, payload: dict, user=Depends(require_any_user)):
    """发送消息，触发一轮诊断（默认快速单轮；deep=true 走多轮深度诊断）。

    诊断在后台异步执行（不阻塞 HTTP），避免慢诊断触发前端 axios 30s 超时；
    前端轮询 /messages 获取结论（与首轮诊断一致）。
    """
    content = payload.get("content", "").strip()
    if not content:
        return {"code": 400, "message": "消息内容不能为空", "data": None}
    deep = bool(payload.get("deep", False))

    async with async_session() as s:
        sess = await s.get(DiagnosisSession, session_id)
        if not sess or sess.user_id != user.id:
            return {"code": 403, "message": "无该会话权限", "data": None}
        cluster_name = ""
        c = await s.get(Cluster, sess.cluster_id)
        if c:
            cluster_name = c.name
        sid = sess.id

    task = asyncio.create_task(_run_turn(sid, content, cluster_name, deep))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    return {"code": 200, "message": "success", "data": {"diagnosing": True}}


@router.post("/sessions/{session_id}/close")
async def close_session(session_id: int, user=Depends(require_any_user)):
    """用户确认处理完成 → 闭环落盘知识库"""
    async with async_session() as s:
        sess = await s.get(DiagnosisSession, session_id)
        if not sess or sess.user_id != user.id:
            return {"code": 403, "message": "无该会话权限", "data": None}

    result = await agent_service.close_session_and_sink(sess)
    return {"code": 200, "message": "success", "data": result}
