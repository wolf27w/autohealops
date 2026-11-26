# -*- coding: utf-8 -*-
"""容器退出证据解析（纯函数、无 IO）

2026-11-23 老板反馈：服务启动失败时平台诊断"只说是探针问题"，没分析真正的启动失败原因。
本模块从 kubectl describe pod 文本里解析每个容器的 State / Last State: Terminated /
Restart Count / Ready，并从日志文本提取第一条强错误行，作为诊断的「容器退出证据」，
让诊断看到「容器上次以什么原因、什么退出码结束」，而不是只看 events 猜"探针失败"。

全部纯函数，不 import 任何项目内模块、不做任何 IO，方便单测。
"""
import re

# 容器名行：2 空格缩进 + 单词 + 冒号结尾（如 "  app:"）
_CONTAINER_HEADER_RE = re.compile(r"^  ([\w.-]+):\s*$")
# 字段行：4 空格缩进（如 "    State:"、"    Last State:"、"    Ready:"、"    Restart Count:"）
_FIELD_RE = re.compile(r"^    ([^:]+):\s*(.*)$")
# 子字段行：6 空格缩进（如 "      Reason:"、"      Exit Code:"、"      Started:"）
_SUBFIELD_RE = re.compile(r"^      ([^:]+):\s*(.*)$")

# 强错误行特征（小写匹配）
_ERROR_MARKERS = ("exception", "caused by", "fatal", "panic", "error", "failed to")

# 硬错误（2026-11-26）：确定性失败原因，优先于普通 ERROR/WARN 返回
_HARD_ERROR_MARKERS = (
    "application failed to start", "an attempt was made to call a method that does not exist",
    "nosuchmethoderror", "noclassdeffounderror", "classnotfoundexception", "linkageerror",
    "outofmemoryerror", "invalid or corrupt jarfile", "no such file or directory",
    "error creating bean", "beancreationexception", "failed to bind", "caused by:",
)


def _split_kv(line: str) -> tuple[str, str]:
    """按首个冒号拆 key/value，value 去首尾空白（容错：无冒号返回整行）"""
    if ":" in line:
        key, _, val = line.partition(":")
        return key.strip(), val.strip()
    return line.strip(), ""


def _to_int(value: str):
    """字符串 → int，失败返回 None"""
    v = (value or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def parse_describe_termination(describe_text: str) -> dict:
    """解析 kubectl describe pod 文本，提取每个容器的退出/状态事实。

    返回 {"containers": [{name, state, state_reason, last_reason, last_exit_code,
                          last_started, last_finished, restart_count, ready}], "summary": str}
    解析失败/无法识别任何容器时返回空结构（containers=[]、summary=""），绝不抛异常。
    """
    empty = {"containers": [], "summary": ""}
    if not describe_text:
        return empty
    try:
        containers: list[dict] = []
        section = ""  # 当前所在 describe 区块（containers / init_containers / 其它）
        current_block = ""  # 当前 4 空格字段所属块："" / "state" / "last_state"

        def _new_container(name: str) -> dict:
            c = {
                "name": name,
                "state": None, "state_reason": None,
                "last_reason": None, "last_exit_code": None,
                "last_started": None, "last_finished": None,
                "restart_count": None, "ready": None,
            }
            containers.append(c)
            return c

        for raw in describe_text.splitlines():
            line = raw.rstrip("\n")
            indent = len(line) - len(line.lstrip(" "))
            stripped = line.strip()
            if not stripped:
                continue

            # 0 缩进：describe 的一级区块标题，切段
            if indent == 0:
                if stripped.startswith("Containers:"):
                    section = "containers"
                elif stripped.startswith("Init Containers:"):
                    section = "containers"
                else:
                    section = "other"
                current_block = ""
                continue

            if section != "containers":
                continue

            # 2 缩进：容器名
            if indent == 2 and _CONTAINER_HEADER_RE.match(line):
                _new_container(_CONTAINER_HEADER_RE.match(line).group(1))
                current_block = ""
                continue

            if not containers:
                continue
            c = containers[-1]

            # 4 缩进：容器字段
            if indent == 4:
                m = _FIELD_RE.match(line)
                if not m:
                    current_block = ""
                    continue
                key, val = m.group(1).strip(), m.group(2).strip()
                if key == "State":
                    c["state"] = val or None
                    current_block = "state"
                elif key == "Last State":
                    # val 通常为 "Terminated"（也可能为空）
                    current_block = "last_state"
                    if val:
                        c["last_reason"] = val  # 仅当值为 Terminated 时才真正有意义，后续 Reason 会覆盖
                elif key == "Ready":
                    c["ready"] = (val.strip().lower() == "true")
                    current_block = ""
                elif key == "Restart Count":
                    c["restart_count"] = _to_int(val)
                    current_block = ""
                else:
                    # Container ID / Image / Port / Started 等字段：结束上一块
                    current_block = ""
                continue

            # 6 缩进：子字段（属于最近的 State / Last State 块）
            m = _SUBFIELD_RE.match(line)
            if not m:
                continue
            key, val = m.group(1).strip(), m.group(2).strip()
            if current_block == "state":
                if key == "Reason":
                    c["state_reason"] = val or None
            elif current_block == "last_state":
                if key == "Reason":
                    c["last_reason"] = val or None
                elif key == "Exit Code":
                    c["last_exit_code"] = _to_int(val)
                elif key == "Started":
                    c["last_started"] = val or None
                elif key == "Finished":
                    c["last_finished"] = val or None

        # 兜底：Last State 块里 value 直接是 "Terminated" 时，last_reason 被误设为 Terminated；
        # 若无 Reason 子字段覆盖，说明只有 Terminated 无 Reason，此时 last_reason 保留为
        # "Terminated"（描述性），但 exitCode 才是关键判定依据，这里不再额外处理。
        summary = _build_summary(containers)
        return {"containers": containers, "summary": summary}
    except Exception:  # noqa: BLE001 —— 纯解析绝不允许抛异常拖垮诊断链路
        return empty


def _build_summary(containers: list[dict]) -> str:
    """把容器事实拼成人类可读的一行一容器文本（含 exitCode= 便于下游正则/诊断提取）。"""
    lines = []
    for c in containers:
        parts = []
        if c.get("state"):
            s = f"State={c['state']}"
            if c.get("state_reason"):
                s += f"({c['state_reason']})"
            parts.append(s)
        if c.get("last_reason") or c.get("last_exit_code") is not None:
            ls = f"LastState={c.get('last_reason') or 'Terminated'}"
            if c.get("last_exit_code") is not None:
                ls += f", exitCode={c['last_exit_code']}"
            if c.get("last_started"):
                ls += f", Started={c['last_started']}"
            if c.get("last_finished"):
                ls += f", Finished={c['last_finished']}"
            parts.append(ls)
        if c.get("ready") is not None:
            parts.append(f"Ready={'True' if c['ready'] else 'False'}")
        if c.get("restart_count") is not None:
            parts.append(f"RestartCount={c['restart_count']}")
        if parts:
            lines.append(f"容器 {c.get('name') or '-'}: " + ", ".join(parts))
    return "\n".join(lines)


def first_error_line(text: str) -> str:
    """从日志文本提取第一条强错误行（Exception/Caused by/ERROR/FATAL/panic/Error:/failed to）。

    逐行匹配、返回原始行（去首尾空白）截断 300 字符；找不到返回空串。

    2026-11-26（老板实测 qa-asfp/dgis-module-universals 诊断跑偏）：原实现「第一个命中标记的行」
    经常先遇到无关 WARN（如 nacosGracefulShutdownDelegate 的 BeanCreationNotAllowed），
    真正的失败点（NoSuchMethodError / APPLICATION FAILED TO START / Caused by）在日志中后段
    → 模型看到的首错行与真因无关。现在分优先级：先找硬错误，再回落普通标记。
    """
    if not text:
        return ""
    lines = text.splitlines()
    for markers in (_HARD_ERROR_MARKERS, _ERROR_MARKERS):
        for line in lines:
            low = line.lower()
            if any(m in low for m in markers):
                return line.strip()[:300]
    return ""


def parse_pod_yaml_termination(yaml_text: str) -> dict:
    """从 Pod YAML 的 status.containerStatuses 解析退出事实（比 describe 文本更可靠）。

    2026-11-23 实测：describe 文本解析在部分格式下拿不到 lastState，导致退出码证据缺失；
    YAML 里 status.containerStatuses[].lastState.terminated.exitCode 是权威字段。
    """
    empty = {"containers": [], "summary": ""}
    if not yaml_text:
        return empty
    try:
        import yaml
        doc = yaml.safe_load(yaml_text) or {}
        statuses = ((doc.get("status") or {}).get("containerStatuses") or [])
        containers: list[dict] = []
        for cs in statuses:
            if not isinstance(cs, dict):
                continue
            state = cs.get("state") or {}
            last = (cs.get("lastState") or {}).get("terminated") or {}
            waiting = state.get("waiting") or {}
            containers.append({
                "name": cs.get("name"),
                "state": next(iter(state.keys()), None) if isinstance(state, dict) else None,
                "state_reason": waiting.get("reason"),
                "last_reason": last.get("reason"),
                "last_exit_code": last.get("exitCode"),
                "last_started": last.get("startedAt"),
                "last_finished": last.get("finishedAt"),
                "restart_count": cs.get("restartCount"),
                "ready": cs.get("ready"),
            })
        return {"containers": containers, "summary": _build_summary(containers)}
    except Exception:  # noqa: BLE001 —— 解析失败不阻断诊断
        return empty


async def collect_pod_termination(cluster_id: int, namespace: str, pod: str) -> dict:
    """采集 Pod 容器退出事实：优先 Pod YAML（权威），失败回退 describe 文本。只读。"""
    if not namespace or not pod:
        return {"containers": [], "summary": ""}
    try:
        from app.clients.executor_client import executor_client  # 延迟导入，避免纯函数模块被 IO 依赖污染
        r = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, namespace, pod)
        if r.get("success") and r.get("yaml"):
            parsed = parse_pod_yaml_termination(r["yaml"])
            if parsed.get("summary"):
                return parsed
    except Exception as e:  # noqa: BLE001
        _ = e
    try:
        from app.clients.executor_client import executor_client
        d = await executor_client.acall(
            executor_client.execute_command, cluster_id, namespace, f"describe pod {pod}")
        return parse_describe_termination(d.get("output", "") if d.get("success") else "")
    except Exception:  # noqa: BLE001
        return {"containers": [], "summary": ""}


# ============ 诊断结论确定性护栏（2026-11-23，覆盖 ReAct 与单次诊断两条路径）============
_EXIT_CODE_RES = (
    re.compile(r"Exit\s*Code[:：]?\s*(-?\d+)", re.IGNORECASE),
    re.compile(r"exitCode[=:\s]+(-?\d+)", re.IGNORECASE),
)
_PROBE_WORDS = ("探针", "健康检查", "readiness", "liveness", "probe")
# 出现这些词说明结论已触及真实退出原因，不触发校正
_NONROOT_WORDS = (
    "退出", "exit", "异常", "exception", "配置", "依赖", "端口", "连接",
    "崩溃", "crash", "oom", "内存", "启动失败", "镜像", "拉取",
)


def extract_exit_code(text: str) -> int | None:
    """从证据文本提取容器退出码（Exit Code:1 / exitCode=1）；取不到返回 None。"""
    if not text:
        return None
    for rx in _EXIT_CODE_RES:
        m = rx.search(text)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                return None
    return None


def apply_probe_guard(root_cause: str, evidence_text: str) -> tuple[str, int | None]:
    """确定性护栏：exitCode 非 (0,137,143) 时禁止把「探针/健康检查」当根因。

    返回 (校正后的 root_cause, exit_code)。已带「【证据校正】」标记的结论原样返回（幂等）。
    无 exitCode 证据、或结论本身已分析真实退出原因 → 原样返回，不改写模型输出。
    """
    rc = root_cause or ""
    code = extract_exit_code(evidence_text or "")
    if code is None or code in (0, 137, 143):
        return rc, code
    if "【证据校正】" in rc:
        return rc, code
    low = rc.lower()
    # 「探针配置」不是真实退出原因的证据，扫描前剔除，避免「配置」一词把探针结论误判为已分析根因
    for _ph, _keep in (("探针配置", "探针"), ("健康检查配置", "健康检查"),
                       ("probe配置", "probe"), ("probe 配置", "probe")):
        low = low.replace(_ph, _keep)
    if not any(w in low for w in _PROBE_WORDS):
        return rc, code
    if any(w in low for w in _NONROOT_WORDS):
        return rc, code
    return (f"【证据校正】容器上次以 exitCode={code} 退出（启动失败），"
            f"健康检查未通过属于伴随现象；{rc}"), code
