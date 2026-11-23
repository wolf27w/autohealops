# -*- coding: utf-8 -*-
"""R75.6 证据缺口显式化与补采指引

背景：诊断容易「拿表象当根因」（用探针配置猜测填补缺失证据）。本模块把诊断过程中的证据缺口
显式列出来，并给出具体补采命令，供工单描述「缺失证据」段使用。

对外主入口：``collect_evidence_gaps(alert_message, alert_data, diagnosis) -> list[dict]``
返回项结构：``{missing, how_to_collect}``

缺失判断（任一缺失即列一项）：
- 无 --previous（上次实例）日志线索（alert_data.recent_logs 不含「上次实例」）
- 无 exitCode / 退出码
- 无 LastState / 上次状态
- 无指标证据（无趋势数据佐证根因）
- 探针未实测（probe_evidence 为空或含「跳过」）

若 AI 已在 analysis.evidence_gaps 里给出缺口，去重合并（AI 优先）。
"""
import re

_EXIT_CODE_RE = re.compile(r"exit\s*code|exitcode|退出码", re.I)
_LAST_STATE_RE = re.compile(r"last\s*state|laststate|上次状态|最后状态|上一次状态", re.I)
_PREVIOUS_RE = re.compile(r"--previous|上次实例|previous", re.I)
_METRIC_RE = re.compile(r"指标|metric|使用率|utilization|趋势|qps|rps|吞吐|流量|延迟|latency", re.I)

# 确定性缺口分类 → 关键词（用于与 AI gaps 去重）
_DET_KINDS: dict[str, tuple[str, ...]] = {
    "previous": ("上次实例", "--previous", "previous"),
    "exitcode": ("exit", "退出码"),
    "laststate": ("laststate", "last state", "上次状态", "最后状态"),
    "metrics": ("指标", "metric", "趋势", "使用率", "qps"),
    "probe": ("探针", "probe", "readiness", "liveness"),
}


def _diag_text(diagnosis: dict) -> str:
    """诊断 JSON 拍平成文本（与 config_patch_suggestion.diagnosis_to_text 同构，此处独立实现避免耦合）。"""
    if not isinstance(diagnosis, dict):
        return ""
    parts = [str(diagnosis.get("root_cause") or "")]
    chain = diagnosis.get("root_cause_chain") or {}
    if isinstance(chain, dict):
        parts.extend(str(v) for v in (chain.get("trigger"), chain.get("direct"), chain.get("root")) if v)
    analysis = diagnosis.get("analysis") or {}
    if isinstance(analysis, dict):
        for hyp in (analysis.get("hypotheses") or []):
            if isinstance(hyp, dict):
                parts.append(str(hyp.get("cause") or ""))
                parts.extend(str(e) for e in (hyp.get("evidence_for") or []))
                parts.extend(str(e) for e in (hyp.get("evidence_against") or []))
        for e in (analysis.get("eliminated") or []):
            parts.append(str(e))
    refs = diagnosis.get("evidence_refs") or []
    if isinstance(refs, list):
        parts.extend(str(r) for r in refs)
    detail = diagnosis.get("suggestions_detail") or {}
    if isinstance(detail, dict):
        for k in ("immediate", "root_fix", "verification", "prevention"):
            parts.extend(str(s) for s in (detail.get(k) or []))
    else:
        for s in (diagnosis.get("suggestions") or []):
            parts.append(str(s))
    return "\n".join(p for p in parts if p and str(p).strip())


def _ai_gaps(diagnosis: dict) -> list[dict]:
    """取 AI 已提供的 evidence_gaps（analysis.evidence_gaps），归一为 [{missing, how_to_collect}]。"""
    if not isinstance(diagnosis, dict):
        return []
    analysis = diagnosis.get("analysis") or {}
    if not isinstance(analysis, dict):
        return []
    raw = analysis.get("evidence_gaps") or []
    out: list[dict] = []
    for g in raw:
        if isinstance(g, dict):
            missing = str(g.get("missing") or "").strip()
            how = str(g.get("how_to_collect") or "").strip()
            if missing:
                out.append({"missing": missing, "how_to_collect": how or "见诊断原文"})
        elif isinstance(g, str) and g.strip():
            out.append({"missing": g.strip(), "how_to_collect": ""})
    return out


def _kind_of(text: str) -> str | None:
    low = (text or "").lower()
    for kind, kws in _DET_KINDS.items():
        if any(kw.lower() in low for kw in kws):
            return kind
    return None


def _merge_gaps(ai_gaps: list[dict], det_gaps: list[dict]) -> list[dict]:
    """AI 优先合并去重：AI 已覆盖的分类，跳过同分类的确定性缺口。"""
    covered: set[str] = set()
    for g in ai_gaps:
        k = _kind_of(g.get("missing") or "")
        if k:
            covered.add(k)
    out = list(ai_gaps)
    for g in det_gaps:
        k = _kind_of(g.get("missing") or "")
        if k in covered:
            continue
        out.append(g)
    return out


def collect_evidence_gaps(alert_message: str, alert_data: dict, diagnosis: dict) -> list[dict]:
    """证据缺口显式化。纯函数，不抛异常。

    alert_data 可含 recent_logs / recent_events / termination_evidence / probe_evidence /
    metrics / metric_evidence（诊断过程中采集的瞬时证据，缺省传 {} 时按缺失处理）。
    """
    try:
        alert_data = alert_data if isinstance(alert_data, dict) else {}
        diagnosis = diagnosis if isinstance(diagnosis, dict) else {}

        recent_logs = str(alert_data.get("recent_logs") or "")
        recent_events = str(alert_data.get("recent_events") or "")
        termination = str(alert_data.get("termination_evidence") or "")
        probe = str(alert_data.get("probe_evidence") or "")
        metrics_text = str(alert_data.get("metrics") or alert_data.get("metric_evidence") or "")

        combined = "\n".join([
            str(alert_message or ""), recent_logs, recent_events, termination, _diag_text(diagnosis),
        ])

        det: list[dict] = []

        # 1. --previous（上次实例）日志
        if "上次实例" not in recent_logs and not _PREVIOUS_RE.search(recent_logs):
            det.append({
                "missing": "--previous（上次实例）日志未采集，无法判断前一次崩溃原因",
                "how_to_collect": "kubectl logs <pod> --previous --tail=200",
            })

        # 2. exitCode
        if not _EXIT_CODE_RE.search(combined):
            det.append({
                "missing": "容器退出码（exitCode）缺失",
                "how_to_collect": "kubectl describe pod <pod> | grep -A12 'Last State' 或节点 crictl logs <container-id>",
            })

        # 3. LastState
        if not _LAST_STATE_RE.search(combined):
            det.append({
                "missing": "容器上次状态（LastState）缺失",
                "how_to_collect": "kubectl describe pod <pod>；节点 /var/log/pods/<ns>_<pod>_<uid>/<container>/ 下按时间翻日志",
            })

        # 4. 指标证据
        has_metric = bool(metrics_text.strip()) or bool(_METRIC_RE.search(combined))
        if not has_metric:
            det.append({
                "missing": "资源/业务指标证据缺失（无趋势数据佐证根因）",
                "how_to_collect": "查询 Prometheus（container_cpu_usage_seconds_total / container_memory_working_set_bytes）或应用监控",
            })

        # 5. 探针实测
        if not probe.strip() or "跳过" in probe:
            det.append({
                "missing": "探针未实测（readiness/liveness 实际探测结果缺失，不得用探针配置猜测填补）",
                "how_to_collect": "kubectl get events | grep -i probe 或 kubectl describe pod <pod> 查看探针失败记录",
            })

        return _merge_gaps(_ai_gaps(diagnosis), det)
    except Exception:  # noqa: BLE001
        return []
