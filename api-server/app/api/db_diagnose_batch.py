# -*- coding: utf-8 -*-
"""数据库批量诊断 API：多实例批量发起诊断（诊断中心「跨库批量诊断」）。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.middleware.permission import require_any_user
from app.services.db_diagnose_batch_service import diagnose_db_batch

router = APIRouter(prefix="/api/v1/db-diagnosis")


class BatchDiagnoseRequest(BaseModel):
    conn_ids: list[int] = []


@router.post("/batch")
async def batch_diagnose(payload: BatchDiagnoseRequest, user=Depends(require_any_user)):
    """批量发起数据库诊断：逐实例鉴权 + 逐实例诊断，单实例失败不中断整批。

    返回逐实例结果（success/failed/skipped + 原因），权限与单实例 /db-diagnosis/{conn_id}/run 一致。
    """
    return await diagnose_db_batch(payload.conn_ids, user)
