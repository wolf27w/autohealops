# -*- coding: utf-8 -*-
"""巡检/诊断报告下载 API

集群巡检（DiagnosticReport.task_type="cluster"）与数据库诊断/巡检（task_type="db"）
统一下载为 Markdown 文件（可另存/归档）。
"""
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select

from app.core.database import async_session
from app.middleware.permission import require_any_user
from app.models import Cluster, DiagnosticReport

router = APIRouter(prefix="/api/v1/inspection-reports")

_REPORT_TYPE_LABEL = {"cluster": "集群巡检报告", "db": "数据库巡检报告", "app": "应用诊断报告"}


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def render_report_markdown(report: DiagnosticReport, cluster_name: str = "") -> str:
    """把报告渲染成 Markdown 文本（集群/数据库通用）"""
    label = _REPORT_TYPE_LABEL.get(report.task_type, "巡检报告")
    try:
        raw = json.loads(report.raw_output or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    lines: list[str] = [f"# {label} #{report.id}", ""]
    lines.append(f"- 报告类型：{report.task_type}")
    lines.append(f"- 生成时间：{report.created_at}")
    if report.task_type == "cluster":
        lines.append(f"- 集群：{cluster_name or raw.get('cluster_name') or raw.get('cluster_id') or '-'}")
        lines.append(f"- 巡检时间：{raw.get('inspected_at', '-')}")
        lines.append(f"- 健康度：{raw.get('health', '-')}")
    else:
        lines.append(f"- 数据库：{raw.get('db_name', '-')}（{raw.get('db_type', '-')}）")
        health = str(raw.get("health") or "")
        if health and "\n" not in health:
            lines.append(f"- 健康度：{health}")
        elif health:
            lines.append("")
            lines += ["## 健康指标", "", "```", health, "```", ""]
    lines.append(f"- 结论：{report.conclusion or '-'}")
    lines.append("")

    lines += ["## 结论", "", report.conclusion or "-", ""]
    if report.root_cause:
        lines += ["## 根因", "", report.root_cause, ""]
    if report.suggestion:
        lines += ["## 优化建议", "", report.suggestion, ""]

    items = raw.get("items")
    if isinstance(items, list) and items:
        lines += ["## 巡检项", ""]
        rows = []
        for it in items:
            if isinstance(it, dict):
                rows.append([
                    it.get("category", ""),
                    it.get("name", ""),
                    str(it.get("value", "")),
                    "正常" if it.get("ok", True) else "异常",
                    it.get("detail", ""),
                ])
        lines.append(_md_table(["分类", "项", "值", "状态", "说明"], rows))
        lines.append("")

    fault_top = raw.get("fault_top")
    if isinstance(fault_top, list) and fault_top:
        lines += ["## 故障 Top", ""]
        rows = [[str(f.get("fault_type", "")), str(f.get("count", ""))] for f in fault_top if isinstance(f, dict)]
        lines.append(_md_table(["故障类型", "次数"], rows))
        lines.append("")

    stats = raw.get("stats")
    if isinstance(stats, dict) and stats:
        lines += ["## 统计", ""]
        lines.append(_md_table(["指标", "值"], [[k, str(v)] for k, v in stats.items()]))
        lines.append("")

    if raw.get("slow_sql"):
        lines += ["## 慢 SQL", "", "```", str(raw["slow_sql"]), "```", ""]
    if raw.get("ai_raw"):
        lines += ["## AI 分析原文", "", str(raw["ai_raw"]), ""]

    lines += ["## 原始数据（JSON）", "", "```json", json.dumps(raw, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


@router.get("/{report_id}/download")
async def download_report(report_id: int, user=Depends(require_any_user)):
    """下载巡检/诊断报告为 Markdown 文件"""
    async with async_session() as session:
        report = await session.get(DiagnosticReport, report_id)
        if not report:
            return Response(content="报告不存在", status_code=404, media_type="text/plain; charset=utf-8")
        cluster_name = ""
        if report.task_type == "cluster":
            try:
                raw = json.loads(report.raw_output or "{}")
                cid = raw.get("cluster_id")
                if cid:
                    c = await session.get(Cluster, int(cid))
                    cluster_name = c.name if c else ""
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    md = render_report_markdown(report, cluster_name)
    label = _REPORT_TYPE_LABEL.get(report.task_type, "report")
    date = (str(report.created_at) or "").replace(":", "").replace(" ", "-")[:15]
    filename = f"{report.task_type}-{label}-{report.id}-{date}.md"
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return Response(content=md.encode("utf-8"), media_type="text/markdown; charset=utf-8", headers=headers)
