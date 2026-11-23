# -*- coding: utf-8 -*-
"""R75.2 非发布类配置错误的「补丁建议」

背景：近 48h 真实数据里 68% 的根因落在 B 层（应用/配置），原逻辑「B 层一律转人工」导致
「分析到了但修不动」。本模块在 B 层诊断转工单时，从诊断结论/分析/证据/告警消息里提取配置线索，
读取关联的 ConfigMap/Secret/挂载，生成**键级补丁建议**（只建议，不自动执行）。

红线（对齐 R75）：
- 不自动修改 ConfigMap/Secret/Nacos/业务代码（只给建议补丁）。
- 不输出 Secret 明文（敏感值一律打码 ***）。
- 任何异常 → 返回 []，不阻断工单。

对外主入口：``suggest_config_patches(alert, diagnosis) -> list[dict]``
返回项结构：``{key, source, status, suggestion, how_to_fix}``
  status ∈ {missing, empty, present, duplicate, unknown}
"""
import re
import yaml

from app.clients.executor_client import executor_client
from app.core.logging import get_logger

logger = get_logger("api-server.config_patch_suggestion")

# ============ 线索提取正则 ============
_BIND_PROPS_RE = re.compile(r"Failed to bind properties under '([^']+)'")
_SPRING_KEY_RE = re.compile(r"spring\.[A-Za-z0-9_.-]+")
_PLACEHOLDER_RE = re.compile(r"Could not resolve placeholder '([^']+)'")
_NO_BEAN_RE = re.compile(r"No qualifying bean of type '([^']+)'")
_ENV_VAR_RE = re.compile(r"environment variable ([A-Z0-9_]+)")
_MISSING_ENV_RE = re.compile(r"missing env|Missing required environment variable", re.I)
_DUP_KEY_RE = re.compile(r"DuplicateKeyException", re.I)

# 敏感 key 关键字（命中 → 值打码）
_SENSITIVE_KEYWORDS = (
    "PASSWORD", "SECRET", "TOKEN", "KEY", "CREDENTIAL", "PWD", "PASSWD",
    "ACCESS_KEY", "PRIVATE", "API_KEY", "CERT", "AUTH",
)


def is_sensitive_key(key: str) -> bool:
    """判断配置 key 是否敏感（密码/密钥/token 类）。"""
    return any(k in (key or "").upper() for k in _SENSITIVE_KEYWORDS)


def mask_value(value, key: str = "") -> str:
    """值打码：敏感 key → ***；否则原样（安全兜底，供输出侧调用）。"""
    if value is None:
        return ""
    if is_sensitive_key(key):
        return "***"
    return str(value)


# ============ 诊断 JSON 拍平成文本 ============

def diagnosis_to_text(diagnosis: dict) -> str:
    """把诊断 JSON 拍平成一段可检索文本（根因 + 机制链 + 假设/已排除/证据缺口 + 建议 + 证据引用）。"""
    if not isinstance(diagnosis, dict):
        return ""
    parts = [str(diagnosis.get("root_cause") or "")]
    chain = diagnosis.get("root_cause_chain") or {}
    if isinstance(chain, dict):
        parts.extend(str(v) for v in (chain.get("trigger"), chain.get("direct"), chain.get("root")) if v)
    analysis = diagnosis.get("analysis") or {}
    if isinstance(analysis, dict):
        for hyp in (analysis.get("hypotheses") or []):
            if isinstance(hyp, dict):
                parts.append(str(hyp.get("cause") or ""))
                parts.extend(str(e) for e in (hyp.get("evidence_for") or []))
                parts.extend(str(e) for e in (hyp.get("evidence_against") or []))
        for e in (analysis.get("eliminated") or []):
            parts.append(str(e))
        for g in (analysis.get("evidence_gaps") or []):
            if isinstance(g, dict):
                parts.append(str(g.get("missing") or ""))
            elif isinstance(g, str):
                parts.append(g)
    refs = diagnosis.get("evidence_refs") or []
    if isinstance(refs, list):
        parts.extend(str(r) for r in refs)
    detail = diagnosis.get("suggestions_detail") or {}
    if isinstance(detail, dict):
        for k in ("immediate", "root_fix", "verification", "prevention"):
            parts.extend(str(s) for s in (detail.get(k) or []))
    else:
        for s in (diagnosis.get("suggestions") or []):
            parts.append(str(s))
    return "\n".join(p for p in parts if p and str(p).strip())


# ============ 线索提取（纯函数，便于单测） ============

def extract_clues(text: str) -> list[dict]:
    """从文本提取配置线索并去重。返回 [{type, key}]。

    type ∈ {config_key, placeholder, no_bean, env_var, dup_key}
    """
    clues: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(ctype: str, key: str) -> None:
        key = (key or "").strip().strip("'\"")
        if not key:
            return
        if (ctype, key) in seen:
            return
        seen.add((ctype, key))
        clues.append({"type": ctype, "key": key})

    for m in _BIND_PROPS_RE.finditer(text):
        _add("config_key", m.group(1))
    for m in _PLACEHOLDER_RE.finditer(text):
        _add("placeholder", m.group(1))
    for m in _NO_BEAN_RE.finditer(text):
        _add("no_bean", m.group(1))
    for m in _ENV_VAR_RE.finditer(text):
        _add("env_var", m.group(1))
    # 通用 spring.*（bind_props 之后，避免重复计数）
    for m in _SPRING_KEY_RE.finditer(text):
        _add("config_key", m.group(0))
    if _MISSING_ENV_RE.search(text) and not any(c["type"] == "env_var" for c in clues):
        _add("env_var", "__MISSING_ENV__")
    if _DUP_KEY_RE.search(text):
        _add("dup_key", "__DUPLICATE_KEY__")
    return clues


# ============ 挂载 / 配置对象读取（executor 只读接口） ============

def _norm_kind(owner_kind: str) -> str:
    """Pod ownerKind（如 Deployment）→ 工作负载 kind（deployment/statefulset/daemonset）。"""
    k = (owner_kind or "").lower()
    return k if k in ("deployment", "statefulset", "daemonset") else ""


def _env_names_from_doc(doc: dict) -> set:
    """从工作负载 YAML 提取环境变量名（env[].name）。"""
    names: set = set()
    spec = (((doc or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
    for c in list(spec.get("containers") or []) + list(spec.get("initContainers") or []):
        for e in (c.get("env") or []) or []:
            n = e.get("name") if isinstance(e, dict) else None
            if n:
                names.add(n)
    return names


async def _read_mounts(alert) -> dict:
    """读取挂载信息 → {volumes, owner_kind, owner_name, env_names}。失败降级为空白（不抛异常）。

    volumes: {volume_name: {type, source}}（type ∈ configMap/secret/...；source = ConfigMap/Secret 名）
    """
    cluster_id = getattr(alert, "cluster_id", None)
    namespace = getattr(alert, "namespace", "") or ""
    pod_name = getattr(alert, "resource_name", "") or ""
    volumes: dict = {}
    owner_kind = ""
    owner_name = getattr(alert, "deployment_name", "") or ""
    env_names: set = set()

    if cluster_id and namespace and pod_name:
        try:
            r = await executor_client.acall(
                executor_client.get_pod_detail, cluster_id, namespace, pod_name)
            detail = r.get("detail") if isinstance(r, dict) else None
            if detail:
                for v in (detail.get("volumes") or []):
                    if v.get("name"):
                        volumes[v["name"]] = {"type": v.get("type"), "source": v.get("source")}
                owner_kind = _norm_kind(detail.get("owner_kind") or "")
                if detail.get("owner_name"):
                    owner_name = detail["owner_name"]
        except Exception as e:  # noqa: BLE001
            logger.debug("读取 Pod 详情失败（补丁建议降级）alert=%s: %s",
                         getattr(alert, "id", None), e)

    # 工作负载级挂载（补全 Pod 模板 volume 引用，Pod 详情取不到时兜底）
    kind = owner_kind or ("deployment" if owner_name else "")
    if cluster_id and namespace and kind and owner_name:
        try:
            r = await executor_client.acall(
                executor_client.get_workload_mounts, cluster_id, kind, namespace, owner_name)
            if isinstance(r, dict):
                for v in (r.get("volumes") or []):
                    if v.get("name") and v["name"] not in volumes:
                        volumes[v["name"]] = {"type": v.get("type"), "source": v.get("source")}
        except Exception as e:  # noqa: BLE001
            logger.debug("读取工作负载挂载失败（补丁建议降级）alert=%s: %s",
                         getattr(alert, "id", None), e)
        # 环境变量名（工作负载 YAML 兜底）
        try:
            ry = await executor_client.acall(
                executor_client.get_workload_yaml, cluster_id, kind, namespace, owner_name)
            if isinstance(ry, dict) and ry.get("success"):
                env_names = _env_names_from_doc(yaml.safe_load(ry.get("yaml") or "") or {})
        except Exception as e:  # noqa: BLE001
            logger.debug("读取工作负载 YAML 失败（环境变量降级）alert=%s: %s",
                         getattr(alert, "id", None), e)

    return {"volumes": volumes, "owner_kind": owner_kind, "owner_name": owner_name,
            "env_names": env_names}


async def _read_object(cluster_id, namespace, obj_type: str, obj_name: str) -> dict | None:
    """读取单个 ConfigMap/Secret 内容 → {type, name, data}；失败返回 None（降级）。"""
    try:
        r = await executor_client.acall(
            executor_client.get_workload_yaml, cluster_id, obj_type, namespace, obj_name)
        if not isinstance(r, dict) or not r.get("success"):
            return None
        doc = yaml.safe_load(r.get("yaml") or "") or {}
        data = doc.get("data") if isinstance(doc, dict) else {}
        if not isinstance(data, dict):
            data = {}
        return {"type": obj_type, "name": obj_name, "data": data}
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 %s/%s 失败（补丁建议降级）: %s", obj_type, obj_name, e)
        return None


async def _read_objects(alert, volumes: dict) -> list[dict]:
    """按挂载的 ConfigMap/Secret volume 读内容。返回 [{type, name, data}]（失败项跳过）。"""
    cluster_id = getattr(alert, "cluster_id", None)
    namespace = getattr(alert, "namespace", "") or ""
    objects: list[dict] = []
    seen_obj: set[tuple[str, str]] = set()
    for v in (volumes or {}).values():
        t = (v.get("type") or "").lower()
        src = (v.get("source") or "").strip()
        if not src or "（可选）" in src:
            continue
        if t not in ("configmap", "secret"):
            continue
        obj_type = "configmap" if t == "configmap" else "secret"
        if (obj_type, src) in seen_obj:
            continue
        seen_obj.add((obj_type, src))
        obj = await _read_object(cluster_id, namespace, obj_type, src)
        if obj is not None:
            objects.append(obj)
    return objects


# ============ 状态判定 ============

def _flatten_dict(d, prefix: str = "") -> set:
    """嵌套 dict → 点分 key 集合（spring.datasource.url 这类层级键）。"""
    keys: set = set()
    if not isinstance(d, dict):
        return keys
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else str(k)
        keys.add(full)
        if isinstance(v, dict):
            keys |= _flatten_dict(v, full)
    return keys


def _lookup(key: str, objects: list[dict], env_names: set) -> tuple[str, dict | None]:
    """在配置对象里找 key → (status, obj)。直接 key 命中 / 值内嵌命中（application.yml 嵌套）。"""
    for obj in objects:
        data = obj.get("data") or {}
        if key in data:
            val = data[key]
            if val is None or (isinstance(val, str) and not val.strip()):
                return "empty", obj
            return "present", obj
        # 嵌套：值里包含该 key（如 application.yml 内 spring.datasource.url）
        for k, v in data.items():
            if not isinstance(v, str):
                continue
            if key in v:
                return "present", obj
            try:
                nested = yaml.safe_load(v)
            except yaml.YAMLError:
                nested = None
            if isinstance(nested, dict) and key in _flatten_dict(nested):
                return "present", obj
    if key in (env_names or set()):
        return "present", {"type": "env", "name": "环境变量", "data": {}}
    return "missing", None


def _source_label(obj: dict | None) -> str:
    if not obj:
        return "unknown"
    t = obj.get("type") or ""
    name = obj.get("name") or ""
    if t == "env":
        return "环境变量"
    label = "configmap" if t == "configmap" else ("secret" if t == "secret" else str(t or "unknown"))
    return f"{label}/{name}" if name else label


def _build_patch(key: str, source: str, status: str, suggestion: str, how_to_fix: str) -> dict:
    return {"key": key, "source": source, "status": status, "suggestion": suggestion,
            "how_to_fix": how_to_fix}


def _resolve_clue(clue: dict, objects: list[dict], env_names: set) -> dict:
    ctype = clue.get("type")
    key = clue.get("key") or ""

    if ctype == "dup_key":
        return _build_patch(
            "application.yml/Nacos 重复 key", "unknown", "duplicate",
            "检测到 DuplicateKeyException：配置源存在同名重复 key，Spring 无法绑定重复属性。",
            "定位重复 key 所在文件与行号（application.yml / Nacos / ConfigMap），删除其一后重启。")

    if ctype == "env_var":
        if key == "__MISSING_ENV__":
            return _build_patch(
                "环境变量缺失", "unknown", "missing",
                "启动报 missing env：缺少必需的环境变量。",
                "在工作负载「编辑设置 → 环境变量」补齐，或从 ConfigMap/Secret 注入后重启（rollout restart）。")
        status, obj = _lookup(key, objects, env_names)
        src = _source_label(obj)
        if status == "missing":
            return _build_patch(
                key, src, "missing",
                f"环境变量 {key} 未在 ConfigMap/Secret/工作负载环境中找到。",
                f"kubectl set env deploy/<workload> {key}=<value>，或写入 ConfigMap/Secret 后重启。")
        if status == "empty":
            return _build_patch(key, src, "empty", f"环境变量 {key} 存在但值为空。",
                                f"填写 {key} 的正确值（敏感值不在此展示）后重启。")
        return _build_patch(key, src, "present", f"环境变量 {key} 已存在，核对取值是否正确。",
                            f"对比错误信息中的期望值，确认 {key} 取值与运行环境一致。")

    if ctype == "placeholder":
        status, obj = _lookup(key, objects, env_names)
        src = _source_label(obj)
        if status == "missing":
            return _build_patch(
                key, src, "missing",
                f"占位符 ${{{key}}} 无法解析：对应配置/环境变量缺失。",
                f"在 ConfigMap/Secret 或环境变量中补充 {key}，或在代码里提供默认值后重启。")
        if status == "empty":
            return _build_patch(key, src, "empty", f"占位符 ${{{key}}} 对应值为空。",
                                f"填写 {key} 的正确值后重启。")
        return _build_patch(key, src, "present", f"占位符 ${{{key}}} 已能解析，核对取值。",
                            f"确认 {key} 的值与引用处期望一致。")

    if ctype == "no_bean":
        return _build_patch(
            key, "unknown", "unknown",
            f"缺少 Bean（No qualifying bean of type '{key}'）：依赖未装配或条件装配未命中。",
            "两种修复路径：① 补充缺失依赖（pom.xml 增加对应 spring-boot-starter-*）；"
            "② 条件装配（加 @ConditionalOnMissingBean / 用配置开关关闭该功能）。")

    # config_key（bind_props / spring.*）
    status, obj = _lookup(key, objects, env_names)
    src = _source_label(obj)
    if status == "missing":
        return _build_patch(
            key, src, "missing",
            f"配置项 {key} 缺失（Failed to bind / 找不到该属性）。",
            f"在 ConfigMap/Secret 的对应 data key 中补充 {key}（含默认值）后重启。")
    if status == "empty":
        return _build_patch(key, src, "empty", f"配置项 {key} 存在但值为空。",
                            f"填写 {key} 的正确值后重启。")
    return _build_patch(
        key, src, "present",
        f"配置项 {key} 已存在，但绑定失败——核对取值类型/格式是否符合错误信息要求。",
        f"检查 {key} 的值（类型、空值、非法字符），改完重启（rollout restart）。")


# ============ 主入口 ============

async def suggest_config_patches(alert, diagnosis: dict) -> list[dict]:
    """B 层诊断 → 生成键级配置补丁建议（只建议，不执行）。

    - 仅当 diagnosis.root_cause_layer == "B" 时执行，否则返回 []。
    - 任何异常 → 返回 []（不阻断工单）。
    """
    try:
        if not isinstance(diagnosis, dict):
            return []
        layer = str(diagnosis.get("root_cause_layer") or "").strip().upper()
        if layer != "B":
            return []
        text = "\n".join(filter(None, [
            diagnosis_to_text(diagnosis),
            str(getattr(alert, "message", "") or ""),
        ]))
        clues = extract_clues(text)
        if not clues:
            return []

        mounts = await _read_mounts(alert)
        objects = await _read_objects(alert, mounts.get("volumes") or {})
        env_names = mounts.get("env_names") or set()

        return [_resolve_clue(c, objects, env_names) for c in clues]
    except Exception as e:  # noqa: BLE001
        logger.warning("配置补丁建议生成失败 alert=%s: %s",
                       getattr(alert, "id", None), e)
        return []
