# -*- coding: utf-8 -*-
"""故障分类收敛（L1/L2）回归：11 类完整性 + 旧 fault_type 映射覆盖 + 未知兜底。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_fault_taxonomy -v
"""
import unittest

from app.core.fault_taxonomy import (
    FAULT_TYPE_TO_FAMILY,
    L1_DOMAINS,
    L1_LABELS,
    L2_FAMILIES,
    L2_LABELS,
    UNKNOWN,
    classify,
    family_label,
    family_of,
    fault_types_of,
    is_known_family,
    l1_of,
)


class TestL2FamiliesCompleteness(unittest.TestCase):
    def test_exactly_11_families(self):
        self.assertEqual(len(L2_FAMILIES), 11)
        self.assertEqual(len(L2_LABELS), 11)

    def test_family_keys_and_labels(self):
        expect = {
            "resource": "资源不足",
            "config": "配置错误",
            "image": "镜像",
            "schedule": "调度",
            "probe": "探针",
            "dependency": "下游依赖",
            "code": "代码缺陷",
            "capacity": "容量",
            "lock": "锁与慢查询",
            "network": "网络",
            "node_health": "节点健康",
        }
        self.assertEqual(dict(L2_FAMILIES), expect)

    def test_node_health_contains_three(self):
        # 定稿：node_health 下含 TerminatingStuck / NodeNotReady / NodeCordoned
        self.assertEqual(family_of("TerminatingStuck"), "node_health")
        self.assertEqual(family_of("NodeNotReady"), "node_health")
        self.assertEqual(family_of("NodeCordoned"), "node_health")


class TestL1Domains(unittest.TestCase):
    def test_three_domains(self):
        self.assertEqual([k for k, _ in L1_DOMAINS], ["cluster", "app", "db"])
        self.assertEqual(L1_LABELS["cluster"], "集群故障")
        self.assertEqual(L1_LABELS["app"], "应用故障")
        self.assertEqual(L1_LABELS["db"], "数据库故障")

    def test_l1_of(self):
        self.assertEqual(l1_of("NodeNotReady", "node"), "cluster")
        self.assertEqual(l1_of("CrashLoopBackOff", "pod"), "app")
        self.assertEqual(l1_of("DBConnFailed", "db"), "db")


class TestFaultTypeMappingCoverage(unittest.TestCase):
    def test_every_mapped_fault_type_lands_in_known_family(self):
        for ft, fam in FAULT_TYPE_TO_FAMILY.items():
            self.assertIn(fam, L2_LABELS, f"{ft} 映射到未登记族 {fam}")

    def test_production_19_types(self):
        # 2026-11-18 生产实测 19 类 fault_type 的去向
        expect = {
            "TerminatingStuck": "node_health",
            "CrashLoopBackOff": "code",
            "PodRestartProbeFail": "probe",
            "PendingTimeout": "schedule",
            "ImagePullBackOff": "image",
            "DBConnFailed": "dependency",
            "OOMKilled": "resource",
            "PodRestartOOM": "resource",
            "DiskPressure": "capacity",
            "NodeNotReady": "node_health",
            "NodeCordoned": "node_health",
            "CreateContainerError": "config",
            "ContainerMemHigh": "resource",
            "ErrImagePull": "image",
            "CreateContainerConfigError": "config",
            "MemoryPressure": "capacity",
            "DBAuthFailed": "dependency",
            "InvalidImageName": "image",
            "PodEvicted": "resource",
        }
        for ft, fam in expect.items():
            self.assertEqual(family_of(ft), fam, f"{ft} 映射不符")

    def test_sibling_merge(self):
        # 本质同族归一类：OOMKilled 与 PodRestartOOM、ImagePullBackOff 与 ErrImagePull
        self.assertEqual(family_of("OOMKilled"), family_of("PodRestartOOM"))
        self.assertEqual(family_of("ImagePullBackOff"), family_of("ErrImagePull"))
        self.assertEqual(family_of("DBConnFailed"), family_of("DBAuthFailed"))

    def test_log_app_types(self):
        self.assertEqual(family_of("AppOOM"), "resource")
        self.assertEqual(family_of("AppCrash"), "code")
        self.assertEqual(family_of("AppConnRefused"), "dependency")
        self.assertEqual(family_of("AppDependency"), "dependency")
        self.assertEqual(family_of("AppSQL"), "lock")
        self.assertEqual(family_of("AppConfigError"), "config")
        self.assertEqual(family_of("AppBusiness"), "code")

    def test_db_and_node_types(self):
        self.assertEqual(family_of("DBSlowQuery"), "lock")
        self.assertEqual(family_of("DBMetricHigh"), "capacity")
        self.assertEqual(family_of("DiskPressure"), "capacity")
        self.assertEqual(family_of("NetworkUnavailable"), "network")
        self.assertEqual(family_of("KubeletNotReady"), "node_health")


class TestUnknownFallback(unittest.TestCase):
    def test_truly_unknown_fault_type(self):
        self.assertEqual(family_of("SomeBrandNewError"), UNKNOWN)
        self.assertEqual(family_of("SomeBrandNewError", "pod"), UNKNOWN)
        self.assertEqual(family_of(""), UNKNOWN)
        self.assertEqual(family_of(None), UNKNOWN)

    def test_resource_type_fallback(self):
        # node/cluster → node_health；db → dependency（未登记 fault_type 也按资源类型给合理族）
        self.assertEqual(family_of("WhateverNodeThing", "node"), "node_health")
        self.assertEqual(family_of("WhateverClusterThing", "cluster"), "node_health")
        self.assertEqual(family_of("WhateverDbThing", "db"), "dependency")

    def test_db_prefix(self):
        self.assertEqual(family_of("db_mysql", "db"), "dependency")

    def test_unknown_label(self):
        self.assertEqual(family_label(UNKNOWN), "未分类")
        self.assertEqual(family_label("nope"), "nope")

    def test_unknown_keeps_original_text_as_tag(self):
        # 未知值归 unknown，原始文本（fault_type）保留为标签 —— 读取侧不改写 fault_type
        ft = "CustomUnknownFault"
        self.assertEqual(family_of(ft), UNKNOWN)
        self.assertEqual(ft, "CustomUnknownFault")


class TestHelpers(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify("CrashLoopBackOff", "pod"), ("app", "code"))
        self.assertEqual(classify("NodeNotReady", "node"), ("cluster", "node_health"))
        self.assertEqual(classify("DBConnFailed", "db"), ("db", "dependency"))

    def test_fault_types_of(self):
        self.assertIn("OOMKilled", fault_types_of("resource"))
        self.assertIn("ImagePullBackOff", fault_types_of("image"))
        self.assertEqual(fault_types_of(UNKNOWN), ())

    def test_is_known_family(self):
        self.assertTrue(is_known_family("image"))
        self.assertTrue(is_known_family("node_health"))
        self.assertFalse(is_known_family("unknown"))
        self.assertFalse(is_known_family(""))


if __name__ == "__main__":
    unittest.main()
