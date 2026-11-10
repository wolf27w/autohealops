# -*- coding: utf-8 -*-
"""R8.5 会话留痕：会话元信息 + 录制块（gzip）落库。

录制格式：块序列（direction=input|output, ts_ms 相对会话开始, data=gzip 原始字节），
前端按 asciinema v2 思路回放（按 ts_ms 顺序回放到 xterm.js）。
"""
from __future__ import annotations

import gzip
import time
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import HostSession, HostSessionChunk

logger = get_logger("api-server.host-session")

FLUSH_INTERVAL_SEC = 1.0
FLUSH_MAX_BYTES = 8192
FLUSH_MAX_CHUNKS = 200


async def create_session(host_id: int, host_name: str, user_id: int, username: str,
                         source_ip: str, cols: int = 80, rows: int = 24,
                         recorded: bool = True) -> HostSession:
    async with async_session() as s:
        sess = HostSession(host_id=host_id, host_name=host_name, user_id=user_id,
                           username=username, source_ip=source_ip, cols=cols, rows=rows,
                           recorded=recorded, status="active", started_at=datetime.now())
        s.add(sess)
        await s.commit()
        await s.refresh(sess)
        return sess


class SessionRecorder:
    """按块缓冲 + 定期落库，避免每条按键都写 MySQL。"""

    def __init__(self, session_id: int, started_monotonic: float | None = None):
        self.session_id = session_id
        self.t0 = started_monotonic if started_monotonic is not None else time.monotonic()
        self.seq = 0
        self.buf: list[tuple[str, int, bytes]] = []
        self.buf_bytes = 0
        self.bytes_in = 0
        self.bytes_out = 0
        self.last_flush = time.monotonic()

    def add(self, direction: str, data: bytes) -> None:
        if not data:
            return
        if direction == "input":
            self.bytes_in += len(data)
        else:
            self.bytes_out += len(data)
        ts_ms = int((time.monotonic() - self.t0) * 1000)
        self.buf.append((direction, ts_ms, data))
        self.buf_bytes += len(data)

    def should_flush(self) -> bool:
        return bool(self.buf) and (
            self.buf_bytes >= FLUSH_MAX_BYTES
            or len(self.buf) >= FLUSH_MAX_CHUNKS
            or (time.monotonic() - self.last_flush) >= FLUSH_INTERVAL_SEC
        )

    async def flush(self) -> None:
        if not self.buf:
            return
        items, self.buf = self.buf, []
        self.buf_bytes = 0
        self.last_flush = time.monotonic()
        try:
            async with async_session() as s:
                for direction, ts_ms, data in items:
                    self.seq += 1
                    s.add(HostSessionChunk(session_id=self.session_id, seq=self.seq,
                                           direction=direction, ts_ms=ts_ms,
                                           data=gzip.compress(data, compresslevel=6)))
                await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("会话 %s 录制落库失败: %s", self.session_id, e)

    async def close(self, status: str, reason: str = "") -> None:
        await self.flush()
        try:
            async with async_session() as s:
                sess = await s.get(HostSession, self.session_id)
                if sess:
                    now = datetime.now()
                    sess.ended_at = now
                    if sess.started_at:
                        sess.duration_sec = max(0, int((now - sess.started_at).total_seconds()))
                    sess.status = status
                    sess.close_reason = (reason or "")[:200]
                    sess.bytes_in = self.bytes_in
                    sess.bytes_out = self.bytes_out
                    await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("会话 %s 收尾失败: %s", self.session_id, e)


async def load_session(session_id: int) -> HostSession | None:
    async with async_session() as s:
        return await s.get(HostSession, session_id)


async def list_sessions(host_id: int, limit: int = 50) -> list[HostSession]:
    async with async_session() as s:
        q = select(HostSession).where(HostSession.host_id == host_id)
        q = q.order_by(HostSession.id.desc()).limit(max(1, min(limit, 200)))
        return list((await s.execute(q)).scalars())


async def load_chunks(session_id: int) -> list[dict]:
    async with async_session() as s:
        q = select(HostSessionChunk).where(HostSessionChunk.session_id == session_id).order_by(HostSessionChunk.seq)
        rows = list((await s.execute(q)).scalars())
    out = []
    for c in rows:
        try:
            raw = gzip.decompress(c.data or b"")
        except Exception:  # noqa: BLE001
            raw = c.data or b""
        out.append({"seq": c.seq, "ts_ms": c.ts_ms, "direction": c.direction,
                    "text": raw.decode("utf-8", errors="replace")})
    return out


async def cleanup_chunks(days: int = 7) -> int:
    """清理超过保留期的录制块（会话元信息保留）。返回删除行数。"""
    from datetime import timedelta
    from sqlalchemy import delete
    cutoff = datetime.now() - timedelta(days=days)
    async with async_session() as s:
        old_ids = list((await s.execute(
            select(HostSession.id).where(HostSession.started_at < cutoff)
        )).scalars())
        if not old_ids:
            return 0
        res = await s.execute(delete(HostSessionChunk).where(HostSessionChunk.session_id.in_(old_ids)))
        await s.commit()
        return int(res.rowcount or 0)
