# -*- coding: utf-8 -*-
"""R43 H2 回归：私钥/凭据集中托管（2026-11-18）

覆盖：
- 加密存储不可逆读原文（ciphertext != plaintext，decrypt 往返一致）
- 列表/详情不回明文（只回 has_secret / has_passphrase）
- 创建 / 编辑（secret 留空不修改）/ 删除
- credential_id 与内联私钥优先级（连接取私钥处优先用凭据库）
- 删除被主机引用时的行为（拒绝并提示）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_host_credentials -v
"""
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.database import async_session, engine
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm
from app.models import Host, HostCredential
from app.services import host_credential_service as hcs
from app.services.host_ssh import resolve_credentials


def _run(coro):
    """在独立事件循环里跑 async 逻辑，跑完 dispose 连接池，避免跨 loop 复用连接。"""

    async def _wrap():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_wrap())


def _ts():
    return int(time.time() * 1000)


def _cred(**kw):
    base = dict(id=1, name="n", cred_type="key", username="root",
                secret_encrypted="", passphrase_encrypted="", description="",
                created_by=1, created_at=None, updated_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _host(inline_key="", inline_pass=None, password="", ssh_key_path="", credential_id=None):
    return SimpleNamespace(
        name="h1", address="1.2.3.4", ssh_port=22, ssh_user="root",
        ssh_password_encrypted=encrypt_aes256_gcm(password) if password else "",
        ssh_key_encrypted=encrypt_aes256_gcm(inline_key) if inline_key else None,
        ssh_key_passphrase_encrypted=encrypt_aes256_gcm(inline_pass) if inline_pass else None,
        ssh_key_path=ssh_key_path,
        credential_id=credential_id,
    )


class TestEncryptionStorage(unittest.TestCase):
    def test_ciphertext_not_plaintext_and_roundtrip(self):
        plain = "-----BEGIN OPENSSH PRIVATE KEY-----secret-content-----END-----"
        ct = encrypt_aes256_gcm(plain)
        self.assertNotIn(plain, ct)
        self.assertNotEqual(ct, plain)
        self.assertEqual(decrypt_aes256_gcm(ct), plain)

    def test_credential_row_hides_secret(self):
        row = hcs.credential_row(_cred(secret_encrypted="CIPHER", passphrase_encrypted="CIPHER2"))
        self.assertTrue(row["has_secret"])
        self.assertTrue(row["has_passphrase"])
        self.assertNotIn("secret", row)
        self.assertNotIn("secret_encrypted", row)
        self.assertNotIn("passphrase_encrypted", row)


class TestResolveCredentialsPriority(unittest.TestCase):
    def test_credential_key_preferred_over_inline(self):
        host = _host(inline_key="INLINE-KEY", credential_id=1)
        cred = {"username": "root", "cred_type": "key", "secret": "CRED-KEY", "passphrase": ""}
        with patch("app.services.host_ssh.asyncssh.import_private_key") as imp:
            imp.return_value = object()
            kwargs = resolve_credentials(host, cred=cred)
        imp.assert_called_once_with("CRED-KEY", passphrase=None)
        self.assertIn("client_keys", kwargs)
        self.assertNotIn("password", kwargs)

    def test_credential_password_type(self):
        host = _host(password="INLINE-PWD", credential_id=1)
        cred = {"username": "root", "cred_type": "password", "secret": "CRED-PWD", "passphrase": ""}
        kwargs = resolve_credentials(host, cred=cred)
        self.assertEqual(kwargs.get("password"), "CRED-PWD")
        self.assertNotIn("client_keys", kwargs)

    def test_inline_key_used_when_no_credential(self):
        host = _host(inline_key="INLINE-KEY")
        with patch("app.services.host_ssh.asyncssh.import_private_key") as imp:
            imp.return_value = object()
            resolve_credentials(host)
        imp.assert_called_once_with("INLINE-KEY", passphrase=None)


class TestCredentialCRUD(unittest.TestCase):
    def test_crud_and_no_plaintext(self):
        async def body():
            name = f"__t_hc_{_ts()}"
            row = await hcs.create_credential(
                {"name": name, "cred_type": "key", "username": "root",
                 "secret": "test-secret-abc", "passphrase": "pp", "description": "d"},
                user_id=1,
            )
            try:
                self.assertTrue(row["has_secret"])
                self.assertNotIn("secret", row)
                self.assertNotIn("secret_encrypted", row)
                # 列表不回明文
                rows = await hcs.list_credentials()
                found = next((r for r in rows if r["id"] == row["id"]), None)
                self.assertIsNotNone(found)
                self.assertNotIn("secret", found)
                # DB 存的是密文
                async with async_session() as s:
                    c = await s.get(HostCredential, row["id"])
                    self.assertNotEqual(c.secret_encrypted, "test-secret-abc")
                    self.assertEqual(decrypt_aes256_gcm(c.secret_encrypted), "test-secret-abc")
                # 编辑：secret 留空 = 不修改
                await hcs.update_credential(row["id"], {"description": "edited"})
                async with async_session() as s:
                    c = await s.get(HostCredential, row["id"])
                    self.assertEqual(decrypt_aes256_gcm(c.secret_encrypted), "test-secret-abc")
                    self.assertEqual(c.description, "edited")
            finally:
                ok, _ = await hcs.delete_credential(row["id"])
                self.assertTrue(ok)

        _run(body())

    def test_delete_referenced_rejected(self):
        async def body():
            name = f"__t_hc_ref_{_ts()}"
            row = await hcs.create_credential(
                {"name": name, "cred_type": "key", "username": "root", "secret": "k"},
                user_id=1,
            )
            hid = None
            try:
                async with async_session() as s:
                    h = Host(name=f"__t_host_ref_{_ts()}", address="127.0.0.1", credential_id=row["id"])
                    s.add(h)
                    await s.commit()
                    hid = h.id
                ok, msg = await hcs.delete_credential(row["id"])
                self.assertFalse(ok)
                self.assertIn("引用", msg)
            finally:
                async with async_session() as s:
                    if hid:
                        h = await s.get(Host, hid)
                        if h:
                            await s.delete(h)
                            await s.commit()
                ok, _ = await hcs.delete_credential(row["id"])
                self.assertTrue(ok)

        _run(body())


if __name__ == "__main__":
    unittest.main()
