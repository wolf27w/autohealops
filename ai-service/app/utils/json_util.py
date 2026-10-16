# -*- coding: utf-8 -*-
"""AI 返回 JSON 解析工具：统一去掉 markdown 代码块围栏 + 健壮地提取 JSON 对象。

收敛点：diagnosis_service / agent_service / plan_service / verification_service /
aux_api 各自手写的「去 ```json 围栏 → json.loads → 失败取 {..} 子串再 loads」重复逻辑。
"""
import json
import re

# 开头围栏：```json / ``` / ```任意语言标记
_FENCE_LEAD_RE = re.compile(r"^```[a-zA-Z0-9_+-]*\s*")
# 结尾围栏：```（可带前后空白）
_FENCE_TAIL_RE = re.compile(r"\s*```\s*$")
# 非法转义：反斜杠后面不是合法 JSON 转义字符（如 Windows 路径 C:\Users、正则 \d、\P 等）
_INVALID_ESCAPE_RE = re.compile(r'\\(?!["\\/bfnrtu])')


def _loads_lenient(s: str):
    """json.loads，失败时把「非法转义反斜杠」补成双反斜杠再试（模型常输错转义导致解析失败）"""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        return json.loads(_INVALID_ESCAPE_RE.sub(r"\\\\", s))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def strip_code_fence(text: str) -> str:
    """去掉 markdown 代码块围栏（```json / ``` / 尾部 ```），返回干净文本"""
    t = (text or "").strip()
    t = _FENCE_LEAD_RE.sub("", t)
    t = _FENCE_TAIL_RE.sub("", t)
    return t.strip()


def iter_json_objects(text: str):
    """扫描文本，逐个产出**平衡的** JSON 对象子串（忽略字符串内的括号）。

    用于模型输出里「文字 + 多个 JSON 对象」的情形（找首个/最后一个有效对象）。
    """
    s = text or ""
    depth = 0
    start = -1
    instr = False
    esc = False
    for i, ch in enumerate(s):
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield s[start:i + 1]
                    start = -1


def extract_json_prefer(text: str, prefer_key: str = "action", default=None):
    """从模型输出里取 JSON 对象；优先返回**含 prefer_key** 的对象，多个时取**最后一个**
    （模型常先写草稿再给出最终决策）。无则回退到 extract_json。
    """
    cands = list(iter_json_objects(strip_code_fence(text)))
    parsed: list[dict] = []
    for c in cands:
        v = _loads_lenient(c)
        if isinstance(v, dict):
            parsed.append(v)
    if parsed:
        with_key = [v for v in parsed if prefer_key in v]
        if with_key:
            return with_key[-1]
        return parsed[-1]
    return extract_json(text, default=default)


def extract_json(text: str, default=None):
    """从模型输出里健壮地提取 JSON 对象（dict）。

    流程：去围栏 → json.loads → 失败取 {..} 子串再 loads；
    结果非 dict（如顶层是数组）或全部失败时返回 default（与既有 dict 语义一致）。

    说明：调用方预期拿到 dict；顶层为数组/标量时视为「未解析出对象」，
    走 {..} 提取，仍找不到则返回 default（原 _extract_json_dict 的行为）。
    """
    t = strip_code_fence(text)
    if not t:
        return default
    v = _loads_lenient(t)
    if isinstance(v, dict):
        return v
    # 兜底：取第一个 { 到最后一个 }
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        v = _loads_lenient(t[start:end + 1])
        if isinstance(v, dict):
            return v
    return default
