# -*- coding: utf-8 -*-
"""诊断质量评估服务（R42 二期）：标注 → 评估集 → 回放评分 → 指标

存储策略（老板口径：优先复用现有存储，不新增表）：
- 复用「文件存储」模式（同 agent_session_store），落单个 JSON 文件：
  `{PROJECT_ROOT}/data/eval-store.json`
- 轨迹本体仍复用现有表（alerts + 关联诊断/方案/执行/验证等），本服务只存「标注、评估集、
  最近一次评估结果」三层元数据，不新增数据库表。

文件结构：
{
  "version": 1,
  "annotations": { "<trace_id>": { trace_id, judgment, correct_root_cause,
                                     expected_tools, note, annotated_by, annotated_at } },
  "eval_sets":  [ { id, name, description, trace_ids, filters, created_by, created_at } ],
  "last_run":   { summary, items, run_at, ... }   # POST /eval/run 结果快照（/eval/summary 读取）
}

指标口径（回放评分，不真跑 AI）：
- 诊断准确率 = Σ(判定得分) / 已标注条数 × 100%；correct=1.0，partial=partial_weight(默认 0.5)，wrong=0.0
- 平均工具调用步数 = 各轨迹「tool_name 非空 step 数」（AI 工具步 + 执行步）的均值
- 平均耗时 = 各轨迹 duration_ms 的均值（duration_text 供展示）
- 工具命中率 = 有「期望工具」的轨迹上，|期望工具 ∩ 实际工具| / |期望工具| 的均值
  （命中判定：期望工具与某实际工具相等，或期望工具是某实际工具的子串，大小写不敏感）
  实际工具 = 轨迹 steps 里 tool_name 非空的去重集合（一期里诊断步="ai_diagnose"、执行步=命令）。

说明：一期轨迹把诊断过程折叠成单个 "ai_diagnose" 工具步；R41 的 Agent Runtime 若把
get_pod_logs / get_metrics 等逐次打点进 steps，本指标自动变得有意义，无需改口径。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.core.logging import get_logger

logger = get_logger("api-server.eval_service")

# ============================================================================
# 纯函数（可单测，不碰 DB / 文件）
# ============================================================================

JUDGMENTS = ("correct", "partial", "wrong")


def _score_judgment(judgment: str, partial_weight: float = 0.5) -> float:
    return {"correct": 1.0, "partial": float(partial_weight), "wrong": 0.0}.get(judgment, 0.0)


def _norm_tool(t: str) -> str:
    return (t or "").strip().lower()


def tool_hit(expected: list[str], actual: list[str]) -> float | None:
    """单条轨迹的工具命中率；期望工具为空时返回 None（不参与命中率均值）"""
    if not expected:
        return None
    hits = 0
    for e in expected:
        el = _norm_tool(e)
        if not el:
            continue
        if any(el == _norm_tool(a) or el in _norm_tool(a) for a in actual if a):
            hits += 1
    return hits / len(expected)


def _count_tool_steps(trace: dict) -> int:
    return sum(1 for s in trace.get("steps", []) if s.get("tool_name"))


def _actual_tools(trace: dict) -> list[str]:
    seen: list[str] = []
    for s in trace.get("steps", []):
        t = (s.get("tool_name") or "").strip()
        if t and t not in seen:
            seen.append(t)
    return seen


def score_trace(annotation: dict | None, trace: dict, partial_weight: float = 0.5) -> dict:
    """单条轨迹评分（纯函数）：标注 + 组装好的 trace → 明细行"""
    ann = annotation or {}
    actual_tools = _actual_tools(trace)
    expected = ann.get("expected_tools") or []
    judgment = ann.get("judgment", "")
    row = {
        "trace_id": trace.get("trace_id", ann.get("trace_id", "")),
        "target": trace.get("target", ""),
        "domain": trace.get("domain", ""),
        "cluster_name": trace.get("cluster_name", ""),
        "namespace": trace.get("namespace", ""),
        "fault_type": trace.get("fault_type", ""),
        "result": trace.get("result", ""),
        "rounds": trace.get("rounds", 0),
        "duration_ms": trace.get("duration_ms", 0),
        "duration_text": trace.get("duration_text", ""),
        "started_at": trace.get("started_at", ""),
        "judgment": judgment or None,
        "score": _score_judgment(judgment, partial_weight) if judgment in JUDGMENTS else None,
        "tool_steps": _count_tool_steps(trace),
        "expected_tools": expected,
        "actual_tools": actual_tools,
        "tool_hit": tool_hit(expected, actual_tools),
        "correct_root_cause": ann.get("correct_root_cause", ""),
        "note": ann.get("note", ""),
        "annotated_by": ann.get("annotated_by", ""),
    }
    return row


def compute_metrics(rows: list[dict], partial_weight: float = 0.5) -> dict:
    """汇总指标（纯函数）：rows 由 score_trace / run_eval 产出"""
    scored = [r for r in rows if r.get("judgment") in JUDGMENTS and r.get("score") is not None]
    n = len(scored)
    base = {
        "total": len(rows),
        "scored": n,
        "correct": 0, "partial": 0, "wrong": 0,
        "accuracy_pct": 0.0,
        "avg_tool_steps": 0.0,
        "avg_rounds": 0.0,
        "avg_duration_ms": 0,
        "avg_duration_text": "—",
        "tool_hit_rate_pct": None,
        "tool_traces": 0,
        "partial_weight": partial_weight,
    }
    if n == 0:
        return base
    correct = sum(1 for r in scored if r["judgment"] == "correct")
    partial = sum(1 for r in scored if r["judgment"] == "partial")
    wrong = sum(1 for r in scored if r["judgment"] == "wrong")
    acc = sum(r["score"] for r in scored) / n * 100.0
    avg_steps = sum(r["tool_steps"] for r in scored) / n
    avg_rounds = sum(r["rounds"] for r in scored) / n
    avg_dur = sum(r["duration_ms"] for r in scored) / n
    tool_rows = [r for r in scored if r.get("tool_hit") is not None]
    hit_rate = (sum(r["tool_hit"] for r in tool_rows) / len(tool_rows) * 100.0) if tool_rows else None

    from app.services import trace_service
    return {
        "total": len(rows),
        "scored": n,
        "correct": correct,
        "partial": partial,
        "wrong": wrong,
        "accuracy_pct": round(acc, 1),
        "avg_tool_steps": round(avg_steps, 2),
        "avg_rounds": round(avg_rounds, 2),
        "avg_duration_ms": int(avg_dur),
        "avg_duration_text": trace_service.format_duration(int(avg_dur)),
        "tool_hit_rate_pct": round(hit_rate, 1) if hit_rate is not None else None,
        "tool_traces": len(tool_rows),
        "partial_weight": partial_weight,
    }


# ============================================================================
# 文件存储（标注 / 评估集 / 最近一次结果）
# ============================================================================

_STORE_DIR = PROJECT_ROOT / "data"
_STORE_FILE = _STORE_DIR / "eval-store.json"
_lock = threading.Lock()

_DEFAULT_STORE: dict[str, Any] = {"version": 1, "annotations": {}, "eval_sets": [], "last_run": None}


def _read_store() -> dict:
    with _lock:
        try:
            if _STORE_FILE.exists():
                data = json.loads(_STORE_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("version", 1)
                    data.setdefault("annotations", {})
                    data.setdefault("eval_sets", [])
                    data.setdefault("last_run", None)
                    return data
        except Exception as e:  # noqa: BLE001
            logger.warning("读评估存储失败，回退空结构: %s", e)
        return json.loads(json.dumps(_DEFAULT_STORE))


def _write_store(store: dict) -> None:
    with _lock:
        try:
            _STORE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _STORE_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, _STORE_FILE)
        except Exception as e:  # noqa: BLE001
            logger.warning("写评估存储失败: %s", e)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _username(user) -> str:
    return getattr(user, "username", "") or ""


# ============================================================================
# DB 层（复用 trace_service 的组装逻辑，轻量加载轨迹）
# ============================================================================

async def _load_trace_light(trace_id: str) -> dict | None:
    """轻量加载轨迹（含 steps，不含 evidence 包）"""
    if not trace_id.startswith("alert-"):
        return None
    try:
        alert_id = int(trace_id[len("alert-"):])
    except ValueError:
        return None

    from app.core.database import async_session
    from app.models import Alert, Cluster
    from app.services import trace_service

    async with async_session() as session:
        alert = await session.get(Alert, alert_id)
        if not alert:
            return None
        cluster_name = ""
        if alert.cluster_id:
            cluster = await session.get(Cluster, alert.cluster_id)
            cluster_name = cluster.name if cluster else ""
        rel = await trace_service._load_relations(session, [alert.id])
        r = rel[alert.id]
        tr = trace_service.assemble_trace(trace_service._to_src(
            alert, r["diagnosis"], r["failure"], r["plan"], r["decision"],
            r["approval"], r["exec_logs"], r["verification"], r["ticket"],
        ), cluster_name=cluster_name)
        return tr


async def _alert_exists(alert_id: int) -> bool:
    from app.core.database import async_session
    from app.models import Alert
    async with async_session() as session:
        return (await session.get(Alert, alert_id)) is not None


def _parse_trace_id(trace_id: str) -> int | None:
    if not trace_id.startswith("alert-"):
        return None
    try:
        return int(trace_id[len("alert-"):])
    except ValueError:
        return None


def _in_range(ts: str, start: str, end: str) -> bool:
    """判断 ISO/空格分隔时间戳是否落在 [start, end)（不含则放行）"""
    if not (start or end):
        return True
    try:
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if start:
            s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if ts < s:
                return False
        if end:
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if ts >= e:
                return False
        return True
    except (TypeError, ValueError, AttributeError):
        return True


def _candidate_item(tr: dict, ann: dict | None) -> dict:
    return {
        "trace_id": tr.get("trace_id"),
        "alert_id": tr.get("alert_id"),
        "target": tr.get("target", ""),
        "domain": tr.get("domain", ""),
        "cluster_name": tr.get("cluster_name", ""),
        "namespace": tr.get("namespace", ""),
        "resource_name": tr.get("resource_name", ""),
        "fault_type": tr.get("fault_type", ""),
        "severity": tr.get("severity", ""),
        "result": tr.get("result", ""),
        "action": tr.get("action", ""),
        "rounds": tr.get("rounds", 0),
        "duration_ms": tr.get("duration_ms", 0),
        "duration_text": tr.get("duration_text", ""),
        "started_at": tr.get("started_at", ""),
        "step_count": len(tr.get("steps", [])),
        "annotated": ann is not None,
        "annotation": ann,
    }


def _public_set(s: dict) -> dict:
    return {
        "id": s.get("id"),
        "name": s.get("name", ""),
        "description": s.get("description", ""),
        "trace_ids": s.get("trace_ids", []),
        "count": len(s.get("trace_ids", [])),
        "filters": s.get("filters") or {},
        "created_by": s.get("created_by", ""),
        "created_at": s.get("created_at", ""),
    }


# ============================================================================
# 对外服务函数（async）
# ============================================================================

async def upsert_annotation(trace_id: str, judgment: str, correct_root_cause: str = "",
                            expected_tools: list[str] | None = None, note: str = "",
                            user=None) -> dict:
    """写入/覆盖单条标注（校验告警存在性，不校验 AI 结论——标注是人工判定）"""
    if judgment not in JUDGMENTS:
        raise ValueError("judgment 必须是 correct/partial/wrong")
    alert_id = _parse_trace_id(trace_id)
    if alert_id is None:
        raise ValueError("trace_id 必须是 alert-<id> 格式")
    if not await _alert_exists(alert_id):
        raise ValueError("轨迹不存在（找不到对应告警）")

    store = _read_store()
    ann = {
        "trace_id": trace_id,
        "judgment": judgment,
        "correct_root_cause": (correct_root_cause or "").strip(),
        "expected_tools": [t.strip() for t in (expected_tools or []) if t and t.strip()],
        "note": (note or "").strip(),
        "annotated_by": _username(user),
        "annotated_at": _now(),
    }
    store["annotations"][trace_id] = ann
    _write_store(store)
    return ann


async def list_sets_and_candidates(page: int = 1, page_size: int = 20, domain: str = "",
                                   fault_type: str = "", start: str = "", end: str = "",
                                   annotated_only: bool = False, user=None) -> dict:
    """返回 { sets: [...], candidates: {total, items} }"""
    store = _read_store()
    sets = [_public_set(s) for s in store.get("eval_sets", [])]

    if annotated_only:
        # 候选 = 已标注 trace 全集（精确、轻量：直接按标注集合加载）
        anns = store.get("annotations", {})
        items: list[dict] = []
        for tid, ann in anns.items():
            tr = await _load_trace_light(tid)
            if tr is None:
                continue
            if domain and tr.get("domain") != domain:
                continue
            if fault_type and tr.get("fault_type") != fault_type:
                continue
            if start or end:
                if not _in_range(tr.get("started_at", ""), start, end):
                    continue
            items.append(_candidate_item(tr, ann))
        items.sort(key=lambda x: x.get("started_at") or "", reverse=True)
        total = len(items)
        s = (max(page, 1) - 1) * max(page_size, 1)
        candidates = {"total": total, "items": items[s:s + max(page_size, 1)]}
    else:
        # 候选 = 全部轨迹（复用 trace_service 列表），标注状态附加
        from app.services import trace_service
        res = await trace_service.list_traces(
            page=max(page, 1), page_size=max(page_size, 1), domain=domain,
            search=fault_type, start=start, end=end, user=user,
        )
        anns = store.get("annotations", {})
        items = []
        for it in res["items"]:
            ann = anns.get(it.get("trace_id"))
            it["annotated"] = ann is not None
            it["annotation"] = ann
            items.append(it)
        candidates = {"total": res["total"], "items": items}

    return {"sets": sets, "candidates": candidates}


async def create_eval_set(name: str, trace_ids: list[str] | None = None,
                          filters: dict | None = None, description: str = "",
                          user=None) -> dict:
    """创建/保存评估集（trace_ids 为权威快照；filters 仅作来源记录）"""
    name = (name or "").strip()
    if not name:
        raise ValueError("评估集名称不能为空")
    ids: list[str] = []
    for t in (trace_ids or []):
        if _parse_trace_id(t) is not None and t not in ids:
            ids.append(t)

    store = _read_store()
    s = {
        "id": "es-" + uuid.uuid4().hex[:12],
        "name": name,
        "description": (description or "").strip(),
        "trace_ids": ids,
        "filters": filters or {},
        "created_by": _username(user),
        "created_at": _now(),
    }
    store.setdefault("eval_sets", []).append(s)
    _write_store(store)
    return _public_set(s)


async def _resolve_ids(store: dict, set_id: str | None, trace_ids: list[str] | None,
                       filters: dict | None) -> list[str] | None:
    """返回评估集对应的 trace_id 有序列表；None 表示未指定（调用方回退到全量已标注）"""
    if set_id:
        for s in store.get("eval_sets", []):
            if s.get("id") == set_id:
                return list(s.get("trace_ids", []))
        raise ValueError(f"评估集不存在: {set_id}")
    if trace_ids is not None:
        return [t for t in trace_ids if _parse_trace_id(t) is not None]
    if filters:
        anns = store.get("annotations", {})
        f_domain = (filters or {}).get("domain", "") or ""
        f_fault = (filters or {}).get("fault_type", "") or ""
        f_start = (filters or {}).get("start", "") or ""
        f_end = (filters or {}).get("end", "") or ""
        out: list[str] = []
        for tid in anns.keys():
            tr = await _load_trace_light(tid)
            if tr is None:
                continue
            if f_domain and tr.get("domain") != f_domain:
                continue
            if f_fault and tr.get("fault_type") != f_fault:
                continue
            if f_start or f_end:
                if not _in_range(tr.get("started_at", ""), f_start, f_end):
                    continue
            out.append(tid)
        return out
    return None


async def run_eval(set_id: str | None = None, trace_ids: list[str] | None = None,
                   filters: dict | None = None, partial_weight: float = 0.5,
                   user=None) -> dict:
    """回放评分：用已标注结果对当前轨迹数据算指标（不真跑 AI），并把结果写 last_run"""
    store = _read_store()
    annotations = store.get("annotations", {})
    ids = await _resolve_ids(store, set_id, trace_ids, filters)
    if ids is None:
        ids = list(annotations.keys())

    # 去重 + 保序
    seen: set[str] = set()
    ordered: list[str] = []
    for t in ids:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    set_name = ""
    if set_id:
        for s in store.get("eval_sets", []):
            if s.get("id") == set_id:
                set_name = s.get("name", "")
                break

    rows: list[dict] = []
    for tid in ordered:
        tr = await _load_trace_light(tid)
        if tr is None:
            rows.append({"trace_id": tid, "target": "", "domain": "", "fault_type": "",
                         "judgment": None, "score": None, "skipped_reason": "轨迹不存在"})
            continue
        ann = annotations.get(tid)
        if ann is None:
            row = score_trace(None, tr, partial_weight)
            row["skipped_reason"] = "未标注"
            rows.append(row)
        else:
            rows.append(score_trace(ann, tr, partial_weight))

    summary = compute_metrics(rows, partial_weight)
    summary["set_id"] = set_id
    summary["set_name"] = set_name
    summary["filters"] = filters or {}
    summary["resolved_count"] = len(ordered)

    result = {
        "summary": summary,
        "items": rows,
        "run_at": _now(),
        "run_by": _username(user),
    }
    store["last_run"] = result
    _write_store(store)
    return result


def get_last_run() -> dict | None:
    return _read_store().get("last_run")
