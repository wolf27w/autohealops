# -*- coding: utf-8 -*-
"""R8.5 操作留痕：堡垒机会话 + 录制块。

- HostSession：一次 Web 终端（或 SFTP）会话的元信息（谁、哪台主机、起止、字节数、结束原因）
- HostSessionChunk：按块存 stdin/stdout 原始字节（gzip 压缩），前端按 asciinema v2 回放

保留期：录制块 7 天（与日志口径一致），会话元信息（审计）不参与清理。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HostSession(Base):
    __tablename__ = "host_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    host_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    host_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), default="", server_default="")
    source_ip: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # active / closed / timeout / kicked / error
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    cols: Mapped[int] = mapped_column(Integer, default=80, server_default="80")
    rows: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    recorded: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    close_reason: Mapped[str] = mapped_column(String(200), default="", server_default="")


class HostSessionChunk(Base):
    __tablename__ = "host_session_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # input = 用户输入（stdin），output = 主机输出（stdout/stderr）
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="output", server_default="output")
    # 相对会话开始的毫秒偏移（回放时间轴）
    ts_ms: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # gzip 压缩后的原始字节
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
