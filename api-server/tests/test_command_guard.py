# -*- coding: utf-8 -*-
"""R36 T1 回归：危险命令拦截规则 + 终端输入行缓冲解析。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_command_guard -v
"""
import unittest

from app.services.command_guard import LineAssembler, check_command


class TestCheckCommand(unittest.TestCase):
    """危险命令黑名单：命中→blocked=True 且带风险等级/原因；安全命令→False/low。"""

    def test_safe_commands_pass(self):
        for cmd in ["ls -l", "cat /etc/hosts", "df -h", "echo hello", "cd /tmp && ls"]:
            blocked, risk, reason = check_command(cmd)
            self.assertFalse(blocked, f"{cmd!r} 不应被拦截")
            self.assertEqual(risk, "low")
            self.assertEqual(reason, "")

    def test_blank_is_low(self):
        self.assertEqual(check_command(""), (False, "low", ""))
        self.assertEqual(check_command("   "), (False, "low", ""))

    def test_rm_rf_root(self):
        for cmd in ["rm -rf /", "rm -rf /*", "sudo rm -rf /", "rm -rf ~"]:
            blocked, risk, reason = check_command(cmd)
            self.assertTrue(blocked, f"{cmd!r} 应被拦截")
            self.assertEqual(risk, "high")

    def test_rm_rf_not_root(self):
        # 删具体目录（/tmp、/var/log）不拦
        for cmd in ["rm -rf /tmp", "rm -rf /var/log/foo", "rm -f /root/x.log"]:
            blocked, _, _ = check_command(cmd)
            self.assertFalse(blocked, f"{cmd!r} 不应被拦截")

    def test_mkfs(self):
        for cmd in ["mkfs.ext4 /dev/sdb", "mkfs.xfs /dev/sdc"]:
            blocked, risk, _ = check_command(cmd)
            self.assertTrue(blocked)
            self.assertEqual(risk, "high")

    def test_dd_to_block_device(self):
        blocked, risk, _ = check_command("dd if=/dev/zero of=/dev/sda bs=1M")
        self.assertTrue(blocked)
        self.assertEqual(risk, "high")
        # dd 写普通文件不拦
        self.assertFalse(check_command("dd if=a of=b.img")[0])

    def test_fork_bomb(self):
        blocked, risk, _ = check_command(":(){ :|:& };:")
        self.assertTrue(blocked)
        self.assertEqual(risk, "high")

    def test_chmod_chown_root(self):
        self.assertTrue(check_command("chmod -R 777 /")[0])
        self.assertTrue(check_command("chown -R root /")[0])
        # 非根目录不拦
        self.assertFalse(check_command("chmod -R 777 /var/www")[0])

    def test_redirect_and_mv(self):
        self.assertTrue(check_command("echo x > /dev/sda")[0])
        self.assertTrue(check_command("mv /* /dev/null")[0])

    def test_drop_database_table(self):
        self.assertTrue(check_command("drop database prod")[0])
        self.assertTrue(check_command("DROP TABLE users")[0])

    def test_medium_shutdown_reboot(self):
        for cmd in ["shutdown -h now", "reboot", "halt", "poweroff", "init 0", "init 6"]:
            blocked, risk, reason = check_command(cmd)
            self.assertTrue(blocked, f"{cmd!r} 应被拦截")
            self.assertEqual(risk, "medium")
            self.assertTrue(reason)

    def test_init_not_shutdown_runlevel(self):
        # init 3/5 是正常切换，不拦
        for cmd in ["init 3", "init 5"]:
            self.assertFalse(check_command(cmd)[0])


class TestLineAssembler(unittest.TestCase):
    """行缓冲：回车提交整行、退格删除、转义序列吞掉、多行粘贴逐条产出。"""

    def test_cr_submit(self):
        a = LineAssembler()
        self.assertEqual(a.feed("ls -l\r"), ["ls -l"])

    def test_crlf_single_submit(self):
        a = LineAssembler()
        self.assertEqual(a.feed("id\r\n"), ["id"])

    def test_chunked_input(self):
        a = LineAssembler()
        self.assertEqual(a.feed("echo a"), [])
        self.assertEqual(a.feed("\n"), ["echo a"])

    def test_backspace(self):
        a = LineAssembler()
        self.assertEqual(a.feed("lss\x08\r"), ["ls"])
        self.assertEqual(a.feed("\x7f\x7f"), [])  # 空缓冲退格不出行

    def test_escape_sequences_swallowed(self):
        a = LineAssembler()
        self.assertEqual(a.feed("\x1b[Al\x1b[Bs\r"), ["ls"])

    def test_multiline_paste(self):
        a = LineAssembler()
        out = a.feed("ls -l\rpwd\rmkfs.ext4 /\r")
        self.assertEqual(out, ["ls -l", "pwd", "mkfs.ext4 /"])

    def test_empty_lines_ignored(self):
        a = LineAssembler()
        self.assertEqual(a.feed("\r\r"), [])

    def test_tab_folded_to_space(self):
        a = LineAssembler()
        self.assertEqual(a.feed("ls\t-l\r"), ["ls -l"])


if __name__ == "__main__":
    unittest.main()
