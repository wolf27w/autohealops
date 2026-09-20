// Package k8sclient 提供 kubeconfig 解密与多集群客户端管理。
//
// 加密格式与 Python 端（api-server/app/core/security.py）严格一致，
// 详见 docs/ENCRYPTION.md：AES-256-GCM，base64(IV12B + ciphertext + tag16B)，IV 在前。
package k8sclient

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"fmt"
)

// encryptionKey 为 32 字节（256 位），从环境变量注入，直接使用不派生
var encryptionKey []byte

// SetEncryptionKey 设置解密密钥（启动时从 ENCRYPTION_KEY 环境变量读取）
func SetEncryptionKey(key string) error {
	if len(key) != 32 {
		return fmt.Errorf("ENCRYPTION_KEY 必须为 32 字节（256 位），当前 %d 字节", len(key))
	}
	encryptionKey = []byte(key)
	return nil
}

// EncryptAES256GCM 加密：返回 base64(IV + ciphertext_with_tag)，IV 在前（与 Python 端一致）
func EncryptAES256GCM(plaintext string) (string, error) {
	key := encryptionKey
	if len(key) == 0 {
		return "", fmt.Errorf("encryption key not set")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}
	iv := make([]byte, gcm.NonceSize()) // 12 字节随机 IV
	if _, err := rand.Read(iv); err != nil {
		return "", err
	}
	ciphertext := gcm.Seal(nil, iv, []byte(plaintext), nil)
	return base64.StdEncoding.EncodeToString(append(iv, ciphertext...)), nil
}

// DecryptAES256GCM 解密：输入 base64(IV + ciphertext_with_tag)
func DecryptAES256GCM(ciphertextB64 string) (string, error) {
	key := encryptionKey
	if len(key) == 0 {
		return "", fmt.Errorf("encryption key not set")
	}
	raw, err := base64.StdEncoding.DecodeString(ciphertextB64)
	if err != nil {
		return "", fmt.Errorf("base64 decode failed: %w", err)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}
	if len(raw) < gcm.NonceSize() {
		return "", fmt.Errorf("ciphertext too short")
	}
	iv, ciphertext := raw[:gcm.NonceSize()], raw[gcm.NonceSize():]
	plaintext, err := gcm.Open(nil, iv, ciphertext, nil)
	if err != nil {
		return "", fmt.Errorf("decrypt failed (AES-GCM): %w", err)
	}
	return string(plaintext), nil
}
