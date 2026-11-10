# -*- coding: utf-8 -*-
"""R8 回归：凭据解析 / 批量导入解析与校验 / SFTP 路径白名单。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_host_r8 -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import asyncssh
from fastapi import HTTPException

from app.core.security import encrypt_aes256_gcm
from app.services import host_import_service as his
from app.services import host_ssh
from app.api import host_terminal


KEY = asyncssh.generate_private_key("ssh-ed25519").export_private_key().decode()


def _host(**kw):
    base = dict(name="h", address="10.0.0.1", ssh_port=22, ssh_user="root",
                ssh_password_encrypted="", ssh_key_path="",
                ssh_key_encrypted=None, ssh_key_passphrase_encrypted=None, auth_type="password")
    base.update(kw)
    return SimpleNamespace(**base)


class TestResolveCredentials(unittest.TestCase):
    def test_password(self):
        h = _host(ssh_password_encrypted=encrypt_aes256_gcm("p@ss"))
        kw = host_ssh.resolve_credentials(h)
        self.assertEqual(kw.get("password"), "p@ss")
        self.assertNotIn("client_keys", kw)
        self.assertIsNone(kw.get("known_hosts"))

    def test_key_content_priority(self):
        h = _host(auth_type="key", ssh_key_encrypted=encrypt_aes256_gcm(KEY),
                  ssh_password_encrypted=encrypt_aes256_gcm("p@ss"))
        kw = host_ssh.resolve_credentials(h)
        self.assertIn("client_keys", kw)
        self.assertEqual(len(kw["client_keys"]), 1)
        self.assertEqual(kw.get("password"), "p@ss")  # 密码仍作为兜底

    def test_invalid_key_raises(self):
        h = _host(auth_type="key", ssh_key_encrypted=encrypt_aes256_gcm("not-a-key"))
        with self.assertRaises(Exception):
            host_ssh.resolve_credentials(h)

    def test_key_path_compat(self):
        h = _host(ssh_key_path="/tmp/id_rsa", auth_type="key")
        kw = host_ssh.resolve_credentials(h)
        self.assertEqual(kw.get("client_keys"), ["/tmp/id_rsa"])


class TestImportParsing(unittest.TestCase):
    def test_parse_with_header(self):
        content = "名称,地址,端口,用户名,认证方式,密码,私钥,分组,进程路径,说明\n" \
                  "a1,10.0.0.1,22,root,password,pwd,,生产,/opt/a.jar;name=/opt/b.jar,说明A\n"
        rows, errs = his.parse_rows(content)
        self.assertEqual(errs, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "a1")
        self.assertEqual(rows[0]["group"], "生产")
        paths = his.parse_process_paths(rows[0]["process_paths"])
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[1]["name"], "name")
        self.assertEqual(paths[1]["path"], "/opt/b.jar")

    def test_parse_tab_without_header(self):
        content = "b1\t10.0.0.2\t22\troot\tpassword\tpwd\t\t\t\t\n"
        rows, _ = his.parse_rows(content)
        self.assertEqual(rows[0]["name"], "b1")
        self.assertEqual(rows[0]["address"], "10.0.0.2")

    def test_validate_errors(self):
        self.assertIn("名称", his._validate({"address": "10.0.0.1"}) or "")
        self.assertIn("地址", his._validate({"name": "x"}) or "")
        self.assertIn("端口", his._validate({"name": "x", "address": "1.1.1.1", "ssh_port": "99999"}) or "")
        self.assertIsNone(his._validate({"name": "x", "address": "1.1.1.1"}))


class TestSftpGuard(unittest.TestCase):
    def test_allow(self):
        self.assertEqual(host_terminal._sftp_guard("/tmp/a.txt"), "/tmp/a.txt")
        self.assertEqual(host_terminal._sftp_guard("/home/user/x"), "/home/user/x")

    def test_deny(self):
        with self.assertRaises(HTTPException):
            host_terminal._sftp_guard("/etc/passwd")
        with self.assertRaises(HTTPException):
            host_terminal._sftp_guard("/root/.ssh/id_rsa")

    def test_outside_whitelist(self):
        with self.assertRaises(HTTPException):
            host_terminal._sftp_guard("/opt/app")


if __name__ == "__main__":
    unittest.main()
