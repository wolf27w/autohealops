// 跨语言加密互测（Go 侧）：
//   1. 读取 Python 生成的向量（/tmp/crypto_py_vectors.json）解密验证
//   2. 生成 Go 向量（/tmp/crypto_go_vectors.json）供 Python 侧验证
// 运行：make crypto-test（或 go test ./internal/k8sclient/ -run Crypto）
package k8sclient

import (
	"encoding/json"
	"os"
	"testing"
)

const testKey = "1234567890abcdef1234567890abcdef"

type cryptoVector struct {
	Plaintext  string `json:"plaintext"`
	Ciphertext string `json:"ciphertext"`
}

func TestCryptoRoundTrip(t *testing.T) {
	if err := SetEncryptionKey(testKey); err != nil {
		t.Fatal(err)
	}
	// 自测往返
	ct, err := EncryptAES256GCM("roundtrip-go")
	if err != nil {
		t.Fatal(err)
	}
	pt, err := DecryptAES256GCM(ct)
	if err != nil {
		t.Fatal(err)
	}
	if pt != "roundtrip-go" {
		t.Fatalf("roundtrip mismatch: %s", pt)
	}
	// 错误密钥解密应失败
	if err := SetEncryptionKey("00000000000000000000000000000000"); err != nil {
		t.Fatal(err)
	}
	if _, err := DecryptAES256GCM(ct); err == nil {
		t.Fatal("wrong key should fail")
	}
	_ = SetEncryptionKey(testKey)
}

// TestDecryptPythonVectors 解密 Python 端生成的向量
func TestDecryptPythonVectors(t *testing.T) {
	if err := SetEncryptionKey(testKey); err != nil {
		t.Fatal(err)
	}
	path := "/tmp/crypto_py_vectors.json"
	data, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("未找到 Python 向量文件（先跑 python tests/test_crypto.py）: %v", err)
	}
	var vectors []cryptoVector
	if err := json.Unmarshal(data, &vectors); err != nil {
		t.Fatal(err)
	}
	for _, v := range vectors {
		pt, err := DecryptAES256GCM(v.Ciphertext)
		if err != nil {
			t.Fatalf("解密 Python 密文失败: %v", err)
		}
		if pt != v.Plaintext {
			t.Fatalf("解密结果不一致: %q != %q", pt, v.Plaintext)
		}
	}
	t.Logf("✅ Go 成功解密 Python 端 %d 组向量", len(vectors))
}

// TestGenerateGoVectors 生成 Go 向量供 Python 验证
func TestGenerateGoVectors(t *testing.T) {
	if err := SetEncryptionKey(testKey); err != nil {
		t.Fatal(err)
	}
	samples := []string{
		"go-plaintext-sample-1",
		"go-kubeconfig-内容-中文-与-special#$%符号",
		`{"current-context":"ctx-a","contexts":[{"name":"ctx-a"}]}`,
	}
	vectors := make([]cryptoVector, 0, len(samples))
	for _, s := range samples {
		ct, err := EncryptAES256GCM(s)
		if err != nil {
			t.Fatal(err)
		}
		vectors = append(vectors, cryptoVector{Plaintext: s, Ciphertext: ct})
	}
	out := "/tmp/crypto_go_vectors.json"
	data, _ := json.Marshal(vectors)
	if err := os.WriteFile(out, data, 0o644); err != nil {
		t.Fatal(err)
	}
	t.Logf("✅ Go 生成 %d 组向量 -> %s（供 python tests/test_crypto.py 验证）", len(vectors), out)
}
