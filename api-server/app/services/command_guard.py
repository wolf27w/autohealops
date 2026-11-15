# -*- coding: utf-8 -*-
"""B8 危险命令拦截规则 + 终端输入行缓冲解析。

- DANGEROUS_RULES：危险命令正则表（pattern, risk, reason）。命中即拦截。
- check_command(cmd)：纯函数，返回 (blocked, risk, reason)，方便单测。
- LineAssembler：把终端输入字节流按行切出「整行命令」，处理退格/转义序列/回车提交，纯类方便单测。

规则设计要点：
- 只拦「整行命令」（用户在终端按回车提交的完整命令行），不拦逐字符输入。
- pattern 用 search（非 fullmatch），因此 `sudo rm -rf /`、`cd / && rm -rf /*` 也能命中。
- 风险分级：直接破坏系统/不可恢复 → high；破坏可用性但可恢复 → medium。
"""
from __future__ import annotations

import re

# 高危/中危命令行匹配（大小写不敏感，命令名子串匹配即可覆盖 sudo / 管道前缀）
DANGEROUS_RULES: list[tuple[re.Pattern, str, str]] = [
    # ---- high：直接破坏系统 / 不可恢复 ----
    (re.compile(
        r"\brm\s+(?:-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s+(?:/\*|~|/(?:\s|;|\||&|$))",
        re.IGNORECASE,
    ), "high", "危险删除命令（rm -rf /）"),
    (re.compile(r"\bmkfs(?:\.[a-z0-9_-]+)?\b", re.IGNORECASE), "high", "格式化文件系统（mkfs）"),
    (re.compile(r"\bdd\b[^\n]*?\bof\s*=\s*/dev/(?:sd|nvme|vd|hd)[a-z0-9]*", re.IGNORECASE),
     "high", "直接写块设备（dd of=/dev/...）"),
    (re.compile(r":\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;?\s*:"), "high", "fork 炸弹"),
    (re.compile(r"\bchmod\s+-[a-zA-Z]*r[a-zA-Z]*\s+777\s+(?:/\*|~|/(?:\s|;|\||&|$))", re.IGNORECASE),
     "high", "递归放宽根目录权限（chmod -R 777 /）"),
    (re.compile(r"\bchown\s+-[a-zA-Z]*r[a-zA-Z]*\s+\S+\s+(?:/\*|~|/(?:\s|;|\||&|$))", re.IGNORECASE),
     "high", "递归变更根目录属主（chown -R ... /）"),
    (re.compile(r">\s*/dev/(?:sd|nvme|vd|hd)[a-z0-9]*"), "high", "覆写块设备（> /dev/...）"),
    (re.compile(r"\bmv\s+/\*\s+/dev/null\b", re.IGNORECASE), "high", "移动根目录到 /dev/null（mv /* /dev/null）"),
    (re.compile(r"\bdrop\s+database\b", re.IGNORECASE), "high", "删除数据库（DROP DATABASE）"),
    (re.compile(r"\bdrop\s+table\b", re.IGNORECASE), "high", "删除表（DROP TABLE）"),
    # ---- medium：破坏服务/可用性但可恢复 ----
    (re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b", re.IGNORECASE), "medium", "关机/重启命令"),
    (re.compile(r"\binit\s+[06]\b", re.IGNORECASE), "medium", "切换运行级别（init 0/6 关机/重启）"),
]


def check_command(cmd: str) -> tuple[bool, str, str]:
    """检测命令是否命中危险规则。

    返回 (blocked, risk, reason)：
    - blocked=True：命中危险规则，应拦截（不转发给 SSH）
    - risk：low（未命中）/ medium / high
    - reason：拦截原因（未命中时为空字符串）
    """
    line = (cmd or "").strip()
    if not line:
        return False, "low", ""
    for pattern, risk, reason in DANGEROUS_RULES:
        if pattern.search(line):
            return True, risk, reason
    return False, "low", ""


class LineAssembler:
    """把终端输入字节流（已 utf-8 解码的可打印文本）按行切出整行命令。

    - `\\r` / `\\n` / `\\r\\n` 视为一次「回车提交」，产出一条命令；空行忽略。
    - `\\x7f`（DEL）与 `\\x08`（BS）为退格，删除缓冲末尾一个字符。
    - `\\x1b[...`（CSI/SS3/OSC）等转义序列与方向键被整体吞掉，不进入缓冲。
    - 其余控制字符忽略，tab 折叠为空格（无碍命令检测）。
    纯类，无 IO/网络，方便单测。
    """

    _BACKSPACE = ("\x08", "\x7f")

    def __init__(self) -> None:
        self._buf: list[str] = []

    def feed(self, text: str) -> list[str]:
        """喂入一段解码文本，返回本次新产出的完整命令行（可能多条/空）。"""
        out: list[str] = []
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":
                i = self._consume_escape(text, i + 1)
                continue
            if ch in self._BACKSPACE:
                if self._buf:
                    self._buf.pop()
                i += 1
                continue
            if ch == "\r":
                # \r 或 \r\n 都算一次提交
                if i + 1 < n and text[i + 1] == "\n":
                    i += 2
                else:
                    i += 1
                self._emit(out)
                continue
            if ch == "\n":
                i += 1
                self._emit(out)
                continue
            if ch == "\t":
                self._buf.append(" ")
                i += 1
                continue
            if ord(ch) < 0x20:
                i += 1
                continue
            self._buf.append(ch)
            i += 1
        return out

    @staticmethod
    def _consume_escape(text: str, pos: int) -> int:
        """吞掉从 pos 起的转义序列，返回下一个未消费位置。"""
        n = len(text)
        if pos >= n:
            return pos
        c = text[pos]
        if c == "[":  # CSI：参数(0x30-0x3f) + 中间(0x20-0x2f) + 终(0x40-0x7e)
            pos += 1
            while pos < n and 0x30 <= ord(text[pos]) <= 0x3f:
                pos += 1
            while pos < n and 0x20 <= ord(text[pos]) <= 0x2f:
                pos += 1
            if pos < n and 0x40 <= ord(text[pos]) <= 0x7e:
                pos += 1
            return pos
        if c == "O":  # SS3：单个终字节
            pos += 1
            if pos < n:
                pos += 1
            return pos
        if c == "]":  # OSC：吞到 BEL 或 ST（ESC \）
            pos += 1
            while pos < n:
                if text[pos] == "\x07":
                    return pos + 1
                if text[pos] == "\x1b" and pos + 1 < n and text[pos + 1] == "\\":
                    return pos + 2
                pos += 1
            return pos
        # 孤立 ESC：只吞 ESC 本身
        return pos

    def _emit(self, out: list[str]) -> None:
        line = "".join(self._buf).rstrip()
        self._buf.clear()
        if line:
            out.append(line)
