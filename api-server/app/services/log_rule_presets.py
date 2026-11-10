# -*- coding: utf-8 -*-
"""R12 内置应用故障分类规则库（按语言栈，可一键导入，可按环境改）。

每条规则：命中关键词 → 写入 fault_type + category + priority + sample_pattern（证据抽取）。
priority 数值越小优先级越高（同服务 5 分钟内多类命中时取最高优先级合并）。
"""
from __future__ import annotations

# 标准应用故障类型
APP_FAULT_TYPES: dict[str, tuple[str, str, int]] = {
    # name: (category, 说明, 默认 priority)
    "AppOOM":          ("app", "内存溢出/无法分配内存", 10),
    "AppCrash":        ("app", "进程崩溃（段错误/panic/core dumped）", 10),
    "AppConnRefused":  ("app", "连接被拒绝", 20),
    "AppTimeout":      ("app", "超时", 20),
    "AppSQL":          ("app", "SQL 异常/死锁/锁等待/连接池耗尽", 30),
    "AppConfigError":  ("app", "配置缺失/文件不存在/环境变量缺失", 30),
    "AppPermission":   ("app", "权限拒绝/403/AccessDenied", 40),
    "AppDependency":   ("app", "下游依赖故障（502/503/504/上游连不上）", 40),
    "AppBusiness":     ("business", "自定义业务错误码", 50),
}

# 分类展示名
CATEGORY_LABELS: dict[str, str] = {
    "app": "应用",
    "db": "数据库",
    "infra": "基础设施",
    "business": "业务",
}

# 内置规则库：按语言栈分组；每项 (name, keywords, fault_type, sample_pattern)
_PRESETS: dict[str, list[dict]] = {
    "java": [
        {"name": "AppOOM-堆内存", "keywords": ["OutOfMemoryError", "Java heap space", "GC overhead limit exceeded"],
         "fault_type": "AppOOM", "sample_pattern": r".*(OutOfMemoryError|Java heap space|GC overhead).*"},
        {"name": "AppOOM-无法分配内存", "keywords": ["cannot allocate memory", "unable to create new native thread"],
         "fault_type": "AppOOM", "sample_pattern": r".*(cannot allocate memory|native thread).*"},
        {"name": "AppSQL-SQL异常", "keywords": ["SQLException", "SQLSyntaxErrorException", "Deadlock found", "Lock wait timeout exceeded", "too many connections"],
         "fault_type": "AppSQL", "sample_pattern": r".*(SQLException|Deadlock|Lock wait timeout|too many connections).*"},
        {"name": "AppConnRefused-连接拒绝", "keywords": ["Connection refused", "connect: Connection refused", "dial tcp", "Connection reset"],
         "fault_type": "AppConnRefused", "sample_pattern": r".*(Connection refused|dial tcp|Connection reset).*"},
        {"name": "AppTimeout-超时", "keywords": ["Read timed out", "connect timed out", "TimeoutException", "timed out waiting"],
         "fault_type": "AppTimeout", "sample_pattern": r".*(timed out|Timeout).*"},
        {"name": "AppConfig-配置缺失", "keywords": ["No such file or directory", "config not found", "ConfigurationException", "missing environment variable", "IllegalArgumentException"],
         "fault_type": "AppConfigError", "sample_pattern": r".*(No such file|config not found|missing environment|IllegalArgument).*"},
        {"name": "AppPermission-权限拒绝", "keywords": ["Permission denied", "AccessDeniedException", "403 Forbidden", "Access denied for user"],
         "fault_type": "AppPermission", "sample_pattern": r".*(Permission denied|AccessDenied|403 Forbidden|Access denied).*"},
        {"name": "AppDependency-下游失败", "keywords": ["502 Bad Gateway", "503 Service Unavailable", "504 Gateway Timeout", "upstream connect error", "feign.RetryableException", "ConnectTimeout"],
         "fault_type": "AppDependency", "sample_pattern": r".*(502 Bad Gateway|503 Service|504 Gateway|upstream|RetryableException).*"},
        {"name": "AppCrash-JVM崩溃", "keywords": ["SIGSEGV", "SIGBUS", "EXCEPTION_ACCESS_VIOLATION", "A fatal error has been detected by the Java Runtime Environment"],
         "fault_type": "AppCrash", "sample_pattern": r".*(SIGSEGV|SIGBUS|fatal error|hs_err).*"},
    ],
    "go": [
        {"name": "AppCrash-panic", "keywords": ["panic:", "fatal error:", "goroutine"],
         "fault_type": "AppCrash", "sample_pattern": r".*(panic:|fatal error:).*"},
        {"name": "AppOOM-内存", "keywords": ["out of memory", "cannot allocate memory", "runtime: out of memory"],
         "fault_type": "AppOOM", "sample_pattern": r".*(out of memory|cannot allocate).*"},
        {"name": "AppConnRefused-连接拒绝", "keywords": ["connection refused", "dial tcp", "connect: connection refused"],
         "fault_type": "AppConnRefused", "sample_pattern": r".*(connection refused|dial tcp).*"},
        {"name": "AppTimeout-超时", "keywords": ["context deadline exceeded", "i/o timeout", "Timeout exceeded"],
         "fault_type": "AppTimeout", "sample_pattern": r".*(deadline exceeded|i/o timeout|Timeout).*"},
        {"name": "AppSQL-数据库", "keywords": ["Error 1062", "Error 1213", "sql:", "deadlock"],
         "fault_type": "AppSQL", "sample_pattern": r".*(Error 1062|Error 1213|deadlock|sql:).*"},
        {"name": "AppDependency-下游失败", "keywords": ["502", "503", "no route to host", "upstream"],
         "fault_type": "AppDependency", "sample_pattern": r".*(no route to host|upstream|502|503).*"},
    ],
    "node": [
        {"name": "AppOOM-JS堆", "keywords": ["JavaScript heap out of memory", "FATAL ERROR: Ineffective mark-compacts near heap limit"],
         "fault_type": "AppOOM", "sample_pattern": r".*(heap out of memory|FATAL ERROR).*"},
        {"name": "AppCrash-异常退出", "keywords": ["Segmentation fault", "core dumped", "FATAL ERROR:"],
         "fault_type": "AppCrash", "sample_pattern": r".*(Segmentation fault|core dumped|FATAL ERROR).*"},
        {"name": "AppConnRefused-连接拒绝", "keywords": ["ECONNREFUSED", "connect ECONNREFUSED", "ECONNRESET"],
         "fault_type": "AppConnRefused", "sample_pattern": r".*(ECONNREFUSED|ECONNRESET).*"},
        {"name": "AppTimeout-超时", "keywords": ["ETIMEDOUT", "TimeoutError", "timed out"],
         "fault_type": "AppTimeout", "sample_pattern": r".*(ETIMEDOUT|TimeoutError|timed out).*"},
        {"name": "AppSQL-数据库", "keywords": ["ER_LOCK_DEADLOCK", "ER_DUP_ENTRY", "QueryFailedError", "SequelizeDatabaseError"],
         "fault_type": "AppSQL", "sample_pattern": r".*(ER_LOCK_DEADLOCK|ER_DUP_ENTRY|QueryFailed|SequelizeDatabase).*"},
        {"name": "AppPermission-权限", "keywords": ["EACCES", "403 Forbidden", "AccessDenied"],
         "fault_type": "AppPermission", "sample_pattern": r".*(EACCES|403 Forbidden|AccessDenied).*"},
        {"name": "AppDependency-下游失败", "keywords": ["502 Bad Gateway", "503 Service Unavailable", "504 Gateway Timeout", "ECONNREFUSED: connect"],
         "fault_type": "AppDependency", "sample_pattern": r".*(502 Bad Gateway|503 Service|504 Gateway).*"},
    ],
    "python": [
        {"name": "AppOOM-内存", "keywords": ["MemoryError", "Cannot allocate memory", "Killed"],
         "fault_type": "AppOOM", "sample_pattern": r".*(MemoryError|Cannot allocate|Killed).*"},
        {"name": "AppCrash-异常退出", "keywords": ["Segmentation fault", "core dumped", "Fatal Python error"],
         "fault_type": "AppCrash", "sample_pattern": r".*(Segmentation fault|core dumped|Fatal Python).*"},
        {"name": "AppConnRefused-连接拒绝", "keywords": ["ConnectionRefusedError", "Connection refused", "Errno 111"],
         "fault_type": "AppConnRefused", "sample_pattern": r".*(ConnectionRefusedError|Connection refused|Errno 111).*"},
        {"name": "AppTimeout-超时", "keywords": ["TimeoutError", "timed out", "ReadTimeout", "ConnectTimeout"],
         "fault_type": "AppTimeout", "sample_pattern": r".*(Timeout|timed out).*"},
        {"name": "AppSQL-数据库", "keywords": ["OperationalError", "Deadlock found", "Lock wait timeout", "IntegrityError"],
         "fault_type": "AppSQL", "sample_pattern": r".*(OperationalError|Deadlock|Lock wait timeout|IntegrityError).*"},
        {"name": "AppConfig-配置", "keywords": ["FileNotFoundError", "No such file", "ConfigError", "KeyError"],
         "fault_type": "AppConfigError", "sample_pattern": r".*(FileNotFoundError|No such file|ConfigError|KeyError).*"},
        {"name": "AppPermission-权限", "keywords": ["PermissionError", "Permission denied", "403 Forbidden"],
         "fault_type": "AppPermission", "sample_pattern": r".*(PermissionError|Permission denied|403 Forbidden).*"},
        {"name": "AppDependency-下游失败", "keywords": ["502 Bad Gateway", "503 Service Unavailable", "504 Gateway Timeout", "ConnectionError"],
         "fault_type": "AppDependency", "sample_pattern": r".*(502 Bad Gateway|503 Service|504 Gateway|ConnectionError).*"},
    ],
}


def all_presets() -> list[dict]:
    """展开成完整规则列表（含 category/priority/severity 默认）。"""
    out: list[dict] = []
    for lang, rules in _PRESETS.items():
        for r in rules:
            ft = r["fault_type"]
            cat, _desc, pri = APP_FAULT_TYPES[ft]
            out.append({
                "lang": lang,
                "name": r["name"],
                "keywords": r["keywords"],
                "fault_type": ft,
                "category": cat,
                "priority": pri,
                "sample_pattern": r.get("sample_pattern", ""),
                "severity": "high" if pri <= 10 else ("medium" if pri <= 30 else "low"),
                "index_pattern": "app-*",
                "time_window": 5,
                "threshold": 3,
            })
    return out


def presets_for(lang: str) -> list[dict]:
    if not lang or lang == "all":
        return all_presets()
    return [r for r in all_presets() if r["lang"] == lang]
