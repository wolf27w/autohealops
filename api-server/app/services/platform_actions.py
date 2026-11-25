# -*- coding: utf-8 -*-
"""平台动作构建器：把「声明层修复意图」翻译成 kubectl 命令 + **配套回滚命令**。

范围与口径（2026-11-25 老板口径，长期有效）
------------------------------------------------
1. 这批动作**只覆盖 K8s 声明层**（env / resources / image / replicas / rollout restart）。
   它们改的是工作负载 spec，由控制器滚动生效，不碰容器内部、不碰 Pod 本身。
2. **禁止用于「异常即删 Pod」**。异常先看日志找根因：代码 bug、依赖不可用、配置错误，
   删 Pod 只会让问题重新长出来（还把现场证据删了）。删 Pod 仅限「卡 Terminating」
   这类既定例外，且走原有的 delete_stuck_pod 动作链，不经本模块。
3. 每个动作必须自带 `rollback_command`；读不到当前 YAML 时回滚可能为空，
   此时 `description` 必须写清「为什么回滚不了、要人工怎么兜」，不允许静默为空。

对外接口
--------
- `build_*`：纯函数，`(current, params) -> {command, rollback_command, description, changed}`。
  `current` 是调用方读 YAML 后传入的已知值（可能为 `{}`），`params` 是目标值。
- `prepare(action, params, cluster_id, trace_id)`：唯一有 IO 的入口，读 YAML → 调 build_*。

`current` 的形状（宽松兼容，两种都收）
------------------------------------
- 按容器聚合（prepare 产出的形状）::

      {"replicas": 3,
       "env":       {"app": {"K": "V"}},
       "resources": {"app": {"requests": {"cpu": "100m"}, "limits": {"cpu": "500m"}}},
       "image":     {"app": "nginx:1.19"}}

- 已展开的单容器扁平形状（老调用方/测试方便）::

      {"replicas": 3, "env": {"K": "V"},
       "resources": {"requests": {...}, "limits": {...}}, "image": "nginx:1.19"}

约定：`workload` 默认按 `deployment` 处理；`params["kind"]` 可覆盖为
`statefulset` / `daemonset`。所有参数一律走 `shlex.quote` 转义，缺必填参数抛 ValueError（中文）。
"""
from __future__ import annotations

import shlex

from app.clients.executor_client import executor_client
from app.core.logging import get_logger

logger = get_logger("api-server.platform_actions")

SUPPORTED_KINDS = ("deployment", "statefulset", "daemonset")

# 视为「生产」的 env 取值（大小写不敏感）
_PROD_ENVS = ("prod", "production", "生产", "线上")

_MISSING = object()


# ============================== 内部工具 ==============================

def _resolve_kind(params: dict) -> str:
    kind = str((params or {}).get("kind") or "deployment").strip().lower()
    if kind not in SUPPORTED_KINDS:
        raise ValueError(
            f"不支持的 workload 类型：{kind}（只支持 {'/'.join(SUPPORTED_KINDS)}）"
        )
    return kind


def _req_str(params: dict, key: str, label: str) -> str:
    v = (params or {}).get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        raise ValueError(f"缺少必填参数：{label}（{key}）")
    return str(v).strip()


def _target(kind: str, workload: str) -> str:
    """kubectl 资源目标，如 deployment/web（整体走 shlex.quote）。"""
    return shlex.quote(f"{kind}/{workload}")


def _kv_token(key: str, value=_MISSING) -> str:
    """环境变量参数：有值 → `K=V`；_MISSING → `K-`（删除）。整段 quote。"""
    if value is _MISSING:
        return shlex.quote(f"{key}-")
    return shlex.quote(f"{key}={value}")


def _is_container_map(d) -> bool:
    """判断是不是「按容器名聚合」的字典（所有值都是 dict）。"""
    if not isinstance(d, dict) or not d:
        return False
    if set(d.keys()) & {"requests", "limits"}:
        return False
    return all(isinstance(v, dict) for v in d.values())


def _pick(current_map, container: str) -> dict:
    """从「按容器聚合」或「扁平」结构里取目标容器那一份（缺省合并全部）。"""
    if not isinstance(current_map, dict) or not current_map:
        return {}
    if _is_container_map(current_map):
        if container:
            return current_map.get(container) or {}
        merged: dict = {}
        for v in current_map.values():
            if isinstance(v, dict):
                merged.update(v)
        return merged
    return current_map


def _current_image(current: dict, container: str) -> str:
    img = (current or {}).get("image")
    if isinstance(img, str):
        return img.strip()
    if isinstance(img, dict):
        return str(img.get(container) or "").strip() if container else ""
    return ""


def _resource_spec(d: dict) -> str:
    """{cpu: 1, memory: 1Gi} → `cpu=1,memory=1Gi`（顺序稳定：cpu 在前）。"""
    if not isinstance(d, dict) or not d:
        return ""
    keys = [k for k in ("cpu", "memory") if k in d] + [k for k in d if k not in ("cpu", "memory")]
    return ",".join(f"{k}={d[k]}" for k in keys)


def _kubectl(kind: str, workload: str, namespace: str, *rest: str) -> str:
    return " ".join(
        ["kubectl", *rest, _target(kind, workload), "-n", shlex.quote(namespace)]
    )


def _result(command: str, rollback: str, description: str, changed: bool) -> dict:
    return {
        "command": command,
        "rollback_command": rollback,
        "description": description,
        "changed": bool(changed),
    }


# ============================== build_set_env ==============================

def build_set_env(current: dict, params: dict) -> dict:
    """改容器环境变量。

    params: {namespace, workload, container(可选), env: {K: V}, unset: [K], kind(可选)}
    回滚：被改的 key 用 current 旧值恢复；current 没有该 key → `K-`（删掉新增项）。
    """
    current = current or {}
    params = params or {}
    ns = _req_str(params, "namespace", "命名空间")
    workload = _req_str(params, "workload", "工作负载名")
    kind = _resolve_kind(params)
    container = str(params.get("container") or "").strip()

    env = params.get("env") or {}
    unset = params.get("unset") or []
    if isinstance(unset, str):
        unset = [unset]
    if not isinstance(env, dict):
        raise ValueError("params.env 必须是 {KEY: VALUE} 映射")
    if not isinstance(unset, (list, tuple)):
        raise ValueError("params.unset 必须是 key 列表")
    if not env and not unset:
        raise ValueError("params.env 与 params.unset 至少给一个")

    for k in list(env.keys()) + [str(x) for x in unset]:
        if not str(k).strip():
            raise ValueError("环境变量名不能为空")
        if not str(k).replace("_", "").replace("-", "").replace(".", "").isalnum():
            logger.debug("环境变量名含非常规字符，按原样下发：%s", k)
    dup = set(map(str, env.keys())) & set(map(str, unset))
    if dup:
        raise ValueError(f"同一个环境变量不能同时出现在 env 与 unset：{', '.join(sorted(dup))}")

    old_env = _pick(current.get("env") or {}, container)
    value_from = set(map(str, current.get("env_value_from") or []))

    parts = []
    for k, v in env.items():
        k = str(k)
        if k in value_from:
            raise ValueError(
                f"环境变量 {k} 当前由 valueFrom（configMapKeyRef/secretKeyRef）注入，"
                f"kubectl set env 覆盖后会丢失引用，请改 YAML"
            )
        parts.append(_kv_token(k, "" if v is None else str(v)))
    for k in unset:
        parts.append(_kv_token(str(k), _MISSING))

    command = _kubectl(kind, workload, ns, "set", "env")
    if container:
        command += f" --containers={shlex.quote(container)}"
    command += " " + " ".join(parts)

    # ---- 回滚 ----
    rb_parts: list[str] = []
    notes: list[str] = []
    for k, v in env.items():
        k = str(k)
        if k in old_env:
            rb_parts.append(_kv_token(k, old_env[k]))
        else:
            rb_parts.append(_kv_token(k, _MISSING))
    for k in unset:
        k = str(k)
        if k in old_env:
            rb_parts.append(_kv_token(k, old_env[k]))
        elif k in value_from:
            notes.append(f"{k} 原为 valueFrom 注入，删除后无法用 set env 还原")
        # 原本就不存在 → 无需回滚

    rollback = ""
    if rb_parts:
        rollback = _kubectl(kind, workload, ns, "set", "env")
        if container:
            rollback += f" --containers={shlex.quote(container)}"
        rollback += " " + " ".join(rb_parts)

    # ---- changed：值真的变了才算 ----
    changed = False
    for k, v in env.items():
        k = str(k)
        old_v = old_env.get(k, _MISSING)
        if old_v is _MISSING or str(old_v) != ("" if v is None else str(v)):
            changed = True
            break
    if not changed:
        changed = any(str(k) in old_env for k in unset)

    scope = f"容器 {container}" if container else "全部容器"
    desc = (
        f"为 {kind}/{workload}（{scope}）设置环境变量 {len(env)} 个"
        f"、删除 {len(unset)} 个"
    )
    if not changed:
        desc += "；与当前值一致，实际无变更"
    if not old_env:
        desc += "；未读到当前环境变量，回滚里新增项按删除处理"
    if notes:
        desc += "；注意：" + "；".join(notes)

    return _result(command, rollback, desc, changed)


# ============================== build_set_resources ==============================

def build_set_resources(current: dict, params: dict) -> dict:
    """改容器 requests/limits。

    params: {namespace, workload, container, requests: {cpu, memory}, limits: {...}, kind(可选)}
    回滚：用 current 里的旧 requests/limits 还原；current 缺哪个维度就不拼哪个参数。
    """
    current = current or {}
    params = params or {}
    ns = _req_str(params, "namespace", "命名空间")
    workload = _req_str(params, "workload", "工作负载名")
    container = _req_str(params, "container", "容器名")
    kind = _resolve_kind(params)

    requests = params.get("requests") or {}
    limits = params.get("limits") or {}
    if not isinstance(requests, dict) or not isinstance(limits, dict):
        raise ValueError("params.requests / params.limits 必须是 {cpu, memory} 映射")
    if not requests and not limits:
        raise ValueError("params.requests 与 params.limits 至少给一个")

    req_spec = _resource_spec(requests)
    lim_spec = _resource_spec(limits)

    command = _kubectl(kind, workload, ns, "set", "resources")
    command += f" -c {shlex.quote(container)}"
    if req_spec:
        command += f" --requests={shlex.quote(req_spec)}"
    if lim_spec:
        command += f" --limits={shlex.quote(lim_spec)}"

    old = _pick(current.get("resources") or {}, container)
    old_req = _resource_spec((old or {}).get("requests") or {})
    old_lim = _resource_spec((old or {}).get("limits") or {})

    # ---- 回滚 ----
    rollback = ""
    if old_req or old_lim:
        rollback = _kubectl(kind, workload, ns, "set", "resources")
        rollback += f" -c {shlex.quote(container)}"
        if old_req:
            rollback += f" --requests={shlex.quote(old_req)}"
        if old_lim:
            rollback += f" --limits={shlex.quote(old_lim)}"

    changed = True
    desc = f"调整 {kind}/{workload} 容器 {container} 的资源配额"
    details = []
    if req_spec:
        details.append(f"requests={req_spec}")
    if lim_spec:
        details.append(f"limits={lim_spec}")
    desc += "（" + "，".join(details) + "）"
    if not (old_req or old_lim):
        desc += "；读不到当前 requests/limits，回滚需按原 YAML 人工恢复"
    else:
        missing = []
        if requests and not old_req:
            missing.append("requests")
        if limits and not old_lim:
            missing.append("limits")
        if missing:
            desc += f"；原值缺 {'/'.join(missing)}，回滚无法还原该维度（kubectl set resources 不支持清空）"

    return _result(command, rollback, desc, changed)


# ============================== build_set_image ==============================

def build_set_image(current: dict, params: dict) -> dict:
    """改容器镜像。

    params: {namespace, workload, container, image, kind(可选)}
    回滚：`<c>=<current_image>`。current 没有镜像 → changed=False + 说明无法回滚。
    """
    current = current or {}
    params = params or {}
    ns = _req_str(params, "namespace", "命名空间")
    workload = _req_str(params, "workload", "工作负载名")
    container = _req_str(params, "container", "容器名")
    image = _req_str(params, "image", "镜像地址")
    kind = _resolve_kind(params)

    command = _kubectl(kind, workload, ns, "set", "image")
    command += f" {_kv_token(container, image)}"

    old_image = _current_image(current, container)
    if old_image:
        rollback = _kubectl(kind, workload, ns, "set", "image")
        rollback += f" {_kv_token(container, old_image)}"
        changed = True
        desc = f"更新 {kind}/{workload} 容器 {container} 镜像：{old_image} → {image}"
    else:
        rollback = ""
        changed = False  # 口径：回滚不可用的变更不标记为 changed（执行方需人工确认）
        desc = (
            f"更新 {kind}/{workload} 容器 {container} 镜像为 {image}"
            f"；读不到当前镜像，**无法生成回滚**，需人工记录原镜像（kubectl rollout undo 可兜底）"
        )

    return _result(command, rollback, desc, changed)


# ============================== build_set_replicas ==============================

def build_set_replicas(current: dict, params: dict) -> dict:
    """改副本数。

    params: {namespace, workload, replicas, env(可选), kind(可选)}
    生产环境（env=prod）禁止缩容到 0 → ValueError。
    回滚：用 current 的 replicas 还原；读不到 → 说明无法回滚。
    """
    current = current or {}
    params = params or {}
    ns = _req_str(params, "namespace", "命名空间")
    workload = _req_str(params, "workload", "工作负载名")
    kind = _resolve_kind(params)

    raw = params.get("replicas")
    if raw is None or isinstance(raw, bool):
        raise ValueError("缺少必填参数：副本数（replicas）")
    try:
        replicas = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"replicas 必须是整数，收到：{raw!r}") from None
    if replicas < 0:
        raise ValueError(f"副本数不能为负数：{replicas}")

    env = str(params.get("env") or "").strip().lower()
    if not env and isinstance(current.get("env"), str):
        env = str(current["env"]).strip().lower()
    if replicas == 0 and env in _PROD_ENVS:
        raise ValueError(
            f"生产环境禁止将 {kind}/{workload} 缩容到 0 副本（env={env}）；"
            f"需要停服请走变更/审批流程"
        )

    command = _kubectl(kind, workload, ns, "scale")
    command += f" --replicas={replicas}"

    old = current.get("replicas", _MISSING)
    rollback = ""
    if isinstance(old, int) and not isinstance(old, bool):
        rollback = _kubectl(kind, workload, ns, "scale")
        rollback += f" --replicas={old}"
        desc = f"调整 {kind}/{workload} 副本数：{old} → {replicas}"
    else:
        desc = (
            f"调整 {kind}/{workload} 副本数 → {replicas}"
            f"；读不到当前副本数，**无法生成回滚**，需人工确认原值（HPA 托管时还会自行拉回）"
        )

    return _result(command, rollback, desc, True)


# ============================== build_rollout_restart ==============================

def build_rollout_restart(current: dict, params: dict) -> dict:
    """滚动重启（不推导回滚，重启本身无副作用）。"""
    params = params or {}
    ns = _req_str(params, "namespace", "命名空间")
    workload = _req_str(params, "workload", "工作负载名")
    kind = _resolve_kind(params)

    command = _kubectl(kind, workload, ns, "rollout", "restart")
    desc = (
        f"滚动重启 {kind}/{workload}（逐个替换 Pod，不中断服务）；"
        f"无需回滚：重启不改 spec，业务影响可忽略"
    )
    return _result(command, "", desc, True)


# ============================== prepare ==============================

_BUILDERS = {
    "set_env": build_set_env,
    "set_resources": build_set_resources,
    "set_image": build_set_image,
    "set_replicas": build_set_replicas,
    "rollout_restart": build_rollout_restart,
}

ACTION_NAMES = {
    "set_env": "改环境变量",
    "set_resources": "调资源配额",
    "set_image": "换镜像",
    "set_replicas": "调副本数",
    "rollout_restart": "滚动重启",
}


def parse_workload_yaml(yaml_text: str) -> dict:
    """从工作负载 YAML 抽取 current 值（env 按容器聚合 / resources / image / replicas）。

    解析失败或空 YAML → 返回 {}（回滚信息缺失由各 build_* 写清楚原因）。
    """
    try:
        import yaml as _yaml

        doc = _yaml.safe_load(yaml_text or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("工作负载 YAML 解析失败：%s", e)
        return {}
    if not isinstance(doc, dict) or not doc:
        return {}

    spec = doc.get("spec") or {}
    tpl = ((spec.get("template") or {}).get("spec") or {})
    containers = tpl.get("containers") or []

    env_by_container: dict = {}
    res_by_container: dict = {}
    image_by_container: dict = {}
    value_from_keys: list = []
    for c in containers:
        if not isinstance(c, dict):
            continue
        cn = str(c.get("name") or "")
        env_map: dict = {}
        for item in (c.get("env") or []):
            if not isinstance(item, dict):
                continue
            k = item.get("name")
            if not k:
                continue
            if "value" in item:
                env_map[str(k)] = "" if item.get("value") is None else str(item.get("value"))
            else:
                value_from_keys.append(str(k))
        env_by_container[cn] = env_map

        res: dict = {}
        resources = c.get("resources") or {}
        if isinstance(resources, dict):
            for dim in ("requests", "limits"):
                d = resources.get(dim)
                if isinstance(d, dict) and d:
                    res[dim] = {str(k): str(v) for k, v in d.items()}
        res_by_container[cn] = res
        image_by_container[cn] = str(c.get("image") or "")

    replicas = spec.get("replicas")
    if replicas is not None and not isinstance(replicas, bool):
        try:
            replicas = int(replicas)
        except (TypeError, ValueError):
            replicas = None

    return {
        "replicas": replicas,
        "env": env_by_container,
        "env_value_from": value_from_keys,
        "resources": res_by_container,
        "image": image_by_container,
        "kind": (doc.get("kind") or ""),
    }


async def prepare(action: str, params: dict, cluster_id: int, trace_id: str = "") -> dict:
    """读当前 YAML → 生成命令与回滚命令（不执行）。

    返回：{platform_action, params, command, rollback_command, description, changed, current_loaded}
    - action 未知 → ValueError
    - 读不到 YAML（集群不通/对象不存在）→ current={}，仍然返回构建结果，
      rollback 可能为空，description 里写清原因
    """
    params = dict(params or {})
    builder = _BUILDERS.get(str(action or ""))
    if not builder:
        raise ValueError(
            f"未知动作：{action}（支持：{'/'.join(_BUILDERS.keys())}）"
        )

    # 必填项先校验，省一次远程调用
    ns = _req_str(params, "namespace", "命名空间")
    workload = _req_str(params, "workload", "工作负载名")
    kind = _resolve_kind(params)

    current: dict = {}
    loaded = False
    try:
        # get_workload_yaml 是同步 gRPC，必须走 acall（内部 to_thread + 并发信号量）
        r = await executor_client.acall(
            executor_client.get_workload_yaml, cluster_id, kind, ns, workload, trace_id
        )
        if isinstance(r, dict) and r.get("success") and r.get("yaml"):
            current = parse_workload_yaml(r.get("yaml") or "")
            loaded = bool(current)
        else:
            logger.info(
                "prepare 读不到 %s/%s/%s 的 YAML（trace=%s）：%s",
                kind, ns, workload, trace_id, (r or {}).get("error") if isinstance(r, dict) else r,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("prepare 读取 %s/%s/%s YAML 失败：%s", kind, ns, workload, e)

    out = builder(current, params)
    out.update({
        "platform_action": action,
        "params": params,
        "current_loaded": loaded,
    })
    if not loaded:
        out["description"] = out.get("description", "") + "（未读到当前工作负载配置）"
    return out
