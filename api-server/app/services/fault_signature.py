# -*- coding: utf-8 -*-
"""故障指纹：判断「还是同一个错误」还是「报错变了」

老板口径（2026-11-26）：
- 同一个错误没修好、容器一直重启 → **不需要重复诊断**（省 AI 成本，诊断台账也不刷重复）；
- 报错/退出原因变了（退出码、OOM 容器、异常首行……）→ **必须重新诊断**。

指纹输入：
  fault_type + 每容器退出事实（容器名:原因:退出码）+ 容器退出证据摘要 + 首条强错误行

归一化：去掉时间戳 / UUID / 长十六进制 / 超大数字（字节数、请求体大小等噪声），
避免「同一条错误每轮算出来的指纹都不一样」。纯函数，无 IO，方便单测。
"""
import hashlib
import re

# 2026-11-26T10:14:42.123Z / 2026-11-26 10:14:42,123+08:00
_TS_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?\b")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# 长十六进制（traceId/指针/哈希片段）
_HEX_RE = re.compile(r"\b[0-9a-fA-F]{12,}\b")
# 超大数字（字节数、容量、耗时 ns 等；退出码/状态码都是小数字，不受影响）
_LONGNUM_RE = re.compile(r"\b\d{6,}\b")
_WS_RE = re.compile(r"\s+")


def normalize(text: str, limit: int = 200) -> str:
    """归一化一行证据文本（大小写不敏感、去时间戳/ID/大数字、压缩空白）"""
    s = (text or "").strip()
    if not s:
        return ""
    s = _TS_RE.sub("<ts>", s)
    s = _UUID_RE.sub("<uuid>", s)
    s = _HEX_RE.sub("<hex>", s)
    s = _LONGNUM_RE.sub("<n>", s)
    s = _WS_RE.sub(" ", s).lower()
    return s[:limit]


def facts_from_pod(pod: dict | None) -> list[str]:
    """从 N5 的 pod dict（ListPodRestartInfo）提取每容器退出事实（排序保证稳定）"""
    out: list[str] = []
    for c in (pod or {}).get("containers") or []:
        out.append("{}:{}:{}".format(
            normalize(str(c.get("name") or "")),
            normalize(str(c.get("last_terminated_reason") or "")),
            normalize(str(c.get("last_terminated_exit_code"))),
        ))
    return sorted(out)


def build(fault_type: str, facts: list[str] | None = None,
          term_summary: str = "", first_error: str = "", phase: str = "") -> str:
    """算指纹（sha1 前 32 位，落 alerts.fault_signature）

    phase（2026-11-26 老板口径）：startup / runtime —— 同一句报错发生在启动期和运行期是不同问题，
    必须分别诊断，所以阶段参与指纹。
    """
    parts = [normalize(fault_type or ""), normalize(phase or "")]
    parts.extend(normalize(x) for x in (facts or []))
    parts.append(normalize(term_summary))
    parts.append(normalize(first_error))
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]


def changed(prev: str, current: str) -> bool:
    """报错是否变化（prev 为空 = 没有基线，不算变化，避免历史数据被反复重诊断）"""
    if not prev or not current:
        return False
    return prev != current
