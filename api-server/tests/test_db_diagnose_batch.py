# -*- coding: utf-8 -*-
"""批量诊断编排逻辑纯单元测试（不连真实库 / 不触发 AI）。

运行：
    cd api-server && ./.venv/bin/python -m unittest tests.test_db_diagnose_batch -v
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.db_diagnose_batch_service import (  # noqa: E402
    MAX_BATCH,
    diagnose_db_batch,
    normalize_conn_ids,
)


def _user(role="operator"):
    return SimpleNamespace(id=1, role=role)


# 默认鉴权：全部放行
async def _allow(_user, _conn_id, _required="read"):
    return True


# 默认诊断：全部成功
async def _ok(conn_id, _user):
    return {"code": 200, "message": "success",
            "data": {"report_id": conn_id * 100, "conclusion": f"实例 {conn_id} 诊断完成"}}


class TestNormalize(unittest.TestCase):
    def test_empty(self):
        ids, err = normalize_conn_ids([])
        self.assertEqual(ids, [])
        self.assertIn("请选择", err)

    def test_none(self):
        _, err = normalize_conn_ids(None)
        self.assertIsNotNone(err)

    def test_not_list(self):
        _, err = normalize_conn_ids("1,2")
        self.assertIsNotNone(err)

    def test_dedup_and_int_coerce(self):
        ids, err = normalize_conn_ids([1, "2", 1, 2, "3"])
        self.assertIsNone(err)
        self.assertEqual(ids, [1, 2, 3])

    def test_invalid_value(self):
        _, err = normalize_conn_ids([1, "abc"])
        self.assertIsNotNone(err)

    def test_bool_rejected(self):
        _, err = normalize_conn_ids([1, True])
        self.assertIsNotNone(err)

    def test_non_positive_rejected(self):
        _, err = normalize_conn_ids([0, -1])
        self.assertIsNotNone(err)

    def test_over_limit(self):
        _, err = normalize_conn_ids(list(range(1, MAX_BATCH + 2)))
        self.assertIsNotNone(err)


class TestBatchOrchestration(unittest.IsolatedAsyncioTestCase):
    async def test_all_success(self):
        r = await diagnose_db_batch([1, 2, 3], _user(), check_permission=_allow, diagnose=_ok)
        self.assertEqual(r["code"], 200)
        d = r["data"]
        self.assertEqual((d["total"], d["success"], d["failed"], d["skipped"]), (3, 3, 0, 0))
        self.assertEqual([x["status"] for x in d["results"]], ["success"] * 3)
        self.assertEqual([x["report_id"] for x in d["results"]], [100, 200, 300])

    async def test_partial_success(self):
        async def mixed(conn_id, _user):
            if conn_id == 2:
                return {"code": 500, "message": "AI 诊断失败: 超时", "data": None}
            if conn_id == 3:
                return {"code": 400, "message": "该数据库巡检开关已关闭", "data": None}
            return {"code": 200, "message": "success",
                    "data": {"report_id": conn_id * 100, "conclusion": "ok"}}

        r = await diagnose_db_batch([1, 2, 3, 4], _user(), check_permission=_allow, diagnose=mixed)
        d = r["data"]
        self.assertEqual((d["total"], d["success"], d["failed"], d["skipped"]), (4, 2, 1, 1))
        by_id = {x["conn_id"]: x for x in d["results"]}
        self.assertEqual(by_id[1]["status"], "success")
        self.assertEqual(by_id[2]["status"], "failed")
        self.assertEqual(by_id[3]["status"], "skipped")
        self.assertEqual(by_id[4]["status"], "success")

    async def test_all_failed(self):
        async def boom(_conn_id, _user):
            return {"code": 500, "message": "boom", "data": None}

        r = await diagnose_db_batch([1, 2], _user(), check_permission=_allow, diagnose=boom)
        d = r["data"]
        self.assertEqual((d["total"], d["success"], d["failed"], d["skipped"]), (2, 0, 2, 0))

    async def test_permission_denied_skipped(self):
        async def deny(_user, conn_id, _required="read"):
            return conn_id == 2  # 只有 2 有权限

        r = await diagnose_db_batch([1, 2, 3], _user(), check_permission=deny, diagnose=_ok)
        d = r["data"]
        by_id = {x["conn_id"]: x for x in d["results"]}
        self.assertEqual(by_id[1]["status"], "skipped")
        self.assertEqual(by_id[1]["message"], "无权限访问该数据库连接")
        self.assertEqual(by_id[2]["status"], "success")
        self.assertEqual(by_id[3]["status"], "skipped")
        self.assertEqual((d["total"], d["success"], d["failed"], d["skipped"]), (3, 1, 0, 2))

    async def test_empty_selection_400(self):
        r = await diagnose_db_batch([], _user(), check_permission=_allow, diagnose=_ok)
        self.assertEqual(r["code"], 400)
        self.assertIsNone(r["data"])

    async def test_exception_does_not_abort_batch(self):
        calls = []

        async def flaky(conn_id, _user):
            calls.append(conn_id)
            if conn_id == 1:
                raise RuntimeError("连接被拒")
            return {"code": 200, "message": "success", "data": {"report_id": conn_id}}

        r = await diagnose_db_batch([1, 2, 3], _user(), check_permission=_allow, diagnose=flaky)
        d = r["data"]
        # 3 个都执行了，1 失败但 2/3 继续
        self.assertEqual(calls, [1, 2, 3])
        by_id = {x["conn_id"]: x for x in d["results"]}
        self.assertEqual(by_id[1]["status"], "failed")
        self.assertEqual(by_id[1]["message"], "连接被拒")
        self.assertEqual(by_id[2]["status"], "success")
        self.assertEqual(by_id[3]["status"], "success")
        self.assertEqual((d["total"], d["success"], d["failed"], d["skipped"]), (3, 2, 1, 0))

    async def test_404_skipped(self):
        async def missing(conn_id, _user):
            return {"code": 404, "message": "数据库连接不存在", "data": None}

        r = await diagnose_db_batch([99], _user(), check_permission=_allow, diagnose=missing)
        d = r["data"]
        self.assertEqual(d["results"][0]["status"], "skipped")
        self.assertEqual(d["results"][0]["message"], "数据库连接不存在")


if __name__ == "__main__":
    unittest.main()
