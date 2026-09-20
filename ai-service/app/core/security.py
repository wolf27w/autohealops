# -*- coding: utf-8 -*-
"""AI 服务安全模块：AES-256-GCM 加解密（API Key 加密存储）

加密格式与 api-server 完全一致（docs/ENCRYPTION.md）：
- AES-256-GCM，随机 12 字节 IV + 密文 + 16 字节 tag
- base64 拼接：base64(IV + ciphertext + tag)，IV 在前
"""
import base64
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings


def encrypt_aes256_gcm(plaintext: str) -> str:
    """AES-256-GCM 加密，返回 base64(IV + ciphertext + tag)"""
    key = settings.ENCRYPTION_KEY.encode("utf-8")
    iv = secrets.token_bytes(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    return base64.b64encode(iv + ciphertext).decode("utf-8")


def decrypt_aes256_gcm(ciphertext_b64: str) -> str:
    """AES-256-GCM 解密，输入 base64(IV + ciphertext + tag)"""
    key = settings.ENCRYPTION_KEY.encode("utf-8")
    raw = base64.b64decode(ciphertext_b64)
    iv, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")
