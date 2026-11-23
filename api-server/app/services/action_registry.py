# -*- coding: utf-8 -*-
"""动作库（R70）：内置自愈动作的纯数据注册表 + 查询函数（不做实际执行）。

设计要点：
- 动作只存**元数据**（风险等级 / 适用范围 / 验证口径 / 回滚说明 / 是否配置驱动），
  实际执行由 executor 侧负责，本模块不 import 任何执行器。
- **新增动作只需在 `ACTIONS` 里加一条数据**，不改判定逻辑（autonomy_gate / user_impact
  只按 `risk` 字段分支）。
- `risk` 取值：green（低危，可自动）/ yellow（中危，有条件自动）/ red（高危，必须人批）
  / forbidden（永不自动执行）。

动作字段约定：
- key：唯一标识（对账 / 审计 / 候选路由用）
- name：中文名
- description：一句话说明
- risk：green | yellow | red | forbidden
- applies_to：适用的 K8s 资源 kind（Deployment / StatefulSet / DaemonSet / Pod / Service）
- verify：验证口径标识（对应监控侧验证器）
- rollback_note：回滚/兜底说明（人批时展示）
- config_driven：是否由配置驱动（统一为 True，表示行为可被配置覆盖）
"""
from __future__ import annotations

from typing import Optional

# ============ 内置动作 ============
ACTIONS: list[dict] = [
    {
        "key": "rebuild_pod",
        "name": "重建单个 Pod",
        "description": "删除单个异常 Pod，由控制器按期望副本重建",
        "risk": "green",
        "applies_to": ["Pod", "Deployment", "StatefulSet", "DaemonSet"],
        "verify": "pod_ready_180s",
        "rollback_note": "仅重建实例，不改变镜像/配置，失败即回到旧状态",
        "config_driven": True,
    },
    {
        "key": "delete_stuck_pod",
        "name": "强删卡死 Pod",
        "description": "强制删除处于 Terminating 卡死状态的 Pod（--force --grace-period=0）",
        "risk": "green",
        "applies_to": ["Pod"],
        "verify": "pod_gone_or_recreated_120s",
        "rollback_note": "删除后由控制器重建，卡死 Pod 本身不可回滚",
        "config_driven": True,
    },
    {
        "key": "rolling_restart",
        "name": "滚动重启工作负载",
        "description": "触发工作负载滚动更新（如 patch 重启时间戳），逐个替换 Pod",
        "risk": "yellow",
        "applies_to": ["Deployment", "StatefulSet", "DaemonSet"],
        "verify": "all_pods_ready_300s",
        "rollback_note": "滚动过程中可随时暂停/回退，逐步替换不整体中断",
        "config_driven": True,
    },
    {
        "key": "rollback_deployment",
        "name": "回滚到上一版本",
        "description": "将 Deployment 回滚到上一个健康版本（kubectl rollout undo）",
        "risk": "yellow",
        "applies_to": ["Deployment"],
        "verify": "pods_ready_and_no_crash_300s",
        "rollback_note": "回滚会恢复上一版本，需确认上一版本本身健康",
        "config_driven": True,
    },
    {
        "key": "fix_image_pull_secret",
        "name": "补镜像拉取凭据",
        "description": "为工作负载补齐/修复 imagePullSecret，使私有镜像可被拉取",
        "risk": "green",
        "applies_to": ["Deployment", "StatefulSet", "DaemonSet", "Pod"],
        "verify": "pods_ready_180s",
        "rollback_note": "仅补 Secret，不改变镜像/副本，可安全回滚",
        "config_driven": True,
    },
    {
        "key": "drain_traffic",
        "name": "摘流",
        "description": "从 Service Endpoint 中摘除异常实例，切断流量后观察",
        "risk": "green",
        "applies_to": ["Service", "Deployment", "StatefulSet"],
        "verify": "endpoint_removed_60s",
        "rollback_note": "重新挂载 Endpoint 即可恢复流量",
        "config_driven": True,
    },
]

_ACTIONS_BY_KEY: dict[str, dict] = {a["key"]: a for a in ACTIONS}

# ============ 故障类型 → 候选动作 ============
# 仅做「建议候选」路由，不硬路由（与 fault_taxonomy 的「fault_type 降级为标签」口径一致）。
# 命中不到 → 回退 DEFAULT_FAULT_ACTIONS（保守：先重建实例，再滚动重启）。
FAULT_ACTION_MAP: dict[str, list[str]] = {
    "CrashLoopBackOff": ["rollback_deployment", "rebuild_pod"],
    "ImagePullBackOff": ["fix_image_pull_secret"],
    "ErrImagePull": ["fix_image_pull_secret"],
    "TerminatingStuck": ["delete_stuck_pod"],
    "PodEvicted": ["rebuild_pod"],
    "OOMKilled": ["rolling_restart", "rebuild_pod"],
    "ContainerCpuHigh": ["rolling_restart"],
    "ContainerMemHigh": ["rolling_restart"],
    "CreateContainerConfigError": ["rebuild_pod"],
    "FailedMount": ["rebuild_pod"],
    "NodeNotReady": ["drain_traffic"],
    "NodeCordoned": ["drain_traffic"],
}

DEFAULT_FAULT_ACTIONS: list[str] = ["rebuild_pod", "rolling_restart"]


def get_action(key: str) -> Optional[dict]:
    """按 key 取单个动作，不存在返回 None。"""
    if not isinstance(key, str):
        return None
    return _ACTIONS_BY_KEY.get(key.strip())


def list_actions() -> list[dict]:
    """返回全部动作（按声明顺序）。返回浅拷贝，避免调用方篡改注册表。"""
    return [dict(a) for a in ACTIONS]


def actions_for_fault(fault_type: str) -> list[dict]:
    """故障类型 → 候选动作列表（返回动作 dict 的浅拷贝，未命中回退保守候选）。"""
    if not isinstance(fault_type, str):
        return [dict(get_action(k)) for k in DEFAULT_FAULT_ACTIONS]
    keys = FAULT_ACTION_MAP.get(fault_type.strip(), DEFAULT_FAULT_ACTIONS)
    result: list[dict] = []
    for k in keys:
        a = _ACTIONS_BY_KEY.get(k)
        if a is not None:
            result.append(dict(a))
    return result
