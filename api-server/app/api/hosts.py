# -*- coding: utf-8 -*-
"""主机/虚拟机纳管 API（N11 + R25）：CRUD + 进程监控检查 + 分组权限收敛"""
import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_, select

from app.core.database import async_session
from app.core.logging import get_logger
from app.core.security import encrypt_aes256_gcm, generate_trace_id
from app.middleware.permission import require_any_user
from app.models import Host, HostGroup, User
from app.services import host_group_service as hgs
from app.services import host_import_service as his
from app.services.audit_service import create_audit_record

router = APIRouter(prefix="/api/v1/hosts")
logger = get_logger("api-server.hosts")


def _row(h: Host, group_name: str = "", can: dict | None = None) -> dict:
    paths = []
    try:
        paths = json.loads(h.process_paths or "[]")
    except Exception:  # noqa: BLE001
        paths = []
    return {
        "id": h.id, "name": h.name, "address": h.address, "ssh_port": h.ssh_port,
        "ssh_user": h.ssh_user, "has_password": bool(h.ssh_password_encrypted),
        "has_key": bool(getattr(h, "ssh_key_encrypted", None)),
        "has_key_passphrase": bool(getattr(h, "ssh_key_passphrase_encrypted", None)),
        "auth_type": getattr(h, "auth_type", "password") or "password",
        "description": getattr(h, "description", "") or "",
        "ssh_key_path": h.ssh_key_path, "process_paths": paths,
        "enabled": h.enabled, "status": h.status,
        "last_error": getattr(h, "last_error", "") or "",
        "last_seen": str(h.last_seen) if h.last_seen else None,
        "kind": h.kind, "group_id": h.group_id, "group_name": group_name,
        "env_tag": getattr(h, "env_tag", "") or "",
        "source": getattr(h, "source", "direct") or "direct",
        "cluster_id": getattr(h, "cluster_id", None),
        "source_key": getattr(h, "source_key", "") or "",
        "last_metrics": _parse_metrics(getattr(h, "last_metrics", "")),
        "can": can or {"view": True, "operate": False, "manage": False, "delete": False},
    }


async def _load_group_names(group_ids: set[int]) -> dict[int, str]:
    if not group_ids:
        return {}
    async with async_session() as s:
        rows = (await s.execute(select(HostGroup).where(HostGroup.id.in_(group_ids)))).scalars()
        return {g.id: g.name for g in rows}


def _validate_private_key(key: str, passphrase: str) -> str | None:
    """校验私钥格式；返回错误文案（None=通过）。"""
    if not key.strip():
        return None
    import asyncssh
    try:
        asyncssh.import_private_key(key, passphrase=passphrase or None)
        return None
    except asyncssh.KeyEncryptionKeyRequired:
        return "私钥已加密，请填写私钥口令"
    except asyncssh.KeyImportError as e:
        return f"私钥格式不正确：{' '.join(str(e).split())[:80]}"
    except Exception as e:  # noqa: BLE001
        return f"私钥解析失败：{' '.join(str(e).split())[:80]}"


async def _audit_host(trace_id: str, detail: str, host_id: int) -> None:
    await create_audit_record(trace_id, trigger_snapshot=json.dumps({"action": "host_op", "host_id": host_id}),
                              execution_detail=detail)


def _parse_metrics(raw: str) -> dict | None:
    try:
        m = json.loads(raw or "")
        return m if isinstance(m, dict) else None
    except Exception:  # noqa: BLE001
        return None


@router.get("")
async def list_hosts(group_id: int | None = None, kind: str | None = None, source: str | None = None,
                     keyword: str = "", user=Depends(require_any_user)):
    async with async_session() as s:
        q = select(Host)
        is_admin = hgs.is_platform_admin(user)
        if not is_admin:
            gids = await hgs.visible_group_ids(user)
            if not gids:
                return {"code": 200, "message": "success", "data": []}
            q = q.where(Host.group_id.in_(gids))
        if group_id is not None:
            if group_id == 0:
                q = q.where(Host.group_id.is_(None))
            else:
                q = q.where(Host.group_id == group_id)
        if kind:
            q = q.where(Host.kind == kind)
        if source:
            q = q.where(Host.source == source)
        if keyword:
            like = f"%{keyword}%"
            q = q.where(or_(Host.name.like(like), Host.address.like(like)))
        hosts = list((await s.execute(q.order_by(Host.id))).scalars())

    group_names = await _load_group_names({h.group_id for h in hosts if h.group_id})
    roles = await hgs.get_group_roles(user)
    data = [
        _row(h, group_names.get(h.group_id, ""), hgs.host_can(user, h, is_admin, roles.get(h.group_id)))
        for h in hosts
    ]
    return {"code": 200, "message": "success", "data": data}


async def _validate_group(user: User, group_id):
    """校验分组存在性 + 用户至少 operate 权限（创建/移入目标组用）。"""
    if group_id is None:
        return
    async with async_session() as s:
        g = await s.get(HostGroup, int(group_id))
    if not g:
        raise HTTPException(status_code=400, detail="分组不存在")
    if not await hgs.can_access_group(user, int(group_id), "operate"):
        raise HTTPException(status_code=403, detail="无该组权限")


@router.post("")
async def create_host(payload: dict, user=Depends(require_any_user)):
    name = (payload.get("name") or "").strip()
    address = (payload.get("address") or "").strip()
    if not name or not address:
        return {"code": 400, "message": "name 和 address 必填", "data": None}
    kind = (payload.get("kind") or "host").strip()
    if kind not in ("host", "vm"):
        return {"code": 400, "message": "kind 仅支持 host / vm", "data": None}
    group_id = payload.get("group_id")
    if group_id is not None:
        group_id = int(group_id)
    if kind == "vm" and not group_id:
        raise HTTPException(status_code=400, detail="创建虚拟机必须先选择分组")
    await _validate_group(user, group_id)

    paths = payload.get("process_paths") or []
    pwd = payload.get("ssh_password") or ""
    key = (payload.get("ssh_private_key") or "").strip()
    passphrase = payload.get("ssh_key_passphrase") or ""
    auth_type = (payload.get("auth_type") or ("key" if key else "password")).strip().lower()
    if auth_type not in ("password", "key"):
        return {"code": 400, "message": "auth_type 仅支持 password / key", "data": None}
    if auth_type == "key" and not key and not (payload.get("ssh_key_path") or "").strip():
        return {"code": 400, "message": "认证方式为 key 时必须填写私钥", "data": None}
    key_err = _validate_private_key(key, passphrase)
    if key_err:
        return {"code": 400, "message": key_err, "data": None}
    async with async_session() as s:
        h = Host(name=name, address=address,
                 ssh_port=int(payload.get("ssh_port") or 22),
                 ssh_user=(payload.get("ssh_user") or "root").strip(),
                 ssh_password_encrypted=encrypt_aes256_gcm(pwd) if pwd else "",
                 auth_type=auth_type,
                 ssh_key_encrypted=encrypt_aes256_gcm(key) if key else None,
                 ssh_key_passphrase_encrypted=encrypt_aes256_gcm(passphrase) if passphrase else None,
                 ssh_key_path=(payload.get("ssh_key_path") or "").strip(),
                 description=(payload.get("description") or "")[:300],
                 process_paths=json.dumps(paths, ensure_ascii=False),
                 enabled=bool(payload.get("enabled", True)),
                 kind=kind, group_id=group_id,
                 env_tag=(payload.get("env_tag") or "").strip())
        s.add(h)
        await s.commit()
        await s.refresh(h)
        hid = h.id
    await _audit_host(generate_trace_id(), f"创建主机：{name}（{kind}）", hid)
    return {"code": 200, "message": "success", "data": _row(h, "", {"view": True, "operate": True, "manage": True, "delete": True})}


@router.put("/{host_id}")
async def update_host(host_id: int, payload: dict, user=Depends(require_any_user)):
    async with async_session() as s:
        h = await s.get(Host, host_id)
        if not h:
            raise HTTPException(status_code=404, detail="主机不存在")
        is_admin = hgs.is_platform_admin(user)
        if not is_admin and not await hgs.host_access(user, h, "operate"):
            raise HTTPException(status_code=403, detail="无该主机操作权限")

        # 分组变更需 manage 权限；虚拟机不允许移出组（无组）
        if "group_id" in payload:
            new_group = payload.get("group_id")
            if new_group is not None:
                new_group = int(new_group)
            if new_group != (h.group_id or 0):
                if not is_admin and not await hgs.host_access(user, h, "manage"):
                    raise HTTPException(status_code=403, detail="无移组权限")
                if h.kind == "vm" and new_group is None:
                    raise HTTPException(status_code=400, detail="虚拟机必须属于某个分组")
                await _validate_group(user, new_group)
                h.group_id = new_group

        for f in ("name", "address", "ssh_user", "ssh_key_path", "env_tag"):
            if payload.get(f) is not None:
                setattr(h, f, str(payload[f]).strip())
        if payload.get("ssh_port") is not None:
            h.ssh_port = int(payload["ssh_port"])
        if payload.get("process_paths") is not None:
            h.process_paths = json.dumps(payload["process_paths"], ensure_ascii=False)
        if payload.get("enabled") is not None:
            h.enabled = bool(payload["enabled"])
        pwd = payload.get("ssh_password") or ""
        if pwd:
            h.ssh_password_encrypted = encrypt_aes256_gcm(pwd)
        if payload.get("auth_type") is not None:
            at = str(payload["auth_type"]).strip().lower()
            if at not in ("password", "key"):
                return {"code": 400, "message": "auth_type 仅支持 password / key", "data": None}
            h.auth_type = at
        key = (payload.get("ssh_private_key") or "").strip()
        passphrase = payload.get("ssh_key_passphrase") or ""
        key_err = _validate_private_key(key, passphrase)
        if key_err:
            return {"code": 400, "message": key_err, "data": None}
        if key:
            h.ssh_key_encrypted = encrypt_aes256_gcm(key)
        if passphrase:
            h.ssh_key_passphrase_encrypted = encrypt_aes256_gcm(passphrase)
        if payload.get("clear_ssh_key"):
            h.ssh_key_encrypted = None
            h.ssh_key_passphrase_encrypted = None
        if payload.get("description") is not None:
            h.description = str(payload["description"])[:300]
        await s.commit()
        await s.refresh(h)
        hid = h.id
        gname = ""
        if h.group_id:
            g = await s.get(HostGroup, h.group_id)
            gname = g.name if g else ""
    await _audit_host(generate_trace_id(), f"修改主机：{host_id}", hid)
    return {"code": 200, "message": "success", "data": _row(h, gname)}


@router.delete("/{host_id}")
async def delete_host(host_id: int, user=Depends(require_any_user)):
    async with async_session() as s:
        h = await s.get(Host, host_id)
        if not h:
            raise HTTPException(status_code=404, detail="主机不存在")
        if not await hgs.host_access(user, h, "manage"):
            raise HTTPException(status_code=403, detail="无该主机删除权限")
        await s.delete(h)
        await s.commit()
    await _audit_host(generate_trace_id(), f"删除主机：{host_id}", host_id)
    return {"code": 200, "message": "success", "data": {"id": host_id}}


@router.post("/{host_id}/check")
async def check_host(host_id: int, user=Depends(require_any_user)):
    """立即对该主机做一次进程检查（需 operate 权限）。"""
    from app.services import host_monitor_service as hms
    async with async_session() as s:
        h = await s.get(Host, host_id)
        if not h:
            raise HTTPException(status_code=404, detail="主机不存在")
    if not await hgs.host_access(user, h, "operate"):
        raise HTTPException(status_code=403, detail="无该主机操作权限")
    res = await hms.check_and_notify(h)
    return {"code": 200, "message": "success", "data": {"host": _row(h), "check": res}}


@router.get("/import/template")
async def import_template(user=Depends(require_any_user)):
    """CSV 导入模板（UTF-8，含 BOM 便于 Excel 打开不乱码）。"""
    content = "\ufeff" + his.IMPORT_TEMPLATE_CSV
    return Response(content=content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="hosts-import-template.csv"'})


@router.post("/import")
async def import_hosts(payload: dict, user=Depends(require_any_user)):
    """批量导入主机（CSV / 粘贴表格）。

    body: {content, format: csv|table, on_duplicate: skip|update, default_group_id, dry_run}
    """
    content = payload.get("content") or ""
    if not content.strip():
        return {"code": 400, "message": "content 不能为空", "data": None}
    is_admin = hgs.is_platform_admin(user)
    if not is_admin:
        roles = await hgs.get_group_roles(user)
        if not any(r in ("admin", "operator") for r in roles.values()):
            raise HTTPException(status_code=403, detail="无导入权限（需要某个分组的操作权限）")
    fmt = (payload.get("format") or "csv").strip().lower()
    on_dup = (payload.get("on_duplicate") or "skip").strip().lower()
    if on_dup not in ("skip", "update"):
        on_dup = "skip"
    default_group_id = payload.get("default_group_id")
    if default_group_id is not None:
        default_group_id = int(default_group_id)
    data = await his.import_hosts(
        user, content, fmt=fmt, on_duplicate=on_dup,
        default_group_id=default_group_id, dry_run=bool(payload.get("dry_run")),
    )
    if not payload.get("dry_run"):
        await _audit_host(generate_trace_id(),
                          f"批量导入主机：成功 {data['success']}，跳过 {data['skipped']}，失败 {data['failed']}",
                          0)
    return {"code": 200, "message": "success", "data": data}
