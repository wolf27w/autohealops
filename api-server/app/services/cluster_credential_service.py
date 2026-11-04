# -*- coding: utf-8 -*-
"""集群凭据到期治理（2026-11-04）

历史问题：`cred_type` / `cred_expires_at` 是后加的能力，之前接入的集群从未回填，
集群管理页「凭据有效期」一直显示「未知」，D1 的到期通知也扫不到。

这里在启动时做一次幂等补齐（只处理 cred_type=unknown 且有 kubeconfig 的集群），
后续新建/替换 kubeconfig 的路径本身会实时解析，不依赖本补齐。
"""
import logging

from sqlalchemy import select

from app.core.database import async_session
from app.core.security import decrypt_aes256_gcm
from app.models import Cluster

logger = logging.getLogger(__name__)


async def backfill_missing_credentials() -> int:
    """补齐缺失的凭据类型/到期时间，返回更新条数"""
    # 延迟导入：clusters 路由模块依赖较重，避免与 main 的导入顺序打架
    from app.api.clusters import _kubeconfig_credential

    updated = 0
    async with async_session() as session:
        rows = (await session.execute(select(Cluster).where(Cluster.cred_type == "unknown"))).scalars().all()
        for c in rows:
            if not c.kubeconfig_encrypted:
                continue  # agent / in-cluster 接入本来就没有 kubeconfig
            try:
                kc = decrypt_aes256_gcm(c.kubeconfig_encrypted)
            except Exception as e:  # noqa: BLE001
                logger.warning("集群 %s kubeconfig 解密失败，跳过凭据补齐: %s", c.id, e)
                continue
            cred_type, expires = _kubeconfig_credential(kc)
            if cred_type == "unknown":
                continue
            c.cred_type = cred_type
            c.cred_expires_at = expires
            updated += 1
        if updated:
            await session.commit()
    return updated
