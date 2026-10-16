# -*- coding: utf-8 -*-
"""AI 诊断助手 API：会话 CRUD + 对话 + 闭环落盘（按 user_id 隔离 + RBAC）"""
import asyncio
import json

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.middleware.permission import require_any_user, require_operator
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
    """我的诊断会话列表（user_id 隔离）。

    - 普通用户：仅自己发起的会话。
    - admin：额外包含平台巡检自动发起的会话（source='proactive'，user_id=0），便于统一查看。
    """
    is_admin = getattr(user, "role", "") == "admin"
    async with async_session() as s:
        cond = DiagnosisSession.user_id == user.id
        if is_admin:
            from sqlalchemy import or_
            cond = or_(DiagnosisSession.user_id == user.id, DiagnosisSession.source == "proactive")
        result = await s.execute(
            select(DiagnosisSession).where(cond)
            .order_by(DiagnosisSession.id.desc()).limit(500)
        )
        sessions = list(result.scalars())
    # N5：巡检会话合并——同 (cluster,namespace,pod) 只保留最新一条，其余计入 merged_count
    merged: list = []
    counts: dict[tuple, int] = {}
    for x in sessions:
        src = getattr(x, "source", "agent") or "agent"
        if src == "proactive":
            k = (x.cluster_id, x.namespace, x.pod_name)
            if k in counts:
                counts[k] += 1
                continue
            counts[k] = 0
        merged.append(x)
    # 集群 id → (name, env_tag)
    cids = {x.cluster_id for x in merged if x.cluster_id}
    cluster_info: dict[int, tuple[str, str]] = {}
    if cids:
        async with async_session() as s2:
            for c in (await s2.execute(select(Cluster).where(Cluster.id.in_(cids)))).scalars():
                cluster_info[c.id] = (c.name, c.env_tag or "")
    return {"code": 200, "message": "success", "data": [
        {"id": x.id, "cluster_id": x.cluster_id, "namespace": x.namespace,
         "pod_name": x.pod_name, "title": x.title, "status": x.status,
         "cluster_name": cluster_info.get(x.cluster_id, ("", ""))[0] if x.cluster_id else "",
         "env_tag": cluster_info.get(x.cluster_id, ("", ""))[1] if x.cluster_id else "",
         "source": getattr(x, "source", "agent") or "agent",
         "merged_count": counts.get((x.cluster_id, x.namespace, x.pod_name), 0)
                          if (getattr(x, "source", "agent") or "agent") == "proactive" else 0,
         "created_at": str(x.created_at), "updated_at": str(x.updated_at)}
        for x in merged
    ]}


@router.get("/proactive/status")
async def proactive_status(user=Depends(require_any_user)):
    """主动诊断（巡检）配置与状态。"""
    from app.services import proactive_diagnosis_service as pds
    cfg = await pds._config()
    return {"code": 200, "message": "success", "data": cfg}


@router.post("/proactive/scan")
async def proactive_scan(payload: dict | None = None, user=Depends(require_operator)):
    """执行一轮巡检（需 operator+）。

    body（可选）：{ cluster_id?, namespace?, kinds?: ["deployment","statefulset"] }
    - 不传 = 全平台自动巡检；传 = 定向巡检（指定集群/命名空间/工作负载类型）。
    """
    from app.services import proactive_diagnosis_service as pds
    p = payload or {}
    cid = p.get("cluster_id")
    ns = (p.get("namespace") or "").strip()
    kinds = p.get("kinds") or []
    if isinstance(kinds, str):
        kinds = [kinds]
    summary = await pds.run_scan(
        cluster_id=int(cid) if cid else None, namespace=ns, kinds=[str(k) for k in kinds],
        user_id=getattr(user, "id", 0),
    )
    return {"code": 200, "message": "success", "data": summary}


@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: int, user=Depends(require_any_user)):
    """会话消息列表（校验归属；admin 可查看巡检等平台会话）。"""
    async with async_session() as s:
        sess = await s.get(DiagnosisSession, session_id)
        if not sess or (sess.user_id != user.id and getattr(user, "role", "") != "admin"):
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
        if not sess or (sess.user_id != user.id and getattr(user, "role", "") != "admin"):
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
        if not sess or (sess.user_id != user.id and getattr(user, "role", "") != "admin"):
            return {"code": 403, "message": "无该会话权限", "data": None}

    result = await agent_service.close_session_and_sink(sess)
    return {"code": 200, "message": "success", "data": result}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, user=Depends(require_any_user)):
    """删除诊断会话（含消息）——本人才可删；admin 可删巡检会话。"""
    async with async_session() as s:
        sess = await s.get(DiagnosisSession, session_id)
        if not sess:
            return {"code": 404, "message": "会话不存在", "data": None}
        if sess.user_id != user.id and getattr(user, "role", "") != "admin":
            return {"code": 403, "message": "无该会话权限", "data": None}
        await s.execute(delete(DiagnosisMessage).where(DiagnosisMessage.session_id == session_id))
        await s.delete(sess)
        await s.commit()
    return {"code": 200, "message": "success", "data": {"id": session_id}}
