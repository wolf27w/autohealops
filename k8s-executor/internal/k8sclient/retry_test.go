package k8sclient

import (
	"context"
	"errors"
	"fmt"
	"testing"
)

func TestIsTransientErr(t *testing.T) {
	yes := []string{
		"list secrets: unexpected error when reading response body. Please retry. Original error: http2: server sent GOAWAY and closed the connection; LastStreamID=379, ErrCode=NO_ERROR, debug=\"\"",
		"unexpected EOF",
		"read tcp 1.2.3.4: read: connection reset by peer",
		"http2: client connection lost",
	}
	for _, s := range yes {
		if !IsTransientErr(errors.New(s)) {
			t.Fatalf("should be transient: %s", s)
		}
	}
	no := []string{"secrets \"x\" is forbidden", "the server could not find the requested resource"}
	for _, s := range no {
		if IsTransientErr(errors.New(s)) {
			t.Fatalf("should NOT be transient: %s", s)
		}
	}
}

func TestRetryOnTransientSucceedsAfterGoaway(t *testing.T) {
	n := 0
	err := RetryOnTransient(context.Background(), 3, func() error {
		n++
		if n == 1 {
			return fmt.Errorf("http2: server sent GOAWAY and closed the connection")
		}
		return nil
	})
	if err != nil {
		t.Fatalf("expected success after retry, got %v", err)
	}
	if n != 2 {
		t.Fatalf("expected 2 attempts, got %d", n)
	}
}

func TestRetryOnTransientStopsOnPermanent(t *testing.T) {
	n := 0
	err := RetryOnTransient(context.Background(), 3, func() error {
		n++
		return errors.New("forbidden")
	})
	if err == nil || n != 1 {
		t.Fatalf("permanent error must not retry (n=%d)", n)
	}
}
