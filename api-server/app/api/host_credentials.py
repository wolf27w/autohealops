# -*- coding: utf-8 -*-
"""私钥/凭据集中托管 API（R43 H2）：admin 增删改，operator 可读（只回 has_* 标志）。"""
import json

from fastapi import APIRouter, Depends, HTTPException

from app.core.security import generate_trace_id
from app.api.hosts import require_host_admin
from app.middleware.permission import require_operator
from app.models import User
from app.services import host_credential_service as hcs
from app.services.audit_service import create_audit_record

router = APIRouter(prefix="/api/v1/host-credentials")


async def _audit(trace_id: str, detail: str, snapshot: dict) -> None:
    await create_audit_record(
        trace_id,
        trigger_snapshot=json.dumps(snapshot, ensure_ascii=False),
        execution_detail=detail,
    )


@router.get("")
async def list_credentials(user=Depends(require_operator)):
    data = await hcs.list_credentials()
    return {"code": 200, "message": "success", "data": data}


@router.post("")
async def create_credential(payload: dict, user: User = Depends(require_host_admin)):  # 2026-11-23 R62
    try:
        row = await hcs.create_credential(payload, user.id)
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    await _audit(generate_trace_id(), f"创建凭据：{row['name']}",
                 {"action": "create_credential", "id": row["id"]})
    return {"code": 200, "message": "success", "data": row}


@router.put("/{credential_id}")
async def update_credential(credential_id: int, payload: dict, user: User = Depends(require_host_admin)):  # 2026-11-23 R62
    try:
        row = await hcs.update_credential(credential_id, payload)
    except KeyError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    except ValueError as e:
        return {"code": 400, "message": str(e), "data": None}
    await _audit(generate_trace_id(), f"修改凭据：{row['name']}",
                 {"action": "update_credential", "id": credential_id})
    return {"code": 200, "message": "success", "data": row}


@router.delete("/{credential_id}")
async def delete_credential(credential_id: int, user: User = Depends(require_host_admin)):  # 2026-11-23 R62
    try:
        ok, msg = await hcs.delete_credential(credential_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="凭据不存在")
    if not ok:
        return {"code": 400, "message": msg, "data": None}
    await _audit(generate_trace_id(), f"删除凭据：{credential_id}",
                 {"action": "delete_credential", "id": credential_id})
    return {"code": 200, "message": "success", "data": {"id": credential_id}}
