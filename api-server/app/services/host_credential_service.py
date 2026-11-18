# -*- coding: utf-8 -*-
"""私钥/凭据集中托管服务（R43 H2）：CRUD + 加密存储 + 解密取用（仅服务端连接用）。

口径：
- 列表/详情永远只回 has_secret / has_passphrase，不回明文。
- 解密取用只发生在服务端连接（host_ssh / 主机采集 / Web 终端），绝不下发前端。
"""
from __future__ import annotations

from sqlalchemy import func, select

from app.core.database import async_session
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.models import Host, HostCredential

VALID_CRED_TYPES = ("key", "password")


def credential_row(cred: HostCredential) -> dict:
    """对外返回：不回明文，只回 has_secret / has_passphrase 标志。"""
    return {
        "id": cred.id,
        "name": cred.name,
        "cred_type": cred.cred_type,
        "username": cred.username or "",
        "has_secret": bool(cred.secret_encrypted),
        "has_passphrase": bool(cred.passphrase_encrypted),
        "description": cred.description or "",
        "created_by": cred.created_by,
        "created_at": str(cred.created_at) if cred.created_at else None,
        "updated_at": str(cred.updated_at) if cred.updated_at else None,
    }


async def list_credentials() -> list[dict]:
    async with async_session() as s:
        rows = list((await s.execute(select(HostCredential).order_by(HostCredential.id))).scalars())
    return [credential_row(c) for c in rows]


async def create_credential(payload: dict, user_id: int) -> dict:
    """新建凭据。payload: name/cred_type/username/secret/passphrase/description。"""
    name = (payload.get("name") or "").strip()
    cred_type = (payload.get("cred_type") or "key").strip().lower()
    secret = payload.get("secret") or ""
    passphrase = payload.get("passphrase") or ""
    if not name:
        raise ValueError("name 必填")
    if cred_type not in VALID_CRED_TYPES:
        raise ValueError("cred_type 仅支持 key / password")
    if not secret:
        raise ValueError("secret 必填")
    async with async_session() as s:
        dup = (await s.execute(select(HostCredential).where(HostCredential.name == name))).scalar_one_or_none()
        if dup:
            raise ValueError("凭据名已存在")
        cred = HostCredential(
            name=name,
            cred_type=cred_type,
            username=(payload.get("username") or "").strip(),
            secret_encrypted=encrypt_aes256_gcm(secret),
            passphrase_encrypted=encrypt_aes256_gcm(passphrase) if passphrase else "",
            description=(payload.get("description") or "").strip()[:300],
            created_by=user_id,
        )
        s.add(cred)
        await s.commit()
        await s.refresh(cred)
        return credential_row(cred)


async def update_credential(cred_id: int, payload: dict) -> dict:
    """修改凭据。secret / passphrase 留空 = 不修改；clear_passphrase=True = 清空口令。"""
    async with async_session() as s:
        cred = await s.get(HostCredential, cred_id)
        if not cred:
            raise KeyError("凭据不存在")
        if payload.get("name") is not None and str(payload["name"]).strip():
            new_name = str(payload["name"]).strip()
            dup = (await s.execute(select(HostCredential).where(
                HostCredential.name == new_name, HostCredential.id != cred_id
            ))).scalar_one_or_none()
            if dup:
                raise ValueError("凭据名已存在")
            cred.name = new_name
        if payload.get("cred_type") is not None:
            ct = str(payload["cred_type"]).strip().lower()
            if ct not in VALID_CRED_TYPES:
                raise ValueError("cred_type 仅支持 key / password")
            cred.cred_type = ct
        if payload.get("username") is not None:
            cred.username = str(payload["username"]).strip()
        if payload.get("description") is not None:
            cred.description = str(payload["description"])[:300]
        secret = payload.get("secret") or ""
        if secret:
            cred.secret_encrypted = encrypt_aes256_gcm(secret)
        passphrase = payload.get("passphrase") or ""
        if passphrase:
            cred.passphrase_encrypted = encrypt_aes256_gcm(passphrase)
        elif payload.get("clear_passphrase"):
            cred.passphrase_encrypted = ""
        await s.commit()
        await s.refresh(cred)
        return credential_row(cred)


async def delete_credential(cred_id: int) -> tuple[bool, str]:
    """删除凭据；被主机引用时拒绝。返回 (ok, message)。"""
    async with async_session() as s:
        cred = await s.get(HostCredential, cred_id)
        if not cred:
            raise KeyError("凭据不存在")
        refs = (await s.execute(select(func.count()).select_from(Host).where(
            Host.credential_id == cred_id
        ))).scalar_one()
        if refs > 0:
            return False, f"该凭据被 {refs} 台主机引用，请先解绑后再删除"
        await s.delete(cred)
        await s.commit()
    return True, ""


async def get_decrypted_credential(cred_id: int) -> dict | None:
    """连接用：取出并解密凭据（仅供服务端连接，绝不下发）。"""
    async with async_session() as s:
        cred = await s.get(HostCredential, cred_id)
        if not cred:
            return None
        return {
            "username": cred.username or "",
            "cred_type": cred.cred_type,
            "secret": decrypt_aes256_gcm(cred.secret_encrypted) if cred.secret_encrypted else "",
            "passphrase": decrypt_aes256_gcm(cred.passphrase_encrypted) if cred.passphrase_encrypted else "",
        }
