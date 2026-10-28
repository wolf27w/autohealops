// 告警落地 sink 的「真库」联调测试（默认跳过，需显式开启）。
//
// 用途：验证 dbSink 的写库/合并/关闭语义没被重构改坏（P2 把 GORM 调用搬进 sink 后回归用）。
// 运行：
//   cd k8s-executor && P2_LIVE_TEST=1 go test ./internal/watcher/ -run TestDbSinkLive -v
//
// 说明：会往 resource 表以外只写 alerts 表（临时 cluster_id=999003），跑完自动清理。
package watcher

import (
	"os"
	"strings"
	"testing"
	"time"

	"github.com/autohealops/k8s-executor/internal/config"
	"github.com/autohealops/k8s-executor/internal/database"
)

func TestDbSinkLive(t *testing.T) {
	if os.Getenv("P2_LIVE_TEST") == "" {
		t.Skip("跳过：需要 P2_LIVE_TEST=1（会连真实 MySQL 并写测试数据）")
	}
	cfg, err := config.Load()
	if err != nil {
		t.Fatalf("加载配置失败: %v", err)
	}
	if err := database.Init(cfg.DatabaseURL); err != nil {
		t.Fatalf("连接数据库失败: %v", err)
	}
	db := database.GetDB()
	const cid = 999003
	cleanup := func() { db.Exec("DELETE FROM alerts WHERE cluster_id = ?", cid) }
	cleanup()
	defer cleanup()

	s := &dbSink{}
	if !s.LocalFilters() {
		t.Fatal("dbSink 应开启本地过滤")
	}

	// 1) 首次上报 pending → 新插
	id1 := s.Report(AlertEvent{ClusterID: cid, ResourceType: "pod", Namespace: "sinktest",
		ResourceName: "web-1-abc-de1", DeploymentName: "web-1", FaultType: "SinkLiveFault",
		Severity: "low", Message: "m1", Status: "pending", DetectedAt: time.Now()})
	if id1 == 0 {
		t.Fatal("首次上报应写入并返回 id")
	}

	// 2) 同 Deployment 再报 → 合并计数，不新建
	id2 := s.Report(AlertEvent{ClusterID: cid, ResourceType: "pod", Namespace: "sinktest",
		ResourceName: "web-1-abc-de2", DeploymentName: "web-1", FaultType: "SinkLiveFault",
		Severity: "low", Message: "m2", Status: "pending", DetectedAt: time.Now()})
	var ec int
	var rname string
	db.Raw("SELECT event_count, resource_name FROM alerts WHERE id = ?", id1).Row().Scan(&ec, &rname)
	if id2 != id1 || ec != 2 || rname != "web-1-abc-de2" {
		t.Fatalf("合并异常: id2=%d id1=%d event_count=%d resource=%s", id2, id1, ec, rname)
	}

	// 3) Resolve 关闭（带后缀）
	s.Resolve(cid, "pod", "web-1-abc-de2", "SinkLiveFault", ResolveOpts{Suffix: "（测试恢复）"})
	var status, msg string
	db.Raw("SELECT status, message FROM alerts WHERE id = ?", id1).Row().Scan(&status, &msg)
	if status != "resolved" || !strings.Contains(msg, "测试恢复") {
		t.Fatalf("关闭异常: status=%s msg=%s", status, msg)
	}
	t.Logf("✅ dbSink 真库联调通过（cluster=%d alert=%d）", cid, id1)
}
