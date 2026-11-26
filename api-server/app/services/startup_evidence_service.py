# -*- coding: utf-8 -*-
"""启动失败证据包（startup evidence pack）——一次采集拿全「为什么起不来」的关键证据。

设计口径（2026-11-25 事故复盘后定）：
平台原有工具能看到「CrashLoopBackOff / exitCode=1 / 首错行」，但看不到真正下刀的地方：
  ① 镜像入口脚本里硬编码的启动参数（凭据、profile、地址）
  ② jar 内 `BOOT-INF/classes/application.yml` 的占位符默认值
  ③ 与上一版本的差异（这次是「去掉了默认值 + 新增强校验」）
于是把这几步固化成**一个只读工具**，一次返回结构化证据包 + 候选结论，避免 8 轮预算烧光。

采集顺序（与人工排障一致）：
  1) Pod 状态：restarts / waiting.reason / lastState.terminated(exitCode,reason)
  2) 日志首错行（不是末尾 100 行）：Application run failed / Caused by / Exception
  3) 入口脚本 + /proc/1/cmdline（启动参数，凭据硬编码在这里）
  4) 镜像内 application.yml（占位符是否还有默认值）
  5) 上一版本同两个文件的差异
  6) 凭据安全判定（默认口令 + 系统属性优先级 + 配置无默认值 → 启动必被框架拦截）

读取通道：
  - 优先 registry（`image_file_reader`，CrashLoop 也能读，需要 REGISTRY_CREDS）
  - 回退 exec（同 Deployment 里有 Running 的 Pod 时可用，零凭据）
任何一步失败都不阻断其它步骤，失败原因写进 notes。
"""
from __future__ import annotations

import re

import yaml

from app.clients.executor_client import executor_client
from app.core.logging import get_logger
from app.services import image_file_reader
from app.utils.pod import infer_deployment_name

logger = get_logger("api-server.startup_evidence")

# 入口脚本可能的位置（按优先级）
ENTRYPOINT_PATHS = [
    "/usr/bin/enpoint",
    "/usr/bin/entrypoint",
    "/usr/local/bin/enpoint",
    "/entrypoint.sh",
    "/opt/entrypoint.sh",
    "/app/entrypoint.sh",
]
# jar 内配置（后缀匹配，jar 名因服务而异）
JAR_CONFIG_SUFFIXES = (
    "BOOT-INF/classes/application.yml",
    "BOOT-INF/classes/application.yaml",
    "BOOT-INF/classes/application.properties",
    "BOOT-INF/classes/bootstrap.yml",
    "BOOT-INF/classes/bootstrap.yaml",
)
JAR_SEARCH_DIRS = ("/apps/works/server", "/app", "/opt/app", "/opt", "/works")

# 首错行识别：越靠前越像「根异常」
# 2026-11-26 调整：
#   ① hard_failure 最高优先——APPLICATION FAILED TO START / 方法不存在 / NoSuchMethod / 缺类 / 类冲突，
#      这些直接定性（实测：原优先级会把「关闭阶段的 BeanCreationNotAllowedException」当根异常排第一）；
#   ② shutdown_noise 最低——初始化失败后的关闭/销毁阶段噪声，不是根因。
_ERROR_PATTERNS = [
    ("hard_failure", re.compile(
        r"(APPLICATION FAILED TO START|Application run failed|does not exist|NoSuchMethodError|"
        r"NoClassDefFoundError|ClassNotFoundException|LinkageError|Invalid or corrupt jarfile)", re.I)),
    ("root_exception", re.compile(r"^(?:Caused by:\s*)?((?:[a-zA-Z_$][\w$]*\.)+[A-Za-z_$][\w$]*(?:Exception|Error)):\s*(.*)$")),
    ("boot_failed", re.compile(r"(Failed to start|Error creating bean)", re.I)),
    ("error_line", re.compile(r"\b(ERROR|FATAL|PANIC|panic:)\b")),
    ("generic", re.compile(r"(exception|error|failed|refused|timeout|timed out)", re.I)),
]

# 关闭/销毁阶段噪声（初始化失败后的清理动作，不可能是根因）
_SHUTDOWN_NOISE_RE = re.compile(
    r"(in destruction|destroy method|GracefulShutdown|Stopping service|closing \.\.\.|"
    r"Start destroying|shutdown)", re.I)

# 敏感值打码：KEY=VALUE / KEY: VALUE / --key=value
_SECRET_KEY = r"(?:password|passwd|pwd|secret|token|access[_-]?key|api[_-]?key)"
_SECRET_RE = re.compile(rf"(?i)({_SECRET_KEY})(\s*[=:]\s*|\s+)(['\"]?)([^\s'\"]+)(\3)")


def mask_secrets(text: str) -> str:
    """把密码/密钥类值打成 ***（诊断结果会进日志/工单，不能带明文）。

    例外：`${KEY}` / `${KEY:default}` 这类占位符**不打码**——它不是密文而是配置事实，
    「有没有默认值」正是启动失败的关键判据（打码会丢掉证据）。
    """
    if not text:
        return ""

    def _sub(m: re.Match) -> str:
        val = m.group(4) or ""
        if not val or val.startswith("${"):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}***{m.group(5)}"

    return _SECRET_RE.sub(_sub, text)


def extract_first_error(text: str, limit: int = 5) -> list[dict]:
    """从日志里提取「首错行」（按严重度排序，最多 limit 条）。

    规则：Java 根异常（xxx.yyy.SomeException: msg）优先，其次 Spring Boot 启动失败标记，
    再次 ERROR/FATAL 行；同一条只保留一次。绝不返回「只取末尾 N 行」的噪声。
    """
    if not text:
        return []
    hits: list[dict] = []
    seen: set[str] = set()
    for idx, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or len(line) > 600:
            continue
        for kind, pat in _ERROR_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            key = line[:200]
            if key in seen:
                break
            seen.add(key)
            # 关闭/销毁阶段噪声降级（除非本身就是 hard_failure）
            if kind != "hard_failure" and _SHUTDOWN_NOISE_RE.search(line):
                kind = "shutdown_noise"
            if kind == "root_exception":
                hits.append({"line_no": idx, "kind": kind, "exception": m.group(1),
                             "message": m.group(2)[:300], "text": line[:400]})
            else:
                hits.append({"line_no": idx, "kind": kind, "exception": "",
                             "message": line[:300], "text": line[:400]})
            break
        if len(hits) >= limit * 3:
            break
    # 真实异常（带异常类+消息）排第一；决定性标记（APPLICATION FAILED TO START / 方法不存在）其次；
    # 关闭阶段噪声垫底（2026-11-26）
    order = {"root_exception": 0, "hard_failure": 1, "boot_failed": 2, "error_line": 3,
             "generic": 4, "shutdown_noise": 9}
    hits.sort(key=lambda h: (order.get(h["kind"], 9), h["line_no"]))
    return hits[:limit]


def _find(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text or "", re.I)
    return m.group(1) if m else default


def assess_credentials(entrypoint_text: str = "", cmdline_text: str = "", app_config_text: str = "") -> dict:
    """判定「默认口令 + 启动参数优先级」风险（这次事故的核心判据）。

    - 启动参数来源以 entrypoint 脚本 / /proc/1/cmdline 为准（`-DKEY=value`）
    - yml 里的 `${KEY:default}` 表示有兜底默认值；`${KEY}` 表示无默认值（必须有外部注入）
    - 系统属性 `-D` 优先级高于环境变量：脚本里写死后，Deployment 配 env 也覆盖不了
    """
    src = mask_secrets(f"{entrypoint_text or ''}\n{cmdline_text or ''}")
    out: dict = {
        "default_password_detected": False,
        "hardcoded_in_entrypoint": False,
        "env_override_would_work": True,
        "keys": [],
        "detail": "",
    }
    # 启动参数里的凭据（值已打码，只判断是否为出厂默认）
    for key in ("NACOS_USERNAME", "NACOS_PASSWORD", "SPRING_DATASOURCE_PASSWORD", "REDIS_PASSWORD"):
        raw_val = _find(rf"-D{key}=([^\s'\"\\]+)", f"{entrypoint_text or ''} {cmdline_text or ''}")
        yml_default = _find(rf"\$\{{{key}:([^}}]*)\}}", app_config_text or "")
        in_yml_no_default = bool(re.search(rf"\$\{{{key}\}}", app_config_text or ""))
        if not raw_val and not yml_default and not in_yml_no_default:
            continue
        is_default = (raw_val or yml_default).strip().lower() in {"nacos", "root", "123456", "password", "admin"}
        out["keys"].append({
            "key": key,
            "value_from_startup": "***" if raw_val else "",
            "yml_default": "***" if yml_default else "",
            "yml_requires_external": in_yml_no_default,
            "is_factory_default": is_default,
        })
        if is_default:
            out["default_password_detected"] = True
        if raw_val:
            out["hardcoded_in_entrypoint"] = True
    if out["hardcoded_in_entrypoint"]:
        # 脚本里的 -D 在 java 命令中位于 JAVA_OPTS 之后 → 覆盖环境变量
        out["env_override_would_work"] = False
    parts = []
    if out["default_password_detected"]:
        parts.append("启动参数里是出厂默认口令（nacos/root/…），新框架/新配置若要求非默认凭据会直接启动失败")
    if out["hardcoded_in_entrypoint"]:
        parts.append("凭据写在镜像入口脚本的 -D 参数里，Deployment 注入 env 无效（系统属性优先级更高）")
    if any(k.get("yml_requires_external") for k in out["keys"]):
        parts.append("application.yml 用无默认值的占位符（${KEY}），必须由外部注入，缺失即启动失败")
    out["detail"] = "；".join(parts)
    return out


def diff_lines(old_text: str, new_text: str, limit: int = 20) -> list[dict]:
    """逐行差异（忽略空行/注释），返回 [{old, new}]。"""
    def _norm(t: str) -> list[str]:
        out = []
        for ln in (t or "").splitlines():
            s = ln.rstrip()
            if not s.strip() or s.strip().startswith("#"):
                continue
            out.append(s)
        return out

    a, b = _norm(old_text), _norm(new_text)
    res: list[dict] = []
    n = max(len(a), len(b))
    for i in range(n):
        va = a[i] if i < len(a) else ""
        vb = b[i] if i < len(b) else ""
        if va != vb:
            res.append({"old": mask_secrets(va), "new": mask_secrets(vb)})
        if len(res) >= limit:
            break
    return res


def parse_pod_state(yaml_text: str) -> dict:
    """从 Pod YAML 解析容器状态（lastState.terminated 是最权威的崩溃证据）。"""
    out: dict = {"containers": [], "image": "", "phase": "", "app": ""}
    if not yaml_text:
        return out
    try:
        doc = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        return out
    spec = doc.get("spec") or {}
    status = doc.get("status") or {}
    out["phase"] = status.get("phase") or ""
    labels = (doc.get("metadata") or {}).get("labels") or {}
    out["app"] = labels.get("app") or labels.get("k8s-app") or ""
    spec_conts = {c.get("name"): c for c in (spec.get("containers") or [])}
    for cs in status.get("containerStatuses") or []:
        name = cs.get("name") or ""
        st = cs.get("state") or {}
        last = (cs.get("lastState") or {}).get("terminated") or {}
        image = (spec_conts.get(name) or {}).get("image") or ""
        if image and not out["image"]:
            out["image"] = image
        out["containers"].append({
            "name": name,
            "image": image,
            "ready": bool(cs.get("ready")),
            "restart_count": cs.get("restartCount") or 0,
            "waiting_reason": (st.get("waiting") or {}).get("reason") or "",
            "waiting_message": ((st.get("waiting") or {}).get("message") or "")[:200],
            "last_exit_code": last.get("exitCode"),
            "last_reason": last.get("reason") or "",
            "last_started": last.get("startedAt") or "",
            "last_finished": last.get("finishedAt") or "",
        })
    return out


def _render(pack: dict) -> str:
    """把证据包渲染成 LLM 易读的文本（≤6000 字）。"""
    lines = [f"【启动失败证据包】{pack.get('pod','')}（{pack.get('namespace','')}）"]
    for c in pack.get("containers") or []:
        lines.append(
            f"- 容器 {c['name']}: ready={c['ready']} restarts={c['restart_count']} "
            f"waiting={c['waiting_reason'] or '-'} "
            f"lastTerminated={{exitCode={c['last_exit_code']}, reason={c['last_reason'] or '-'}}}"
        )
    if pack.get("first_error"):
        lines.append("- 首错行（日志）：")
        for e in pack["first_error"]:
            lines.append(f"  · [L{e['line_no']}] {e['text']}")
    if pack.get("startup_window"):
        lines.append("- 启动失败详情（关键段落）：")
        for ln in (pack.get("startup_window") or [])[:60]:
            lines.append(f"  {ln[:400]}")
    src = pack.get("source") or {}
    lines.append(f"- 镜像内文件来源：{src.get('channel','-')}"
                 + (f"（{src.get('note')}）" if src.get("note") else ""))
    if pack.get("entrypoint"):
        lines.append("- 入口脚本/启动命令（凭据已打码）：")
        for ln in pack["entrypoint"].splitlines()[:25]:
            lines.append(f"  {ln[:400]}")
    cfg = pack.get("app_config") or {}
    if cfg:
        lines.append(f"- 镜像内 application.yml 关键行（{cfg.get('path','')}）：")
        for ln in (cfg.get("key_lines") or [])[:20]:
            lines.append(f"  {ln[:300]}")
    cred = pack.get("credential_risk") or {}
    if cred.get("detail"):
        lines.append(f"- 凭据风险评估：{cred['detail']}")
    prev = pack.get("previous") or {}
    if prev.get("image"):
        lines.append(f"- 上一版本对比（{prev['image']}）：")
        for d in (prev.get("entrypoint_diff") or [])[:8]:
            lines.append(f"  · 入口 - {d['old']}")
            lines.append(f"    + {d['new']}")
        for d in (prev.get("diff") or [])[:12]:
            lines.append(f"  · - {d['old']}")
            lines.append(f"    + {d['new']}")
        if not prev.get("comparable_entrypoint") and not prev.get("comparable_config"):
            lines.append("  · 无法逐行对比（旧版本文件未读到：镜像已清理或无 exec 目标）")
        elif not (prev.get("entrypoint_diff") or prev.get("diff")) and (
                prev.get("comparable_entrypoint") or prev.get("comparable_config")):
            lines.append("  · 已读到的关键文件无差异（差异可能在 jar 内依赖/框架版本，需人工深挖）")
    if pack.get("hints"):
        lines.append("- 候选结论（需与日志交叉验证，不要直接当最终结论）：")
        for h in pack["hints"]:
            lines.append(f"  · {h}")
    if pack.get("notes"):
        lines.append("- 采集说明/失败项：")
        for n in pack["notes"][:10]:
            lines.append(f"  · {n}")
    return "\n".join(lines)[:6000]


def _config_key_lines(text: str) -> list[str]:
    """yml 里与启动/凭据/地址相关的关键行。"""
    keys = ("nacos", "password", "username", "config.import", "profiles", "url", "addr", "port")
    out = []
    for ln in (text or "").splitlines():
        s = ln.rstrip()
        if not s.strip() or s.strip().startswith("#"):
            continue
        if any(k in s.lower() for k in keys):
            out.append(mask_secrets(s))
        if len(out) >= 40:
            break
    return out


async def _exec(pod: str, ns: str, cluster_id, cmd: str, container: str = "") -> dict:
    try:
        return await executor_client.acall(executor_client.exec_in_pod, cluster_id, ns, pod, cmd,
                                           container=container)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "output": "", "error": str(e)}


async def _read_via_exec(cluster_id, ns: str, pod: str, container: str) -> dict:
    """exec 通道：从 Running 的 Pod 读入口脚本 + jar 内配置（零凭据）。"""
    notes: list[str] = []
    entry_text = ""
    for p in ENTRYPOINT_PATHS:
        r = await _exec(pod, ns, cluster_id, f"cat {p}", container)
        if r.get("success") and (r.get("output") or "").strip():
            entry_text = r["output"]
            notes.append(f"入口脚本读取自 {p}")
            break
    if not entry_text:
        r = await _exec(pod, ns, cluster_id, "cat /proc/1/cmdline", container)
        if r.get("success") and (r.get("output") or "").strip():
            entry_text = r["output"].replace("\x00", " ")
            notes.append("入口脚本未找到，改用 /proc/1/cmdline")
    cfg_text, cfg_path = "", ""
    jar_path = ""
    for d in JAR_SEARCH_DIRS:
        r = await _exec(pod, ns, cluster_id, f"find {d} -maxdepth 2 -name *.jar", container)
        found = [ln.strip() for ln in (r.get("output") or "").splitlines() if ln.strip().endswith(".jar")]
        if found:
            jar_path = found[0]
            break
    if jar_path:
        for sfx in ("BOOT-INF/classes/application.yml", "BOOT-INF/classes/application.yaml"):
            r = await _exec(pod, ns, cluster_id, f"unzip -p {jar_path} {sfx}", container)
            if r.get("success") and (r.get("output") or "").strip():
                cfg_text, cfg_path = r["output"], f"{jar_path}!/{sfx}"
                break
        if not cfg_text:
            notes.append(f"容器内无 unzip 或读取失败，未能读取 {jar_path} 内配置"
                         "（配置只读仓库凭据 REGISTRY_CREDS 可改走镜像仓库通道）")
    else:
        notes.append("未找到应用 jar（非 Java 或不在此次搜索目录）")
    return {"channel": "exec", "note": "；".join(notes), "entrypoint": entry_text,
            "app_config": cfg_text, "app_config_path": cfg_path}


async def _read_via_registry(image: str) -> dict:
    """registry 通道：直接从镜像层读入口脚本 + jar 内配置（CrashLoop 也能读）。"""
    wanted: list[str] = []
    for p in ENTRYPOINT_PATHS:
        wanted.extend(image_file_reader.path_variants(p))
    r = await image_file_reader.read_image_files(image, wanted=wanted, jar_entries=JAR_CONFIG_SUFFIXES)
    files = r.get("files") or {}
    entry_path = next((p for p in wanted if p in files), "")
    cfg_path = next((p for p in sorted(files)
                     if "!/" in p and p.rsplit("!/", 1)[-1] in JAR_CONFIG_SUFFIXES), "")
    note = "；".join((r.get("notes") or [])[:3])
    return {
        "channel": "registry",
        "note": note or (r.get("error") or ""),
        "entrypoint": files.get(entry_path, ""),
        "app_config": files.get(cfg_path, ""),
        "app_config_path": cfg_path,
        "ok": bool(files),
        "error": r.get("error") or "",
    }


async def collect(context: dict, container: str = "", compare_previous: bool = True) -> dict:
    """采集启动失败证据包（只读，不抛异常）。"""
    cluster_id = context.get("cluster_id")
    ns = context.get("namespace") or ""
    pod = context.get("pod_name") or ""
    pack: dict = {"pod": pod, "namespace": ns, "containers": [], "first_error": [],
                  "entrypoint": "", "app_config": {}, "credential_risk": {},
                  "previous": {}, "source": {}, "hints": [], "notes": []}
    if not cluster_id or not pod:
        pack["notes"].append("缺少集群/Pod 上下文")
        pack["output"] = _render(pack)
        return {"success": False, "output": pack["output"], "error": "缺少集群/Pod 上下文", "data": pack}

    # 1) Pod 状态（lastState 最权威）
    try:
        y = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, ns, pod)
        state = parse_pod_state(y.get("yaml") or "")
    except Exception as e:  # noqa: BLE001
        state = {"containers": [], "image": "", "phase": "", "app": ""}
        pack["notes"].append(f"读取 Pod 失败: {e}")
    pack["containers"] = state["containers"]
    image = state.get("image") or ""
    app = state.get("app") or ""
    # 2) 日志首错行
    try:
        from app.utils.pod_logs import fetch_pod_logs
        logs = await fetch_pod_logs(cluster_id, ns, pod, tail=300)
        pack["first_error"] = extract_first_error(logs)
        # 2026-11-26：把「启动失败详情」块（APPLICATION FAILED TO START + Description/Action）
        # 直接放进证据包靠前位置——首错行常常只能给一行，定性靠这段
        try:
            from app.utils.pod_logs import extract_startup_window
            pack["startup_window"] = extract_startup_window([ln for ln in logs.splitlines()])
        except Exception:  # noqa: BLE001
            pack["startup_window"] = []
    except Exception as e:  # noqa: BLE001
        pack["notes"].append(f"日志采集失败: {e}")

    # 3) 镜像内文件：优先「当前镜像」的证据（registry 优先，其次同镜像 Running Pod）
    dep = infer_deployment_name(pod) or ""
    pod_infos: list[dict] = []
    try:
        if dep:
            r = await executor_client.acall(executor_client.list_deployment_pods, cluster_id, ns, dep)
            for p in (r.get("pods") or []):
                name = p.get("name") or ""
                if not name or name == pod:
                    continue
                y2 = await executor_client.acall(executor_client.get_pod_yaml, cluster_id, ns, name)
                st2 = parse_pod_state(y2.get("yaml") or "")
                c0 = (st2.get("containers") or [{}])[0]
                pod_infos.append({"name": name, "image": st2.get("image") or "",
                                  "ready": bool(c0.get("ready"))})
                if len(pod_infos) >= 6:
                    break
    except Exception as e:  # noqa: BLE001
        pack["notes"].append(f"列举同 Deployment Pod 失败: {e}")

    self_running = False
    if pack["containers"]:
        c0 = pack["containers"][0]  # 第一个容器 = 应用容器（sidecar 就绪不算应用就绪）
        self_running = bool(c0.get("ready")) or c0.get("last_exit_code") is None

    read: dict = {}
    files_are_old = False
    reg_cur = await _read_via_registry(image) if image else {"ok": False, "error": "未知当前镜像"}
    if reg_cur.get("ok"):
        read = reg_cur
        pack["source"] = {"channel": "registry", "note": reg_cur.get("note") or "", "image": image}
    else:
        target = pod if self_running else next(
            (p["name"] for p in pod_infos if p["ready"] and p["image"] == image), "")
        if target:
            rr = await _read_via_exec(cluster_id, ns, target, container)
            rr["ok"] = bool(rr.get("entrypoint") or rr.get("app_config"))
            read = rr
            pack["source"] = {"channel": "exec", "note": f"读取自同版本 Pod {target}；{rr.get('note') or ''}",
                              "image": image}
        else:
            # 当前版本读不到：用集群里还在跑的旧版本 Pod 作参考（明确标注，不冒充当前版本）
            old = next((p for p in pod_infos if p["ready"] and p["image"] and p["image"] != image), None)
            if old:
                rr = await _read_via_exec(cluster_id, ns, old["name"], container)
                if rr.get("entrypoint") or rr.get("app_config"):
                    read, files_are_old = rr, True
                    pack["source"] = {
                        "channel": "exec",
                        "note": f"⚠ 取自旧版本 Pod {old['name']}（{old['image']}），当前版本文件未读到；"
                                f"入口脚本/启动参数通常与上一版一致。{rr.get('note') or ''}",
                        "image": old["image"],
                        "stale_from_old": True,
                    }
            if not read:
                pack["notes"].append(
                    f"镜像内文件读取失败（{reg_cur.get('error') or '未知'}）：容器无 unzip、无同版本 Running Pod；"
                    "建议配置只读仓库凭据 REGISTRY_CREDS（registry 通道可直读镜像层文件）")

    pack["entrypoint"] = mask_secrets(read.get("entrypoint") or "")
    pack["app_config"] = {
        "path": read.get("app_config_path") or "",
        "key_lines": _config_key_lines(read.get("app_config") or ""),
    }
    pack["credential_risk"] = assess_credentials(
        entrypoint_text=read.get("entrypoint") or "",
        cmdline_text=read.get("entrypoint") or "",
        app_config_text=read.get("app_config") or "",
    )

    # 4) 与上一版本对比（registry 追历史 tag → 不行就用集群里还在跑的旧 Pod）
    if compare_previous and not files_are_old:
        prev_image, prev_texts = "", {}
        candidates: list[str] = []
        try:
            if dep:
                rev = await executor_client.acall(executor_client.list_revisions, cluster_id, ns, dep)
                items = sorted((rev.get("items") or []), key=lambda i: i.get("revision") or 0)
                cur = rev.get("current_revision") or 0
                for it in reversed(items):
                    imgs = it.get("images") or []
                    if (it.get("revision") or 0) < cur and imgs and imgs[0] != image:
                        candidates.append(imgs[0])
        except Exception as e:  # noqa: BLE001
            pack["notes"].append(f"读取发布历史失败: {e}")

        # 4a) registry 通道：逐个历史 tag 尝试（旧 tag 可能已被仓库清理）
        for cand in candidates[:3]:
            rr = await _read_via_registry(cand)
            if rr.get("ok"):
                prev_image, prev_texts = cand, rr
                break
        if candidates and not prev_texts:
            pack["notes"].append(f"历史镜像 {', '.join(candidates[:3])} 均无法从仓库读取（可能已被清理，或需 REGISTRY_CREDS）")

        # 4b) exec 通道：集群里还留着旧版本 Running Pod（本地镜像缓存）
        if not prev_texts:
            old = next((p for p in pod_infos if p["ready"] and p["image"] and p["image"] != image), None)
            if old:
                rr = await _read_via_exec(cluster_id, ns, old["name"], container)
                if rr.get("entrypoint") or rr.get("app_config"):
                    prev_texts, prev_image = rr, old["image"]
                    pack["notes"].append(f"上一版本文件读取自运行中的旧 Pod {old['name']}")
        if prev_texts:
            pack["previous"] = {
                "image": prev_image,
                "entrypoint_diff": diff_lines(prev_texts.get("entrypoint") or "", read.get("entrypoint") or ""),
                "diff": diff_lines(prev_texts.get("app_config") or "", read.get("app_config") or ""),
                "comparable_entrypoint": bool(prev_texts.get("entrypoint") and read.get("entrypoint")),
                "comparable_config": bool(prev_texts.get("app_config") and read.get("app_config")),
            }
        elif not candidates:
            pack["notes"].append("未找到可对比的上一版本（无历史 revision 或无法读取）")
    elif compare_previous and files_are_old:
        pack["notes"].append("入口脚本取自旧版本 Pod，与「上一版本」为同一来源，故不做版本差异对比")

    # 5) 候选结论（确定性推导，不做最终定性）
    hints = []
    exits = [c for c in pack["containers"] if (c.get("last_exit_code") not in (None, 0))]
    if exits:
        hints.append(f"容器以 exitCode={exits[0]['last_exit_code']} 退出（非 137/143），属应用主动退出，"
                     "根因应在应用启动逻辑/配置/依赖，不是探针")
    fe = pack["first_error"][0] if pack["first_error"] else {}
    if fe.get("kind") == "root_exception" and fe.get("exception"):
        hints.append(f"首错异常类型 {fe['exception']}：{fe.get('message','')[:120]}")
    cred = pack["credential_risk"]
    if cred.get("default_password_detected") and not cred.get("env_override_would_work"):
        hints.append("镜像内硬编码了出厂默认凭据且环境变量覆盖无效；若新版本框架要求非默认凭据 → 启动必失败")
    if any(k.get("yml_requires_external") for k in cred.get("keys") or []):
        hints.append("application.yml 使用无默认值占位符，必须有外部注入（凭据/地址），缺失即启动失败")
    if pack["previous"].get("diff") or pack["previous"].get("entrypoint_diff"):
        hints.append("上一版本与当前版本的入口脚本/配置存在差异，优先核对差异行是否引入必填项或强校验")
    if not pack["app_config"].get("key_lines"):
        hints.append("镜像内 application.yml 未取到（容器无 unzip 且未配仓库凭据），缺这一项时"
                     "「占位符是否还有默认值」需人工确认")
    pack["hints"] = hints

    pack["output"] = _render(pack)
    ok = bool(pack["containers"] or pack["first_error"] or pack["entrypoint"])
    return {"success": ok, "output": pack["output"],
            "error": "" if ok else "未采集到有效证据", "data": pack}
