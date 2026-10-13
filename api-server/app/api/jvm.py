# -*- coding: utf-8 -*-
"""JVM 诊断 API（P0-R2）：对 Pod 内 Java 进程一键执行 jstack / jmap / jcmd / JFR。

设计要点
--------
- **零 gRPC 协议改动**：复用 executor 的 `ExecInPod`（容器内单命令执行），
  因为本机未安装 protoc-gen-go / protoc-gen-go-grpc，改 proto 无法重新生成 stub。
- **命令白名单化**：只允许下方 `_ACTIONS` 里的固定模板，pid 强制为纯数字，
  从根上阻断任意命令注入（executor 侧还会再拦 shell/管道等元字符）。
- **依赖**：目标容器需自带 JDK 工具（jstack/jmap/jcmd，含在 JDK，JRE 无）。
  缺失时如实返回错误提示，不静默。

动作分类
--------
- 文本类：直接返回命令输出（线程栈、直方图、堆信息、VM 参数等）
- 文件类（heap_dump / jfr_start）：产物写入容器内 /tmp，返回路径，
  前端再调用现有 `/resources/.../pods/.../file` 接口下载。
"""
import re

import yaml
from fastapi import APIRouter, Depends

from app.clients.executor_client import executor_client
from app.core.logging import get_logger
from app.middleware.permission import require_any_user, require_operator
from app.services.user_permission_service import check_cluster_permission

router = APIRouter(prefix="/api/v1/jvm")
logger = get_logger("api-server.jvm")


# 动作 → (命令模板, 是否产物文件, 是否需 pid, 中文名/说明)
# 模板中的 {pid} 会替换为校验过的纯数字 pid；{file} 替换为固定安全路径。
_ACTIONS: dict[str, dict] = {
    "thread_dump": {
        "cmd": "jstack {pid}", "file": False, "need_pid": True,
        "label": "线程栈 jstack", "desc": "定位 CPU 飙高 / 死锁 / 线程池打满 / 接口卡住",
    },
    "thread_print": {
        "cmd": "jcmd {pid} Thread.print", "file": False, "need_pid": True,
        "label": "线程栈(jcmd)", "desc": "同上，走 jcmd，兼容性更好",
    },
    "heap_histo": {
        "cmd": "jmap -histo:live {pid}", "file": False, "need_pid": True,
        "label": "堆直方图 jmap -histo", "desc": "对象数量/大小排行，查内存占用大户",
    },
    "heap_info": {
        "cmd": "jcmd {pid} GC.heap_info", "file": False, "need_pid": True,
        "label": "堆信息 GC.heap_info", "desc": "各代容量/已用，查 GC 压力",
    },
    "gc_class_histo": {
        "cmd": "jcmd {pid} GC.class_histogram", "file": False, "need_pid": True,
        "label": "类直方图", "desc": "GC 后存活对象直方图",
    },
    "vm_flags": {
        "cmd": "jcmd {pid} VM.flags", "file": False, "need_pid": True,
        "label": "JVM 启动参数 VM.flags", "desc": "查看 -Xms/-Xmx/-XX 等生效参数",
    },
    "sys_props": {
        "cmd": "jcmd {pid} VM.system_properties", "file": False, "need_pid": True,
        "label": "系统属性", "desc": "System.getProperties() 全量",
    },
    "heap_dump": {
        "cmd": "jmap -dump:live,format=b,file=/tmp/jvm-heap.hprof {pid}", "file": True, "need_pid": True,
        "label": "堆转储 heap dump", "desc": "导出堆快照（大文件，落 /tmp 后下载分析）",
    },
    "jfr_start": {
        "cmd": "jcmd {pid} JFR.start name=apiops duration=30s filename=/tmp/jvm-recording.jfr", "file": True, "need_pid": True,
        "label": "飞行记录 JFR", "desc": "低开销持续记录 GC/线程/锁/IO（30s，落 /tmp 后下载）",
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


async def _locate_jdk_bin(cluster_id: int, namespace: str, pod: str, container: str):
    """定位 JDK bin 前缀（'' = jcmd 在 PATH；返回 None = 找不到）。"""
    r = await _exec(cluster_id, namespace, pod, "jcmd -l", container)
    if r.get("success"):
        return ""
    r = await _exec(cluster_id, namespace, pod, "readlink /proc/1/exe", container)
    if r.get("success"):
        p = (r.get("output") or "").strip()
        if p.endswith("/java"):
            return p[: -len("java")]
    return None


async def _resolve_jdk_bin(cluster_id: int, namespace: str, pod: str, container: str) -> str:
    """返回 JDK bin 目录前缀（含结尾 /，''=jcmd 在 PATH）。

    定位顺序：① jcmd 在 PATH；② 从 PID 1 的 java 二进制（readlink /proc/1/exe）反推；
    ③ 都失败 → **探测包管理器并自动装 JDK 工具**（apt/yum/dnf/apk），装完再定位。
    返回 ''（既可能=在 PATH，也可能=最终没找到——调用方靠 jcmd 执行报错区分）。
    """
    loc = await _locate_jdk_bin(cluster_id, namespace, pod, container)
    if loc is not None:
        return loc
    for pm, cmds in _JDK_INSTALL.items():
        probe = await _exec(cluster_id, namespace, pod, f"which {pm}", container)
        if not (probe.get("output") or "").strip():
            continue
        logger.info("JVM 诊断：容器缺 jcmd，尝试用 %s 安装 JDK 工具", pm)
        for c in cmds:
            await _exec(cluster_id, namespace, pod, c, container)
        loc = await _locate_jdk_bin(cluster_id, namespace, pod, container)
        return loc if loc is not None else ""
    return ""


async def _find_java_pid(cluster_id: int, namespace: str, pod: str, container: str) -> tuple[int | None, str]:
    """通过 `jcmd -l` 定位容器内第一个 Java 进程 pid（jcmd 不在 PATH 时自动定位 JDK bin）。

    返回 (pid, 原始输出)。找不到时 pid=None。
    """
    jc = (await _resolve_jdk_bin(cluster_id, namespace, pod, container)) + "jcmd"
    r = await _exec(cluster_id, namespace, pod, f"{jc} -l", container)
    if not r.get("success"):
        return None, r.get("error", "执行 jcmd -l 失败（容器内无 jcmd：镜像可能为 JRE，请用基于 JDK 的镜像）")
    out = r.get("output", "") or ""
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        m = _JCMD_LINE.match(line)
        if not m:
            continue
        pid, cls = m.group(1), m.group(2)
        # 排除 jcmd/jps 自身
        if "jcmd" in cls or "tools.jcmd" in cls:
            continue
        return int(pid), out
    return None, out or "（jcmd -l 无输出：容器内可能没有 Java 进程或未装 JDK 工具）"


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
    """列出容器内 Java 进程（jcmd -l）"""
    if denied := await _deny(user, cluster_id, namespace):
        return denied
    jc = (await _resolve_jdk_bin(cluster_id, namespace, pod_name, container)) + "jcmd"
    r = await _exec(cluster_id, namespace, pod_name, f"{jc} -l", container)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "执行 jcmd -l 失败（容器内无 jcmd）"), "data": None}
    out = r.get("output", "") or ""
    procs = []
    for line in out.splitlines():
        line = line.strip()
        m = _JCMD_LINE.match(line)
        if not m:
            continue
        pid, cls = m.group(1), m.group(2)
        procs.append({"pid": int(pid), "target": cls, "is_jcmd": ("jcmd" in cls or "tools.jcmd" in cls)})
    return {"code": 200, "message": "success", "data": {"raw": out, "processes": procs}}


@router.post("/clusters/{cluster_id}/namespaces/{namespace}/pods/{pod_name}/diagnose")
async def jvm_diagnose(cluster_id: int, namespace: str, pod_name: str, payload: dict,
                       user=Depends(require_operator)):
    """对 Pod 内 Java 进程执行一个 JVM 诊断动作。

    body: { container?: str, action: str, pid?: int }
    - 未传 pid 时先用 jcmd -l 自动定位第一个 Java 进程。
    - 文件类动作返回 file_path，前端再调资源接口下载产物。
    """
    if denied := await _deny(user, cluster_id, namespace):
        return denied
    container = (payload.get("container") or "").strip()
    action = (payload.get("action") or "").strip()
    spec = _ACTIONS.get(action)
    if not spec:
        return {"code": 400, "message": f"不支持的诊断动作：{action}（可选：{', '.join(_ACTIONS)}）", "data": None}

    # 解析 / 定位 pid
    pid = payload.get("pid")
    pid_out = ""
    if spec["need_pid"]:
        if pid is not None:
            pid_str = str(pid).strip()
            if not _SAFE_PID.match(pid_str):
                return {"code": 400, "message": "pid 非法（应为纯数字）", "data": None}
        else:
            found, pid_out = await _find_java_pid(cluster_id, namespace, pod_name, container)
            if found is None:
                return {"code": 500, "message": f"未能自动定位 Java 进程（{pid_out}）", "data": None}
            pid_str = str(found)
    else:
        pid_str = ""

    cmd = spec["cmd"].format(pid=pid_str)
    bin_dir = await _resolve_jdk_bin(cluster_id, namespace, pod_name, container)
    if bin_dir:
        cmd = bin_dir + cmd
    r = await _exec(cluster_id, namespace, pod_name, cmd, container)
    if not r.get("success"):
        return {"code": 500, "message": r.get("error", "诊断命令执行失败（容器内可能无 JDK 工具）"), "data": None}

    data = {
        "action": action,
        "command": cmd,
        "pid": pid_str or None,
        "output": r.get("output", ""),
        "file": spec["file"],
    }
    if spec["file"]:
        data["file_path"] = _FILE_PATHS[action]
        data["hint"] = "产物已写入容器内路径，可点「下载产物」拉取"
    return {"code": 200, "message": "success", "data": data}


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
