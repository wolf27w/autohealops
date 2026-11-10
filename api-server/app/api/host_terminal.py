# -*- coding: utf-8 -*-
"""R8.4 堡垒机 Web 终端 + 8.5 会话留痕 / 回放。

- WebSocket：`/api/v1/ws/hosts/{host_id}/terminal?token=xxx&cols=120&rows=32`
- 浏览器 ↔ api-server ↔ asyncssh 交互式 shell（pty），全程录 stdin/stdout
- 权限：平台 admin 或该主机所属分组的 operator(含)以上（复用 host_group_service）
- 临时凭据：连接后 5 秒内可发 `{"type":"auth","username":"..","password":".."}`（不落库）
"""
from __future__ import annotations

import asyncio
import base64
import json
import time

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import Host, User
from app.services import host_group_service as hgs
from app.services import host_session_service as hss
from app.services import host_ssh
from app.api.terminal import _resolve_user
from app.middleware.permission import require_any_user

router = APIRouter(prefix="/api/v1")
logger = get_logger("api-server.host-terminal")

IDLE_TIMEOUT_SEC = 600          # 空闲超时（10 分钟）
MAX_SESSIONS_PER_USER = 5       # 单用户并发会话上限
AUTH_FRAME_TIMEOUT_SEC = 5

# session_id -> {"websocket": ws, "user_id": int, "task": asyncio.Task | None}
ACTIVE: dict[int, dict] = {}


def _client_ip(websocket: WebSocket) -> str:
    xff = websocket.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()[:64]
    return (websocket.client.host if websocket.client else "")[:64]


async def _load_host(host_id: int) -> Host | None:
    async with async_session() as s:
        return await s.get(Host, host_id)


async def _wait_auth_frame(websocket: WebSocket, timeout: float) -> dict | None:
    try:
        msg = await asyncio.wait_for(websocket.receive(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    if msg.get("type") != "websocket.receive":
        return None
    text = msg.get("text") or ""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if data.get("type") == "auth" else None


@router.websocket("/ws/hosts/{host_id}/terminal")
async def ws_host_terminal(websocket: WebSocket, host_id: int):
    token = websocket.query_params.get("token", "")
    user = await _resolve_user(token)
    if user is None:
        await websocket.close(code=4001, reason="未认证")
        return

    host = await _load_host(host_id)
    if host is None:
        await websocket.close(code=4004, reason="主机不存在")
        return
    if not await hgs.host_access(user, host, "operate"):
        await websocket.close(code=4003, reason="无该主机操作权限")
        return
    if (host.source or "direct") == "agent":
        await websocket.close(code=4003, reason="agent 接入的主机不支持平台直连终端（凭据在对方集群）")
        return

    my_sessions = [s for s in ACTIVE.values() if s["user_id"] == user.id]
    if len(my_sessions) >= MAX_SESSIONS_PER_USER:
        await websocket.close(code=4003, reason=f"并发会话数超过上限（{MAX_SESSIONS_PER_USER}）")
        return

    cols = int(websocket.query_params.get("cols") or 120)
    rows = int(websocket.query_params.get("rows") or 32)
    recorded = websocket.query_params.get("record", "1") != "0"

    # 临时凭据（可选）：先 accept 再等首帧
    temp_auth: dict | None = None
    if websocket.query_params.get("use_saved") == "0":
        await websocket.accept()
        temp_auth = await _wait_auth_frame(websocket, AUTH_FRAME_TIMEOUT_SEC)
        if not temp_auth:
            await websocket.send_json({"type": "error", "message": "未收到临时凭据，连接关闭"})
            await websocket.close(code=4001)
            return
    else:
        await websocket.accept()

    sess = await hss.create_session(host.id, host.name, user.id, user.username,
                                    _client_ip(websocket), cols=cols, rows=rows, recorded=recorded)
    recorder = hss.SessionRecorder(sess.id) if recorded else None
    if recorder:
        ACTIVE[sess.id] = {"websocket": websocket, "user_id": user.id, "task": None}

    conn = None
    proc = None
    status, reason = "closed", ""
    try:
        if temp_auth:
            conn = await asyncio.wait_for(
                asyncssh.connect(host.address, port=int(host.ssh_port or 22),
                                 username=(temp_auth.get("username") or host.ssh_user or "root"),
                                 password=(temp_auth.get("password") or None),
                                 known_hosts=None, connect_timeout=8),
                timeout=15,
            )
        else:
            conn = await asyncio.wait_for(host_ssh.connect(host, connect_timeout=8), timeout=15)

        proc = await conn.create_process(term_type="xterm-256color", term_size=(rows, cols),
                                         encoding=None, stdin=asyncssh.PIPE,
                                         stdout=asyncssh.PIPE, stderr=asyncssh.PIPE)
        await websocket.send_json({"type": "ready", "session_id": sess.id,
                                   "host": host.name, "user": host.ssh_user,
                                   "idle_timeout": IDLE_TIMEOUT_SEC})

        last_activity = time.monotonic()
        stop = asyncio.Event()

        async def pump_ws_to_ssh():
            nonlocal last_activity
            while not stop.is_set():
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    stop.set()
                    return
                if msg.get("type") != "websocket.receive":
                    continue
                last_activity = time.monotonic()
                raw = msg.get("bytes")
                text = msg.get("text")
                if raw:
                    data = bytes(raw)
                elif text:
                    try:
                        j = json.loads(text)
                    except json.JSONDecodeError:
                        j = None
                    if isinstance(j, dict) and j.get("type") == "resize":
                        try:
                            proc.change_terminal_size(int(j.get("cols") or cols),
                                                      int(j.get("rows") or rows))
                        except Exception:  # noqa: BLE001
                            pass
                        continue
                    data = text.encode("utf-8")
                else:
                    continue
                if recorder:
                    recorder.add("input", data)
                proc.stdin.write(data)

        async def pump_ssh_to_ws():
            nonlocal last_activity
            while not stop.is_set():
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                last_activity = time.monotonic()
                if recorder:
                    recorder.add("output", chunk)
                try:
                    await websocket.send_bytes(bytes(chunk))
                except Exception:  # noqa: BLE001
                    stop.set()
                    return

        async def idle_guard():
            nonlocal status, reason
            while not stop.is_set():
                await asyncio.sleep(5)
                if time.monotonic() - last_activity > IDLE_TIMEOUT_SEC:
                    status, reason = "timeout", "空闲超时自动断开"
                    stop.set()
                    try:
                        await websocket.send_json({"type": "error", "message": "空闲超时，会话已断开"})
                    except Exception:  # noqa: BLE001
                        pass
                    return

        async def flusher():
            while not stop.is_set():
                await asyncio.sleep(hss.FLUSH_INTERVAL_SEC)
                if recorder and recorder.should_flush():
                    await recorder.flush()

        tasks = [asyncio.create_task(pump_ws_to_ssh()),
                 asyncio.create_task(pump_ssh_to_ws()),
                 asyncio.create_task(idle_guard()),
                 asyncio.create_task(flusher())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        stop.set()
        for t in pending:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if status == "closed":
            reason = "用户断开"
    except asyncio.TimeoutError:
        status, reason = "error", "连接超时"
        await _send_safe(websocket, {"type": "error", "message": "SSH 连接超时"})
    except (asyncssh.PermissionDenied, asyncssh.PasswordChangeRequired):
        status, reason = "error", "认证失败"
        await _send_safe(websocket, {"type": "error", "message": "认证失败（用户名/密码/密钥错误）"})
    except WebSocketDisconnect:
        status, reason = "closed", "用户断开"
    except Exception as e:  # noqa: BLE001
        status, reason = "error", host_ssh.describe_error(e)
        logger.warning("主机终端会话 %s 异常: %s", sess.id, e)
        await _send_safe(websocket, {"type": "error", "message": host_ssh.describe_error(e)})
    finally:
        ACTIVE.pop(sess.id, None)
        if proc:
            try:
                proc.close()
            except Exception:  # noqa: BLE001
                pass
        if conn:
            try:
                conn.close()
                await conn.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        if recorder:
            await recorder.close(status, reason)
        await _close_safe(websocket)


async def _send_safe(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(payload)
    except Exception:  # noqa: BLE001
        pass


async def _close_safe(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:  # noqa: BLE001
        pass


# ---------------- 会话历史 / 回放 / 踢下线 ----------------


@router.get("/hosts/{host_id}/sessions")
async def list_host_sessions(host_id: int, limit: int = 50, user: User = Depends(require_any_user)):
    host = await _load_host(host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="主机不存在")
    if not await hgs.host_access(user, host, "view"):
        raise HTTPException(status_code=403, detail="无该主机查看权限")
    rows = await hss.list_sessions(host_id, limit)
    return {"code": 200, "message": "success", "data": [
        {"id": r.id, "host_id": r.host_id, "host_name": r.host_name, "user_id": r.user_id,
         "username": r.username, "source_ip": r.source_ip, "status": r.status,
         "started_at": str(r.started_at) if r.started_at else None,
         "ended_at": str(r.ended_at) if r.ended_at else None,
         "duration_sec": r.duration_sec, "bytes_in": r.bytes_in, "bytes_out": r.bytes_out,
         "recorded": bool(r.recorded), "close_reason": r.close_reason,
         "active": r.id in ACTIVE}
        for r in rows]}


@router.get("/host-sessions/{session_id}")
async def get_host_session(session_id: int, user: User = Depends(require_any_user)):
    sess = await hss.load_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    host = await _load_host(sess.host_id)
    if host is None or not await hgs.host_access(user, host, "view"):
        raise HTTPException(status_code=403, detail="无该主机查看权限")
    return {"code": 200, "message": "success", "data": {
        "id": sess.id, "host_id": sess.host_id, "host_name": sess.host_name,
        "username": sess.username, "source_ip": sess.source_ip, "status": sess.status,
        "started_at": str(sess.started_at) if sess.started_at else None,
        "ended_at": str(sess.ended_at) if sess.ended_at else None,
        "duration_sec": sess.duration_sec, "bytes_in": sess.bytes_in, "bytes_out": sess.bytes_out,
        "cols": sess.cols, "rows": sess.rows, "recorded": bool(sess.recorded),
        "close_reason": sess.close_reason, "active": sess.id in ACTIVE}}


@router.get("/host-sessions/{session_id}/replay")
async def replay_host_session(session_id: int, user: User = Depends(require_any_user)):
    """回放数据：块列表（按 seq 排序，text 已解压）。"""
    sess = await hss.load_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    host = await _load_host(sess.host_id)
    if host is None or not await hgs.host_access(user, host, "view"):
        raise HTTPException(status_code=403, detail="无该主机查看权限")
    chunks = await hss.load_chunks(session_id)
    return {"code": 200, "message": "success", "data": {
        "session": {"id": sess.id, "host_name": sess.host_name, "username": sess.username,
                    "started_at": str(sess.started_at) if sess.started_at else None,
                    "duration_sec": sess.duration_sec, "cols": sess.cols, "rows": sess.rows},
        "chunks": chunks}}


@router.post("/host-sessions/{session_id}/kick")
async def kick_host_session(session_id: int, user: User = Depends(require_any_user)):
    """强制下线（仅平台管理员）。"""
    if not hgs.is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅管理员可强制下线")
    entry = ACTIVE.get(session_id)
    if not entry:
        return {"code": 404, "message": "会话已结束或不存在", "data": None}
    await _send_safe(entry["websocket"], {"type": "error", "message": "已被管理员强制下线"})
    await _close_safe(entry["websocket"])
    return {"code": 200, "message": "success", "data": {"id": session_id}}


# ---------------- SFTP 文件传输（列表 / 下载 / 上传）----------------


def _sftp_guard(path: str) -> str:
    """路径白名单/黑名单校验（默认允许 /tmp、/var/tmp、/home；拒绝系统关键目录）。"""
    import os
    allow = [p for p in (os.getenv("HOST_SFTP_ALLOW") or "/tmp,/var/tmp,/home").split(",") if p.strip()]
    deny = [p for p in (os.getenv("HOST_SFTP_DENY") or "/etc,/boot,/dev,/proc,/sys,/root/.ssh").split(",") if p.strip()]
    p = (path or "/tmp").strip() or "/tmp"
    if not p.startswith("/"):
        p = "/" + p
    p = os.path.normpath(p)
    for d in deny:
        if p == d or p.startswith(d.rstrip("/") + "/"):
            raise HTTPException(status_code=403, detail=f"越权路径（禁止访问 {d}）")
    for a in allow:
        a = a.rstrip("/") or "/"
        if a == "/" or p == a or p.startswith(a + "/"):
            return p
    raise HTTPException(status_code=403, detail="路径不在允许范围内（白名单：/tmp、/var/tmp、/home）")


async def _sftp_conn(host_id: int, user: User, need: str = "operate"):
    host = await _load_host(host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="主机不存在")
    if not await hgs.host_access(user, host, need):
        raise HTTPException(status_code=403, detail="无该主机操作权限")
    if (host.source or "direct") == "agent":
        raise HTTPException(status_code=403, detail="agent 主机不支持平台直连文件传输")
    conn = await asyncio.wait_for(host_ssh.connect(host, connect_timeout=8), timeout=15)
    return host, conn


@router.get("/hosts/{host_id}/sftp/list")
async def sftp_list(host_id: int, path: str = "/tmp", user: User = Depends(require_any_user)):
    p = _sftp_guard(path)
    host, conn = await _sftp_conn(host_id, user)
    try:
        async with conn.start_sftp_client() as sftp:
            entries = []
            for e in await sftp.readdir(p):
                if e.filename in (".", ".."):
                    continue
                try:
                    st = await sftp.stat(e.filename)
                except Exception:  # noqa: BLE001
                    st = None
                entries.append({
                    "name": e.filename.split("/")[-1], "path": e.filename,
                    "is_dir": bool(e.attrs.permissions and (e.attrs.permissions & 0o170000) == 0o040000),
                    "size": (st.size if st else getattr(e.attrs, "size", 0)) or 0,
                    "mtime": int(getattr(st, "mtime", 0) or 0) if st else 0,
                    "perm": oct((st.permissions if st else e.attrs.permissions) or 0)[-4:],
                })
        entries.sort(key=lambda x: (not x["is_dir"], x["name"]))
        return {"code": 200, "message": "success", "data": {"path": p, "entries": entries}}
    finally:
        conn.close()
        await conn.wait_closed()


@router.get("/hosts/{host_id}/sftp/download")
async def sftp_download(host_id: int, path: str, user: User = Depends(require_any_user)):
    """流式下载（不落服务器磁盘）。"""
    from fastapi.responses import StreamingResponse
    p = _sftp_guard(path)
    host, conn = await _sftp_conn(host_id, user)
    sftp = await conn.start_sftp_client()
    try:
        attrs = await sftp.stat(p)
        size = int(getattr(attrs, "size", 0) or 0)
    except Exception as e:  # noqa: BLE001
        conn.close()
        raise HTTPException(status_code=404, detail=f"文件不存在或不可读：{e}") from e

    async def iterator():
        import hashlib

        from app.services.audit_service import create_audit_record
        from app.core.security import generate_trace_id
        h = hashlib.sha256()
        total = 0
        try:
            async with sftp.open(p, "rb") as f:
                while True:
                    chunk = await f.read(65536)
                    if not chunk:
                        break
                    h.update(chunk)
                    total += len(chunk)
                    yield chunk
        finally:
            conn.close()
            try:
                await create_audit_record(
                    generate_trace_id(),
                    trigger_snapshot=json.dumps({"action": "host_sftp_download", "host_id": host_id}),
                    execution_detail=f"下载文件 {p}（{total} 字节，sha256={h.hexdigest()}）来自 {host.name}",
                )
            except Exception:  # noqa: BLE001
                pass

    name = p.rsplit("/", 1)[-1] or "download"
    return StreamingResponse(iterator(), media_type="application/octet-stream",
                             headers={"Content-Length": str(size),
                                      "Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/hosts/{host_id}/sftp/upload")
async def sftp_upload(host_id: int, request: Request, path: str = "/tmp", filename: str = "",
                      user: User = Depends(require_any_user)):
    """上传：请求体即文件二进制（Content-Type: application/octet-stream）。

    query: path=目标目录（默认 /tmp）、filename=目标文件名。
    单文件上限 HOST_SFTP_MAX_MB（默认 200MB）。
    """
    import os

    from app.services.audit_service import create_audit_record
    from app.core.security import generate_trace_id

    max_mb = int(os.getenv("HOST_SFTP_MAX_MB") or 200)
    length = int(request.headers.get("content-length") or 0)
    if length and length > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过上限（{max_mb}MB）")

    target_dir = _sftp_guard(path)
    name = os.path.basename(filename or "") or "upload.bin"
    target = target_dir.rstrip("/") + "/" + name
    _sftp_guard(target)

    host, conn = await _sftp_conn(host_id, user)
    written = 0
    import hashlib
    hasher = hashlib.sha256()
    try:
        async with conn.start_sftp_client() as sftp:
            async with sftp.open(target, "wb") as f:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_mb * 1024 * 1024:
                        raise HTTPException(status_code=413, detail=f"文件超过上限（{max_mb}MB）")
                    hasher.update(chunk)
                    await f.write(chunk)
    finally:
        conn.close()
        await conn.wait_closed()

    await create_audit_record(generate_trace_id(),
                              trigger_snapshot=json.dumps({"action": "host_sftp_upload", "host_id": host_id}),
                              execution_detail=f"上传文件 {target}（{written} 字节，sha256={hasher.hexdigest()}）到 {host.name}")
    return {"code": 200, "message": "success",
            "data": {"path": target, "size": written, "host": host.name}}
