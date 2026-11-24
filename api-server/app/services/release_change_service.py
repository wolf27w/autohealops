# -*- coding: utf-8 -*-
"""发布变更关联（2026-11-23）

背景：老板反馈「分析的问题不是主要原因，主要原因修复不出来」。根因之一：
根因落在 B 层（应用/配置）时直接转人工，从不关联「最近有没有发布变更」——
qa-bgs 一批服务新镜像启动即崩（YAML 重复键/缺依赖），平台只会建「需改代码走发布」工单，
不会回滚到上一个健康版本。

本模块只做一件事：给定 Deployment，反查它的发布历史（ReplicaSet 历史），
对比当前 revision 与上一版的 PodTemplateSpec 差异，产出：
  - changed：两版模板是否有差异；
  - recent：当前 revision 的创建时间是否落在 lookback_hours 窗口内；
  - rollback_to：可回滚的目标 revision（= 上一版）；
  - summary / diff：人类可读的差异描述（供诊断上下文与工单展示）。

设计原则：任何异常 / 无 Deployment / 只有 1 个 revision 都返回 changed=False，
绝不抛异常（调用方在告警处理链路上，抛异常会拖垮整条闭环）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import yaml

from app.clients.executor_client import executor_client
from app.core.logging import get_logger

logger = get_logger("api-server.release_change")

# 本地时区（created_at 无时区信息时按 Asia/Shanghai 解析）
_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

# created_at 可能出现的多种时间格式（gRPC 传回的字符串），容错解析
_TIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",       # 2026-11-23T14:30:00+08:00
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%SZ",        # UTC
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%S",         # 无时区 → 按本地 Asia/Shanghai 解析
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d",
)

# 探针对比字段（path/port/timeout/initialDelay）
_PROBE_FIELDS = ("path", "port", "timeout", "initialDelay")

# 需要打码的 env 关键字（命中 → 值显示 ***）
_SENSITIVE_KEYWORDS = ("PASSWORD", "SECRET", "TOKEN", "KEY")


def _parse_time(value) -> datetime | None:
    """把 created_at 字符串解析成 Asia/Shanghai 时区的 aware datetime；解析失败返回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in _TIME_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_TZ)
            return dt.astimezone(_TZ)
        except ValueError:
            continue
    # 数字时间戳（秒 / 毫秒）
    try:
        ts = float(s)
        if ts > 1e12:  # 毫秒
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=_TZ)
    except (ValueError, OverflowError, TypeError):
        return None


def _extract_pod_spec(doc) -> dict:
    """兼容 PodTemplateSpec 与完整 Deployment：统一取出 PodSpec（containers/volumes 所在层）。

    PodTemplateSpec = {metadata, spec: {containers, volumes, ...}}；
    Deployment      = {spec: {template: {metadata, spec: {containers, ...}}}}。
    """
    if not isinstance(doc, dict):
        return {}
    spec = doc.get("spec") or {}
    if not isinstance(spec, dict):
        return {}
    if spec.get("containers"):
        return spec  # PodTemplateSpec
    tpl = spec.get("template") or {}
    inner = (tpl.get("spec") if isinstance(tpl, dict) else None) or {}
    if inner.get("containers"):
        return inner
    return spec


def _container_map(spec: dict) -> dict:
    """PodSpec → {容器名: 容器 spec}"""
    out = {}
    for c in (spec or {}).get("containers") or []:
        if isinstance(c, dict) and c.get("name"):
            out[c["name"]] = c
    return out


def _env_map(container: dict) -> dict:
    """容器 env → {name: value}；valueFrom 统一标 <valueFrom>（取不到字面量值）。"""
    out = {}
    for e in (container or {}).get("env") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        if "valueFrom" in e:
            out[e["name"]] = "<valueFrom>"
        else:
            out[e["name"]] = e.get("value")
    return out


def _mask_env(key: str, value) -> str:
    """env 值打码：key 含敏感关键字 → ***；否则截断 60 字。"""
    if any(k in str(key).upper() for k in _SENSITIVE_KEYWORDS):
        return "***"
    s = "" if value is None else str(value)
    return s[:60] + ("..." if len(s) > 60 else "")


def _probe_sig(probe) -> dict | None:
    """探针 → 可比对的签名（path/port/timeout/initialDelay）；非 dict 返回 None（未配置）。"""
    if not isinstance(probe, dict):
        return None
    http = probe.get("httpGet") or {}
    return {
        "path": http.get("path") if isinstance(http, dict) else None,
        "port": http.get("port") if isinstance(http, dict) else None,
        "timeout": probe.get("timeoutSeconds"),
        "initialDelay": probe.get("initialDelaySeconds"),
    }


def _resources_sig(container: dict) -> dict:
    """容器 resources → {"requests": {k: v}, "limits": {k: v}}（值统一转字符串便于比较）。"""
    res = (container or {}).get("resources") or {}
    out = {}
    for key in ("requests", "limits"):
        kv = res.get(key) or {}
        out[key] = {str(k): str(v) for k, v in kv.items()} if isinstance(kv, dict) else {}
    return out


def _volume_refs(spec: dict) -> set:
    """PodSpec volumes 里引用的 ConfigMap/Secret 名称 → {(kind, name)}"""
    refs = set()
    for v in (spec or {}).get("volumes") or []:
        if not isinstance(v, dict):
            continue
        cm = v.get("configMap") or {}
        if isinstance(cm, dict) and cm.get("name"):
            refs.add(("configMap", cm["name"]))
        sec = v.get("secret") or {}
        if isinstance(sec, dict) and sec.get("secretName"):
            refs.add(("secret", sec["secretName"]))
    return refs


def diff_pod_templates(cur_yaml, prev_yaml) -> list[str]:
    """纯函数：对比两个 PodTemplateSpec（已 yaml.safe_load 的 dict），返回差异行列表。

    覆盖：镜像 / command / args / env（同名值变化/新增/删除，含打码）/ resources /
    探针（liveness/readiness/startup 的 path/port/timeout/initialDelay）/ volumes 引用的 ConfigMap/Secret。
    完全一致返回空列表。
    """
    cur_spec = _extract_pod_spec(cur_yaml)
    prev_spec = _extract_pod_spec(prev_yaml)
    cur_containers = _container_map(cur_spec)
    prev_containers = _container_map(prev_spec)
    diffs: list[str] = []

    all_names = set(prev_containers) | set(cur_containers)
    multi = len(all_names) > 1  # 多容器时加 [name] 前缀消歧
    for name in sorted(all_names):
        prev_c = prev_containers.get(name)
        cur_c = cur_containers.get(name)
        tag = f"[{name}] " if multi else ""
        if prev_c is None:
            diffs.append(f"{tag}容器: 新增")
            continue
        if cur_c is None:
            diffs.append(f"{tag}容器: 删除")
            continue
        # 镜像
        if (cur_c.get("image") or "") != (prev_c.get("image") or ""):
            diffs.append(f"{tag}镜像: {prev_c.get('image') or '(空)'} → {cur_c.get('image') or '(空)'}")
        # command / args
        if (cur_c.get("command") or []) != (prev_c.get("command") or []):
            diffs.append(f"{tag}command: {prev_c.get('command')} → {cur_c.get('command')}")
        if (cur_c.get("args") or []) != (prev_c.get("args") or []):
            diffs.append(f"{tag}args: {prev_c.get('args')} → {cur_c.get('args')}")
        # env
        cur_env = _env_map(cur_c)
        prev_env = _env_map(prev_c)
        for ek in sorted(set(prev_env) | set(cur_env)):
            pv, cv = prev_env.get(ek), cur_env.get(ek)
            if ek not in prev_env:
                diffs.append(f"{tag}env: 新增 {ek}={_mask_env(ek, cv)}")
            elif ek not in cur_env:
                diffs.append(f"{tag}env: 删除 {ek}")
            elif cv != pv:
                diffs.append(f"{tag}env: {ek}: {_mask_env(ek, pv)} → {_mask_env(ek, cv)}")
        # resources
        cur_res = _resources_sig(cur_c)
        prev_res = _resources_sig(prev_c)
        for key in ("requests", "limits"):
            for rk in sorted(set(prev_res[key]) | set(cur_res[key])):
                pv, cv = prev_res[key].get(rk), cur_res[key].get(rk)
                if rk not in prev_res[key]:
                    diffs.append(f"{tag}resources.{key}.{rk}: 新增 {cv}")
                elif rk not in cur_res[key]:
                    diffs.append(f"{tag}resources.{key}.{rk}: 删除")
                elif cv != pv:
                    diffs.append(f"{tag}resources.{key}.{rk}: {pv} → {cv}")
        # 探针（liveness/readiness/startup）
        for probe_name in ("livenessProbe", "readinessProbe", "startupProbe"):
            cur_p = _probe_sig(cur_c.get(probe_name))
            prev_p = _probe_sig(prev_c.get(probe_name))
            if cur_p is None and prev_p is None:
                continue
            if cur_p is None:
                diffs.append(f"{tag}探针 {probe_name}: 删除")
                continue
            if prev_p is None:
                diffs.append(f"{tag}探针 {probe_name}: 新增")
                continue
            for field in _PROBE_FIELDS:
                if cur_p.get(field) != prev_p.get(field):
                    diffs.append(f"{tag}探针 {probe_name}.{field}: {prev_p.get(field)} → {cur_p.get(field)}")

    # volumes 引用的 ConfigMap/Secret 名称
    cur_vols = _volume_refs(cur_spec)
    prev_vols = _volume_refs(prev_spec)
    for kind, name in sorted(prev_vols | cur_vols):
        if (kind, name) not in prev_vols:
            diffs.append(f"volumes: 新增 {kind}/{name}")
        elif (kind, name) not in cur_vols:
            diffs.append(f"volumes: 删除 {kind}/{name}")

    return diffs


def _empty_result(deployment: str = "", kind: str = "deployment") -> dict:
    return {
        "changed": False,
        "recent": False,
        "deployment": deployment,
        "kind": (kind or "deployment").lower(),
        "current_revision": 0,
        "rollback_to": 0,
        "change_time": "",
        "summary": "",
        "diff": [],
    }


def _skip(deployment: str, kind: str, namespace: str, reason: str) -> dict:
    """检测不成立时统一的「跳过」出口 + 可观测日志（2026-11-24：原来静默返回空，排查无迹可寻）"""
    logger.info("发布变更检测跳过: %s/%s reason=%s", namespace, deployment, reason)
    return _empty_result(deployment, kind)


async def detect_release_change(cluster_id, namespace, deployment, lookback_hours: int = 6,
                               kind: str = "deployment") -> dict:
    """反查工作负载最近发布变更，返回 {changed, recent, deployment, current_revision,
    rollback_to, change_time, summary, diff}。任何异常 / 无工作负载 / 只有 1 个 revision
    → 返回 changed=False，绝不抛异常。

    kind：工作负载类型（deployment/statefulset/daemonset）。三者走同一发布历史通道
    （list_revisions 在 executor 侧按 Deployment→StatefulSet→DaemonSet 顺序探测，
    Deployment 走 ReplicaSet、STS/DS 走 ControllerRevision），本侧信任该探测结果、
    统一生成 diff。kind 仅作意图标识，不参与 RPC 参数（proto 无 kind 字段）。
    """
    if not deployment or not namespace:
        return _skip(deployment, kind, namespace, "缺少 deployment 或 namespace")
    try:
        revs = await executor_client.acall(
            executor_client.list_revisions, cluster_id, namespace, deployment)
    except Exception as e:  # noqa: BLE001
        logger.debug("list_revisions 调用失败（%s/%s）: %s", namespace, deployment, e)
        return _skip(deployment, kind, namespace, "list_revisions 调用异常")
    if not (revs or {}).get("success"):
        return _skip(deployment, kind, namespace, "list_revisions 返回失败")

    items = [it for it in (revs.get("items") or [])
             if isinstance(it, dict) and it.get("revision") is not None]
    if not items:
        return _skip(deployment, kind, namespace, "无有效 revision 记录")
    by_num = {it["revision"]: it for it in items}
    nums = sorted(by_num.keys())

    # current = 返回的 current_revision（缺失则取最大 revision）
    current = revs.get("current_revision") or 0
    if current not in by_num:
        current = 0
    if not current:
        current = nums[-1]

    # prev = 回滚目标：优先「有就绪副本且未判失败」的历史 revision
    # （2026-11-24：连续两个 bad revision 时只退一步仍会坏——实测 :3 回退到 :2 依旧 CrashLoop）
    smaller = [n for n in nums if n < current]
    if not smaller:
        # 只有 1 个 revision（无可回滚目标）
        return _skip(deployment, kind, namespace, "只有一个 revision，无可回滚目标")
    healthy = [n for n in smaller
               if not (by_num.get(n) or {}).get("failed")
               and int((by_num.get(n) or {}).get("ready_replicas") or 0) > 0]
    # 补充判定（2026-11-24）：滚动发布卡住时旧 RS 的 spec.replicas 已缩到 0 →
    # status.readyReplicas 归零，但旧 Pod 仍在跑。用「仍有 Ready Pod 的 RS」反推健康 revision，
    # 否则会把健康版本漏掉 → 回退到另一个坏版本。
    try:
        _pods = await executor_client.acall(
            executor_client.list_deployment_pods, cluster_id, namespace, deployment)
        if (_pods or {}).get("success"):
            _ready_rs = {
                "-".join((p.get("name") or "").split("-")[:-1])
                for p in (_pods.get("pods") or []) if p.get("ready") and p.get("name")
            }
            for _n in smaller:
                _rs = (by_num.get(_n) or {}).get("replicaset") or ""
                if _rs and _rs in _ready_rs and _n not in healthy:
                    healthy.append(_n)
    except Exception as _e:  # noqa: BLE001
        logger.debug("按 Ready Pod 反推健康 revision 失败（%s/%s）: %s", namespace, deployment, _e)
    prev = max(healthy) if healthy else max(smaller)
    if healthy and max(smaller) not in healthy:
        logger.info("回滚目标跳过失败版本: %s/%s current=%s → 直接回退到 %s（有 %s 个就绪副本）",
                    namespace, deployment, current, prev,
                    (by_num.get(prev) or {}).get("ready_replicas"))

    try:
        cur_tpl = await executor_client.acall(
            executor_client.get_revision_template, cluster_id, namespace, deployment, current)
        prev_tpl = await executor_client.acall(
            executor_client.get_revision_template, cluster_id, namespace, deployment, prev)
    except Exception as e:  # noqa: BLE001
        logger.debug("get_revision_template 调用失败（%s/%s）: %s", namespace, deployment, e)
        return _skip(deployment, kind, namespace, "get_revision_template 调用异常")
    if not (cur_tpl or {}).get("success") or not (prev_tpl or {}).get("success"):
        # 模板取不到（旧 RS 被 revisionHistoryLimit 回收等）：若目标版本有就绪副本，仍可判为发布引入
        if prev in healthy:
            _created = (by_num.get(current) or {}).get("created_at") or ""
            _dt = _parse_time(_created)
            _recent = bool(_dt and (datetime.now(_TZ) - _dt) <= timedelta(hours=lookback_hours))
            logger.info("发布变更按就绪副本判定: %s/%s current=%s → %s（模板对比不可用）",
                        namespace, deployment, current, prev)
            return {
                "changed": True, "recent": _recent, "deployment": deployment,
                "current_revision": int(current), "rollback_to": int(prev),
                "change_time": _dt.strftime("%Y-%m-%d %H:%M:%S") if _dt else "",
                "kind": (kind or "deployment").lower(),
                "summary": (f"当前 revision {current} 启动异常，历史 revision {prev} 仍有就绪副本"
                            "（模板对比不可用，按就绪副本判定）"),
                "diff": [],
            }
        return _skip(deployment, kind, namespace, "revision 模板获取失败")

    cur_doc = yaml.safe_load((cur_tpl or {}).get("yaml") or "") or {}
    prev_doc = yaml.safe_load((prev_tpl or {}).get("yaml") or "") or {}
    diff = diff_pod_templates(cur_doc, prev_doc)
    changed = bool(diff)

    cur_item = by_num.get(current) or {}
    created_at_raw = cur_item.get("created_at") or ""
    change_dt = _parse_time(created_at_raw)
    recent = False
    change_time = ""
    if change_dt is not None:
        change_time = change_dt.strftime("%Y-%m-%d %H:%M:%S")
        recent = (datetime.now(_TZ) - change_dt) <= timedelta(hours=lookback_hours)

    summary = ""
    if changed:
        recent_label = f"{lookback_hours}h 内" if recent else f"{lookback_hours}h 外"
        time_part = change_time or created_at_raw or "未知时间"
        summary = (f"当前 revision {current}（创建于 {time_part}，{recent_label}）"
                   f"对比上一版 {prev}：\n" + "\n".join("- " + d for d in diff))

    logger.info("发布变更检测: %s/%s current=%s prev=%s changed=%s recent=%s (change_time=%s)",
                namespace, deployment, current, prev, changed, recent, change_time)
    return {
        "changed": changed,
        "recent": recent,
        "deployment": deployment,
        "current_revision": int(current),
        "rollback_to": int(prev),
        "change_time": change_time,
        "kind": (kind or "deployment").lower(),
        "summary": summary,
        "diff": diff,
    }
