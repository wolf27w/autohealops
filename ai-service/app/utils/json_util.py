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


def strip_code_fence(text: str) -> str:
    """去掉 markdown 代码块围栏（```json / ``` / 尾部 ```），返回干净文本"""
    t = (text or "").strip()
    t = _FENCE_LEAD_RE.sub("", t)
    t = _FENCE_TAIL_RE.sub("", t)
    return t.strip()


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
    try:
        v = json.loads(t)
    except (json.JSONDecodeError, TypeError, ValueError):
        v = None
    else:
        if isinstance(v, dict):
            return v
    # 兜底：取第一个 { 到最后一个 }
    start = t.find("{")
    end = t.rfind("}")
    if start >= 0 and end > start:
        try:
            v = json.loads(t[start:end + 1])
        except (json.JSONDecodeError, TypeError, ValueError):
            v = None
        else:
            if isinstance(v, dict):
                return v
    return default
