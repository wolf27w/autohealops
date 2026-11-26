# -*- coding: utf-8 -*-
"""容器 stdout 日志采集（诊断上下文用）。

口径（老板 2026-10-14 定）：诊断的日志**只用容器 stdout**（kubectl logs 等价），**不走 ES**。
- 优先取 --previous（上次被杀的实例，最接近崩溃现场），再取当前实例。
- 为避免大批 INFO 日志淹没崩溃现场：每个来源取「关键错误/告警行（跳过超长 payload 行）」+「日志末尾」两段。

2026-11-26 重写（老板实测 qa-asfp/dgis-module-universals 诊断"证据不足"）：
  原实现用 `kubectl logs --all-containers=true --previous`，多容器 Pod 里只要有一个容器从未重启
  （filebeat sidecar），整条命令直接失败（`previous terminated container "filebeat" ... not found`）
  → 上次崩溃日志全丢；只剩 `--all-containers` 的当前日志，又被 filebeat 每 30s 一条的超大 JSON
  监控日志挤满 tail，应用容器的崩溃栈根本进不了上下文。
  现在：先按容器维度取（Pod YAML 的 containerStatuses 拿容器名），**跳过日志 sidecar**，
  每个容器独立取 previous / 当前（单容器失败不影响其它），并给每段打容器标签。
"""
import re
import time

from app.clients.executor_client import executor_client
from app.core.logging import get_logger

logger = get_logger("api-server.pod_logs")

# 关键错误/告警行特征
_LOG_ERR_HINTS = ("error", "exception", "warn", "caused by", "timeout", "timed out",
                  "failed", "refused", "unhealthy", "503", "oom", "killed",
                  "startup", "liveness", "readiness",
                  # 2026-11-23：Spring Boot 启动失败三段（否则只能拿到"APPLICATION FAILED TO START"标题，
                  # 拿不到 Description/Action 里的具体失败项）
                  "application failed to start", "description:", "action:",
                  "error creating bean", "beancreationexception", "failed to bind",
                  # 2026-11-26：类/依赖冲突（NoSuchMethodError / ClassNotFound 等）关键行
                  "nosuchmethod", "noclassdef", "classnotfound", "abstractmethod",
                  "did not exist", "incompatible", "linkageerror")

# 启动失败关键段落（Spring Boot / 通用异常栈）：从触发行起截取后续行，保留完整 Description/Action/Caused by
_STARTUP_MARKERS = (
    "application failed to start", "failed to start", "error creating bean",
    "beancreationexception", "unsatisfieddependencyexception", "failed to bind",
    "duplicatekeyexception", "invalid or corrupt jarfile", "no such file or directory",
    # 2026-11-26：Spring Boot 3 / 依赖冲突现场
    "an attempt was made to call a method that does not exist",
    "nosuchmethoderror", "noclassdeffounderror",
)

# 2026-11-26：最决定性的段落标记（先找它们，再回落普通标记）。
# 实测：日志里「初始化失败→关闭流程」的 WARN/BeanCreationException 常排在前面，
# 按第一个标记截窗会让 40 行窗口被堆栈帧吃满，真正定性的 Description/Action 反而被截掉。
_HARD_STARTUP_MARKERS = (
    "application failed to start",
    "an attempt was made to call a method that does not exist",
    "nosuchmethoderror", "noclassdeffounderror", "invalid or corrupt jarfile",
)

# 日志类 sidecar：日志不进诊断上下文（会挤掉应用容器证据）；也不该按它判 --previous 可用性
_SIDECAR_PREFIXES = ("filebeat", "fluent-bit", "fluentd", "logstash", "logtail", "vector",
                     "istio-proxy", "envoy", "linkerd-proxy", "sidecar")

# 2026-11-26（老板）：很多 Java 应用日志**写在容器内文件**里（stdout 只有启动脚本的 Killed 行），
# 例如 -DJM.LOG.PATH=/apps/works/logs/afbase/。诊断必须优先看容器内日志文件，stdout 只作补充。
_PATH_HINT_RES = (
    re.compile(r"-D[\w.]*LOG[_A-Z]*PATH=([^\s\"'=]+)", re.I),      # -DJM.LOG.PATH=/apps/works/logs/afbase/
    re.compile(r"-D[\w.]*log(?:\.path|\.dir)=([^\s\"'=]+)", re.I),  # -Dapp.log.path=...
    re.compile(r"\b(?:LOG_PATH|LOG_DIR|LOGGING_PATH|LOG_HOME|LOG_DIRECTORY)=([^\s\"'=]+)", re.I),
)
_DEFAULT_LOG_DIRS = (
    "/apps/works/logs", "/app/logs", "/opt/logs", "/usr/local/tomcat/logs",
    "/home/app/logs", "/var/log/app", "/logs",
)
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_./@-]+$")

# 容器名缓存（同一 Pod 在一次诊断里会被调多次；容器名基本不变）
_CTN_CACHE: dict[tuple[int, str, str], tuple[float, list[str]]] = {}
_CTN_TTL_SECONDS = 120


def extract_startup_window(lines: list[str], window: int = 40, max_windows: int = 2) -> list[str]:
    """从日志行中截取「启动失败现场」：命中标记行起后续 window 行（最多 max_windows 段）。

    2026-11-23 老板反馈「分析不到主要原因」：原来只留关键错误行 + 末尾 30 行，
    Spring 的 Description/Action/首个 Caused by 常被截掉；这里保留完整上下文窗口。

    2026-11-26：先用「决定性标记」（APPLICATION FAILED TO START / 方法不存在 / NoSuchMethod）
    截一段；命中就不再用普通标记（否则会被前面的 BeanCreation 堆栈占满窗口）。
    """
    out = _windows(lines, _HARD_STARTUP_MARKERS, window, 1)
    if out:
        return out
    return _windows(lines, _STARTUP_MARKERS, window, max_windows)


def _windows(lines: list[str], markers: tuple[str, ...], window: int, max_windows: int) -> list[str]:
    out: list[str] = []
    taken = 0
    i = 0
    n = len(lines)
    while i < n and taken < max_windows:
        low = lines[i].lower()
        if any(m in low for m in markers):
            out.append("…（启动失败现场）")
            out.extend(ln[:400] for ln in lines[i:i + window])
            taken += 1
            i += window
            continue
        i += 1
    return out


def _is_sidecar(name: str) -> bool:
    low = (name or "").lower()
    return any(low.startswith(p) for p in _SIDECAR_PREFIXES)


async def _container_names(cluster_id: int, namespace: str, pod_name: str) -> list[str]:
    """取要采日志的容器名（跳过日志 sidecar）；拿不到返回空列表（调用方回退默认容器）"""
    key = (cluster_id, namespace, pod_name)
    hit = _CTN_CACHE.get(key)
    if hit and time.time() - hit[0] < _CTN_TTL_SECONDS:
        return hit[1]
    names: list[str] = []
    try:
        from app.services.termination_evidence import collect_pod_termination
        facts = await collect_pod_termination(cluster_id, namespace, pod_name)
        all_names = [str(c.get("name") or "") for c in (facts.get("containers") or [])]
        all_names = [n for n in all_names if n]
        primary = [n for n in all_names if not _is_sidecar(n)]
        names = primary or all_names
    except Exception as e:  # noqa: BLE001
        logger.debug("取 Pod %s/%s 容器名失败: %s", namespace, pod_name, e)
    if names:
        _CTN_CACHE[key] = (time.time(), names)
    return names


def _log_dirs_from_hints(*texts: str) -> list[str]:
    """从启动参数/日志里解析应用自己的日志目录（纯函数，去重、只保留绝对路径）"""
    out: list[str] = []
    for text in texts:
        if not text:
            continue
        for rx in _PATH_HINT_RES:
            for m in rx.finditer(text):
                p = (m.group(1) or "").strip().rstrip(";,")
                if not p.startswith("/"):
                    continue
                # 取值可能是「目录」也可能是「文件」：带日志扩展名当文件取父目录，否则当目录
                low = p.lower()
                if low.endswith((".log", ".out", ".txt", ".json", ".log.gz")) or (
                        not p.endswith("/") and "." in p.rsplit("/", 1)[-1]):
                    d = p.rsplit("/", 1)[0]
                else:
                    d = p
                d = d.rstrip("/") or "/"
                if d not in out:
                    out.append(d)
    return out


def _safe_path(p: str) -> bool:
    return bool(p) and ".." not in p and bool(_SAFE_PATH_RE.match(p))


async def _list_dir(exec_fn, d: str) -> tuple[list[str], list[str]]:
    """列目录：返回 (文件名列表, 子目录列表)。用 `ls -1lt` 看首字符区分（无 shell 可用）。"""
    r = await exec_fn(f"ls -1lt {d}")
    if not r.get("success"):
        return [], []
    files, dirs = [], []
    for ln in (r.get("output") or "").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("total ") or len(ln) < 10:
            continue
        mode = ln[0]
        name = ln.split()[-1]
        if not _safe_path(name) or name.startswith("."):
            continue
        if mode == "d":
            dirs.append(name)
        elif mode == "-":
            files.append(name)
    return files, dirs


async def _container_log_files(cluster_id: int, namespace: str, pod_name: str,
                               container: str, hints: str) -> str:
    """读**容器内日志文件**（应用日志通常在这里，stdout 只有启动脚本输出）。

    2026-11-26 关键约束：exec_in_pod 是「单命令、不经 shell」，管道/重定向/通配符全不可用
    （会报「诊断命令包含非法字符」）。所以只用 ls / tail 两个命令，过滤放 Python 侧。

    流程：日志目录（启动参数 -Dxxx.LOG.PATH + 常见目录，最多 5 个）→ 列文件；
    目录里只有子目录时下钻一层（如 afbase/nacos/logs）→ 取最新日志文件 tail 2000 行
    → Python 提错误行。单步失败不影响其它。
    """
    if not container:
        return ""

    async def _exec(cmd: str) -> dict:
        try:
            return await executor_client.acall(
                executor_client.exec_in_pod, cluster_id, namespace, pod_name, cmd,
                container=container)
        except Exception as e:  # noqa: BLE001
            logger.debug("容器内取日志失败 %s: %s", cmd[:80], e)
            return {"success": False, "output": "", "error": str(e)}

    async def _read_file(d: str, fname: str) -> str:
        fpath = f"{d.rstrip('/')}/{fname}"
        if not _safe_path(fpath):
            return ""
        tail = await _exec(f"tail -n 2000 {fpath}")
        text = (tail.get("output") or "").strip() if tail.get("success") else ""
        if not text:
            return ""
        return _render(f"容器内日志文件 {fpath}", text)

    dirs = _log_dirs_from_hints(hints) + list(_DEFAULT_LOG_DIRS)
    parts: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        if not _safe_path(d) or d in seen:
            continue
        seen.add(d)
        if len(seen) > 5:
            break
        files, subdirs = await _list_dir(_exec, d)
        if files:
            for fname in files[:2]:
                seg = await _read_file(d, fname)
                if seg:
                    parts.append(seg)
                if len(parts) >= 2:
                    break
        else:
            # 目录里只有子目录 → 下钻一层（如 afbase/nacos/logs）
            for sub in subdirs[:2]:
                subdir = f"{d.rstrip('/')}/{sub}"
                sf, _ignored = await _list_dir(_exec, subdir)
                for fname in sf[:2]:
                    seg = await _read_file(subdir, fname)
                    if seg:
                        parts.append(seg)
                    if len(parts) >= 2:
                        break
                if len(parts) >= 2:
                    break
        if parts:
            break
    return "\n\n".join(parts)


# 2026-11-26：噪声行（DEBUG/TRACE、MyBatis SQL 参数）——有错误行时一律不进上下文，
# 否则 2000 行里 95% 是 SQL DEBUG，模型看到的「日志」全是噪声（老板实测结论跑偏）。
_NOISE_RE = re.compile(r"\b(DEBUG|TRACE)\b|==>\s+Preparing|==>\s+Parameters|<==\s+", re.I)


def _is_noise(line: str) -> bool:
    return bool(_NOISE_RE.search(line))


# 强错误特征（不看日志级别也要保留）
_HARD_ERR = ("error", "exception", "caused by", "fatal", "panic", "outofmemory",
             "nosuchmethod", "classnotfound", "application failed to start", "killed")


def _is_error_line(line: str) -> bool:
    """是否是「值得给模型看」的错误行（2026-11-26）

    原来只要命中 _LOG_ERR_HINTS 就留 → 连 `INFO ... connect timeout:1000` 这种配置日志都算错误行，
    模型拿到的「关键错误」其实是噪声。现在：强特征无条件留；弱特征（timeout/unhealthy/startup…）
    必须是 WARN/ERROR/FATAL 级别；DEBUG/INFO 的 SQL/参数行一律排除。
    """
    if _is_noise(line):
        return False
    low = line.lower()
    if any(h in low for h in _HARD_ERR):
        return True
    if any(h in low for h in _LOG_ERR_HINTS):
        return bool(re.search(r"\b(WARN|ERROR|FATAL)\b", line))
    return False


def _render(label: str, txt: str) -> str:
    """把单容器单实例的原始日志压成「关键行 + 末尾 + 启动失败现场」"""
    lines = [ln for ln in txt.splitlines() if ln.strip()]
    if not lines:
        return ""
    err_lines = [ln[:300] for ln in lines if len(ln) <= 400 and _is_error_line(ln)][-25:]
    # 有错误行就只留 8 行末尾做「现场收尾」；没有错误行才给 30 行末尾（并从末尾里剔噪声）
    if err_lines:
        clean_tail = [ln[:300] for ln in lines if not _is_noise(ln)][-8:]
    else:
        clean_tail = [ln[:300] for ln in lines if not _is_noise(ln)][-30:]
    startup_lines = extract_startup_window([ln for ln in lines if not _is_noise(ln)])
    seg = f"【{label}】"
    if startup_lines:
        seg += "\n启动失败详情：\n" + "\n".join(startup_lines)
    if err_lines:
        seg += "\n关键错误/告警行：\n" + "\n".join(err_lines)
    if clean_tail:
        seg += "\n日志末尾：\n" + "\n".join(clean_tail)
    return seg


async def _collect_pod_logs(cluster_id: int, namespace: str, pod_name: str, tail: int) -> tuple[list[str], bool]:
    """按容器采一个 Pod 的日志；返回 (段落列表, 是否疑似 Pod 不存在)"""
    parts: list[str] = []
    not_found = False
    targets = await _container_names(cluster_id, namespace, pod_name)
    if not targets:
        targets = [""]  # 拿不到容器名：交给后端按默认容器取（单容器 Pod 可用）
    for c in targets:
        for prev in (True, False):
            label = (f"{c}·上次实例(--previous)" if prev else f"{c}·当前实例") if c else \
                    ("默认容器·上次实例(--previous)" if prev else "默认容器·当前实例")
            try:
                r = await executor_client.acall(
                    executor_client.get_pod_logs, cluster_id, namespace, pod_name,
                    container=c, tail_lines=tail, previous=prev,
                )
                if not r.get("success"):
                    err = str(r.get("error") or "")
                    if "not found" in err.lower() or "notfound" in err.lower():
                        not_found = True
                    logger.debug("取日志失败 %s/%s [%s]: %s", namespace, pod_name, label, err)
                    continue
                txt = (r.get("logs") or "").strip()
            except Exception as e:  # noqa: BLE001
                logger.warning("拉取 Pod %s/%s 日志异常(%s): %s", namespace, pod_name, label, e)
                continue
            seg = _render(label, txt)
            if seg:
                parts.append(seg)
    return parts, not_found


async def _resolve_live_pod(cluster_id: int, namespace: str, pod_name: str) -> str:
    """告警上的 Pod 已被重建/删除时，找同 Deployment 当前在跑（优先未就绪=正在堆）的 Pod"""
    try:
        from app.utils.pod import infer_deployment_name
        dep = infer_deployment_name(pod_name)
        if not dep:
            return ""
        r = await executor_client.acall(executor_client.list_deployment_pods, cluster_id, namespace, dep)
        if not r.get("success"):
            return ""
        cands = [p for p in (r.get("pods") or []) if str(p.get("name") or "") != pod_name]
        if not cands:
            return ""
        # 优先「未就绪 + 重启多」的（正在崩溃的那个），其次创建较新的（名字后半段排序不可靠，只用就绪/重启量）
        cands.sort(key=lambda p: (bool(p.get("ready")), -(int(p.get("restart_count") or 0))))
        return str(cands[0].get("name") or "")
    except Exception as e:  # noqa: BLE001
        logger.debug("解析 Pod %s/%s 同服务存活 Pod 失败: %s", namespace, pod_name, e)
        return ""


async def fetch_pod_logs(cluster_id: int, namespace: str, pod_name: str, tail: int = 200) -> str:
    """拉取容器 stdout 日志（含上次崩溃实例）作为诊断上下文；返回精简文本（无则空串）。

    按容器逐个取（container 维度走 GetPodLogs RPC，隧道感知），previous 单独一次调用：
    某个容器没有 previous（从未重启）只丢那一路，不影响其它容器/当前实例。
    告警上的 Pod 已被重建（CrashLoop 下常见）→ 自动改取同服务当前在跑的 Pod，并在文首标明。
    """
    if not pod_name:
        return ""
    # 1) 先取 stdout（同时拿到启动参数里的日志目录线索）
    parts, not_found = await _collect_pod_logs(cluster_id, namespace, pod_name, tail)
    stdout_text = "\n\n".join(parts)
    note = ""
    if not parts and not_found:
        # 2026-11-26：告警记录的 Pod 名可能已过期（Pod 被删/重建），取证不能就此落空
        live = await _resolve_live_pod(cluster_id, namespace, pod_name)
        if live:
            _CTN_CACHE.pop((cluster_id, namespace, live), None)
            parts, _nf = await _collect_pod_logs(cluster_id, namespace, live, tail)
            if parts:
                note = f"（告警 Pod {pod_name} 已不存在，改用同服务当前 Pod {live}）\n"
                logger.info("Pod %s/%s 已不存在，诊断日志改取 %s", namespace, pod_name, live)
    # 2) 2026-11-26（老板）：容器内日志文件才是主证据（Java 应用多写文件，stdout 只有启动脚本输出）
    file_seg = ""
    try:
        targets = await _container_names(cluster_id, namespace, pod_name)
        cname = targets[0] if targets else ""
        hint_src = stdout_text + "\n" + (note or "")
        file_seg = await _container_log_files(cluster_id, namespace, pod_name, cname, hint_src)
    except Exception as e:  # noqa: BLE001
        logger.debug("容器内日志文件采集失败: %s", e)
    if file_seg:
        parts = [file_seg] + parts
    # 2026-11-26：按「有没有硬错误行」排序——带真错误的段放最前（文件日志不一定比 stdout 有料，
    # 比如本例真正的 OOM 证据就在 stdout 的 Killed 行），保证首错行来自真证据。
    def _rank(seg: str) -> int:
        low = seg.lower()
        return 0 if any(h in low for h in ("oomkilled", "outofmemory", "exception", "caused by",
                                           "fatal", "panic", "error ", "killed")) else 1
    parts.sort(key=_rank)
    return (note + "\n\n".join(parts)) if parts else ""
