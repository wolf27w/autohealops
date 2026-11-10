# -*- coding: utf-8 -*-
"""R9 回归：主机 SSH 采集改为 asyncssh，失败原因分类（2026-11-09）

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_host_monitor -v
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import asyncssh

from app.services import host_monitor_service


def _host():
    return SimpleNamespace(
        name="h", address="10.0.0.1", ssh_port=22, ssh_user="root",
        ssh_password_encrypted="", ssh_key_path="", process_paths="[]",
    )


class _FakeConn:
    def __init__(self, stdout="", exit_status=0, stderr=""):
        self._stdout = stdout
        self._exit = exit_status
        self._stderr = stderr
        self.closed = False

    async def run(self, cmd):
        self.last_cmd = cmd
        return SimpleNamespace(stdout=self._stdout, exit_status=self._exit, stderr=self._stderr)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class TestSshPsClassification(unittest.IsolatedAsyncioTestCase):
    async def test_auth_failure(self):
        with patch("app.services.host_ssh.asyncssh.connect",
                   side_effect=asyncssh.PermissionDenied("auth failed")):
            out, err = await host_monitor_service.ssh_ps(_host())
        self.assertIsNone(out)
        self.assertIn("认证失败", err)

    async def test_network_unreachable(self):
        with patch("app.services.host_ssh.asyncssh.connect",
                   side_effect=OSError(113, "No route to host")):
            out, err = await host_monitor_service.ssh_ps(_host())
        self.assertIsNone(out)
        self.assertIn("网络不可达", err)

    async def test_timeout(self):
        import asyncio
        with patch("app.services.host_ssh.asyncssh.connect",
                   side_effect=asyncio.TimeoutError()):
            out, err = await host_monitor_service.ssh_ps(_host())
        self.assertIsNone(out)
        self.assertIn("超时", err)

    async def test_success(self):
        conn = _FakeConn(stdout="PID CPU MEM RSS ARGS\n1 0.0 1.0 100 /usr/sbin/sshd\n")
        async def _connect(*a, **k):
            return conn
        with patch("app.services.host_ssh.asyncssh.connect", side_effect=_connect):
            out, err = await host_monitor_service.ssh_ps(_host())
        self.assertEqual(err, "")
        self.assertIn("/usr/sbin/sshd", out or "")
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
