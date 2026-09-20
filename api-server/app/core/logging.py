# -*- coding: utf-8 -*-
"""日志模块：JSON 结构化输出，贯穿 trace_id"""
import json
import logging
import sys
import time
from typing import Optional

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志格式器（与 Go zap JSON 格式对齐）"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "service": "api-server",
            "trace_id": getattr(record, "trace_id", ""),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def get_logger(name: str, trace_id: Optional[str] = None) -> logging.Logger:
    """获取带 JSON 格式的 logger；可绑定 trace_id"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(settings.LOG_LEVEL)
        logger.propagate = False
    if trace_id:
        logger = logging.LoggerAdapter(logger, {"trace_id": trace_id})
    return logger


def get_trace_adapter(logger: logging.Logger, trace_id: str) -> logging.LoggerAdapter:
    """给已有 logger 绑定 trace_id（日志过滤器中读取）"""
    return logging.LoggerAdapter(logger, {"trace_id": trace_id})


class TraceIdFilter(logging.Filter):
    """日志过滤器：从 record 取 trace_id"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = getattr(record, "trace_id", "")
        return True
