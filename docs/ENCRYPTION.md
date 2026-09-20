# 跨语言加密格式约定（Python ↔ Go）

> 本文件是 kubeconfig 加密/解密的唯一格式标准，两端代码必须严格遵循。
> api-server（Python）加密 → k8s-executor（Go）解密，必须逐字节兼容。

## 算法与参数

| 项 | 值 |
|---|---|
| 算法 | AES-256-GCM |
| 密钥 | 32 字节（256 位），环境变量 `ENCRYPTION_KEY` 注入，**直接使用，不额外派生** |
| IV / Nonce | 随机 12 字节，每次加密重新生成 |
| Tag | 16 字节（GCM 默认） |
| 输出格式 | `base64(IV + ciphertext + tag)`，**IV 在前** |

## 拼接规则

```
原始数据: plaintext (UTF-8 bytes)
加密:      ciphertext_with_tag = AESGCM(key, iv).encrypt(plaintext)   # 内含 16 字节 tag
输出:      base64(iv + ciphertext_with_tag)
```

解密时：base64 解码 → 前 12 字节为 IV → 剩余为 ciphertext_with_tag → AESGCM.decrypt。

## Python 实现（api-server/app/core/security.py）

```python
import base64, secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def encrypt_aes256_gcm(plaintext: str) -> str:
    iv = secrets.token_bytes(12)
    ct = AESGCM(KEY).encrypt(iv, plaintext.encode(), None)
    return base64.b64encode(iv + ct).decode()

def decrypt_aes256_gcm(b64: str) -> str:
    raw = base64.b64decode(b64)
    return AESGCM(KEY).decrypt(raw[:12], raw[12:], None).decode()
```

## Go 实现（k8s-executor/internal/k8sclient/crypto.go）

```go
func Encrypt(plaintext string) (string, error) {
    key := []byte(encryptionKey)
    block, _ := aes.NewCipher(key)
    gcm, _ := cipher.NewGCM(block)
    iv := make([]byte, gcm.NonceSize())
    rand.Read(iv)
    ct := gcm.Seal(nil, iv, []byte(plaintext), nil)
    return base64.StdEncoding.EncodeToString(append(iv, ct...)), nil
}

func Decrypt(b64 string) (string, error) {
    raw, _ := base64.StdEncoding.DecodeString(b64)
    key := []byte(encryptionKey)
    block, _ := aes.NewCipher(key)
    gcm, _ := cipher.NewGCM(block)
    pt, err := gcm.Open(nil, raw[:gcm.NonceSize()], raw[gcm.NonceSize():], nil)
    return string(pt), err
}
```

## 互测验证

`make crypto-test` 执行：
1. Python 端生成 3 组测试向量（明文 → 密文）
2. Go 端测试：解密 Python 密文，断言与明文一致
3. Go 端生成 3 组密文
4. Python 端测试：解密 Go 密文，断言与明文一致

**任何一端改动加密实现后，必须通过 `make crypto-test` 才能提交。**
