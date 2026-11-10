# -*- coding: utf-8 -*-
"""主机/虚拟机进程监控（N11）：SSH 采集 ps，按「进程路径」判断存活/CPU/内存。

- 不引入 agent：用本机 ssh（密钥/口令）执行 `ps -eo pid,pcpu,pmem,rss,args`。
- 每个 Host 配置若干「进程路径」；路径命中任一命令行即视为该服务在跑。
- 状态翻转（进程消失/恢复）→ notify_platform 通知闭环。
"""
import asyncio
import json

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.redis_client import redis_pool
from app.models import Host
from app.services import host_ssh

logger = get_logger("api-server.host-monitor")

CHECK_INTERVAL_SEC = 60


def _parse_paths(host: Host) -> list[dict]:
    try:
        p = json.loads(host.process_paths or "[]")
        return [x for x in p if isinstance(x, dict) and (x.get("path") or "").strip()]
    except Exception:  # noqa: BLE001
        return []


async def ssh_ps(host: Host) -> tuple[str | None, str]:
    """SSH 到主机跑 ps，返回 (stdout, error)。

    认证优先级（R9，老板 2026-11-05 定）：保存的密码 → 私钥路径 → 系统默认密钥。
    失败原因区分：认证失败 / 网络不可达 / 超时，不再统一显示「离线」。
    """
    conn = None
    try:
        conn = await asyncio.wait_for(host_ssh.connect(host, connect_timeout=8), timeout=12)
    except asyncio.TimeoutError:
        return None, "连接超时"
    except Exception as e:  # noqa: BLE001
        return None, host_ssh.describe_error(e)

    try:
        result = await asyncio.wait_for(
            conn.run("ps -eo pid,pcpu,pmem,rss,args"), timeout=15,
        )
        if result.exit_status != 0:
            return None, (result.stderr or f"exit {result.exit_status}").strip()[:200]
        return (result.stdout or ""), ""
    except asyncio.TimeoutError:
        return None, "命令执行超时"
    except Exception as e:  # noqa: BLE001
        return None, f"命令执行失败：{' '.join(str(e).split())[:120]}"
    finally:
        conn.close()
        await conn.wait_closed()


async def check_host(host: Host) -> dict:
    """对主机执行一次进程检查。返回 {online, paths:[{name,path,running,pids,cpu_pct,mem_mb}], error}。"""
    out, err = await ssh_ps(host)
    if out is None:
        return {"online": False, "paths": [], "error": err}
    lines = out.splitlines()[1:]  # 去掉表头
    specs = _parse_paths(host)
    paths = []
    for sp in specs:
        path = sp["path"].strip()
        name = sp.get("name") or path
        pids, cpu, mem = [], 0.0, 0.0
        for ln in lines:
            cols = ln.split(None, 4)
            if len(cols) < 5:
                continue
            if path in cols[4]:
                try:
                    pids.append(int(cols[0]))
                    cpu = max(cpu, float(cols[1]))
                    mem += int(cols[3])  # RSS KB
                except (ValueError, IndexError):  # noqa: BLE001
                    pass
        paths.append({"name": name, "path": path, "running": bool(pids),
                      "pid_count": len(pids), "cpu_pct": round(cpu, 1),
                      "mem_mb": round(mem / 1024, 1)})
    return {"online": True, "paths": paths, "error": ""}


async def _state_key(host_id: int) -> str:
    return f"host:state:{host_id}"


async def _notify(content: str) -> None:
    try:
        from app.services.notification_service import notify_platform
        await notify_platform(content, at_all=False)
    except Exception as e:  # noqa: BLE001
        logger.warning("主机监控通知失败: %s", e)


async def flip_notify(host_id: int, host_name: str, paths: list[dict]) -> None:
    """按进程路径检测状态翻转并通知（直连 / agent 代采共用）。"""
    try:
        prev_raw = await redis_pool.get(await _state_key(host_id))
        prev = json.loads(prev_raw) if prev_raw else {}
    except Exception:  # noqa: BLE001
        prev = {}
    new = {p["path"]: ("up" if p.get("running") else "down")
           for p in paths if isinstance(p, dict) and p.get("path")}
    for path, st in new.items():
        old = prev.get(path)
        if old is not None and old != st:
            if st == "down":
                await _notify(f"⚠️ 主机【{host_name}】进程掉线：{path}")
            else:
                await _notify(f"✅ 主机【{host_name}】进程恢复：{path}")
    try:
        await redis_pool.set(await _state_key(host_id), json.dumps(new, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass


async def check_and_notify(host: Host) -> dict:
    """检查 + 状态翻转通知。返回 check_host 结果。"""
    res = await check_host(host)
    # 持久化 online 状态
    try:
        async with async_session() as s:
            h = await s.get(Host, host.id)
            if h:
                h.status = "online" if res["online"] else "offline"
                h.last_error = "" if res["online"] else (res.get("error") or "")[:255]
                from datetime import datetime
                h.last_seen = datetime.now()
                await s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("写主机状态失败: %s", e)
    # 翻转检测（按进程路径）
    await flip_notify(host.id, host.name, res["paths"])
    return res


async def background_loop() -> None:
    while True:
        try:
            async with async_session() as s:
                hosts = list((await s.execute(select(Host).where(Host.enabled.is_(True), Host.source == "direct"))).scalars())
            for h in hosts:
                try:
                    await check_and_notify(h)
                except Exception as e:  # noqa: BLE001
                    logger.warning("主机 %s 检查异常: %s", h.name, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("主机监控循环异常: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SEC)
