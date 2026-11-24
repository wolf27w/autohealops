# -*- coding: utf-8 -*-
"""R60-P3 L-b 镜像层「文件级内容」对比 单测。

全部用进程内构造的 tar.gz 字节流，不联网、不打真实 registry。
覆盖：新增/删除/修改文件识别、mode 变化、大文件截断、非 gzip 层跳过、
zstd/foreign/未压缩 tar 预判跳过、汇总结构（_file_level_compare 编排 + 层聚合标注 + 上限截断 + 降级）。

运行：cd api-server && ./.venv/bin/python -m unittest tests.test_image_compare_content -v
"""
import asyncio
import gzip
import io
import os
import tarfile
import unittest
from unittest.mock import AsyncMock, patch

from app.services import image_compare_service as svc


def _tar_gz(entries):
    """构造 tar.gz 字节流。entries: [(name, content, mode, typeflag)]，
    typeflag ∈ {"file", "dir", "sym"}；sym 时 content 为目标路径。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, content, mode, typ in entries:
            ti = tarfile.TarInfo(name)
            ti.mode = mode
            if typ == "dir":
                ti.type = tarfile.DIRTYPE
                ti.size = 0
                tf.addfile(ti)
            elif typ == "sym":
                ti.type = tarfile.SYMTYPE
                ti.linkname = content
                ti.size = 0
                tf.addfile(ti)
            else:
                ti.size = len(content)
                tf.addfile(ti, io.BytesIO(content))
    raw = buf.getvalue()
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb") as gz:
        gz.write(raw)
    return out.getvalue()


def _map_from_tar_gz(entries, layer_digest):
    """解析 tar.gz → {path: record(含 layer_digest)}。"""
    data = _tar_gz(entries)
    records, err = svc._parse_layer_blob(data, 200 * 1024 * 1024)
    assert err == "", f"解析应成功，实际 skip_reason={err!r}"
    return {r["path"]: dict(r, layer_digest=layer_digest) for r in records}


def _run(coro):
    return asyncio.run(coro)


class TestParseLayerBlob(unittest.TestCase):
    """纯函数 _parse_layer_blob：识别文件清单、mode、截断、非 gzip。"""

    def test_parse_regular_files_with_mode(self):
        data = _tar_gz([
            ("app/a.txt", b"hello", 0o644, "file"),
            ("app/sub/", b"", 0o755, "dir"),
        ])
        records, err = svc._parse_layer_blob(data, 200 * 1024 * 1024)
        self.assertEqual(err, "")
        by_path = {r["path"]: r for r in records}
        self.assertIn("app/a.txt", by_path)
        self.assertEqual(by_path["app/a.txt"]["size"], 5)
        self.assertEqual(by_path["app/a.txt"]["mode"], 0o644)
        self.assertEqual(by_path["app/a.txt"]["typeflag"], "file")
        self.assertIn("app/sub", by_path)
        self.assertEqual(by_path["app/sub"]["typeflag"], "dir")

    def test_non_gzip_layer_skipped(self):
        records, err = svc._parse_layer_blob(b"this is not gzip at all", 200 * 1024 * 1024)
        self.assertEqual(records, [])
        self.assertIn("非 gzip", err)

    def test_dotfile_and_dot_slash_prefix(self):
        # "./" 前缀去掉；点开头文件名保留
        data = _tar_gz([
            ("./app/run.sh", b"#!/bin/sh", 0o755, "file"),
            (".dockerignore", b"node_modules\n", 0o644, "file"),
        ])
        records, err = svc._parse_layer_blob(data, 200 * 1024 * 1024)
        self.assertEqual(err, "")
        paths = {r["path"] for r in records}
        self.assertIn("app/run.sh", paths)
        self.assertIn(".dockerignore", paths)
        self.assertNotIn("dockerignore", paths)

    def test_empty_blob_skipped(self):
        records, err = svc._parse_layer_blob(b"", 200 * 1024 * 1024)
        self.assertEqual(records, [])
        self.assertIn("空 blob", err)

    def test_oversize_layer_truncated(self):
        # 解压后远大于 100 字节 → 命中单层解压上限
        data = _tar_gz([("big.bin", b"x" * 4096, 0o644, "file")])
        records, err = svc._parse_layer_blob(data, 100)
        self.assertEqual(records, [])
        self.assertIn("超过上限", err)
        self.assertIn("100", err)


class TestMediaSkipReason(unittest.TestCase):
    """mediaType 预判跳过：zstd / foreign / 未压缩 tar。"""

    def test_zstd(self):
        self.assertIn("zstd", svc._layer_media_skip_reason(
            {"media_type": "application/vnd.oci.image.layer.v1.tar+zstd"}))

    def test_foreign_windows(self):
        self.assertIn("foreign", svc._layer_media_skip_reason(
            {"media_type": "application/vnd.docker.image.rootfs.foreign.diff.tar.gzip"}))

    def test_plain_tar(self):
        self.assertIn("未压缩 tar", svc._layer_media_skip_reason(
            {"media_type": "application/vnd.oci.image.layer.v1.tar"}))

    def test_gzip_ok(self):
        self.assertEqual("", svc._layer_media_skip_reason(
            {"media_type": "application/vnd.docker.image.rootfs.diff.tar.gzip"}))
        self.assertEqual("", svc._layer_media_skip_reason(
            {"media_type": "application/vnd.oci.image.layer.v1.tar+gzip"}))


class TestDiffFileMaps(unittest.TestCase):
    """_diff_file_maps：新增/删除/修改 + mode 变化。"""

    def test_add_remove_modify(self):
        map_a = _map_from_tar_gz([
            ("app/a.txt", b"aaa", 0o644, "file"),
            ("app/keep.txt", b"keep", 0o644, "file"),
            ("app/changed.txt", b"old", 0o644, "file"),
        ], "sha256:layer-a")
        map_b = _map_from_tar_gz([
            ("app/keep.txt", b"keep", 0o644, "file"),
            ("app/changed.txt", b"new", 0o644, "file"),
            ("app/b.txt", b"bbb", 0o644, "file"),
        ], "sha256:layer-b")
        d = svc._diff_file_maps(map_a, map_b)
        self.assertEqual(d["added"], ["app/b.txt"])
        self.assertEqual(d["removed"], ["app/a.txt"])
        self.assertEqual([m["path"] for m in d["modified"]], ["app/changed.txt"])

    def test_mode_change_counts_as_modified(self):
        map_a = _map_from_tar_gz([("app/cfg", b"same", 0o644, "file")], "sha256:a")
        map_b = _map_from_tar_gz([("app/cfg", b"same", 0o755, "file")], "sha256:b")
        d = svc._diff_file_maps(map_a, map_b)
        self.assertEqual(len(d["modified"]), 1)
        m = d["modified"][0]
        self.assertEqual(m["path"], "app/cfg")
        self.assertEqual(m["mode_a"], 0o644)
        self.assertEqual(m["mode_b"], 0o755)
        self.assertEqual(m["sha256_a"], m["sha256_b"])  # 内容未变，仅 mode 变

    def test_identical_no_diff(self):
        map_a = _map_from_tar_gz([("app/x", b"x", 0o644, "file")], "sha256:a")
        map_b = _map_from_tar_gz([("app/x", b"x", 0o644, "file")], "sha256:b")
        d = svc._diff_file_maps(map_a, map_b)
        self.assertEqual(d["added"], [])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["modified"], [])


class TestFileLevelCompare(unittest.TestCase):
    """_file_level_compare 编排：层选择/短路、聚合标注、上限截断、降级。"""

    def test_aggregate_structure(self):
        async def _t():
            layers_a = [{"digest": "sha256:aa", "size": 1, "media_type": "application/vnd.docker.image.rootfs.diff.tar.gzip"},
                        {"digest": "sha256:common", "size": 2}]
            layers_b = [{"digest": "sha256:bb", "size": 1},
                        {"digest": "sha256:common", "size": 2}]

            async def fake_dl(client, host, repo, layer, creds, max_bytes):
                d = layer.get("digest")
                if d == "sha256:aa":
                    return [{"path": "app/removed.txt", "size": 3, "mode": 0o644,
                             "sha256": "h1", "typeflag": "file"},
                            {"path": "app/changed.txt", "size": 3, "mode": 0o644,
                             "sha256": "old", "typeflag": "file"}], "", False
                if d == "sha256:bb":
                    return [{"path": "app/added.txt", "size": 3, "mode": 0o644,
                             "sha256": "h2", "typeflag": "file"},
                            {"path": "app/changed.txt", "size": 5, "mode": 0o755,
                             "sha256": "new", "typeflag": "file"}], "", False
                return [], "unexpected", False

            with patch.object(svc, "_download_and_parse_layer", new=fake_dl):
                return await svc._file_level_compare(
                    "reg.example.com/ns/app:v1", "reg.example.com/ns/app:v2",
                    layers_a, layers_b)

        r = _run(_t())
        self.assertEqual(r["layers_compared"], ["sha256:aa", "sha256:bb"])
        self.assertEqual(r["layers_skipped"], [])
        self.assertEqual([f["path"] for f in r["files_added"]], ["app/added.txt"])
        self.assertEqual([f["path"] for f in r["files_removed"]], ["app/removed.txt"])
        self.assertEqual([m["path"] for m in r["files_modified"]], ["app/changed.txt"])
        m = r["files_modified"][0]
        self.assertEqual(m["layer_digest_a"], "sha256:aa")
        self.assertEqual(m["layer_digest"], "sha256:bb")
        self.assertEqual(m["mode_a"], 0o644)
        self.assertEqual(m["mode_b"], 0o755)
        # 层聚合标注：added 文件标注来源层
        self.assertEqual(r["files_added"][0]["layer_digest"], "sha256:bb")
        self.assertFalse(r["truncated"])
        self.assertEqual(r["layer_content_error"], "")

    def test_layer_limit_truncation(self):
        async def _t():
            layers_a = [{"digest": "sha256:l1", "size": 1},
                        {"digest": "sha256:l2", "size": 1}]
            layers_b = [{"digest": "sha256:l3", "size": 1}]

            async def fake_dl(client, host, repo, layer, creds, max_bytes):
                return [{"path": f"{layer['digest']}/f", "size": 1, "mode": 0o644,
                         "sha256": "x", "typeflag": "file"}], "", False

            with patch.object(svc, "_download_and_parse_layer", new=fake_dl), \
                 patch.dict(os.environ, {"IMAGE_COMPARE_MAX_LAYERS": "1"}):
                return await svc._file_level_compare(
                    "r.example.com/ns/app:v1", "r.example.com/ns/app:v2",
                    layers_a, layers_b)

        r = _run(_t())
        self.assertTrue(r["truncated"])
        self.assertTrue(any("层数" in x for x in r["truncation_reasons"]))
        # 仅比较前 1 层（A 侧只处理 l1）
        self.assertEqual(r["layers_compared"], ["sha256:l1", "sha256:l3"])

    def test_file_entry_limit_truncation(self):
        async def _t():
            layers_a = [{"digest": "sha256:aa", "size": 1}]
            layers_b = [{"digest": "sha256:bb", "size": 1}]

            async def fake_dl(client, host, repo, layer, creds, max_bytes):
                if layer["digest"] == "sha256:aa":
                    return [], "", False
                return [{"path": f"app/f{i}", "size": 1, "mode": 0o644,
                         "sha256": "x", "typeflag": "file"} for i in range(3)], "", False

            with patch.object(svc, "_download_and_parse_layer", new=fake_dl), \
                 patch.dict(os.environ, {"IMAGE_COMPARE_MAX_FILES_PER_CLASS": "2"}):
                return await svc._file_level_compare(
                    "r.example.com/ns/app:v1", "r.example.com/ns/app:v2",
                    layers_a, layers_b)

        r = _run(_t())
        self.assertTrue(r["truncated"])
        self.assertEqual(len(r["files_added"]), 2)
        self.assertTrue(any("files_added" in x for x in r["truncation_reasons"]))

    def test_auth_degradation_sets_layer_content_error(self):
        async def _t():
            layers_a = [{"digest": "sha256:aa", "size": 1}]
            layers_b = [{"digest": "sha256:bb", "size": 1}]

            async def fake_dl(client, host, repo, layer, creds, max_bytes):
                if layer["digest"] == "sha256:aa":
                    return [], "下载 blob 需要认证或无权访问（HTTP 401，请配置只读凭据或匿名访问）", True
                return [{"path": "app/new.txt", "size": 1, "mode": 0o644,
                         "sha256": "x", "typeflag": "file"}], "", False

            with patch.object(svc, "_download_and_parse_layer", new=fake_dl):
                return await svc._file_level_compare(
                    "r.example.com/ns/app:v1", "r.example.com/ns/app:v2",
                    layers_a, layers_b)

        r = _run(_t())
        # 降级：不把失败当「无差异」，明确 layer_content_error
        self.assertTrue(r["layer_content_error"])
        self.assertIn("401", r["layer_content_error"])
        self.assertEqual(len(r["layers_skipped"]), 1)
        self.assertIn("401", r["layers_skipped"][0]["reason"])
        # B 侧仍正常比较出 added
        self.assertEqual([f["path"] for f in r["files_added"]], ["app/new.txt"])

    def test_common_layers_short_circuited(self):
        async def _t():
            layers_a = [{"digest": "sha256:common", "size": 1}]
            layers_b = [{"digest": "sha256:common", "size": 1}]

            async def fake_dl(client, host, repo, layer, creds, max_bytes):
                raise AssertionError("公共层不应被下载")

            with patch.object(svc, "_download_and_parse_layer", new=fake_dl):
                return await svc._file_level_compare(
                    "r.example.com/ns/app:v1", "r.example.com/ns/app:v2",
                    layers_a, layers_b)

        r = _run(_t())
        self.assertEqual(r["layers_compared"], [])
        self.assertEqual(r["files_added"], [])
        self.assertEqual(r["files_removed"], [])
        self.assertEqual(r["files_modified"], [])


if __name__ == "__main__":
    unittest.main()
