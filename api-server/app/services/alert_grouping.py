# -*- coding: utf-8 -*-
"""告警列表「同类型同服务」合并分组（纯函数，便于单测）。

老板口径：同一个故障类型（fault_type）+ 同一个服务 出现多条告警时合并成一条，
只列最新的一条。判定键：cluster_id + namespace + fault_type + 服务名；
服务名 = deployment_name（非空）优先，否则 resource_name。

不依赖 DB：入参为任意带 id/cluster_id/namespace/fault_type/deployment_name/
resource_name 属性的对象列表（如 Alert ORM 或 SimpleNamespace）。
"""


def _service_name(alert) -> str:
    """服务名：deployment_name 非空优先，否则回退 resource_name。"""
    dep = getattr(alert, "deployment_name", None)
    if dep:
        return dep
    return getattr(alert, "resource_name", None) or ""


def _group_key(alert):
    """分组键：(cluster_id, namespace, fault_type, service_name)。"""
    return (
        getattr(alert, "cluster_id", None),
        getattr(alert, "namespace", None) or "",
        getattr(alert, "fault_type", None),
        _service_name(alert),
    )


def group_alerts(rows: list) -> list:
    """将告警列表按「同类型同服务」合并分组。

    入参 rows 通常已按 id desc 排序（接口侧会先 order_by(Alert.id.desc())），
    但本函数不依赖入参顺序：每组显式取 id 最大的一条为「最新」，
    并对组间按最新 id desc 排序，保证结果稳定。

    返回列表，每个元素结构：
        {"alert": <最新 Alert>, "group_count": int, "group_ids": list[int]}
    group_ids 含最新一条，按 id desc 排序。
    """
    groups: dict = {}
    for a in rows:
        groups.setdefault(_group_key(a), []).append(a)

    result = []
    for members in groups.values():
        # 每组保留最新（id 最大）一条
        latest = max(members, key=lambda x: getattr(x, "id", 0) or 0)
        ids = sorted((getattr(x, "id", 0) or 0 for x in members), reverse=True)
        result.append({
            "alert": latest,
            "group_count": len(members),
            "group_ids": ids,
        })

    # 组间顺序 = 各组最新告警 id desc
    result.sort(key=lambda g: getattr(g["alert"], "id", 0) or 0, reverse=True)
    return result
