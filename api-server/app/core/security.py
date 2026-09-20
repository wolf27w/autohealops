# -*- coding: utf-8 -*-
"""安全模块：AES-256-GCM 加解密、bcrypt 密码哈希、JWT、API Key、trace_id

加密格式（与 k8s-executor Go 端严格一致，见 docs/ENCRYPTION.md）：
- AES-256-GCM，随机 12 字节 IV + 密文 + 16 字节 tag
- base64 拼接：base64(IV + ciphertext + tag)，IV 在前
"""
import base64
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import JWTError, jwt

from app.core.config import settings


def encrypt_aes256_gcm(plaintext: str) -> str:
    """AES-256-GCM 加密，返回 base64(IV + ciphertext + tag)"""
    key = settings.ENCRYPTION_KEY.encode("utf-8")
    iv = secrets.token_bytes(12)  # 随机 12 字节 IV
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # IV 在前 + 密文（含 16 字节 tag）
    return base64.b64encode(iv + ciphertext).decode("utf-8")


def decrypt_aes256_gcm(ciphertext_b64: str) -> str:
    """AES-256-GCM 解密，输入 base64(IV + ciphertext + tag)"""
    key = settings.ENCRYPTION_KEY.encode("utf-8")
    raw = base64.b64decode(ciphertext_b64)
    iv, ciphertext = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")


def hash_password(password: str) -> str:
    """bcrypt 密码哈希"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_jwt(user_id: int, username: str, role: str) -> str:
    """生成 JWT，有效期 JWT_EXPIRE_HOURS"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    payload = {"sub": str(user_id), "username": username, "role": role, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """验证并解析 JWT，失败抛 JWTError"""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_api_key() -> str:
    """生成 API Key"""
    return secrets.token_urlsafe(32)


def generate_trace_id() -> str:
    """生成链路追踪 ID"""
    return uuid.uuid4().hex


def generate_decision_id() -> str:
    """生成决策 ID"""
    return uuid.uuid4().hex


def generate_lock_value() -> str:
    """生成 Redis 分布式锁 value（唯一标识）"""
    return uuid.uuid4().hex
