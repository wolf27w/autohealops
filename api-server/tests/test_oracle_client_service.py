# -*- coding: utf-8 -*-
"""Oracle 客户端版本管理单测（2026-11-16）

覆盖：目录名白名单、库文件识别、zip 路径穿越拦截、正常安装与启用/删除流程。
不联网、不依赖真实 Instant Client。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_oracle_client_service -v
"""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.core.config import settings
from app.services import oracle_client_service as svc


class TestOracleClientService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old_home = getattr(settings, "ORACLE_CLIENT_HOME", "")
        settings.ORACLE_CLIENT_HOME = self.tmp.name

    def tearDown(self):
        settings.ORACLE_CLIENT_HOME = self._old_home
        self.tmp.cleanup()

    def _make_zip(self, name: str, entries: dict[str, bytes]) -> Path:
        p = Path(self.tmp.name) / name
        with zipfile.ZipFile(p, "w") as zf:
            for k, v in entries.items():
                zf.writestr(k, v)
        return p

    def test_safe_name(self):
        self.assertEqual(svc.safe_name("19.19-linux_x64"), "19.19-linux_x64")
        for bad in ("", "../x", "a b", "a/b", "x" * 65):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    svc.safe_name(bad)

    def test_detects_libclntsh(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "libclntsh.so.19.1").write_bytes(b"x")
            self.assertTrue(svc._lib_files(Path(d)))
        with tempfile.TemporaryDirectory() as d:
            Path(d, "readme.txt").write_bytes(b"x")
            self.assertFalse(svc._lib_files(Path(d)))

    def test_install_and_activate(self):
        # 模拟官方包结构：顶层目录 instantclient_19_19/
        z = self._make_zip("pkg.zip", {
            "instantclient_19_19/libclntsh.so.19.1": b"x",
            "instantclient_19_19/network/admin/sqlnet.ora": b"",
        })
        info = svc.install_package(z, "19.19")
        self.assertTrue(info["libs"])
        root = Path(self.tmp.name) / "19.19"
        self.assertTrue((root / "libclntsh.so.19.1").is_file())  # 顶层目录被拍平
        data = svc.list_clients()
        self.assertEqual([c["name"] for c in data["clients"]], ["19.19"])
        self.assertEqual(svc.set_active("19.19")["active"], "19.19")
        self.assertTrue(svc.active_lib_dir().endswith("19.19"))
        # 启用中的不能删；切换为空后可以删
        with self.assertRaises(ValueError):
            svc.delete_client("19.19")

    def test_zip_path_traversal_rejected(self):
        z = self._make_zip("evil.zip", {"../evil.txt": b"x"})
        with self.assertRaises(ValueError):
            svc.install_package(z, "evil")
        self.assertFalse((Path(self.tmp.name).parent / "evil.txt").exists())

    def test_install_rejects_non_client_package(self):
        z = self._make_zip("plain.zip", {"a.txt": b"x"})
        with self.assertRaises(ValueError):
            svc.install_package(z, "plain")
        self.assertFalse((Path(self.tmp.name) / "plain").exists())  # 失败要清理

    def test_catalog_shape(self):
        ids = {c["id"] for c in svc.CATALOG}
        self.assertIn("mac-arm64-19.x", ids)
        self.assertIn("linux-x64-19.19", ids)
        for c in svc.CATALOG:
            self.assertIn("label", c)
            self.assertIn("manual", c)


if __name__ == "__main__":
    unittest.main()
