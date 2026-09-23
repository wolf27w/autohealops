package watcher

import (
	"testing"
	"time"
)

// TestTerminatingBackoff 验证单页 List 瞬时错误的重试退避序列：
// 第 1 次重试前 200ms，第 2 次 500ms，第 3 次及以后 1s（封顶）。
func TestTerminatingBackoff(t *testing.T) {
	cases := []struct {
		attempt int
		want    time.Duration
	}{
		{attempt: 0, want: 200 * time.Millisecond},
		{attempt: 1, want: 500 * time.Millisecond},
		{attempt: 2, want: time.Second},
		{attempt: 3, want: time.Second},
		{attempt: 10, want: time.Second}, // 封顶 1s，不越界
		{attempt: -1, want: time.Second}, // 负值按 default 处理
	}
	for _, c := range cases {
		if got := terminatingBackoff(c.attempt); got != c.want {
			t.Errorf("terminatingBackoff(%d) = %v, want %v", c.attempt, got, c.want)
		}
	}
}
