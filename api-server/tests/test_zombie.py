# -*- coding: utf-8 -*-
"""R20 回归：nginx 日志解析 / 探针排除 / upstream→Service / Service→Deployment 映射。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_zombie -v
"""
import unittest

from app.services import zombie_service as zs

LINE = ('10.244.1.5 - - [10/Nov/2026:17:00:00 +0800] "GET /api/orders?id=1 HTTP/1.1" 200 12 "-" '
        '"Mozilla/5.0" 10.244.2.9:8080 200 0.012 [qa-bgs-eft-order-8080] [] 10.244.2.9:8080 12 0.011')
PROBE = ('10.244.0.1 - - [10/Nov/2026:17:00:00 +0800] "GET /healthz HTTP/1.1" 200 2 "-" '
         '"kube-probe/1.29" - -')


class TestParse(unittest.TestCase):
    def test_parse_combined(self):
        r = zs.parse_nginx_line(LINE)
        self.assertIsNotNone(r)
        self.assertEqual(r["path"], "/api/orders")
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["upstream"], "10.244.2.9:8080")

    def test_parse_svc_upstream(self):
        line = ('1.2.3.4 - - [10/Nov/2026:17:00:00 +0800] "GET /x HTTP/1.1" 200 1 "-" "curl" '
                'eft-order.qa-bgs.svc.cluster.local:8080')
        r = zs.parse_nginx_line(line)
        self.assertEqual(zs.upstream_service(r["upstream"]), "eft-order")

    def test_parse_bad(self):
        self.assertIsNone(zs.parse_nginx_line("not a log line"))


class TestProbe(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(zs.DEFAULT_CONFIG)

    def test_health_path(self):
        self.assertTrue(zs.is_probe({"path": "/healthz", "ua": "curl"}, self.cfg))
        self.assertTrue(zs.is_probe({"path": "/ready", "ua": "curl"}, self.cfg))

    def test_kube_probe_ua(self):
        self.assertTrue(zs.is_probe({"path": "/api", "ua": "kube-probe/1.29"}, self.cfg))

    def test_normal_request(self):
        self.assertFalse(zs.is_probe({"path": "/api/orders", "ua": "Mozilla/5.0"}, self.cfg))


class TestUpstreamService(unittest.TestCase):
    def test_ip_not_attributed(self):
        self.assertEqual(zs.upstream_service("10.244.2.9:8080"), "")

    def test_svc_name(self):
        self.assertEqual(zs.upstream_service("eft-auth:8080"), "eft-auth")
        self.assertEqual(zs.upstream_service("eft-auth.qa-bgs.svc.cluster.local:8080"), "eft-auth")

    def test_empty(self):
        self.assertEqual(zs.upstream_service(""), "")
        self.assertEqual(zs.upstream_service("-"), "")


class TestMapping(unittest.TestCase):
    def test_exact_and_prefix(self):
        deps = [{"name": "eft-auth"}, {"name": "eft-order"}]
        svcs = [{"name": "eft-auth"}, {"name": "eft-order-svc"}, {"name": "unrelated"}]
        ing = [{"name": "eft-order"}]
        m = zs.map_services_to_deployments(deps, svcs, ing)
        self.assertIn("eft-auth", m["eft-auth"])
        self.assertIn("eft-order-svc", m["eft-order"])
        self.assertIn("eft-order", m["eft-order"])
        self.assertEqual(m["eft-auth"], {"eft-auth"})


class TestConfig(unittest.TestCase):
    def test_merge_user_config(self):
        cfg = zs._cfg('{"days": 3, "max_requests": 5}')
        self.assertEqual(cfg["days"], 3)
        self.assertEqual(cfg["max_requests"], 5)
        self.assertIn("exclude_paths", cfg)  # 默认项保留

    def test_bad_json_falls_back(self):
        cfg = zs._cfg("not-json")
        self.assertEqual(cfg["days"], zs.DEFAULT_CONFIG["days"])


if __name__ == "__main__":
    unittest.main()
