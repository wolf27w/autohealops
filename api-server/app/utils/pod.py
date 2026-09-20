# -*- coding: utf-8 -*-
"""Pod 相关工具（去重：原 infer_deployment_name 在 3 处各写一份）"""
from __future__ import annotations


def infer_deployment_name(pod_name: str | None) -> str:
    """从 Pod 名推断 Deployment 名（Pod 名格式：{deployment}-{rs-hash}-{pod-hash}）

    Pod 名不足 3 段时原样返回（兼容非标准命名/空值）。
    """
    parts = (pod_name or "").split("-")
    if len(parts) >= 3:
        return "-".join(parts[:-2])
    return pod_name or ""
