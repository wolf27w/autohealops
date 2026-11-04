# -*- coding: utf-8 -*-
"""JVM 诊断 API（P0-R2）：对 Pod 内 Java 进程一键执行 jstack / jmap / jcmd / JFR。

设计要点
--------
- **零 gRPC 协议改动**：复用 executor 的 `ExecInPod`（容器内单命令执行），
  因为本机未安装 protoc-gen-go / protoc-gen-go-grpc，改 proto 无法重新生成 stub。
- **命令白名单化**：只允许下方 `_ACTIONS` 里的固定模板，pid 强制为纯数字，
  从根上阻断任意命令注入（executor 侧还会再拦 shell/管道等元字符）。
- **两种执行后端**（2026-10-14 加，解决 JRE 生产镜像）：
  1. **JDK 后端**：容器自带 JDK 工具（jstack/jmap/jcmd）时，直接用原生命令。
  2. **jattach 后端**：容器仅含 JRE（无 jcmd/jstack/jmap）时，注入一个 ~63KB 的
     `jattach` 静态二进制（走 JVM attach socket），可用 threaddump / inspectheap /
     jcmd 子命令，**JRE 也能出线程栈 / 堆直方图 / 堆信息 / VM 参数**。
     无法装 JDK 时不再裸执行 `jcmd` 抛 OCI 报错，而是明确降级 / 明确报因。
  缺失时如实返回错误提示与「自动安装 JDK 失败原因」，不静默。

动作分类
--------
- 文本类：直接返回命令输出（线程栈、直方图、堆信息、VM 参数等）
- 文件类（heap_dump / jfr_start）：产物写入容器内 /tmp，返回路径，
  前端再调用现有 `/resources/.../pods/.../file` 接口下载。
"""
import re
import time

import yaml
from fastapi import APIRouter, Depends

from app.clients.executor_client import executor_client
from app.core.config import settings
from app.core.logging import get_logger
from app.middleware.permission import require_any_user, require_operator
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/jvm")

# JDK/jattach 解析结果缓存（同一 Pod 容器多次动作不再重复安装/下载；TTL 10 分钟）
_resolve_cache: dict[tuple, tuple[float, object]] = {}
_RESOLVE_TTL = 600


def _cache_get(key: tuple):
    v = _resolve_cache.get(key)
    if v and time.time() - v[0] < _RESOLVE_TTL:
        return v[1]
    return None


def _cache_set(key: tuple, val: object) -> None:
    _resolve_cache[key] = (time.time(), val)

# 「一键 AI 综合诊断」自动采集的轻量只读动作（文本类；排除 heap_dump/jfr_start 重文件操作）
_AI_COLLECT_ACTIONS = ["thread_dump", "heap_histo", "heap_info", "gc_class_histo", "vm_flags", "sys_props"]
# 单个动作输出进 prompt 的上限（避免超长；超出追加截断标记）
_AI_EVIDENCE_LIMIT = 20000
# 全部证据总上限（字符）
_AI_EVIDENCE_TOTAL = 100000
logger = get_logger("api-server.jvm")


# 动作 → (JDK 命令模板, jattach 子命令模板, 是否产物文件, 是否需 pid, 中文名/说明)
# cmd 中的 {pid} 会替换为校验过的纯数字 pid；文件路径为固定安全路径。
# attach 为空 = 该动作无 JRE 等价实现（如 jmap -histo 的 live 语义），降级时报明。
_ACTIONS: dict[str, dict] = {
    "thread_dump": {
        "cmd": "jstack {pid}", "attach": "threaddump", "file": False, "need_pid": True,
        "label": "线程栈 jstack", "desc": "定位 CPU 飙高 / 死锁 / 线程池打满 / 接口卡住",
    },
    "thread_print": {
        "cmd": "jcmd {pid} Thread.print", "attach": "threaddump", "file": False, "need_pid": True,
        "label": "线程栈(jcmd)", "desc": "同上，走 jcmd，兼容性更好",
    },
    "heap_histo": {
        "cmd": "jmap -histo:live {pid}", "attach": "inspectheap", "file": False, "need_pid": True,
        "label": "堆直方图 jmap -histo", "desc": "对象数量/大小排行，查内存占用大户",
    },
    "heap_info": {
        "cmd": "jcmd {pid} GC.heap_info", "attach": "jcmd GC.heap_info", "file": False, "need_pid": True,
        "label": "堆信息 GC.heap_info", "desc": "各代容量/已用，查 GC 压力",
    },
    "gc_class_histo": {
        "cmd": "jcmd {pid} GC.class_histogram", "attach": "jcmd GC.class_histogram", "file": False, "need_pid": True,
        "label": "类直方图", "desc": "GC 后存活对象直方图",
    },
    "vm_flags": {
        "cmd": "jcmd {pid} VM.flags", "attach": "jcmd VM.flags", "file": False, "need_pid": True,
        "label": "JVM 启动参数 VM.flags", "desc": "查看 -Xms/-Xmx/-XX 等生效参数",
    },
    "sys_props": {
        "cmd": "jcmd {pid} VM.system_properties", "attach": "jcmd VM.system_properties", "file": False, "need_pid": True,
        "label": "系统属性", "desc": "System.getProperties() 全量",
    },
    "heap_dump": {
        "cmd": "jmap -dump:live,format=b,file=/tmp/jvm-heap.hprof {pid}",
        "attach": "dumpheap /tmp/jvm-heap.hprof", "file": True, "need_pid": True,
        "label": "堆转储 heap dump", "desc": "导出堆快照（大文件，落 /tmp 后下载分析）",
    },
    "jfr_start": {
        "cmd": "jcmd {pid} JFR.start name=autohealops duration=30s filename=/tmp/jvm-recording.jfr",
        "attach": "jcmd JFR.start name=autohealops duration=30s filename=/tmp/jvm-recording.jfr",
        "file": True, "need_pid": True,
        "label": "飞行记录 JFR", "desc": "低开销持续记录 GC/线程/锁/IO（30s，落 /tmp 后下载；JRE8 无 JFR）",
    },
}

# 产物文件的固定路径（前端下载时用）
_FILE_PATHS = {
    "heap_dump": "/tmp/jvm-heap.hprof",
    "jfr_start": "/tmp/jvm-recording.jfr",
}

_SAFE_PID = re.compile(r"^\d{1,7}$")
# jcmd -l 输出行：<pid> <主类/命令...>
_JCMD_LINE = re.compile(r"^(\d{1,7})\s+(.*)$")
# ps -eo pid,args 输出行：<pid> <args...>
_PS_LINE = re.compile(r"^\s*(\d{1,7})\s+(.*)$")
# 独立出现的 java 可执行（兼容裸 `java` 与 `/usr/lib/jvm/.../bin/java`；排除 -javaagent: 之类的参数）
_JAVA_PROC = re.compile(r"(^|[\s/])java(w)?(\s|$)")


async def _deny(user, cluster_id: int, namespace: str = "", required: str = "operate"):
    """集群资源级鉴权：无权限返回 403，否则 None"""
    if not await check_cluster_permission(user, cluster_id, namespace, required):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    return None


async def _exec(cluster_id: int, namespace: str, pod: str, command: str, container: str = "") -> dict:
    """在容器内执行单命令（executor 侧有白名单/元字符校验）"""
    return await executor_client.acall(
        executor_client.exec_in_pod, cluster_id, namespace, pod, command, container=container
    )


# 容器内装 JDK 工具：按包管理器探测（缺命令则自装；老板 2026-10-13 确认需要）
_JDK_INSTALL = {
    "apt-get": ["apt-get update", "apt-get install -y --no-install-recommends default-jdk-headless"],
    "yum": ["yum install -y java-17-openjdk-headless"],
    "dnf": ["dnf install -y java-17-openjdk-headless"],
    "apk": ["apk add --no-cache openjdk17-jdk"],
}


def _no_jdk_msg(note: str = "") -> str:
    """容器无任何 JVM 诊断能力的统一提示（含自动安装失败原因）。"""
    msg = (
        "容器内没有 JDK 诊断工具（jcmd/jstack/jmap），且 jattach 注入失败；"
        "该镜像疑似仅含 JRE，无法执行 JVM 诊断。"
        "（常见原因：/tmp 等可写目录被挂成 noexec 无法执行注入的二进制，或镜像只读/无网络）"
        "建议改用基于 JDK 的基础镜像，或在镜像内预装 JDK 工具。"
    )
    if note:
        msg += f" 自动安装 JDK 失败：{note[:600]}"
    else:
        msg += " 未探测到可用包管理器，无法自动安装 JDK。"
    return msg


async def _locate_jdk_bin(cluster_id: int, namespace: str, pod: str, container: str):
    """定位可用的 JDK bin 目录前缀（含结尾 '/'）。

    返回：'' = jcmd 在 PATH；非空 = bin 前缀；None = 找不到。
    顺序：① jcmd 在 PATH；② which jcmd；③ 常见目录 find jcmd（覆盖 JAVA_HOME/
    发行版默认路径，且 PID1 非 java 的情况）；④ 从 PID1 的 java 二进制反推。
    """
    r = await _exec(cluster_id, namespace, pod, "jcmd -l", container)
    if r.get("success"):
        return ""
    # which jcmd（PATH 有 jcmd 但 -l 失败时兜底）
    r = await _exec(cluster_id, namespace, pod, "which jcmd", container)
    for line in (r.get("output") or "").splitlines():
        line = line.strip()
        if line.endswith("/jcmd"):
            return line[: -len("jcmd")]
    # 常见 JDK 目录（PID1 是包装脚本 / JAVA_HOME 未在 PATH 时也能命中）
    r = await _exec(
        cluster_id, namespace, pod,
        "find /usr/lib/jvm /usr/java /usr/local /opt /app -maxdepth 4 -name jcmd -type f", container,
    )
    for line in (r.get("output") or "").splitlines():
        line = line.strip()
        if line.endswith("/jcmd"):
            return line[: -len("jcmd")]
    # 从 PID1 的 java 反推
    r = await _exec(cluster_id, namespace, pod, "readlink /proc/1/exe", container)
    if r.get("success"):
        p = (r.get("output") or "").strip()
        if p.endswith("/java"):
            return p[: -len("java")]
    return None


async def _switch_to_aliyun_repo(cluster_id: int, namespace: str, pod: str, container: str) -> None:
    """检查容器包源，尽量切到阿里云镜像（mirrors.aliyun.com）后再装。

    注意：executor 的 exec 不做引号处理（strings.Fields 切分），故 sed 命令**不带引号**。
    按 /etc/os-release 判 OS，best-effort（文件不存在/无 sed 则跳过）。
    """
    r = await _exec(cluster_id, namespace, pod, "cat /etc/os-release", container)
    out = (r.get("output") or "").lower()
    A = "mirrors.aliyun.com"
    if "debian" in out:
        expr, files = ["deb.debian.org", "security.debian.org"], ["/etc/apt/sources.list", "/etc/apt/sources.list.d/debian.sources"]
    elif "ubuntu" in out:
        expr, files = ["archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com"], ["/etc/apt/sources.list", "/etc/apt/sources.list.d/ubuntu.sources"]
    elif any(x in out for x in ("centos", "rhel", "almalinux", "rocky", "fedora")):
        expr, files = ["mirror.centos.org", "mirrorlist.centos.org", "dl.fedoraproject.org"], [
            "/etc/yum.repos.d/CentOS-Base.repo", "/etc/yum.repos.d/CentOS-Linux-BaseOS.repo",
            "/etc/yum.repos.d/Rocky-BaseOS.repo", "/etc/yum.repos.d/almalinux.repo",
        ]
    elif "alpine" in out:
        expr, files = ["dl-cdn.alpinelinux.org"], ["/etc/apk/repositories"]
    else:
        return
    sed_args = " ".join(f"-e s#{h}#{A}#g" for h in expr)
    for f in files:
        await _exec(cluster_id, namespace, pod, f"sed -i {sed_args} {f}", container)
    logger.info("JVM 诊断：已尝试把包源切至阿里云（%s）", files)


async def _resolve_jdk_bin(cluster_id: int, namespace: str, pod: str, container: str) -> tuple[str | None, str]:
    """返回 (bin 前缀, 安装失败说明)。

    bin 前缀：'' = jcmd 在 PATH；非空 = 目录前缀（含结尾 '/'）；**None = 找不到**。
    找不到时才尝试自动安装（apt/yum/dnf/apk）；安装命令的输出**完整保留**，
    失败原因随 note 返回给前端（原来的坑：输出被丢弃，只报 cryptic 错误）。
    """
    loc = await _locate_jdk_bin(cluster_id, namespace, pod, container)
    if loc is not None:
        return loc, ""
    ck = ("jdk", cluster_id, namespace, pod, container)
    cached = _cache_get(ck)
    if cached is not None:
        return cached  # type: ignore[return-value]
    notes: list[str] = []
    for pm, cmds in _JDK_INSTALL.items():
        probe = await _exec(cluster_id, namespace, pod, f"which {pm}", container)
        if not (probe.get("output") or "").strip():
            continue
        logger.info("JVM 诊断：容器缺 jcmd，尝试用 %s 安装 JDK 工具", pm)
        await _switch_to_aliyun_repo(cluster_id, namespace, pod, container)
        for c in cmds:
            rr = await _exec(cluster_id, namespace, pod, c, container)
            if not rr.get("success"):
                detail = (rr.get("error") or rr.get("output") or "").strip().replace("\n", " ")
                notes.append(f"[{c}] {detail[:300]}")
        loc = await _locate_jdk_bin(cluster_id, namespace, pod, container)
        if loc is not None:
            _cache_set(ck, (loc, ""))
            return loc, ""
        if not notes:
            notes.append(f"{pm} 安装执行完毕但仍未找到 jcmd")
        break
    result = (None, " ; ".join(notes))
    _cache_set(ck, result)
    return result


async def _ensure_jattach(cluster_id: int, namespace: str, pod: str, container: str) -> str | None:
    """确保容器内有 jattach：返回**可执行**的路径，失败返回 None。

    用于 JRE 镜像（无 JDK 工具）：jattach 是单文件静态二进制（~63KB），走 JVM
    attach socket，可做 threaddump / inspectheap / dumpheap / jcmd 子命令。
    ⚠️ 有些容器的 /tmp 被挂成 **noexec**（chmod +x 后仍 permission denied）→
    依次尝试多个可写目录，并实际探测可执行性后再采用。
    """
    base = (getattr(settings, "JVM_JATTACH_PATH", "/tmp/.autohealops-jattach") or "/tmp/.autohealops-jattach")
    url = (getattr(settings, "JVM_JATTACH_URL", "") or "").strip()
    ck = ("jattach", cluster_id, namespace, pod, container)
    cached = _cache_get(ck)
    if cached is not None:
        return cached  # type: ignore[return-value]
    cands: list[str] = [base]
    for d in ("/var/tmp", "/dev/shm", "/usr/local/bin", "/opt", "/root", "/app", "/home"):
        p = f"{d}/.autohealops-jattach"
        if p not in cands:
            cands.append(p)
    for path in cands:
        exists = (await _exec(cluster_id, namespace, pod, f"ls {path}", container)).get("success")
        if not exists:
            if not url:
                return None
            dl = await _exec(cluster_id, namespace, pod, f"curl -sSL -m 25 -o {path} {url}", container)
            if not dl.get("success"):
                dl = await _exec(cluster_id, namespace, pod, f"wget -q -O {path} {url}", container)
            if not dl.get("success"):
                logger.warning("JVM 诊断：jattach 下载失败(%s)：%s", path, (dl.get("error") or "")[:160])
                continue
        await _exec(cluster_id, namespace, pod, f"chmod +x {path}", container)
        # 实际探测可执行性（noexec 挂载会 permission denied）
        probe = await _exec(cluster_id, namespace, pod, path, container)
        err = (probe.get("error") or "").lower()
        if "permission denied" in err:
            logger.info("JVM 诊断：jattach 路径 %s 不可执行（可能 noexec），尝试其他目录", path)
            continue
        if "no such file" in err:
            continue
        _cache_set(ck, path)
        return path
    return None


async def _find_java_pid_ps(cluster_id: int, namespace: str, pod: str, container: str) -> tuple[int | None, str]:
    """通过 `ps -eo pid,args` 定位 java 进程 pid（JRE 无 jcmd 时的兜底）。"""
    r = await _exec(cluster_id, namespace, pod, "ps -eo pid,args", container)
    out = r.get("output") or ""
    for line in out.splitlines():
        m = _PS_LINE.match(line)
        if not m:
            continue
        pid, args = m.group(1), m.group(2)
        if "/.autohealops-jattach" in args or args.strip().startswith("ps "):
            continue
        if _JAVA_PROC.search(f" {args} "):
            return int(pid), out
    return None, out or "（ps 无输出：容器内可能没有 java 进程，或镜像无 ps/procps）"


async def _find_java_pid(cluster_id: int, namespace: str, pod: str, container: str,
                         jdk_bin: str | None) -> tuple[int | None, str]:
    """定位容器内第一个 Java 进程 pid。

    jdk_bin 非 None 时优先走 `jcmd -l`；否则（JRE）走 `ps -eo pid,args`。
    返回 (pid, 原始输出)；找不到时 pid=None。
    """
    if jdk_bin is not None:
        jc = jdk_bin + "jcmd"
        r = await _exec(cluster_id, namespace, pod, f"{jc} -l", container)
        if r.get("success"):
            out = r.get("output", "") or ""
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                m = _JCMD_LINE.match(line)
                if not m:
                    continue
                pid, cls = m.group(1), m.group(2)
                if "jcmd" in cls or "tools.jcmd" in cls:
                    continue
                return int(pid), out
            return None, out or "（jcmd -l 无输出：容器内可能没有 Java 进程）"
    return await _find_java_pid_ps(cluster_id, namespace, pod, container)


@router.get("/actions")
async def list_actions(user=Depends(require_operator)):
    """列出可用的 JVM 诊断动作（前端按钮用）"""
    data = [
        {"action": k, "label": v["label"], "desc": v["desc"], "file": v["file"]}
        for k, v in _ACTIONS.items()
    ]
    return {"code": 200, "message": "success", "data": data}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/processes")
async def list_java_processes(cluster_id: int, namespace: str, pod_name: str, container: str = "",
                              user=Depends(require_operator)):
    """列出容器内 Java 进程（JDK：jcmd -l；JRE：ps 兜底）"""
    if denied := await _deny(user, cluster_id, namespace):
        return denied
    jdk_bin, note = await _resolve_jdk_bin(cluster_id, namespace, pod_name, container)
    procs: list[dict] = []
    raw = ""
    if jdk_bin is not None:
        jc = jdk_bin + "jcmd"
        r = await _exec(cluster_id, namespace, pod_name, f"{jc} -l", container)
        if not r.get("success"):
            return {"code": 500, "message": r.get("error", "执行 jcmd -l 失败（容器内无 jcmd）"), "data": None}
        raw = r.get("output", "") or ""
        for line in raw.splitlines():
            line = line.strip()
            m = _JCMD_LINE.match(line)
            if not m:
                continue
            pid, cls = m.group(1), m.group(2)
            procs.append({"pid": int(pid), "target": cls, "is_jcmd": ("jcmd" in cls or "tools.jcmd" in cls)})
        backend = "jdk"
    else:
        # JRE：ps 兜底，仍能列进程（并提示将用 jattach）
        found, raw = await _find_java_pid_ps(cluster_id, namespace, pod_name, container)
        if found is not None:
            procs.append({"pid": found, "target": "java（ps 定位；容器为 JRE，诊断将用 jattach）", "is_jcmd": False})
        backend = "ps"

    data = {"raw": raw, "processes": procs, "backend": backend}
    if jdk_bin is None:
        data["hint"] = "容器无 JDK 工具（疑似 JRE）；诊断动作将自动注入 jattach 执行。" + (f"（自动装 JDK 失败：{note[:200]}）" if note else "")
    return {"code": 200, "message": "success", "data": data}


async def run_jvm_action(cluster_id: int, namespace: str, pod_name: str,
                         container: str, action: str, pid=None) -> dict:
    """执行一个 JVM 诊断动作的核心实现（供 HTTP 接口与诊断工具 `jvm_diag` 复用）。

    - 未传 pid 时自动定位容器内第一个 Java 进程。
    - 后端选择：容器有 JDK → 原生 jcmd/jstack/jmap；仅 JRE → 注入 jattach。
    - 文件类动作返回 file_path。
    返回 {"code", "message", "data"}（code=200 成功）。
    """
    spec = _ACTIONS.get(action)
    if not spec:
        return {"code": 400, "message": f"不支持的诊断动作：{action}（可选：{', '.join(_ACTIONS)}）", "data": None}

    # 定位执行后端：优先 JDK，其次 jattach（JRE）
    jdk_bin, note = await _resolve_jdk_bin(cluster_id, namespace, pod_name, container)
    jattach = None
    if jdk_bin is None:
        jattach = await _ensure_jattach(cluster_id, namespace, pod_name, container)
        if jattach is None:
            return {"code": 500, "message": _no_jdk_msg(note), "data": None}
        if not spec.get("attach"):
            return {"code": 500, "message": f"动作 {action} 在 JRE 容器（无 JDK）下无等价实现", "data": None}

    # 解析 / 定位 pid
    pid_out = ""
    if spec["need_pid"]:
        if pid is not None:
            pid_str = str(pid).strip()
            if not _SAFE_PID.match(pid_str):
                return {"code": 400, "message": "pid 非法（应为纯数字）", "data": None}
        else:
            found, pid_out = await _find_java_pid(cluster_id, namespace, pod_name, container, jdk_bin)
            if found is None:
                return {"code": 500, "message": f"未能自动定位 Java 进程（{pid_out[:300]}）", "data": None}
            pid_str = str(found)
    else:
        pid_str = ""

    if jdk_bin is not None:
        cmd = (jdk_bin + spec["cmd"]).format(pid=pid_str)
        backend = "jdk"
    else:
        cmd = f"{jattach} {pid_str} {spec['attach']}"
        backend = "jattach"

    r = await _exec(cluster_id, namespace, pod_name, cmd, container)
    if not r.get("success") and not (r.get("output") or "").strip():
        return {"code": 500, "message": r.get("error", "诊断命令执行失败（容器内可能无 JDK 工具）"), "data": None}

    data = {
        "action": action,
        "command": cmd,
        "pid": pid_str or None,
        "output": r.get("output", ""),
        "file": spec["file"],
        "backend": backend,
    }
    if spec["file"]:
        data["file_path"] = _FILE_PATHS[action]
        data["hint"] = "产物已写入容器内路径，可点「下载产物」拉取"
    return {"code": 200, "message": "success", "data": data}


@router.post("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/diagnose")
async def jvm_diagnose(cluster_id: int, namespace: str, pod_name: str, payload: dict,
                       user=Depends(require_operator)):
    """对 Pod 内 Java 进程执行一个 JVM 诊断动作。

    body: { container?: str, action: str, pid?: int }
    - 未传 pid 时自动定位容器内第一个 Java 进程。
    - 后端选择：容器有 JDK → 原生 jcmd/jstack/jmap；仅 JRE → 注入 jattach。
    - 文件类动作返回 file_path，前端再调资源接口下载产物。
    """
    if denied := await _deny(user, cluster_id, namespace):
        return denied
    return await run_jvm_action(
        cluster_id, namespace, pod_name,
        (payload.get("container") or "").strip(),
        (payload.get("action") or "").strip(),
        payload.get("pid"),
    )


# ============ R2.2 JVM 启动参数采集（只读） ============

def _extract_jvm_params(tokens: list) -> dict:
    """从容器的 command+args 令牌中提取常见 JVM 启动参数。"""
    out = {"xms": None, "xmx": None, "xss": None, "max_metaspace": None, "others": []}
    for t in (tokens or []):
        s = str(t)
        if s.startswith("-Xms"):
            out["xms"] = s[4:]
        elif s.startswith("-Xmx"):
            out["xmx"] = s[4:]
        elif s.startswith("-Xss"):
            out["xss"] = s[4:]
        elif s.startswith("-XX:MaxMetaspaceSize="):
            out["max_metaspace"] = s.split("=", 1)[1]
        elif s.startswith("-X") or s.startswith("-XX") or s.startswith("-D"):
            out["others"].append(s)
    return out


@router.post("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/ai-analyze")
async def jvm_ai_analyze(cluster_id: int, namespace: str, pod_name: str, payload: dict,
                         user=Depends(require_operator)):
    """把本次 JVM 诊断采集的证据发给 AI，做应用健康综合诊断（也可单独看原始输出）"""
    if denied := await _deny(user, cluster_id, namespace):
        return denied
    evidence = str(payload.get("evidence") or "")
    if not evidence.strip():
        return {"code": 400, "message": "evidence 必填（JVM 采集结果拼成的文本）", "data": None}
    from app.clients.ai_client import ai_client
    alert_data = {
        "fault_type": "application_diagnosis",
        "severity": str(payload.get("severity") or "high"),
        "namespace": namespace, "pod_name": pod_name, "resource_name": pod_name,
        "container": str(payload.get("container") or ""), "cluster_name": str(cluster_id),
    }
    try:
        data = await ai_client.jvm_analyze(alert_data, {"jvm_evidence": evidence}, "")
    except Exception as e:  # noqa: BLE001
        return {"code": 500, "message": f"JVM 综合诊断失败: {e}", "data": None}
    return {"code": 200, "message": "success", "data": data}


async def collect_and_analyze(cluster_id: int, namespace: str, pod_name: str,
                             container: str = "", pid=None, severity: str = "high") -> dict:
    """采集全部轻量 JVM 动作 → 汇总证据发 AI 综合诊断。供 HTTP 与定时采集复用。

    返回 {"items", "analysis", "evidence", "message"}。
    """
    items: list[dict] = []
    for action in _AI_COLLECT_ACTIONS:
        r = await run_jvm_action(cluster_id, namespace, pod_name, container, action, pid)
        d = r.get("data") or {}
        ok = r.get("code") == 200
        raw = (d.get("output") or "") if ok else ""
        truncated = len(raw) > _AI_EVIDENCE_LIMIT
        items.append({
            "action": action,
            "label": _ACTIONS[action]["label"],
            "ok": ok,
            "output": raw[:_AI_EVIDENCE_LIMIT] + ("\n…[输出过长已截断]" if truncated else "") if ok else "",
            "message": "" if ok else str(r.get("message") or "执行失败"),
        })
    if not any(i["ok"] for i in items):
        return {"items": items, "analysis": None, "evidence": "",
                "message": "所有 JVM 动作均失败（容器内可能无 JDK/jattach，或未定位到 Java 进程）：" + (items[0]["message"] if items else "")}
    parts: list[str] = []
    total = 0
    for i in items:
        seg = f"### {i['label']} ({i['action']})\n" + (i["output"] or f"[采集失败] {i['message']}")
        if total + len(seg) > _AI_EVIDENCE_TOTAL:
            seg = seg[: max(0, _AI_EVIDENCE_TOTAL - total)] + "\n[总证据超限已截断]"
        parts.append(seg)
        total += len(seg)
    evidence = "\n\n".join(parts)
    from app.clients.ai_client import ai_client
    alert_data = {
        "fault_type": "application_diagnosis", "severity": severity,
        "namespace": namespace, "pod_name": pod_name, "resource_name": pod_name,
        "container": container, "cluster_name": str(cluster_id),
    }
    try:
        analysis = await ai_client.jvm_analyze(alert_data, {"jvm_evidence": evidence}, "")
    except Exception as e:  # noqa: BLE001
        return {"items": items, "analysis": None, "evidence": evidence, "message": f"采集成功但 AI 诊断失败: {e}"}
    return {"items": items, "analysis": analysis, "evidence": evidence, "message": "success"}


@router.post("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/ai-analyze-all")
async def jvm_ai_analyze_all(cluster_id: int, namespace: str, pod_name: str, payload: dict,
                             user=Depends(require_operator)):
    """一键：自动跑完所有轻量 JVM 动作（jstack/jmap/jcmd/jattach 文本类）→ 汇总证据发 AI 综合诊断。

    - 重文件操作（heap_dump / jfr_start）不自动跑，避免大文件与长停顿；仍需手动单独执行。
    - 返回 items（每个动作的原始输出，可单独看）+ analysis（AI 诊断结论）。
    """
    if denied := await _deny(user, cluster_id, namespace):
        return denied
    container = str(payload.get("container") or "")
    res = await collect_and_analyze(cluster_id, namespace, pod_name, container,
                                    payload.get("pid"), str(payload.get("severity") or "high"))
    if res["analysis"] is None and not any(i["ok"] for i in res["items"]):
        return {"code": 500, "message": res["message"], "data": None}
    if res["analysis"] is None:
        return {"code": 200, "message": res["message"], "data": {"items": res["items"], "analysis": None}}
    return {"code": 200, "message": "success", "data": {"items": res["items"], "analysis": res["analysis"]}}


@router.get("/clusters/{cluster_id}/namespaces/{namespace}/deployments/{name}/params")
async def get_deployment_jvm_params(cluster_id: int, namespace: str, name: str, container: str = "",
                                    user=Depends(require_any_user)):
    """读取 Deployment 各容器的 JVM 启动参数（从 command/args 提取，只读）。"""
    if denied := await _deny(user, cluster_id, namespace, required="read"):
        return denied
    r = await executor_client.acall(executor_client.get_workload_yaml, cluster_id, "deployment", namespace, name)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "读取 Deployment YAML 失败"), "data": None}
    try:
        doc = yaml.safe_load(r.get("yaml", "")) or {}
    except yaml.YAMLError as e:
        return {"code": 500, "message": f"解析 YAML 失败：{e}", "data": None}
    containers = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
    items = []
    for c in containers:
        if container and c.get("name") != container:
            continue
        env = {e.get("name"): e.get("value") for e in (c.get("env") or []) if e.get("name")}
        env_jvm = []
        for k in ("JAVA_OPTS", "JAVA_TOOL_OPTIONS", "JVM_OPTS", "CATALINA_OPTS"):
            if env.get(k):
                env_jvm += str(env[k]).split()
        tokens = list(c.get("command") or []) + list(c.get("args") or []) + env_jvm
        items.append({
            "container": c.get("name"),
            "image": c.get("image"),
            "params": _extract_jvm_params(tokens),
            "command": c.get("command") or [],
            "args": c.get("args") or [],
            "env_jvm": env_jvm,
        })
    return {"code": 200, "message": "success", "data": {"deployment": name, "namespace": namespace, "containers": items}}


# ============ R2.3 JVM 堆/G1 指标（Prometheus jvm_* 数据源） ============
# 前提：集群内 JVM 应用已挂 JMX Exporter（jmx_prometheus_javaagent）并被 Prometheus 采集（jvm_* 指标）。
# 未接入时相关指标返回空，接口给出 hint（不报错）。物料见 docs/jvm-exporter/。

from app.services import prometheus_service  # noqa: E402

# metric -> (label, unit, promql_template(含 {SEL}), extra_selectors)
JVM_METRICS = {
    "heap_used":    ("堆已用", "bytes", "sum(jvm_memory_used_bytes{SEL}) by (pod)", 'area="heap"'),
    "heap_max":     ("堆上限", "bytes", "sum(jvm_memory_max_bytes{SEL}) by (pod)", 'area="heap"'),
    "heap_used_pct": ("堆使用率", "%",
                      "100 * sum(jvm_memory_used_bytes{SEL}) by (pod) / clamp_min(sum(jvm_memory_max_bytes{SEL}) by (pod), 1)",
                      'area="heap"'),
    "gc_time_rate": ("GC 耗时/秒", "s/s", "sum(rate(jvm_gc_collection_seconds_sum{SEL}[5m])) by (pod)", ""),
    "gc_count_rate": ("GC 次数/秒", "ops/s", "sum(rate(jvm_gc_collection_seconds_count{SEL}[5m])) by (pod)", ""),
    "threads":      ("线程数", "", "sum(jvm_threads_current{SEL}) by (pod)", ""),
    "classes_loaded": ("已加载类", "", "sum(jvm_classes_loaded{SEL}) by (pod)", ""),
}


def _jvm_sel(namespace: str, pod: str, extra: str) -> str:
    pairs = []
    if extra:
        pairs.append(extra)
    if namespace:
        pairs.append(f'namespace="{namespace}"')
    if pod:
        pairs.append(f'pod="{pod}"')
    return "{" + ",".join(pairs) + "}" if pairs else ""


@router.get("/clusters/{cluster_id}/jvm-metrics")
async def jvm_metrics(cluster_id: int, namespace: str = "", pod: str = "",
                      user=Depends(require_any_user)):
    """JVM 堆/GC 等指标（即时）。需集群已接入 JMX Exporter（jvm_* 指标）；未接入返回空 + hint。"""
    if not await check_cluster_permission(user, cluster_id, namespace, "read"):
        return {"code": 403, "message": "无权限访问该集群", "data": None}
    out = {}
    any_hit = False
    for name, (label, unit, tpl, extra) in JVM_METRICS.items():
        promql = tpl.replace("{SEL}", _jvm_sel(namespace, pod, extra))
        r = await prometheus_service.query(cluster_id, promql)
        items = r.get("result", []) if r.get("success") else []
        if items:
            any_hit = True
        out[name] = {"label": label, "unit": unit, "items": items}
    hint = "" if any_hit else ("未检测到 jvm_* 指标：请先为该应用接入 JMX Exporter（见 docs/jvm-exporter/）并由 Prometheus 采集。")
    return {"code": 200, "message": "success", "data": {"metrics": out, "connected": any_hit, "hint": hint}}
