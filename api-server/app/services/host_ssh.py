# -*- coding: utf-8 -*-
"""R8：主机 SSH 连接凭据解析（监控采集 / Web 终端 / SFTP 共用）。

认证优先级（R8 口径）：
1. `auth_type=key` 且库里有加密私钥内容 → 用私钥内容（可带私钥口令）
2. `ssh_key_path`（本地文件路径，兼容旧数据）
3. 保存的密码（`ssh_password_encrypted`）
最终同时把可用的凭据传给 asyncssh，由服务端支持的认证方式决定，避免单一路径失败。
"""
from __future__ import annotations

import asyncssh

from app.core.logging import get_logger
from app.core.security import decrypt_aes256_gcm
from app.models import Host

logger = get_logger("api-server.host-ssh")

PermissionDenied = asyncssh.PermissionDenied
PasswordChangeRequired = asyncssh.PasswordChangeRequired
ConnectionLost = asyncssh.ConnectionLost


def _squash(e: BaseException) -> str:
    s = str(e).strip() or e.__class__.__name__
    return " ".join(s.split())[:120]


def _decrypt(value: str | None, host_name: str, what: str) -> str:
    if not value:
        return ""
    try:
        return decrypt_aes256_gcm(value)
    except Exception as e:  # noqa: BLE001
        logger.warning("主机 %s %s解密失败: %s", host_name, what, e)
        return ""


def resolve_credentials(host: Host) -> dict:
    """解析主机凭据，返回 asyncssh.connect 所需 kwargs（不含 address/port/username）。"""
    password = _decrypt(getattr(host, "ssh_password_encrypted", ""), host.name, "密码")
    key_content = _decrypt(getattr(host, "ssh_key_encrypted", ""), host.name, "私钥")
    passphrase = _decrypt(getattr(host, "ssh_key_passphrase_encrypted", ""), host.name, "私钥口令") or None

    kwargs: dict = {"known_hosts": None}
    if key_content:
        kwargs["client_keys"] = [asyncssh.import_private_key(key_content, passphrase=passphrase)]
        if passphrase:
            kwargs["passphrase"] = passphrase
    elif (host.ssh_key_path or "").strip():
        kwargs["client_keys"] = [host.ssh_key_path.strip()]
    if password:
        # 私钥优先，密码作为兜底（服务端支持的认证方式决定）
        kwargs["password"] = password
    return kwargs


async def connect(host: Host, connect_timeout: int = 8, **extra):
    """建立 SSH 连接（调用方负责 close / wait_closed）。异常由调用方分类处理。"""
    kwargs = resolve_credentials(host)
    kwargs.update(extra)
    return await asyncssh.connect(
        host.address,
        port=int(host.ssh_port or 22),
        username=(host.ssh_user or "root"),
        connect_timeout=connect_timeout,
        **kwargs,
    )


def describe_error(e: BaseException) -> str:
    """把 asyncssh 异常翻译成可展示的中文原因（监控 / 终端共用口径）。"""
    if isinstance(e, PermissionDenied):
        return "认证失败（用户名/密码/密钥错误）"
    if isinstance(e, PasswordChangeRequired):
        return "认证失败（要求修改密码）"
    if isinstance(e, ConnectionLost):
        return "网络不可达（连接被关闭/拒绝）"
    if isinstance(e, asyncssh.KeyImportError):
        return f"私钥无效：{_squash(e)}"
    if isinstance(e, (OSError, asyncssh.DisconnectError)):
        return f"网络不可达：{_squash(e)}"
    if isinstance(e, asyncssh.Error):
        return f"SSH 失败：{_squash(e)}"
    return f"SSH 失败：{_squash(e)}"
