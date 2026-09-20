# -*- coding: utf-8 -*-
"""RAG 检索服务：内存向量索引（矩阵化余弦）+ 知识库写入

性能优化（A1）：
- 原实现每次检索都全表取向量 + Python 逐条余弦（O(N) 且带 DB 往返）。
- 现改为：进程内缓存「归一化向量矩阵 + 条目元数据」，检索时一次矩阵乘（numpy）拿到 Top-K；
- 写入/去重后通过 invalidate_index() 失效；另加 TTL（60s）避免多实例/外部写入导致脏读。
- 未引入任何新依赖（仅 numpy，原本已用）。
"""
import logging
import time

import numpy as np
from sqlalchemy import select

from app.core.database import async_session
from app.models import KnowledgeEntry
from app.services.embedding_service import embed

logger = logging.getLogger("ai-service.rag")

# ===== 内存向量索引 =====
_INDEX_TTL_SECONDS = 60.0
_INDEX: dict = {"mat": None, "norms": None, "entries": [], "built_at": 0.0}
_index_lock = None  # asyncio.Lock，懒初始化（避免导入期绑定事件循环）


def _get_lock():
    global _index_lock
    if _index_lock is None:
        import asyncio
        _index_lock = asyncio.Lock()
    return _index_lock


def invalidate_index() -> None:
    """知识库变更后调用，让下次检索重建索引"""
    _INDEX["built_at"] = 0.0


def index_stats() -> dict:
    return {
        "count": len(_INDEX["entries"]),
        "built_at": _INDEX["built_at"],
        "ttl_seconds": _INDEX_TTL_SECONDS,
    }


async def _ensure_index() -> None:
    now = time.time()
    if _INDEX["mat"] is not None and (now - _INDEX["built_at"]) < _INDEX_TTL_SECONDS:
        return
    async with _get_lock():
        now = time.time()
        if _INDEX["mat"] is not None and (now - _INDEX["built_at"]) < _INDEX_TTL_SECONDS:
            return
        async with async_session() as session:
            rows = list((await session.execute(
                select(KnowledgeEntry).where(
                    KnowledgeEntry.vector_embedding.isnot(None),
                    KnowledgeEntry.pending_review.is_(False),
                    KnowledgeEntry.is_active.is_(True),
                )
            )).scalars())
        entries, vecs = [], []
        for e in rows:
            try:
                v = np.frombuffer(e.vector_embedding, dtype=np.float32)
            except (TypeError, ValueError):
                continue
            if v.size == 0:
                continue
            entries.append(e)
            vecs.append(v)
        if vecs:
            mat = np.vstack(vecs).astype(np.float32)
            norms = np.linalg.norm(mat, axis=1)
            norms[norms == 0] = 1e-9
            _INDEX.update({"mat": mat, "norms": norms, "entries": entries, "built_at": now})
        else:
            _INDEX.update({"mat": None, "norms": None, "entries": [], "built_at": now})
        logger.info("RAG 向量索引已重建：%d 条", len(entries))


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度（单条，保留兼容）"""
    if a.shape != b.shape or a.size == 0:
        return 0.0
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


async def search_similar(query: str, top_k: int = 5) -> list[dict]:
    """检索相似知识条目（内存矩阵 + 向量化余弦）"""
    query_vec = np.array(await embed(query), dtype=np.float32)
    await _ensure_index()
    mat = _INDEX["mat"]
    if mat is None or not _INDEX["entries"]:
        return []
    qn = np.linalg.norm(query_vec) or 1e-9
    sims = (mat @ query_vec) / (_INDEX["norms"] * qn)
    k = max(1, min(int(top_k or 5), len(_INDEX["entries"])))
    idx = np.argsort(-sims)[:k]
    out = []
    for i in idx:
        e = _INDEX["entries"][int(i)]
        out.append({
            "id": e.id,
            "fault_type": e.fault_type,
            "root_cause": e.root_cause,
            "repair_steps": e.repair_steps,
            "execution_steps": e.execution_steps or "",
            "similarity": float(sims[int(i)]),
        })
    return out


def _norm(text: str) -> str:
    """文本归一化（合并空白 + 小写），用于「解决方法一致」判定"""
    return " ".join((text or "").split()).strip().lower()


async def _find_duplicate(fault_type: str, root_cause: str, repair_steps: str, execution_steps: str = "") -> int | None:
    """知识库去重：解决方法相同只保留一条

    1. 解决方法一致（跨故障类型）：repair_steps / execution_steps 文本一致 → 重复
    2. 同类型精确匹配：同 fault_type + 相同 root_cause
    3. 语义相似：同 fault_type + 相似度 >= 0.9（embedding 可用时）
    返回已有条目 id（重复），无重复返回 None。
    """
    rs, es, rc = _norm(repair_steps), _norm(execution_steps), _norm(root_cause)
    ft = (fault_type or "").strip()

    # 1/2. 精确匹配（一次性载入比对）
    async with async_session() as session:
        entries = list((await session.execute(select(KnowledgeEntry))).scalars())
    for e in entries:
        if rs and _norm(e.repair_steps) == rs:
            return e.id  # 解决方法一模一样 → 不保留多份（跨故障类型）
        if es and _norm(e.execution_steps) == es:
            return e.id  # 可执行步骤一模一样 → 重复
        if ft and e.fault_type == ft and rc and _norm(e.root_cause) == rc:
            return e.id  # 同类型 + 同根因 → 重复

    # 3. 语义相似去重（embedding 可用时）
    try:
        results = await search_similar(f"{fault_type} {root_cause} {repair_steps}", 3)
        for r in results:
            if r.get("fault_type") == fault_type and r.get("similarity", 0) >= 0.9:
                return r["id"]
    except Exception as e:  # noqa: BLE001
        logger.debug("知识库语义去重失败（embedding 可能未配置）: %s", e)
    return None


async def dedup_existing() -> dict:
    """清理存量重复：按解决方法（repair_steps 归一化）分组，保留最早一条并合并计数。

    返回 {"removed": 删除条数, "groups": 合并组数, "total": 扫描总数}。
    """
    async with async_session() as session:
        entries = list((await session.execute(
            select(KnowledgeEntry).order_by(KnowledgeEntry.id.asc())
        )).scalars())

        groups: dict[str, list[KnowledgeEntry]] = {}
        for e in entries:
            key = _norm(e.repair_steps) or _norm(e.execution_steps) or f"__id_{e.id}"
            groups.setdefault(key, []).append(e)

        removed = 0
        merged = 0
        for key, group in groups.items():
            if key.startswith("__id_") or len(group) <= 1:
                continue
            keep = group[0]
            for dup in group[1:]:
                keep.success_count = (keep.success_count or 0) + (dup.success_count or 0)
                keep.usage_count = (keep.usage_count or 0) + (dup.usage_count or 0)
                await session.delete(dup)
                removed += 1
            merged += 1
        await session.commit()
    invalidate_index()  # 去重后失效向量索引（A1）
    logger.info("知识库去重：扫描 %d 条，合并 %d 组，删除 %d 条重复", len(entries), merged, removed)
    return {"removed": removed, "groups": merged, "total": len(entries)}


def classify_fault_type(fault_type: str) -> str:
    """故障类型 → 知识库分类：pod_crash/db/application/resource/infra/other"""
    ft = (fault_type or "").strip()
    if ft.startswith("db_"):
        return "db"
    if ft in ("CrashLoopBackOff", "OOMKilled", "ImagePullBackOff", "ErrImagePull", "PendingTimeout"):
        return "pod_crash"
    if ft in ("ContainerCpuHigh", "ContainerMemHigh"):
        return "resource"
    if ft in ("NodeNotReady", "MemoryPressure", "DiskPressure", "PIDPressure"):
        return "infra"
    if ft == "application_issue":
        return "application"
    return "other"


def is_valuable_knowledge(repair_steps: str, execution_steps: str) -> bool:
    """判断知识是否有价值：删除/重启/关闭/审批痕迹等临时操作（治标不治本）无价值

    有价值的标志：含根因修复（改配置/改代码/优化/加索引/调整参数等）。
    无价值：空方案、审批/操作痕迹（同意/已删除/已关闭等）、删除/重启/重建/关闭类临时操作。
    """
    rs = (repair_steps or "").strip()
    es = (execution_steps or "").strip()
    # 空方案 → 无价值
    if not rs and not es:
        return False
    text = f"{rs} {es}".lower()
    # 审批/操作痕迹（不是解决方案）
    op_trace = ["同意", "已删除", "已关闭", "以关闭", "已解决", "人工解决", "批准"]
    if rs in op_trace:
        return False
    value_marks = ["修改", "调整", "改配置", "改代码", "加索引", "优化", "升级", "发布",
                   "patch", "update", "set ", "increase", "decrease", "limit", "config", "参数",
                   "认证", "补", "describe", "logs", "索引", "慢sql", "慢查询"]
    if any(m in text for m in value_marks):
        return True
    no_value_marks = ["delete", "删除", "重启", "restart", "recreate", "重建", "关闭", "close"]
    if any(m in text for m in no_value_marks):
        return False
    return True  # 默认有价值（保守）


async def add_knowledge(fault_type: str, root_cause: str, repair_steps: str, tags: str = "", execution_steps: str = "",
                        source: str = "ai", is_locked: bool = True) -> int:
    """新增知识条目（含向量嵌入 + 可执行修复步骤）

    source=ai（AI 自动沉淀，默认锁定）/ manual（人工录入，不锁定）
    embedding 失败时降级：仍存文本条目（向量置空），不阻塞知识沉淀；
    向量检索在 embedding 配好后对新条目自动生效。
    去重：同类型错误 + 相同解决方法只保留一条（返回已有条目 id）。
    价值过滤：AI 沉淀时，删除/重启/关闭等无价值解决方式不落盘（返回 0）。
    """
    # 价值过滤（仅 AI 自动沉淀；人工录入不拦截）
    if source == "ai" and not is_valuable_knowledge(repair_steps, execution_steps):
        logger.info("知识库过滤：无价值解决方式（删除/重启/关闭），不落盘")
        return 0

    # 去重：同类型同解决方法只保留一条
    dup_id = await _find_duplicate(fault_type, root_cause, repair_steps, execution_steps)
    if dup_id:
        logger.info("知识库去重：同类型同解决方法已存在（id=%d），不重复落盘", dup_id)
        return dup_id

    text = f"{root_cause} {repair_steps}"
    try:
        vec = np.array(await embed(text), dtype=np.float32)
        bytes_embedding = vec.tobytes()  # 384 * 4 = 1536 字节（维度随模型实际输出）
    except Exception as e:  # noqa: BLE001
        logger.warning("知识条目向量化失败，降级为无向量存储: %s", e)
        bytes_embedding = None

    async with async_session() as session:
        entry = KnowledgeEntry(
            fault_type=fault_type,
            root_cause=root_cause,
            repair_steps=repair_steps,
            execution_steps=execution_steps,
            tags=tags,
            category=classify_fault_type(fault_type),
            source=source,
            is_locked=is_locked,
            vector_embedding=bytes_embedding,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        invalidate_index()  # 新增后失效向量索引（A1）
        return entry.id


async def inject_similar_cases(alert_data: dict, top_k: int = 3) -> str:
    """检索相似案例，格式化为可注入诊断提示词的上下文文本"""
    query = f"{alert_data.get('fault_type', '')} {alert_data.get('pod_name', alert_data.get('node_name', ''))}"
    results = await search_similar(query, top_k)
    if not results:
        return ""
    lines = ["【相似历史案例】"]
    for r in results:
        lines.append(f"- 故障类型：{r['fault_type']}（相似度 {r['similarity']:.2f}）\n  根因：{r['root_cause']}\n  修复：{r['repair_steps']}")
    return "\n".join(lines)
