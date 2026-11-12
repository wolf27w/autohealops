# -*- coding: utf-8 -*-
"""大屏实时推送：进程内发布订阅（ScreenBus）+ SSE 帧组装。

设计：
- 单进程内存总线（每个订阅者一个 asyncio.Queue），无 DB / Redis / 网络依赖；
- publish(event_type, payload) 为同步方法，async / sync 上下文均可调用；
- 增量去重：同一 event_type + 同一身份（payload.id 优先，否则按 payload 规范化 JSON）
  在 dedup_window 秒内只下发一次，避免重复写入（重试/幂等）刷屏大屏；
- 帧组装为纯函数（build_frame / encode_frame），便于单测与复用。

事件类型（约定）：alert_created / alert_merged / selfheal_done / ticket_created / ticket_closed。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

KNOWN_EVENT_TYPES = frozenset({
    "alert_created",
    "alert_merged",
    "selfheal_done",
    "ticket_created",
    "ticket_closed",
})

DEFAULT_DEDUP_WINDOW = 2.0   # 秒
DEFAULT_QUEUE_SIZE = 1000


def dedup_key(event_type: str, payload: dict | None) -> str:
    """纯函数：事件去重键。payload 含 id 时按 `event_type:id`（身份去重），否则按规范化 JSON。"""
    payload = payload or {}
    if isinstance(payload, dict):
        pid = payload.get("id")
        if pid is not None:
            return f"{event_type}:{pid}"
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return f"{event_type}:{canonical}"


def build_frame(event_type: str, payload: dict | None, ts: str | None = None) -> dict:
    """纯函数：组装事件帧（可 JSON 序列化的 dict）。

    返回：{"event_type", "payload", "ts"}，ts 默认当前本地时间 ISO 串。
    """
    return {
        "event_type": event_type,
        "payload": payload or {},
        "ts": ts or datetime.now().isoformat(),
    }


def encode_frame(event_type: str, payload: dict | None, ts: str | None = None) -> str:
    """纯函数：把事件组装成 SSE 片段（`event:` + `data:` + 空行）。

    data 为 build_frame 结果的 JSON；客户端可用 addEventListener(event_type) 接收。
    """
    frame = build_frame(event_type, payload, ts)
    data = json.dumps(frame, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n"


class ScreenBus:
    """进程内发布订阅总线（asyncio）。"""

    def __init__(self, dedup_window: float = DEFAULT_DEDUP_WINDOW,
                 queue_size: int = DEFAULT_QUEUE_SIZE):
        self._dedup_window = dedup_window
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue] = set()
        self._recent: dict[str, float] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue:
        """注册订阅者，返回其独占队列（元素为已编码的 SSE 帧字符串）。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event_type: str, payload: dict | None = None) -> bool:
        """发布事件到所有订阅者；返回是否真正下发（窗口内重复返回 False）。

        幂等去重：同 event_type + 同身份在 dedup_window 秒内只下发首条。
        """
        key = dedup_key(event_type, payload)
        now = time.monotonic()
        if len(self._recent) >= 4096:
            self._recent = {k: v for k, v in self._recent.items() if now - v < self._dedup_window}
        last = self._recent.get(key)
        if last is not None and now - last < self._dedup_window:
            return False
        self._recent[key] = now

        frame = encode_frame(event_type, payload)
        self._deliver(frame)
        return True

    # ---- 内部 ----

    def _deliver(self, frame: str) -> None:
        subscribers = tuple(self._subscribers)
        if not subscribers:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop and self._loop is not None:
            # 与订阅方同一 loop：直接同步入队（无跨线程问题，单测也确定）
            for q in subscribers:
                self._enqueue(q, frame)
            return
        # 跨线程 / sync 上下文：借用订阅 loop 调度（线程安全）
        loop = self._loop
        for q in subscribers:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self._enqueue, q, frame)
            else:
                self._enqueue(q, frame)

    @staticmethod
    def _enqueue(q: asyncio.Queue, frame: str) -> None:
        """入队（满则丢最旧一条，大屏只关心最新状态）。"""
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                pass


screen_bus = ScreenBus()
