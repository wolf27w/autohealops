# -*- coding: utf-8 -*-
"""故障分类收敛：L1 域 + L2 故障族（纯函数，无 IO / 无 DB 依赖）

背景（docs/FAULT-CLASSIFICATION-CONVERGENCE-2026-11-18.md，commit daeb34e / 3ae0e42）：

- `fault_type` 由自由文本**降级为「标签」**：继续用于统计/检索/通知/评估，**不再硬路由**。
- **L2 故障族 = 11 类**（含 node_health 下的 TerminatingStuck / NodeNotReady / NodeCordoned）。
- **L1 域 = 集群 / 应用 / 数据库 三域**，直接复用 `diag_category`（`alerts.diag_category` 生成列已落库），
  本模块只做薄封装，不重复维护 fault_type→域 的清单。
- **存量 fault_type 不迁移**：查询时按本模块映射表换算 L2，未知值归 `unknown` 并保留原始文本作为标签。

约定：
- 新增 fault_type 时**只需在 `FAULT_TYPE_TO_FAMILY` 登记**（必要时同步 `diag_category`），不要在各调用点散写判断。
- 本模块必须是**纯函数**：不 import 数据库、配置、redis，保证可在告警读取/报表/测试任意处安全调用。
"""
from __future__ import annotations

from app.services.diag_category import category_of

# ============ L1 域（三域，与 diag_category 对齐） ============
L1_DOMAINS: list[tuple[str, str]] = [
    ("cluster", "集群故障"),
    ("app", "应用故障"),
    ("db", "数据库故障"),
]
L1_LABELS: dict[str, str] = dict(L1_DOMAINS)

# ============ L2 故障族（11 类，顺序即展示顺序） ============
L2_FAMILIES: list[tuple[str, str]] = [
    ("resource", "资源不足"),
    ("config", "配置错误"),
    ("image", "镜像"),
    ("schedule", "调度"),
    ("probe", "探针"),
    ("dependency", "下游依赖"),
    ("code", "代码缺陷"),
    ("capacity", "容量"),
    ("lock", "锁与慢查询"),
    ("network", "网络"),
    ("node_health", "节点健康"),
]
L2_LABELS: dict[str, str] = dict(L2_FAMILIES)

# 未知族：未登记的 fault_type 统一归此类（保留原始文本作为标签）
UNKNOWN = "unknown"
UNKNOWN_LABEL = "未分类"


# ============ 旧 fault_type（自由文本）→ L2 故障族 映射表 ============
# 覆盖平台代码里出现过的全部 fault_type（告警/日志规则/资源阈值/节点/DB 各链路），
# 以及 2026-11-18 生产实测的 19 类。原则：本质同族归一类。
FAULT_TYPE_TO_FAMILY: dict[str, str] = {
    # --- node_health 节点健康（TerminatingStuck / NodeNotReady / NodeCordoned 按定稿归此类） ---
    "TerminatingStuck": "node_health",
    "NodeNotReady": "node_health",
    "NodeCordoned": "node_health",
    "NodeUncordoned": "node_health",
    "KubeletNotReady": "node_health",
    "KubeletDown": "node_health",
    "ClusterComponentUnhealthy": "node_health",
    # --- resource 资源不足（OOM/CPU/内存/驱逐） ---
    "OOMKilled": "resource",
    "PodRestartOOM": "resource",
    "ContainerCpuHigh": "resource",
    "ContainerMemHigh": "resource",
    "PodEvicted": "resource",
    "AppOOM": "resource",
    # --- config 配置错误（容器创建/挂载/权限/配置） ---
    "CreateContainerError": "config",
    "CreateContainerConfigError": "config",
    "RunContainerError": "config",
    "ContainerCannotRun": "config",
    "FailedMount": "config",
    "FailedAttachVolume": "config",
    "AppConfigError": "config",
    "AppPermission": "config",
    # --- image 镜像（拉取/镜像名/仓库） ---
    "ImagePullBackOff": "image",
    "ErrImagePull": "image",
    "ErrImageNeverPull": "image",
    "ImageInspectError": "image",
    "InvalidImageName": "image",
    "RegistryUnavailable": "image",
    # --- schedule 调度（待调度/调度失败） ---
    "PendingTimeout": "schedule",
    "FailedScheduling": "schedule",
    # --- probe 探针 ---
    "PodRestartProbeFail": "probe",
    # --- dependency 下游依赖（DB 连接/认证、下游连接被拒/超时/依赖失败） ---
    "DBConnFailed": "dependency",
    "DBAuthFailed": "dependency",
    "AppConnRefused": "dependency",
    "AppTimeout": "dependency",
    "AppDependency": "dependency",
    # --- code 代码缺陷（启动崩溃/应用崩溃/业务错误） ---
    "CrashLoopBackOff": "code",
    "AppCrash": "code",
    "AppBusiness": "code",
    "PodFailed": "code",
    # --- capacity 容量（节点磁盘/内存/PID 压力、DB 指标超阈值） ---
    "DiskPressure": "capacity",
    "MemoryPressure": "capacity",
    "PIDPressure": "capacity",
    "DBMetricHigh": "capacity",
    # --- lock 锁与慢查询（慢 SQL/锁等待/死锁/连接池耗尽） ---
    "DBSlowQuery": "lock",
    "AppSQL": "lock",
    # --- network 网络（节点网络不可用/容器网卡超阈值/沙箱创建失败） ---
    "NetworkUnavailable": "network",
    "ContainerNetRxHigh": "network",
    "ContainerNetTxHigh": "network",
    "FailedCreatePodSandBox": "network",
}

# resource_type → 族兜底（fault_type 未登记时按资源类型推断；其余归 unknown）
_RESOURCE_TYPE_FALLBACK: dict[str, str] = {
    "node": "node_health",
    "cluster": "node_health",
    "db": "dependency",
}


def _norm(value: str | None) -> str:
    return (value or "").strip()


def family_of(fault_type: str | None, resource_type: str | None = None) -> str:
    """fault_type → L2 故障族。

    顺序：精确映射 → `db_` 前缀（DB 域默认依赖族）→ resource_type 兜底（node/cluster→node_health、db→dependency）
    → 未知归 `unknown`（保留原始文本作为标签）。
    """
    ft = _norm(fault_type)
    if ft in FAULT_TYPE_TO_FAMILY:
        return FAULT_TYPE_TO_FAMILY[ft]
    if ft.startswith("db_"):
        return "dependency"
    rt = _norm(resource_type).lower()
    return _RESOURCE_TYPE_FALLBACK.get(rt, UNKNOWN)


def family_label(family: str | None) -> str:
    """族 key → 展示名；未知族返回「未分类」。"""
    f = _norm(family)
    if f == UNKNOWN:
        return UNKNOWN_LABEL
    return L2_LABELS.get(f, f or UNKNOWN_LABEL)


def l1_of(fault_type: str | None, resource_type: str | None = None) -> str:
    """fault_type → L1 域（cluster/app/db）。直接复用 diag_category（唯一事实源）。"""
    return category_of(resource_type, fault_type)


def classify(fault_type: str | None, resource_type: str | None = None) -> tuple[str, str]:
    """一次返回 (L1 域, L2 族)。"""
    return l1_of(fault_type, resource_type), family_of(fault_type, resource_type)


def fault_types_of(family: str) -> tuple[str, ...]:
    """返回某族下的全部 fault_type（供 SQL `fault_type IN (...)` 过滤用；unknown 返回空）。"""
    f = _norm(family)
    if f == UNKNOWN:
        return ()
    return tuple(sorted(ft for ft, fam in FAULT_TYPE_TO_FAMILY.items() if fam == f))


def is_known_family(family: str | None) -> bool:
    """是否为已登记的 11 类族之一。"""
    return _norm(family) in L2_LABELS
