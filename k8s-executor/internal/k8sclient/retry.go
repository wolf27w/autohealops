// Package k8sclient 传输层健壮性工具（2026-11-26）
//
// 背景（老板反馈点进生产集群报错）：
//
//	list secrets: unexpected error when reading response body. Please retry.
//	Original error: http2: server sent GOAWAY and closed the connection;
//	LastStreamID=379, ErrCode=NO_ERROR, debug=""
//
// 这是 HTTP/2 连接的正常生命周期行为（apiserver 到上限/空闲会发 GOAWAY），但客户端在
// 「读响应体中途」遇到 GOAWAY 时本次请求即失败。平台侧一次进集群会并发拉
// secrets/configmaps/pvc/ingress 等全量列表，单个连接跑了几百个 stream，命中概率很高。
//
// 处理分两层：
//  1. 连接层：executor 的 clientset 默认**关闭 HTTP/2**（走 HTTP/1.1），从根上避免 GOAWAY
//     （见 manager.go tuneTransport；可用 K8S_HTTP2=1 打开）。
//  2. 请求层：对传输层瞬时错误做有限重试（本文件 RetryOnTransient），
//     并对 secrets/configmaps 这类大对象分页拉取（Limit+Continue），减少单次响应体积。
package k8sclient

import (
	"context"
	"errors"
	"strings"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
)

// transientMarkers 传输层瞬时错误特征（小写匹配）
var transientMarkers = []string{
	"goaway",
	"unexpected eof",
	"unexpected error when reading response body",
	"connection reset by peer",
	"broken pipe",
	"use of closed network connection",
	"tls: use of closed connection",
	"http2: client connection lost",
	"stream error",
}

// IsTransientErr 判断是否「重试一次就好」的瞬时错误
func IsTransientErr(err error) bool {
	if err == nil {
		return false
	}
	if apierrors.IsTimeout(err) || apierrors.IsServerTimeout(err) ||
		apierrors.IsTooManyRequests(err) || apierrors.IsServiceUnavailable(err) ||
		apierrors.IsInternalError(err) {
		return true
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		// 调用方主动取消不重试；但 apiserver 侧超时（DeadlineExceeded 由 transport 抛出）值得重试
		return !errors.Is(err, context.Canceled)
	}
	low := strings.ToLower(err.Error())
	for _, m := range transientMarkers {
		if strings.Contains(low, m) {
			return true
		}
	}
	return false
}

// RetryOnTransient 对瞬时错误重试（默认 3 次，200ms/500ms 退避）；非瞬时错误立即返回
func RetryOnTransient(ctx context.Context, attempts int, fn func() error) error {
	if attempts <= 0 {
		attempts = 3
	}
	backoff := []time.Duration{200 * time.Millisecond, 500 * time.Millisecond, time.Second}
	var err error
	for i := 0; i < attempts; i++ {
		err = fn()
		if err == nil {
			return nil
		}
		if !IsTransientErr(err) {
			return err
		}
		if ctx != nil {
			select {
			case <-ctx.Done():
				return err
			case <-time.After(backoff[min(i, len(backoff)-1)]):
			}
		} else {
			time.Sleep(backoff[min(i, len(backoff)-1)])
		}
	}
	return err
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
