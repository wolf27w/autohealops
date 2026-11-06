# -*- coding: utf-8 -*-
"""R22 回归：默认没有宽限期（重启就报）；只有连续异常重启 3 次后才进入宽限期。

背景（老板 2026-11-06 口径）：
- 任何服务默认**不允许宽限期**：只要重启次数增加就应告警
- 同一 Pod 连续异常重启达到 3 次 → 进入宽限期（抑制后续告警）；恢复后解除
- 手工重启注解（restartedAt）**不再排除**（旧逻辑导致生产 efreight-afbase 漏报）

运行：cd api-server && ./.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
"""
import unittest

from app.services.pod_restart_service import GRACE_THRESHOLD, _detect_restart_diffs


def _pod(name="app-1", ns="ns", restarts=1, restarted_at=""):
    return {"name": name, "namespace": ns, "restart_count": restarts, "restarted_at": restarted_at}


class TestRestartNoGraceByDefault(unittest.TestCase):
    def test_annotation_does_not_exclude(self):
        """有手工重启注解也照常告警（旧逻辑会整条跳过）"""
        inc, base = _detect_restart_diffs(
            [_pod(restarts=2, restarted_at="2026-11-06T12:10:01+08:00")], {"ns/app-1": 1})
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0][1:], (1, 2))
        self.assertEqual(base.get("ns/app-1"), 2)

    def test_invalid_annotation_still_tracks(self):
        inc, _ = _detect_restart_diffs(
            [_pod(restarts=2, restarted_at="not-a-time")], {"ns/app-1": 1})
        self.assertEqual(len(inc), 1)

    def test_first_seen_no_alert(self):
        inc, base = _detect_restart_diffs([_pod(restarts=5)], {})
        self.assertEqual(inc, [])
        self.assertEqual(base.get("ns/app-1"), 5)

    def test_increment_detected(self):
        inc, _ = _detect_restart_diffs([_pod(restarts=3)], {"ns/app-1": 2})
        self.assertEqual(len(inc), 1)

    def test_no_change_no_alert(self):
        inc, _ = _detect_restart_diffs([_pod(restarts=2)], {"ns/app-1": 2})
        self.assertEqual(inc, [])

    def test_grace_threshold_is_three(self):
        self.assertEqual(GRACE_THRESHOLD, 3)


if __name__ == "__main__":
    unittest.main()
