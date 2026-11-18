# -*- coding: utf-8 -*-
"""R43 H4 会话录制与回放：asciinema cast v2 录像落盘 + 读取 + 保留期清理。

设计：
- 录制格式 = asciinema cast v2：首行 JSON 头（version/width/height/timestamp/env），
  之后每行一个事件 `[相对秒数(float), "o"/"i", "文本"]`（o=输出、i=输入），JSON 编码保证转义正确。
- 文件落盘 `{HOST_SESSION_DIR 或 <api-server>/data/host-recordings}/{session_id}.cast`。
- 写盘走 `asyncio.to_thread`（线程池），内存缓冲 + 定期 flush，绝不阻塞终端交互；
  任何写失败只记 warning，绝不中断会话。
- 会话元信息 `host_sessions` 补 `record_path` / `record_bytes`（路径只存服务端，不回传前端）。
- 保留期默认 30 天，`purge_old_recordings(days)` 删除过期录像文件并清空 record_path。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.models import HostSession

logger = get_logger("api-server.host-session-record")

# 默认录像根目录（api-server/data/host-recordings），可用环境变量 HOST_SESSION_DIR 覆盖
DEFAULT_RECORDING_DIR = Path(__file__).resolve().parents[2] / "data" / "host-recordings"

# 与 host_session_service 的 flush 节奏保持一致（1s / 8KB / 200 条）
FLUSH_INTERVAL_SEC = 1.0
FLUSH_MAX_BYTES = 8192
FLUSH_MAX_CHUNKS = 200

RETENTION_DAYS_DEFAULT = 30

_CAST_ENV = {"SHELL": "/bin/bash", "TERM": "xterm-256color"}


def recording_dir() -> Path:
    """录像根目录：优先 HOST_SESSION_DIR，否则 data/host-recordings（解析为绝对路径）。"""
    raw = os.environ.get("HOST_SESSION_DIR", "").strip()
    p = Path(raw) if raw else DEFAULT_RECORDING_DIR
    return p.resolve()


def cast_header_line(cols: int = 80, rows: int = 24, started_epoch: float | None = None) -> str:
    """asciinema cast v2 首行 JSON 头。started_epoch 为会话开始时刻（epoch 秒）。"""
    header = {
        "version": 2,
        "width": int(cols or 80),
        "height": int(rows or 24),
        "timestamp": int(started_epoch if started_epoch is not None else time.time()),
        "env": dict(_CAST_ENV),
    }
    return json.dumps(header, ensure_ascii=False) + "\n"


def format_event(ts: float, kind: str, text: str) -> str:
    """单条录制事件：`[相对秒数, "o"/"i", "文本"]`，JSON 编码（换行/引号/控制字符安全）。"""
    return json.dumps([round(float(ts), 6), kind, text], ensure_ascii=False) + "\n"


class CastRecorder:
    """会话录像写入器：内存缓冲 + 定期 flush，写盘走线程池，失败不打断会话。

    - add(direction, data)：direction 为 input|output，data 为 bytes 或 str，只进内存缓冲。
    - flush()：把缓冲刷到磁盘（asyncio.to_thread）。
    - close(status, reason)：终刷 + fsync + 关文件 + 回填 host_sessions.record_path/record_bytes。
    """

    def __init__(self, session_id: int, cols: int = 80, rows: int = 24,
                 base_dir: Path | None = None, started_epoch: float | None = None):
        self.session_id = session_id
        self.cols = int(cols or 80)
        self.rows = int(rows or 24)
        self.t0 = time.monotonic()
        self.started_epoch = started_epoch if started_epoch is not None else time.time()
        self.buf: list[str] = []
        self.buf_bytes = 0
        self.total_bytes = 0
        self.last_flush = time.monotonic()
        self._path = (base_dir or recording_dir()) / f"{session_id}.cast"
        self._file = None
        self._failed = False
        self._open()

    # ---- 文件句柄 ----
    def _open(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._path, "w", encoding="utf-8")
            self._file.write(cast_header_line(self.cols, self.rows, self.started_epoch))
            self._file.flush()
        except Exception as e:  # noqa: BLE001
            self._failed = True
            self._file = None
            logger.warning("会话 %s 录像文件打开失败（后续不录制）: %s", self.session_id, e)

    def resize(self, cols: int, rows: int) -> None:
        self.cols = int(cols or self.cols)
        self.rows = int(rows or self.rows)

    def add(self, direction: str, data: bytes | str) -> None:
        if self._failed or not data:
            return
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        kind = "i" if direction == "input" else "o"
        ts = time.monotonic() - self.t0
        self.buf.append(format_event(ts, kind, text))
        self.buf_bytes += len(text)

    def should_flush(self) -> bool:
        return bool(self.buf) and (
            self.buf_bytes >= FLUSH_MAX_BYTES
            or len(self.buf) >= FLUSH_MAX_CHUNKS
            or (time.monotonic() - self.last_flush) >= FLUSH_INTERVAL_SEC
        )

    def _write_buf(self, items: list[str]) -> None:
        """线程池里执行：把一批事件写到文件。"""
        if not self._file:
            return
        self._file.write("".join(items))
        self._file.flush()

    async def flush(self) -> None:
        if self._failed or not self.buf:
            return
        items, self.buf = self.buf, []
        n = self.buf_bytes
        self.buf_bytes = 0
        self.last_flush = time.monotonic()
        try:
            await asyncio.to_thread(self._write_buf, items)
            self.total_bytes += n
        except Exception as e:  # noqa: BLE001
            logger.warning("会话 %s 录像写盘失败: %s", self.session_id, e)

    def _close_file(self) -> None:
        if self._file:
            try:
                self._file.flush()
                self._file.close()
            except Exception:  # noqa: BLE001
                pass
            self._file = None

    async def close(self, status: str = "finished", reason: str = "") -> None:
        """终刷 + 关文件 + 回填元数据。status/reason 仅占位（会话状态由 host_session_service 维护）。"""
        await self.flush()
        if self._file:
            try:
                await asyncio.to_thread(self._close_file)
            except Exception:  # noqa: BLE001
                pass
        if self._failed:
            return
        # 回填 record_path（绝对路径，仅服务端用）+ record_bytes
        size = 0
        try:
            size = self._path.stat().st_size
        except Exception:  # noqa: BLE001
            size = self.total_bytes
        try:
            async with async_session() as s:
                sess = await s.get(HostSession, self.session_id)
                if sess is not None:
                    sess.record_path = str(self._path)
                    sess.record_bytes = int(size or 0)
                    await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("会话 %s 录像元数据回填失败: %s", self.session_id, e)


# ---- 读取 ----

def cast_exists(record_path: str | None) -> bool:
    """录像文件是否存在（record_path 为 NULL 或文件缺失 → False）。"""
    if not record_path:
        return False
    try:
        return Path(record_path).is_file()
    except Exception:  # noqa: BLE001
        return False


def read_cast_header(record_path: str | None) -> dict | None:
    """读取 cast 首行 JSON 头（version/width/height/timestamp/env）；无文件/坏 JSON → None。"""
    if not record_path:
        return None
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            line = f.readline()
        data = json.loads(line)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def resolve_cast_path(record_path: str | None) -> Path | None:
    """把 record_path 解析为 Path；为空或不存在返回 None。"""
    if not record_path:
        return None
    p = Path(record_path)
    return p if p.is_file() else None


def _remove_record_file(record_path: str | None) -> bool:
    """删除单个录像文件（纯函数，便于单测）。删除成功返回 True，不存在/失败返回 False。"""
    if not record_path:
        return False
    try:
        p = Path(record_path)
        if p.is_file():
            p.unlink()
            return True
    except Exception as e:  # noqa: BLE001
        logger.warning("删除录像文件失败 %s: %s", record_path, e)
    return False


# ---- 清理 ----

async def purge_old_recordings(days: int = RETENTION_DAYS_DEFAULT) -> int:
    """清理超过保留期（默认 30 天）的录像：删除文件 + 清空 record_path。返回删除的文件数。"""
    cutoff = datetime.now() - timedelta(days=max(1, int(days or RETENTION_DAYS_DEFAULT)))
    async with async_session() as s:
        rows = list((await s.execute(
            select(HostSession).where(
                HostSession.started_at < cutoff,
                HostSession.record_path.isnot(None),
            )
        )).scalars())
    removed = 0
    for sess in rows:
        path = sess.record_path
        if _remove_record_file(path):
            removed += 1
        try:
            async with async_session() as s:
                cur = await s.get(HostSession, sess.id)
                if cur is not None:
                    cur.record_path = None
                    cur.record_bytes = 0
                    await s.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("会话 %s 录像 record_path 清空失败: %s", sess.id, e)
    if removed:
        logger.info("已清理过期会话录像 %d 个（保留 %d 天）", removed, max(1, int(days or RETENTION_DAYS_DEFAULT)))
    return removed
