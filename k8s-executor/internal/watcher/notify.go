// 告警即时处理回调：告警写入后通知 api-server 立即处理（知识库优先 → AI 诊断 → 修复 → 转人工）
package watcher

import (
	"bytes"
	"fmt"
	"net/http"
	"time"

	"go.uber.org/zap"
)

var (
	alertNotifyAPIURL string
	alertNotifyToken  string
	alertNotifyLogger *zap.Logger
	alertNotifyClient = &http.Client{Timeout: 5 * time.Second}
)

// InitAlertNotify 注入 api-server 地址与内部令牌（main 启动时调用一次）
func InitAlertNotify(apiURL, token string, logger *zap.Logger) {
	alertNotifyAPIURL = apiURL
	alertNotifyToken = token
	alertNotifyLogger = logger
}

// triggerAlertProcess 异步通知 api-server 立即处理指定告警（失败仅记日志，不影响主流程）
func triggerAlertProcess(alertID uint) {
	if alertNotifyAPIURL == "" || alertID == 0 {
		return
	}
	go func() {
		url := fmt.Sprintf("%s/api/v1/alerts/%d/auto-process", alertNotifyAPIURL, alertID)
		req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader([]byte("{}")))
		if err != nil {
			return
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Internal-Token", alertNotifyToken)
		resp, err := alertNotifyClient.Do(req)
		if err != nil {
			if alertNotifyLogger != nil {
				alertNotifyLogger.Debug("告警即时处理回调失败（后台轮询兜底）", zap.Uint("alert", alertID), zap.Error(err))
			}
			return
		}
		resp.Body.Close()
		if resp.StatusCode >= 300 && alertNotifyLogger != nil {
			alertNotifyLogger.Warn("告警即时处理回调返回非 2xx",
				zap.Uint("alert", alertID), zap.Int("status", resp.StatusCode))
		}
	}()
}

// triggerRecoveryNotify 异步通知 api-server 发送「已恢复」通知（节点故障恢复关闭告警后回调）。
// 与 triggerAlertProcess 复用同一 api-server 地址 / 内部令牌 / HTTP client。
func triggerRecoveryNotify(alertID uint) {
	if alertNotifyAPIURL == "" || alertID == 0 {
		return
	}
	go func() {
		url := fmt.Sprintf("%s/api/v1/alerts/%d/recovery-notify", alertNotifyAPIURL, alertID)
		req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader([]byte("{}")))
		if err != nil {
			return
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("X-Internal-Token", alertNotifyToken)
		resp, err := alertNotifyClient.Do(req)
		if err != nil {
			if alertNotifyLogger != nil {
				alertNotifyLogger.Debug("告警恢复通知回调失败（不影响主流程）", zap.Uint("alert", alertID), zap.Error(err))
			}
			return
		}
		resp.Body.Close()
		if resp.StatusCode >= 300 && alertNotifyLogger != nil {
			alertNotifyLogger.Warn("告警恢复通知回调返回非 2xx",
				zap.Uint("alert", alertID), zap.Int("status", resp.StatusCode))
		}
	}()
}
