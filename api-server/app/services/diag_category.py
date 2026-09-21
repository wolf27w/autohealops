# -*- coding: utf-8 -*-
"""诊断分类（N3）：把告警/知识库/诊断记录划入**三类彻底分开**的诊断域

老板口径（2026-09-20）：诊断必须分为
- `cluster` 集群故障（节点 / 集群组件层）
- `app`     应用故障（Pod / 工作负载 / 容器 / 日志）
- `db`      数据库故障（DB 连接 / 指标 / 慢 SQL）

用途：
1. 告警建单时落 `alerts.diag_category`，故障诊断列表按类别过滤（三类互不串）
2. 知识库条目落 `knowledge_entries.diag_category`，AI 复用检索按类别过滤
3. 诊断报告按类别分区展示；权限沿用现有 集群+ns 权限 / DB 权限

约定：
- 未知 fault_type 时按 `resource_type` 兜底（node→cluster / db→db / 其余→app）
- 新增 fault_type 时**只需在此登记**，不要在各调用点散写判断
"""
from __future__ import annotations

CLUSTER = "cluster"
APP = "app"
DB = "db"

# 三类 + 展示名（前端下拉/分区标题统一取这里）
CATEGORIES: list[tuple[str, str]] = [
    (CLUSTER, "集群故障"),
    (APP, "应用故障"),
    (DB, "数据库故障"),
]
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)

# ============ 故障类型 → 诊断域 ============
# 集群/节点层（node_watcher 上报的节点类故障）
CLUSTER_FAULT_TYPES: set[str] = {
    "NodeNotReady",
    "NodeCordoned",
    "NodeUncordoned",
    "DiskPressure",
    "MemoryPressure",
    "PIDPressure",
    "NetworkUnavailable",
    "KubeletNotReady",
    "KubeletDown",
    "ClusterComponentUnhealthy",
}

# 应用层（Pod / 工作负载 / 容器 / 日志）
APP_FAULT_TYPES: set[str] = {
    # Pod 状态类
    "CrashLoopBackOff",
    "OOMKilled",
    "ImagePullBackOff",
    "ErrImagePull",
    "ErrImageNeverPull",
    "ImageInspectError",
    "InvalidImageName",
    "RegistryUnavailable",
    "PendingTimeout",
    "TerminatingStuck",
    "PodFailed",
    "PodEvicted",
    # N5：重启原因诊断
    "PodRestartOOM",
    "PodRestartProbeFail",
    # 容器资源阈值类（resource_alert_service）
    "ContainerCpuHigh",
    "ContainerMemHigh",
    # 日志关键词类（log_alert_service）
    "LogKeyword",
}

# 数据库层（db_alert_service）
DB_FAULT_TYPES: set[str] = {
    "DBConnFailed",
    "DBAuthFailed",
    "DBSlowQuery",
    "DBMetricHigh",
}

# resource_type → 兜底域
_RESOURCE_TYPE_CATEGORY: dict[str, str] = {
    "node": CLUSTER,
    "cluster": CLUSTER,
    "db": DB,
    "log": APP,
    "pod": APP,
}

_FAULT_TYPE_CATEGORY: dict[str, str] = {}
for _ft in CLUSTER_FAULT_TYPES:
    _FAULT_TYPE_CATEGORY[_ft] = CLUSTER
for _ft in APP_FAULT_TYPES:
    _FAULT_TYPE_CATEGORY[_ft] = APP
for _ft in DB_FAULT_TYPES:
    _FAULT_TYPE_CATEGORY[_ft] = DB


def category_of(resource_type: str | None, fault_type: str | None) -> str:
    """判定诊断域：fault_type 优先，未知则按 resource_type 兜底，再兜底 app

    注意：db_diagnosis_service 写知识库时 fault_type 用 `db_{db_type}` 前缀（如 db_mysql），
    此处必须识别 `db_` 前缀 → db，与 ai-service 的 rag_service.diag_category_of 保持一致
    （两服务独立进程，无法互相 import，需镜像实现；改口径时两处同步改）。
    """
    ft = (fault_type or "").strip()
    if ft in _FAULT_TYPE_CATEGORY:
        return _FAULT_TYPE_CATEGORY[ft]
    if ft.startswith("db_"):
        return DB
    rt = (resource_type or "").strip().lower()
    return _RESOURCE_TYPE_CATEGORY.get(rt, APP)


def label_of(category: str) -> str:
    return CATEGORY_LABELS.get(category, category)


def sql_backfill_case(column: str = "fault_type", resource_column: str = "resource_type") -> str:
    """生成 SQL CASE 表达式（供迁移回填历史数据用；顺序：fault_type → resource_type → app）"""

    def _in(values: set[str]) -> str:
        return ", ".join("'%s'" % v for v in sorted(values))

    return (
        "CASE "
        f"WHEN {column} IN ({_in(CLUSTER_FAULT_TYPES)}) THEN '{CLUSTER}' "
        f"WHEN {column} IN ({_in(DB_FAULT_TYPES)}) THEN '{DB}' "
        f"WHEN {column} IN ({_in(APP_FAULT_TYPES)}) THEN '{APP}' "
        f"WHEN {resource_column} = 'node' THEN '{CLUSTER}' "
        f"WHEN {resource_column} = 'db' THEN '{DB}' "
        f"ELSE '{APP}' END"
    )
