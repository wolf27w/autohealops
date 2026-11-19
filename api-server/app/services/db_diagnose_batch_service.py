# -*- coding: utf-8 -*-
"""跨库批量诊断编排：多实例逐条鉴权 + 发起诊断，单实例失败不中断整批。

编排逻辑与真实 DB / AI 解耦：`check_permission` / `diagnose` 通过参数注入，便于纯逻辑单测
（不连真实库、不触发 AI）。生产调用时默认走 user_permission_service.check_db_permission 与
db_diagnosis_service.diagnose_db，口径与单实例 `/db-diagnosis/{conn_id}/run` 完全一致。
"""
from app.core.logging import get_logger

logger = get_logger("api-server.db_diagnose_batch")

# 单次批量上限，防止一次请求打爆 AI 服务 / 数据库
MAX_BATCH = 50


def normalize_conn_ids(conn_ids) -> tuple[list[int], str | None]:
    """校验并去重 conn_ids。返回 (ids, error_msg)；error_msg 非空表示非法输入。"""
    if conn_ids is None:
        return [], "请选择要诊断的数据库实例"
    if not isinstance(conn_ids, (list, tuple, set)):
        return [], "conn_ids 必须是数组"
    ids: list[int] = []
    seen: set[int] = set()
    for raw in conn_ids:
        # bool 是 int 子类，需显式排除
        if isinstance(raw, bool):
            return [], "conn_ids 含非法值"
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            return [], "conn_ids 含非法值"
        if cid <= 0:
            return [], "conn_ids 含非法值"
        if cid not in seen:
            seen.add(cid)
            ids.append(cid)
    if not ids:
        return [], "请选择要诊断的数据库实例"
    if len(ids) > MAX_BATCH:
        return [], f"单次最多诊断 {MAX_BATCH} 个实例"
    return ids, None


async def diagnose_db_batch(conn_ids, user, *, check_permission=None, diagnose=None) -> dict:
    """批量诊断编排。

    Args:
        conn_ids: 数据库连接 id 列表（int 或可 int 化的值）。
        user: 当前用户（SimpleNamespace / ORM 对象），用于鉴权。
        check_permission(user, conn_id, "read") -> bool：鉴权回调，默认 check_db_permission。
        diagnose(conn_id, user) -> dict：单实例诊断回调，默认 db_diagnosis_service.diagnose_db。

    Returns:
        {"code": 200, "message": "success", "data": {
            "total": N, "success": N, "failed": N, "skipped": N,
            "results": [{"conn_id": int, "status": "success|failed|skipped",
                         "message": str, "report_id": int|None}]
        }}
    """
    ids, err = normalize_conn_ids(conn_ids)
    if err:
        return {"code": 400, "message": err, "data": None}

    if check_permission is None:
        from app.services.user_permission_service import check_db_permission
        check_permission = check_db_permission
    if diagnose is None:
        from app.services.db_diagnosis_service import diagnose_db
        diagnose = diagnose_db

    results: list[dict] = []
    for cid in ids:
        item: dict = {"conn_id": cid, "status": "success", "message": "", "report_id": None}
        try:
            # 口径与单实例接口一致：read 权限；admin 由 check_db_permission 内部放行。
            if user is not None and not await check_permission(user, cid, "read"):
                item["status"] = "skipped"
                item["message"] = "无权限访问该数据库连接"
                results.append(item)
                continue

            resp = await diagnose(cid, user)
            code = resp.get("code") if isinstance(resp, dict) else 500
            msg = (resp.get("message") or "") if isinstance(resp, dict) else "诊断返回异常"
            if code == 200:
                data = resp.get("data") or {}
                item["report_id"] = data.get("report_id")
                item["message"] = data.get("conclusion") or "诊断完成"
                item["status"] = "success"
            elif code == 404:
                item["status"] = "skipped"
                item["message"] = msg or "数据库连接不存在"
            elif code == 400:
                # 巡检开关关闭等「不满足诊断条件」按跳过处理，不算失败
                item["status"] = "skipped"
                item["message"] = msg or "跳过：不满足诊断条件"
            else:
                item["status"] = "failed"
                item["message"] = msg or f"诊断失败（code={code}）"
        except Exception as e:  # noqa: BLE001 —— 单实例异常不得中断整批
            logger.warning("批量诊断 conn=%s 异常: %s", cid, e)
            item["status"] = "failed"
            item["message"] = str(e)
        results.append(item)

    total = len(results)
    success = sum(1 for r in results if r["status"] == "success")
    failed = sum(1 for r in results if r["status"] == "failed")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    return {"code": 200, "message": "success", "data": {
        "total": total,
        "success": success,
        "failed": failed,
        "skipped": skipped,
        "results": results,
    }}
