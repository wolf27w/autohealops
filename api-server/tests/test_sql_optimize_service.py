# -*- coding: utf-8 -*-
"""慢 SQL 逐条优化诊断服务单测（2026-11-18）

覆盖：mysql/pg/oracle 执行计划、非查询 SQL 跳过、异常兜底、索引聚合、
表名粗提取、AI 成功 / AI 不可用兜底、缓存命中、digest 未找到。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_sql_optimize_service -v
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import sql_optimize_service as svc


def _conn(db_type="mysql", cid=1, database=""):
    return SimpleNamespace(id=cid, db_type=db_type, database=database)


def _empty_run():
    return AsyncMock(return_value={"columns": [], "rows": []})


def _no_redis():
    return SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock(return_value=True))


class TestHeadAndStrip(unittest.TestCase):
    def test_strip_semicolon(self):
        self.assertEqual(svc._strip_semicolon("SELECT 1;"), "SELECT 1")
        self.assertEqual(svc._strip_semicolon("SELECT 1;;;  "), "SELECT 1")
        self.assertEqual(svc._strip_semicolon("SELECT 1"), "SELECT 1")

    def test_head_keyword_basic(self):
        self.assertEqual(svc._head_keyword("SELECT * FROM t"), "select")
        self.assertEqual(svc._head_keyword("  WITH x AS (SELECT 1) SELECT * FROM x"), "with")

    def test_head_keyword_ignores_comments(self):
        self.assertEqual(svc._head_keyword("-- 说明\nSELECT * FROM t"), "select")
        self.assertEqual(svc._head_keyword("/* 块注释 */ SELECT * FROM t"), "select")

    def test_head_keyword_non_query(self):
        self.assertEqual(svc._head_keyword("UPDATE t SET a=1"), "update")
        self.assertEqual(svc._head_keyword(""), "")


class TestExtractTables(unittest.TestCase):
    def test_basic_join(self):
        names = [t[1] for t in svc._extract_tables(
            "SELECT * FROM users u JOIN orders o ON u.id = o.uid")]
        self.assertIn("users", names)
        self.assertIn("orders", names)

    def test_schema_qualified(self):
        tables = svc._extract_tables("SELECT * FROM app.users u")
        self.assertEqual(tables[0], ("app", "users"))

    def test_backtick_schema(self):
        tables = svc._extract_tables("SELECT * FROM `db`.`users`")
        self.assertEqual(tables[0], ("db", "users"))

    def test_ignores_subquery_alias(self):
        tables = svc._extract_tables("SELECT * FROM (SELECT id FROM users) t")
        names = [t[1] for t in tables]
        self.assertNotIn("t", names)
        self.assertIn("users", names)

    def test_update_and_into(self):
        names = [t[1] for t in svc._extract_tables("UPDATE users SET a=1; INSERT INTO orders (id) VALUES (1)")]
        self.assertIn("users", names)
        self.assertIn("orders", names)

    def test_max_eight_tables(self):
        sql = " ".join(f"FROM t{i} x{i}" for i in range(20))
        self.assertLessEqual(len(svc._extract_tables(sql)), 8)


class TestExplain(unittest.IsolatedAsyncioTestCase):
    async def test_mysql_explain(self):
        fake_run = AsyncMock(return_value={
            "columns": ["id", "select_type", "table", "type", "key"],
            "rows": [[1, "SIMPLE", "users", "ALL", None]],
        })
        with patch.object(svc, "_run_sql", fake_run):
            out = await svc._explain(_conn("mysql"), "SELECT * FROM users WHERE id = 1;")
        self.assertTrue(out["available"])
        self.assertEqual(out["mode"], "explain")
        self.assertEqual(out["db_type"], "mysql")
        self.assertEqual(out["rows"][0]["table"], "users")
        called = fake_run.await_args[0][1]
        self.assertTrue(called.startswith("EXPLAIN "))
        self.assertNotIn(";", called)

    async def test_pg_explain_json(self):
        plan = [{"Plan": {"Node Type": "Seq Scan", "Relation Name": "users"}}]
        fake_run = AsyncMock(return_value={
            "columns": ["QUERY PLAN"], "rows": [[json.dumps(plan)]],
        })
        with patch.object(svc, "_run_sql", fake_run):
            out = await svc._explain(_conn("pg"), "SELECT * FROM users")
        self.assertTrue(out["available"])
        self.assertEqual(out["mode"], "explain")
        self.assertEqual(out["rows"], plan)
        self.assertIn("FORMAT JSON", fake_run.await_args[0][1])

    async def test_oracle_skipped(self):
        out = await svc._explain(_conn("oracle"), "SELECT * FROM users")
        self.assertFalse(out["available"])
        self.assertEqual(out["mode"], "skipped")
        self.assertIn("Oracle", out["error"])

    async def test_non_query_skipped(self):
        out = await svc._explain(_conn("mysql"), "UPDATE users SET name='x'")
        self.assertFalse(out["available"])
        self.assertEqual(out["mode"], "skipped")
        self.assertEqual(out["error"], "非查询语句跳过执行计划")

    async def test_explain_exception_graceful(self):
        fake_run = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(svc, "_run_sql", fake_run):
            out = await svc._explain(_conn("mysql"), "SELECT * FROM t")
        self.assertFalse(out["available"])
        self.assertEqual(out["mode"], "failed")
        self.assertEqual(out["error"], "boom")


class TestIndexes(unittest.IsolatedAsyncioTestCase):
    async def test_mysql_indexes_aggregate(self):
        fake_run = AsyncMock(return_value={
            "columns": ["Key_name", "Non_unique", "Column_name"],
            "rows": [
                ["PRIMARY", 0, "id"],
                ["idx_name", 1, "name"],
                ["idx_name", 1, "age"],
            ],
        })
        with patch.object(svc, "_run_sql", fake_run):
            out = await svc._indexes(_conn("mysql"), "SELECT * FROM users", "")
        by_name = {i["index"]: i for i in out}
        self.assertEqual(by_name["PRIMARY"]["columns"], "id")
        self.assertTrue(by_name["PRIMARY"]["unique"])
        self.assertEqual(by_name["idx_name"]["columns"], "name, age")
        self.assertFalse(by_name["idx_name"]["unique"])
        self.assertTrue(fake_run.await_args[0][1].startswith("SHOW INDEX"))

    async def test_pg_indexes_extract_columns(self):
        fake_run = AsyncMock(return_value={
            "columns": ["indexname", "indexdef"],
            "rows": [[
                "idx_users_name",
                "CREATE INDEX idx_users_name ON public.users USING btree (name)",
            ]],
        })
        with patch.object(svc, "_run_sql", fake_run):
            out = await svc._indexes(_conn("pg"), "SELECT * FROM users", "public")
        self.assertEqual(out[0]["index"], "idx_users_name")
        self.assertEqual(out[0]["columns"], "name")
        self.assertFalse(out[0]["unique"])

    async def test_indexes_oracle_empty(self):
        out = await svc._indexes(_conn("oracle"), "SELECT * FROM users", "")
        self.assertEqual(out, [])

    async def test_indexes_exception_partial(self):
        """表查索引抛异常 → 吞掉，返回已拿到的部分（这里为空）。"""
        fake_run = AsyncMock(side_effect=RuntimeError("down"))
        with patch.object(svc, "_run_sql", fake_run):
            out = await svc._indexes(_conn("mysql"), "SELECT * FROM users", "")
        self.assertEqual(out, [])


class TestOptimize(unittest.IsolatedAsyncioTestCase):
    async def test_ai_success(self):
        conn = _conn("mysql", cid=1, database="test")
        digest_info = {"sql_text": "SELECT * FROM users WHERE id=1", "schema_name": "test",
                       "stats": {"avg_latency_ms": 1200.0, "max_latency_ms": 5000.0,
                                 "exec_count": 10, "schema_name": "test"}}
        ai_resp = {"verdict": "optimizable", "summary": "缺索引", "risk_level": "high",
                   "issues": [{"type": "x", "detail": "d", "severity": "high"}],
                   "suggestions": [{"title": "t", "detail": "d"}],
                   "model": "gpt-x", "raw": "..."}
        fake_ai = AsyncMock(return_value=ai_resp)
        with patch.object(svc, "_load_digest", AsyncMock(return_value=digest_info)), \
             patch.object(svc, "_run_sql", _empty_run()), \
             patch("app.clients.ai_client.ai_client.sql_optimize", fake_ai), \
             patch("app.core.redis_client.redis_pool", _no_redis()):
            out = await svc.optimize(conn, "d1", "", "")
        self.assertEqual(out["verdict"], "optimizable")
        self.assertEqual(out["verdict_label"], "可优化")
        self.assertEqual(out["model"], "gpt-x")
        self.assertEqual(out["stats"]["exec_count"], 10)
        self.assertEqual(out["digest"], "d1")
        sent = fake_ai.await_args[0][0]
        self.assertEqual(sent["db_type"], "mysql")
        self.assertEqual(sent["sql_text"], "SELECT * FROM users WHERE id=1")
        self.assertIn("rules", sent)

    async def test_ai_unavailable_fallback(self):
        conn = _conn("mysql", cid=1, database="test")
        digest_info = {"sql_text": "SELECT * FROM users", "schema_name": "",
                       "stats": {"avg_latency_ms": 0.0, "max_latency_ms": 0.0,
                                 "exec_count": 0, "schema_name": ""}}
        fake_ai = AsyncMock(side_effect=RuntimeError("timeout"))
        with patch.object(svc, "_load_digest", AsyncMock(return_value=digest_info)), \
             patch.object(svc, "_run_sql", _empty_run()), \
             patch("app.clients.ai_client.ai_client.sql_optimize", fake_ai), \
             patch("app.core.redis_client.redis_pool", _no_redis()):
            out = await svc.optimize(conn, "d1", "", "")
        self.assertEqual(out["verdict"], "maybe")
        self.assertIn("AI 分析不可用", out["summary"])
        self.assertEqual(out["model"], "")
        issue_types = {i["type"] for i in out["issues"]}
        self.assertIn("select_star", issue_types)
        self.assertIn("no_where", issue_types)

    async def test_digest_not_found(self):
        with patch.object(svc, "_load_digest", AsyncMock(return_value=None)):
            out = await svc.optimize(_conn("mysql"), "missing", "", "")
        self.assertIsNone(out)

    async def test_cache_hit(self):
        conn = _conn("mysql", cid=1, database="")
        cached = {"digest": "d1", "verdict": "hard", "verdict_label": "优化空间有限",
                  "summary": "cached", "risk_level": "low", "issues": [], "suggestions": [],
                  "explain": {}, "indexes": [], "stats": {}, "model": ""}
        redis = SimpleNamespace(get=AsyncMock(return_value=json.dumps(cached)),
                                set=AsyncMock(return_value=True))
        fake_ai = AsyncMock()
        with patch.object(svc, "_load_digest", AsyncMock(return_value=None)), \
             patch("app.core.redis_client.redis_pool", redis), \
             patch("app.clients.ai_client.ai_client.sql_optimize", fake_ai):
            out = await svc.optimize(conn, "d1", "SELECT 1", "")
        self.assertEqual(out["verdict"], "hard")
        fake_ai.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class TestPlaceholdersAndSpacing(unittest.TestCase):
    def test_bind_skips_quoted_literals(self):
        sql = "SELECT * FROM t WHERE a=? AND b='x?y' AND c LIKE '%s%'"
        self.assertEqual(svc._bind_placeholders(sql),
                         "SELECT * FROM t WHERE a=NULL AND b='x?y' AND c LIKE '%s%'")

    def test_bind_token_one(self):
        self.assertEqual(svc._bind_placeholders("SELECT * FROM t WHERE a=? LIMIT ?", "1"),
                         "SELECT * FROM t WHERE a=1 LIMIT 1")

    def test_bind_pg_percent_s(self):
        self.assertEqual(svc._bind_placeholders("SELECT * FROM t WHERE a=%s AND b='%s'", "1"),
                         "SELECT * FROM t WHERE a=1 AND b='%s'")

    def test_normalize_spacing_function_call(self):
        # performance_schema 的 DIGEST_TEXT 形态：MAX ( x ) → MAX( x )
        self.assertEqual(svc._normalize_spacing("SELECT MAX ( a ) FROM t"),
                         "SELECT MAX( a ) FROM t")


class TestExplainFindings(unittest.TestCase):
    def test_unavailable_returns_empty(self):
        issues, sugg = svc._explain_findings({"available": False, "rows": None})
        self.assertEqual(issues, [])
        self.assertEqual(sugg, [])

    def test_full_scan_and_filesort(self):
        explain = {"available": True, "rows": [
            {"table": "orders", "type": "ALL", "key": None,
             "possible_keys": "idx_a", "rows": 500000, "Extra": "Using where; Using filesort"},
        ]}
        issues, sugg = svc._explain_findings(explain)
        kinds = [i["type"] for i in issues]
        self.assertIn("全表扫描", kinds)
        self.assertIn("文件排序", kinds)
        self.assertIn("候选索引未使用", kinds)
        self.assertIn("扫描行数偏大", kinds)
        self.assertTrue(all(i["severity"] in ("high", "medium", "low") for i in issues))
        self.assertTrue(all("title" in s for s in sugg))

    def test_degenerate_detection(self):
        self.assertTrue(svc._explain_degenerate(None))
        self.assertTrue(svc._explain_degenerate([{"Extra": "no matching row in const table"}]))
        self.assertFalse(svc._explain_degenerate([{"Extra": "Using index", "type": "ref"}]))
