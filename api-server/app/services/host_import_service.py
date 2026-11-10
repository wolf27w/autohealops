# -*- coding: utf-8 -*-
"""R8.2 主机批量导入：CSV / 粘贴表格 → 校验 → 入库（可选跳过/覆盖重复）。

列（表头支持中英文，顺序不限）：
    名称, 地址, 端口, 用户名, 认证方式(password|key), 密码, 私钥, 分组, 进程路径(; 分隔), 说明

口径：
- 唯一性判重按 `(address, ssh_port, ssh_user)`；重复时按 on_duplicate 跳过或覆盖。
- 分组不存在：平台管理员自动建组；普通用户该行失败（无建组权限）。
- 私钥内容全部 AES-256-GCM 加密入库，接口不回显。
"""
from __future__ import annotations

import csv
import io
import json
import re

from sqlalchemy import select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import encrypt_aes256_gcm
from app.models import Host, HostGroup, User
from app.services import host_group_service as hgs

logger = get_logger("api-server.host-import")

COLUMN_ALIASES = {
    "name": {"名称", "名字", "主机名", "name"},
    "address": {"地址", "ip", "ip地址", "address", "host"},
    "ssh_port": {"端口", "port", "ssh端口", "ssh_port"},
    "ssh_user": {"用户名", "用户", "user", "username", "ssh_user"},
    "auth_type": {"认证方式", "认证", "auth", "auth_type", "authtype"},
    "password": {"密码", "password", "passwd", "pwd"},
    "private_key": {"私钥", "密钥", "private_key", "key", "ssh_key"},
    "key_passphrase": {"私钥口令", "密钥口令", "passphrase", "key_passphrase"},
    "group": {"分组", "组", "group", "group_name"},
    "process_paths": {"进程路径", "进程", "process_paths", "paths", "process"},
    "description": {"说明", "描述", "备注", "description", "desc"},
    "kind": {"类型", "kind", "type"},
    "env_tag": {"环境", "环境标签", "env", "env_tag"},
}

IP_RE = re.compile(r"^[a-zA-Z0-9._-]{1,200}$")
HEADER_HINTS = {"名称", "name", "地址", "address", "ip"}


def _norm_header(h: str) -> str:
    return (h or "").strip().lower().replace(" ", "")


def _map_header(raw: str) -> str | None:
    key = _norm_header(raw)
    for field, aliases in COLUMN_ALIASES.items():
        if key in {_norm_header(a) for a in aliases}:
            return field
    return None


def _split_lines(content: str) -> list[str]:
    return [ln for ln in (content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]


def _detect_delimiter(line: str) -> str:
    if "\t" in line:
        return "\t"
    if "," in line:
        return ","
    if "，" in line:
        return "，"
    return "\t"


def parse_rows(content: str, fmt: str = "csv") -> tuple[list[dict], list[dict]]:
    """解析成 [{field: value, _line: n}]。返回 (rows, parse_errors)。"""
    lines = _split_lines(content)
    if not lines:
        return [], [{"line": 0, "reason": "内容为空"}]
    delimiter = _detect_delimiter(lines[0])
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    table = [r for r in reader if any((c or "").strip() for c in r)]
    if not table:
        return [], [{"line": 0, "reason": "内容为空"}]

    first = [(_norm_header(c)) for c in table[0]]
    has_header = any(h in HEADER_HINTS for h in first)
    header = table[0] if has_header else None
    body = table[1:] if has_header else table

    # 无表头 → 按模板默认顺序解析
    default_fields = ["name", "address", "ssh_port", "ssh_user", "auth_type",
                      "password", "private_key", "group", "process_paths", "description"]
    fields = ([_map_header(h) for h in header] if header else default_fields)

    rows: list[dict] = []
    errors: list[dict] = []
    for idx, cells in enumerate(body, start=2 if has_header else 1):
        item: dict = {"_line": idx}
        for pos, cell in enumerate(cells):
            field = fields[pos] if pos < len(fields) else None
            if field:
                item[field] = (cell or "").strip()
        rows.append(item)
    return rows, errors


def parse_process_paths(raw: str) -> list[dict]:
    """`/opt/app.jar; name=/opt/b.jar` → [{name, path}]"""
    out: list[dict] = []
    for part in re.split(r"[;；]", raw or ""):
        p = part.strip()
        if not p:
            continue
        if "=" in p:
            name, path = p.split("=", 1)
            name, path = name.strip(), path.strip()
        else:
            name, path = p, p
        if path:
            out.append({"name": name or path, "path": path})
    return out


def _validate(item: dict) -> str | None:
    name = item.get("name", "")
    address = item.get("address", "")
    if not name:
        return "名称不能为空"
    if len(name) > 100:
        return "名称超过 100 字符"
    if not address:
        return "地址不能为空"
    if not IP_RE.match(address):
        return "地址格式不合法"
    port = item.get("ssh_port") or "22"
    try:
        p = int(port)
        if not 1 <= p <= 65535:
            return "端口超出范围"
    except ValueError:
        return "端口不是数字"
    auth = (item.get("auth_type") or "").strip().lower() or ("key" if item.get("private_key") else "password")
    if auth not in ("password", "key"):
        return "认证方式仅支持 password / key"
    if auth == "key" and not item.get("private_key"):
        return "认证方式为 key 但未提供私钥"
    kind = (item.get("kind") or "host").strip().lower() or "host"
    if kind not in ("host", "vm"):
        return "类型仅支持 host / vm"
    return None


async def import_hosts(user: User, content: str, fmt: str = "csv",
                       on_duplicate: str = "skip", default_group_id: int | None = None,
                       dry_run: bool = False) -> dict:
    """执行导入，返回 {total, success, skipped, failed, rows:[...], groups_created:[...]}"""
    rows, parse_errors = parse_rows(content, fmt)
    is_admin = hgs.is_platform_admin(user)
    result_rows: list[dict] = list(parse_errors)
    created_groups: list[str] = []
    success = skipped = failed = len(parse_errors)

    async with async_session() as s:
        groups = {g.name: g for g in (await s.execute(select(HostGroup))).scalars()}
        seen_in_batch: dict[tuple[str, int, str], str] = {}

        for item in rows:
            line = item.get("_line", 0)
            label = item.get("name") or item.get("address") or ""
            err = _validate(item)
            if err:
                failed += 1
                result_rows.append({"line": line, "name": label, "address": item.get("address", ""),
                                    "status": "failed", "reason": err})
                continue

            # 分组解析（不存在且 admin → 建组）
            gname = (item.get("group") or "").strip()
            group_id = default_group_id
            if gname:
                g = groups.get(gname)
                if g:
                    if not is_admin and not await hgs.can_access_group(user, g.id, "operate"):
                        failed += 1
                        result_rows.append({"line": line, "name": label, "address": item.get("address", ""),
                                            "status": "failed", "reason": f"无分组【{gname}】的操作权限"})
                        continue
                    group_id = g.id
                elif is_admin:
                    if dry_run:
                        group_id = None
                        created_groups.append(gname) if gname not in created_groups else None
                    else:
                        ng = HostGroup(name=gname, description="批量导入自动创建")
                        s.add(ng)
                        await s.flush()
                        groups[gname] = ng
                        group_id = ng.id
                        created_groups.append(gname)
                else:
                    failed += 1
                    result_rows.append({"line": line, "name": label, "address": item.get("address", ""),
                                        "status": "failed", "reason": f"分组【{gname}】不存在（仅管理员可自动建组）"})
                    continue
            elif group_id is not None and not is_admin:
                if not await hgs.can_access_group(user, int(group_id), "operate"):
                    failed += 1
                    result_rows.append({"line": line, "name": label, "address": item.get("address", ""),
                                        "status": "failed", "reason": "无默认分组的操作权限"})
                    continue

            kind = (item.get("kind") or "host").strip().lower() or "host"
            has_group = group_id is not None or bool(gname)
            if kind == "vm" and not has_group:
                failed += 1
                result_rows.append({"line": line, "name": label, "address": item.get("address", ""),
                                    "status": "failed", "reason": "虚拟机必须先选择分组"})
                continue

            address = item.get("address", "")
            port = int(item.get("ssh_port") or 22)
            ssh_user = (item.get("ssh_user") or "root").strip() or "root"
            dedupe_key = (address, port, ssh_user)
            if dedupe_key in seen_in_batch:
                skipped += 1
                result_rows.append({"line": line, "name": label, "address": address,
                                    "status": "skipped",
                                    "reason": f"本次导入内重复（第 {seen_in_batch[dedupe_key]} 行）"})
                continue
            seen_in_batch[dedupe_key] = line

            existing = (await s.execute(
                select(Host).where(Host.address == address, Host.ssh_port == port, Host.ssh_user == ssh_user)
            )).scalars().first()

            if existing:
                if on_duplicate != "update":
                    skipped += 1
                    result_rows.append({"line": line, "name": label, "address": address,
                                        "status": "skipped", "reason": "已存在（可选择覆盖）"})
                    continue
                if not await hgs.host_access(user, existing, "operate"):
                    failed += 1
                    result_rows.append({"line": line, "name": label, "address": address,
                                        "status": "failed", "reason": "已存在且无该主机操作权限"})
                    continue
                if not dry_run:
                    existing.name = item.get("name") or existing.name
                    existing.auth_type = (item.get("auth_type") or "password").strip().lower() or "password"
                    pwd = item.get("password")
                    if pwd:
                        existing.ssh_password_encrypted = encrypt_aes256_gcm(pwd)
                    key = item.get("private_key")
                    if key:
                        existing.ssh_key_encrypted = encrypt_aes256_gcm(key)
                    passphrase = item.get("key_passphrase")
                    if passphrase:
                        existing.ssh_key_passphrase_encrypted = encrypt_aes256_gcm(passphrase)
                    if item.get("process_paths"):
                        existing.process_paths = json.dumps(parse_process_paths(item["process_paths"]),
                                                            ensure_ascii=False)
                    if group_id is not None:
                        existing.group_id = group_id
                    if item.get("description"):
                        existing.description = item["description"][:300]
                    if item.get("env_tag"):
                        existing.env_tag = item["env_tag"][:50]
                success += 1
                result_rows.append({"line": line, "name": label, "address": address,
                                    "status": "updated", "reason": ""})
                continue

            # 名称唯一性
            dup_name = (await s.execute(select(Host).where(Host.name == item["name"]))).scalars().first()
            if dup_name:
                failed += 1
                result_rows.append({"line": line, "name": label, "address": address,
                                    "status": "failed", "reason": "名称已被其它主机占用"})
                continue

            if not dry_run:
                h = Host(
                    name=item["name"], address=address, ssh_port=port, ssh_user=ssh_user,
                    auth_type=(item.get("auth_type") or "password").strip().lower() or "password",
                    ssh_password_encrypted=encrypt_aes256_gcm(item["password"]) if item.get("password") else "",
                    ssh_key_encrypted=encrypt_aes256_gcm(item["private_key"]) if item.get("private_key") else None,
                    ssh_key_passphrase_encrypted=(encrypt_aes256_gcm(item["key_passphrase"])
                                                 if item.get("key_passphrase") else None),
                    process_paths=json.dumps(parse_process_paths(item.get("process_paths", "")), ensure_ascii=False),
                    kind=kind, group_id=group_id,
                    description=(item.get("description") or "")[:300],
                    env_tag=(item.get("env_tag") or "")[:50],
                )
                s.add(h)
            success += 1
            result_rows.append({"line": line, "name": label, "address": address, "status": "created", "reason": ""})

        if dry_run:
            await s.rollback()
        else:
            await s.commit()

    return {"total": len(rows) + len(parse_errors), "success": success, "skipped": skipped,
            "failed": failed, "rows": result_rows, "groups_created": created_groups,
            "dry_run": dry_run}


IMPORT_TEMPLATE_CSV = (
    "名称,地址,端口,用户名,认证方式,密码,私钥,分组,进程路径,说明\n"
    "biz-app-01,192.168.6.41,22,root,password,******,,生产,/opt/app/app.jar,业务应用 A\n"
    "biz-app-02,192.168.6.42,22,root,key,,,生产,/opt/app/app.jar;name=/opt/app/agent.jar,业务应用 B\n"
)
