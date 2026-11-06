# -*- coding: utf-8 -*-
"""R22 回归：手工重启注解（restartedAt）只应在 5 分钟宽限期内忽略，不能永久排除。

背景：注解会从 Deployment 模板继承到每个新 Pod；旧逻辑见到注解就 `continue`（且不写基线），
导致这些服务的重启永不告警（生产 efreight-afbase 15:22 liveness 重启漏报）。

运行：cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from app.services.pod_restart_service import _detect_restart_diffs


def _pod(name="app-1", ns="ns", restarts=1, restarted_at=""):
    return {"name": name, "namespace": ns, "restart_count": restarts, "restarted_at": restarted_at}


class TestRestartAnnotationGrace(unittest.TestCase):
    NOW = datetime(2026, 11, 6, 7, 40, 0, tzinfo=timezone.utc)

    def test_within_grace_skips_alert_but_records_baseline(self):
        ts = (self.NOW - timedelta(minutes=2)).isoformat()
        inc, base = _detect_restart_diffs([_pod(restarts=1, restarted_at=ts)], {}, now=self.NOW)
        self.assertEqual(inc, [])
        self.assertEqual(base.get("ns/app-1"), 1)  # 关键：基线要写

    def test_after_grace_tracks_normally(self):
        ts = (self.NOW - timedelta(minutes=30)).isoformat()
        inc, base = _detect_restart_diffs(
            [_pod(restarts=2, restarted_at=ts)], {"ns/app-1": 1}, now=self.NOW)
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0][1:], (1, 2))
        self.assertEqual(base.get("ns/app-1"), 2)

    def test_no_annotation_normal(self):
        inc, base = _detect_restart_diffs([_pod(restarts=3)], {"ns/app-1": 2}, now=self.NOW)
        self.assertEqual(len(inc), 1)
        self.assertEqual(base.get("ns/app-1"), 3)

    def test_invalid_annotation_not_excluded(self):
        inc, base = _detect_restart_diffs(
            [_pod(restarts=2, restarted_at="not-a-time")], {"ns/app-1": 1}, now=self.NOW)
        self.assertEqual(len(inc), 1)

    def test_first_seen_no_alert(self):
        inc, base = _detect_restart_diffs([_pod(restarts=5)], {}, now=self.NOW)
        self.assertEqual(inc, [])
        self.assertEqual(base.get("ns/app-1"), 5)

    def test_zulu_suffix_supported(self):
        ts = (self.NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        inc, base = _detect_restart_diffs([_pod(restarts=1, restarted_at=ts)], {}, now=self.NOW)
        self.assertEqual(inc, [])
        self.assertIn("ns/app-1", base)


if __name__ == "__main__":
    unittest.main()
