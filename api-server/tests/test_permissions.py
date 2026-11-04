# -*- coding: utf-8 -*-
"""权限覆盖测试（M8：低权限越权防护）

目标：保证**没有漏鉴权的接口**，且低权限角色访问受限接口被拒（403）。

分两部分：
1) 结构覆盖：遍历全部路由，断言每个接口要么有角色守卫 / 用户鉴权，要么在**显式白名单**里
   （白名单里的接口各自有专属保护：内部令牌 / 外部令牌 / SSE JWT / Webhook 验签 / 健康探针）。
2) 运行时越权：用 TestClient 把当前用户替换成 viewer，访问 admin/operator 接口，断言 403。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_permissions -v
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.routing import APIRoute  # noqa: E402

from app.main import app  # noqa: E402

# 显式白名单：无角色守卫且无标准用户鉴权，但**各有专属保护**（改动时需同步更新并说明）
PUBLIC_ALLOWLIST = {
    ("POST", "/api/v1/auth/login"),                       # 登录入口
    ("GET", "/health"),                                   # 存活探针
    ("GET", "/ready"),                                    # 就绪探针
    ("GET", "/metrics"),                                  # 平台指标（Prometheus 抓取）
    ("GET", "/mcp"),                                      # MCP 发现（无敏感信息）；POST /mcp 需鉴权
    ("POST", "/api/v1/alerts/{alert_id}/auto-process"),   # 内部令牌 X-Internal-Token
    ("POST", "/api/v1/alerts/{alert_id}/recovery-notify"),# 内部令牌 X-Internal-Token
    ("GET", "/api/v1/external/tickets"),                  # 外部令牌 X-External-Token
    ("POST", "/api/v1/external/tickets"),                 # 外部令牌 X-External-Token
    ("GET", "/api/v1/stats/dashboard/stream"),            # SSE，?token= JWT
    ("POST", "/api/v1/webhooks/dingtalk/callback"),       # 机器人回调验签
    ("POST", "/api/v1/webhooks/feishu/callback"),         # 飞书验签
    ("POST", "/api/v1/webhooks/wechat/callback"),         # 企微验签
    # agent 一键安装清单：集群侧 kubectl 拉取，只能靠接入令牌（sha256 比对、可吊销），
    # 没有平台账号；泄漏面=该集群 agent 安装能力，吊销即时失效
    ("GET", "/api/v1/clusters/{cluster_id}/agent/install.yaml"),
}


def _route_auth(route: APIRoute) -> tuple[set, bool]:
    """返回 (允许角色集合, 是否含标准用户鉴权)。"""
    roles: set[str] = set()
    has_user_auth = False

    def walk(dep):
        nonlocal has_user_auth
        fn = getattr(dep, "call", None)
        if fn is not None:
            rs = getattr(fn, "_allowed_roles", None)
            if rs:
                roles.update(rs)
            if getattr(fn, "__name__", "") in ("get_current_active_user", "get_current_user"):
                has_user_auth = True
        for child in getattr(dep, "dependencies", []) or []:
            walk(child)

    walk(route.dependant)
    return roles, has_user_auth


class TestRouteAuthCoverage(unittest.TestCase):
    """结构覆盖：不得存在「既无角色守卫、又无用户鉴权、又非白名单」的接口"""

    def test_every_route_is_protected_or_allowlisted(self):
        offenders = []
        for r in app.routes:
            if not isinstance(r, APIRoute):
                continue
            roles, has_user = _route_auth(r)
            if roles or has_user:
                continue
            methods = {m for m in r.methods if m not in ("HEAD", "OPTIONS")}
            if not any((m, r.path) in PUBLIC_ALLOWLIST for m in methods):
                offenders.append(f"{','.join(sorted(methods))} {r.path}")
        self.assertEqual(offenders, [], f"发现未鉴权且不在白名单的接口：{offenders}")

    def test_known_sensitive_endpoints_guarded(self):
        """关键接口的角色要求（防止被误降级）"""
        expect = {
            "/api/v1/settings": {"admin"},
            "/api/v1/users": {"admin"},
            "/api/v1/jvm/actions": {"admin", "operator"},
        }
        by_path = {}
        for r in app.routes:
            if isinstance(r, APIRoute):
                by_path.setdefault(r.path, set()).update(_route_auth(r)[0])
        for path, roles in expect.items():
            self.assertEqual(by_path.get(path), roles, f"{path} 角色要求应={roles}，实际={by_path.get(path)}")


class TestViewerForbidden(unittest.TestCase):
    """运行时越权：viewer 访问 admin/operator 接口 → 403（依赖解析阶段即拒绝，不触库）"""

    def setUp(self):
        from fastapi.testclient import TestClient
        from app.middleware.auth import get_current_active_user

        self._orig = app.dependency_overrides.get(get_current_active_user)
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            id=999, username="viewer", role="viewer", is_active=True
        )
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        from app.middleware.auth import get_current_active_user
        if self._orig is not None:
            app.dependency_overrides[get_current_active_user] = self._orig
        else:
            app.dependency_overrides.pop(get_current_active_user, None)

    def test_admin_endpoints_403_for_viewer(self):
        for path in ("/api/v1/settings", "/api/v1/users", "/api/v1/external/tickets/token",
                     "/api/v1/decisions/risk-policy"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 403, f"{path} 应 403，实际 {resp.status_code}")

    def test_operator_endpoint_403_for_viewer(self):
        resp = self.client.get("/api/v1/jvm/actions")
        self.assertEqual(resp.status_code, 403)

    def test_viewer_allowed_any_user_not_403(self):
        # require_any_user 接口：viewer 不应被 403（可能因依赖不可用返回其它码，但绝不能是 403）
        resp = self.client.get("/api/v1/agent/proactive/status")
        self.assertNotEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
