# -*- coding: utf-8 -*-
"""审计服务：SHA256 哈希链防篡改"""
import hashlib
import json

from sqlalchemy import select

from app.core.database import async_session
from app.core.security import generate_decision_id
from app.models import AuditRecord, Cluster


def _parse_business(trigger_snapshot: str) -> dict:
    """从 trigger_snapshot JSON 提取业务维度（集群/命名空间/资源）"""
    try:
        d = json.loads(trigger_snapshot) if trigger_snapshot else {}
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(d, dict):
        return {}
    return {
        "cluster_id": d.get("cluster_id"),
        "alert_id": d.get("alert_id"),
        "namespace": d.get("namespace", ""),
        "resource": d.get("resource", ""),
        "resource_type": d.get("resource_type", ""),
        "fault_type": d.get("fault_type", ""),
    }


def _hash(previous_hash: str, data: str) -> str:
    """计算哈希链节点值"""
    return hashlib.sha256((previous_hash + data).encode("utf-8")).hexdigest()


def _serialize(**fields) -> str:
    """序列化审计字段（按固定顺序拼接）"""
    parts = [
        fields.get("trigger_snapshot", ""),
        fields.get("ai_call_detail", ""),
        fields.get("decision_detail", ""),
        fields.get("execution_detail", ""),
        fields.get("verification_detail", ""),
    ]
    return json.dumps(parts, ensure_ascii=False, sort_keys=True)


async def create_audit_record(trace_id: str, **fields) -> str:
    """创建审计记录，返回 decision_id"""
    async with async_session() as session:
        # 查询最后一条记录的 hash_value 作为 previous_hash（行锁防并发分叉）
        result = await session.execute(
            select(AuditRecord).order_by(AuditRecord.id.desc()).limit(1).with_for_update()
        )
        last = result.scalar_one_or_none()
        previous_hash = last.hash_value if last else ""
        data = _serialize(**fields)
        hash_value = _hash(previous_hash, data)
        decision_id = generate_decision_id()
        record = AuditRecord(
            decision_id=decision_id,
            trace_id=trace_id,
            trigger_snapshot=fields.get("trigger_snapshot", ""),
            ai_call_detail=fields.get("ai_call_detail", ""),
            decision_detail=fields.get("decision_detail", ""),
            execution_detail=fields.get("execution_detail", ""),
            verification_detail=fields.get("verification_detail", ""),
            hash_value=hash_value,
            previous_hash=previous_hash,
        )
        session.add(record)
        await session.commit()
        return decision_id


async def update_audit_record(trace_id: str, field: str, data: str) -> None:
    """更新审计记录字段并重算 hash"""
    async with async_session() as session:
        result = await session.execute(select(AuditRecord).where(AuditRecord.trace_id == trace_id).order_by(AuditRecord.id.desc()).limit(1))
        record = result.scalar_one_or_none()
        if not record:
            return
        if field in ("trigger_snapshot", "ai_call_detail", "decision_detail", "execution_detail", "verification_detail"):
            setattr(record, field, data)
            data_str = _serialize(
                trigger_snapshot=record.trigger_snapshot,
                ai_call_detail=record.ai_call_detail,
                decision_detail=record.decision_detail,
                execution_detail=record.execution_detail,
                verification_detail=record.verification_detail,
            )
            record.hash_value = _hash(record.previous_hash, data_str)
        await session.commit()


async def verify_audit_chain() -> dict:
    """验证整条哈希链完整性"""
    async with async_session() as session:
        result = await session.execute(select(AuditRecord).order_by(AuditRecord.id.asc()))
        records = list(result.scalars())
    total = len(records)
    verified = 0
    previous = ""
    integrity_ok = True
    for r in records:
        data_str = _serialize(
            trigger_snapshot=r.trigger_snapshot,
            ai_call_detail=r.ai_call_detail,
            decision_detail=r.decision_detail,
            execution_detail=r.execution_detail,
            verification_detail=r.verification_detail,
        )
        if r.hash_value == _hash(previous, data_str) and r.previous_hash == previous:
            verified += 1
        else:
            integrity_ok = False
        previous = r.hash_value
    return {"total_records": total, "verified": verified, "integrity_ok": integrity_ok}


async def get_trace(trace_id: str) -> list[dict]:
    """查询某 trace_id 的审计链"""
    async with async_session() as session:
        result = await session.execute(select(AuditRecord).where(AuditRecord.trace_id == trace_id).order_by(AuditRecord.id.asc()))
        records = list(result.scalars())
    return [
        {
            "decision_id": r.decision_id, "trace_id": r.trace_id,
            "trigger_snapshot": r.trigger_snapshot, "ai_call_detail": r.ai_call_detail,
            "decision_detail": r.decision_detail, "execution_detail": r.execution_detail,
            "verification_detail": r.verification_detail, "hash_value": r.hash_value,
            "previous_hash": r.previous_hash, "created_at": str(r.created_at),
        }
        for r in records
    ]


async def search_audit(trace_id: str = "", start_time=None, end_time=None, page: int = 1, page_size: int = 20,
                      cluster_id: int | None = None, namespace: str = "", resource: str = "") -> dict:
    """搜索审计记录；返回业务维度（集群/命名空间/资源），便于识别"""
    from sqlalchemy import and_

    async with async_session() as session:
        query = select(AuditRecord)
        conditions = []
        if trace_id:
            conditions.append(AuditRecord.trace_id == trace_id)
        if start_time:
            conditions.append(AuditRecord.created_at >= start_time)
        if end_time:
            conditions.append(AuditRecord.created_at <= end_time)
        if conditions:
            query = query.where(and_(*conditions))
        query = query.order_by(AuditRecord.id.desc())
        result = await session.execute(query)
        records = list(result.scalars())

        # 集群 id → name 映射（旧记录可能只有 alert_id，需从 Alert 反查 cluster_id）
        biz_list = [_parse_business(r.trigger_snapshot) for r in records]
        alert_cluster_map: dict[int, int] = {}
        cluster_ids = {b.get("cluster_id") for b in biz_list if b.get("cluster_id")}
        alert_ids = {b.get("alert_id") for b in biz_list if b.get("alert_id") and not b.get("cluster_id")}
        if alert_ids:
            from app.models import Alert
            aresult = await session.execute(select(Alert.id, Alert.cluster_id).where(Alert.id.in_(alert_ids)))
            for aid, cid in aresult.all():
                if cid:
                    alert_cluster_map[aid] = cid
                    cluster_ids.add(cid)
        cluster_names: dict[int, str] = {}
        if cluster_ids:
            cresult = await session.execute(select(Cluster).where(Cluster.id.in_(cluster_ids)))
            cluster_names = {c.id: c.name for c in cresult.scalars()}

    # Python 侧业务维度筛选 + 组装
    items = []
    for r, biz in zip(records, biz_list):
        cid = biz.get("cluster_id")
        if cid is None and biz.get("alert_id"):
            cid = alert_cluster_map.get(biz["alert_id"])
        if cluster_id is not None and cid != cluster_id:
            continue
        if namespace and namespace not in biz.get("namespace", ""):
            continue
        if resource and resource not in biz.get("resource", ""):
            continue
        items.append({
            "decision_id": r.decision_id, "trace_id": r.trace_id, "hash_value": r.hash_value,
            "created_at": str(r.created_at),
            "cluster_id": cid,
            "cluster_name": cluster_names.get(cid, "") if cid else "",
            "namespace": biz.get("namespace", ""),
            "resource": biz.get("resource", ""),
            "resource_type": biz.get("resource_type", ""),
            "fault_type": biz.get("fault_type", ""),
        })

    total = len(items)
    start = (page - 1) * page_size
    return {"total": total, "items": items[start:start + page_size]}
