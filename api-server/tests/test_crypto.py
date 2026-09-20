# -*- coding: utf-8 -*-
"""加密互测（Python 侧）：生成测试向量供 Go 验证，并验证 Go 生成的向量

运行：python tests/test_crypto.py（make crypto-test 调用）
"""
import base64
import json
import os
import sys
import tempfile

# 允许从项目根导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.security import decrypt_aes256_gcm, encrypt_aes256_gcm

# 固定测试密钥（与 Go 测试一致；互测不走真实 ENCRYPTION_KEY，避免环境依赖）
TEST_KEY = "1234567890abcdef1234567890abcdef"


def python_to_go():
    """Python 加密 → 输出向量文件，供 Go 测试解密"""
    settings.ENCRYPTION_KEY = TEST_KEY
    samples = [
        "plaintext-sample-1",
        "kubeconfig-content-with-中文-和-special!@#chars",
        "{\"apiVersion\":\"v1\",\"clusters\":[{\"name\":\"demo\"}]}",
    ]
    vectors = [{"plaintext": s, "ciphertext": encrypt_aes256_gcm(s)} for s in samples]
    out = "/tmp/crypto_py_vectors.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(vectors, f, ensure_ascii=False)
    print(f"[python] 生成 {len(vectors)} 组向量 -> {out}")
    return out


def go_to_python():
    """读取 Go 生成的向量文件并解密验证"""
    path = "/tmp/crypto_go_vectors.json"
    if not os.path.exists(path):
        print("[python] 未找到 Go 向量文件，跳过（先跑 Go 侧）")
        return True
    settings.ENCRYPTION_KEY = TEST_KEY
    with open(path, encoding="utf-8") as f:
        vectors = json.load(f)
    for v in vectors:
        pt = decrypt_aes256_gcm(v["ciphertext"])
        assert pt == v["plaintext"], f"Go 密文解密不一致: {v['plaintext']!r} != {pt!r}"
    print(f"[python] 成功解密 Go 端 {len(vectors)} 组向量 ✅")


if __name__ == "__main__":
    # 自测：Python 加解密往返
    settings.ENCRYPTION_KEY = TEST_KEY
    ct = encrypt_aes256_gcm("roundtrip-test")
    assert decrypt_aes256_gcm(ct) == "roundtrip-test"
    print("[python] 自测往返 OK")
    python_to_go()
    go_to_python()
    print("[python] 互测（Python 侧）全部通过 ✅")
