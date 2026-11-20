# -*- coding: utf-8 -*-
"""诊断质量评估 API（R42 二期）

标注 → 评估集 → 回放评分 → 指标。前缀 /api/v1/eval。
路由注册由父会话统一在 main.py 里做（本文件只定义 router，不改 main.py）。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.middleware.permission import require_any_user
from app.services import eval_service

router = APIRouter(prefix="/api/v1/eval")


class AnnotationIn(BaseModel):
    trace_id: str = Field(..., description="轨迹 id（alert-<id>）")
    judgment: str = Field(..., pattern="^(correct|partial|wrong)$", description="人工判定")
    correct_root_cause: str = ""
    expected_tools: list[str] = []
    note: str = ""


class EvalSetIn(BaseModel):
    name: str = Field(..., min_length=1)
    description: str = ""
    trace_ids: list[str] = []
    filters: dict | None = None


class RunIn(BaseModel):
    set_id: str | None = None
    trace_ids: list[str] | None = None
    filters: dict | None = None
    partial_weight: float = Field(0.5, ge=0.0, le=1.0)


@router.post("/annotations")
async def upsert_annotation(body: AnnotationIn, user=Depends(require_any_user)):
    try:
        data = await eval_service.upsert_annotation(
            body.trace_id, body.judgment, body.correct_root_cause,
            body.expected_tools, body.note, user=user,
        )
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    return {"code": 200, "message": "success", "data": data}


@router.get("/sets")
async def list_sets(
    page: int = 1,
    page_size: int = 20,
    domain: str = "",
    fault_type: str = "",
    start: str = "",
    end: str = "",
    annotated_only: bool = False,
    user=Depends(require_any_user),
):
    data = await eval_service.list_sets_and_candidates(
        page=page, page_size=page_size, domain=domain, fault_type=fault_type,
        start=start, end=end, annotated_only=annotated_only, user=user,
    )
    return {"code": 200, "message": "success", "data": data}


@router.post("/sets")
async def create_eval_set(body: EvalSetIn, user=Depends(require_any_user)):
    try:
        data = await eval_service.create_eval_set(
            body.name, body.trace_ids, body.filters, body.description, user=user,
        )
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    return {"code": 200, "message": "success", "data": data}


@router.post("/run")
async def run_eval(body: RunIn, user=Depends(require_any_user)):
    try:
        data = await eval_service.run_eval(
            set_id=body.set_id, trace_ids=body.trace_ids, filters=body.filters,
            partial_weight=body.partial_weight, user=user,
        )
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    return {"code": 200, "message": "success", "data": data}


@router.get("/summary")
async def summary(user=Depends(require_any_user)):
    data = eval_service.get_last_run()
    return {"code": 200, "message": "success", "data": data}
