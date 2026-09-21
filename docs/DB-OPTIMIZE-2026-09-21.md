# 数据库优化方案（2026-09-21）

针对「本机mysql（192.168.6.99 / api_ops）」的巡检诊断结果（诊断报告 #16）。

## 诊断结论

| 项 | 值 |
|---|---|
| 库容量 | 2217 MB |
| resource_snapshots | **1848 MB（约 83%）** |
| pod_metrics | 324 MB |
| slow_sql_records | 37 MB |
| 连接/锁 | 使用率 2.4%、活跃 3、无锁等待；缓冲池命中 95.41% → 正常 |
| 慢 SQL | 集中在 resource_snapshots，单次执行约 **2400 秒（40 分钟）** |

慢 SQL（相关子查询，逐行算 MAX）：

```sql
SELECT cluster_id, resource_type, COUNT(*) FROM resource_snapshots
WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM resource_snapshots rs2
                       WHERE rs2.cluster_id = resource_snapshots.cluster_id
                         AND rs2.resource_type = resource_snapshots.resource_type)
```

现有索引均为**单列**（`ix_resource_snapshots_cluster_id` / `_namespace` / `_snapshot_time`），
对上面这条查询无帮助。

## 一、DB 侧执行脚本（DBA 手动执行，低峰）

```sql
-- 0) 查看现状
SHOW INDEX FROM resource_snapshots;
SELECT table_name, table_rows, ROUND(data_length/1024/1024) AS mb
FROM information_schema.tables WHERE table_schema='api_ops'
ORDER BY data_length DESC LIMIT 10;

-- 1) 复合索引：覆盖「按集群/类型取最新快照 + 计数」（在线 DDL，不锁表）
ALTER TABLE resource_snapshots
  ADD INDEX ix_rs_cluster_type_time (cluster_id, resource_type, snapshot_time),
  ALGORITHM=INPLACE, LOCK=NONE;

-- 2) 删除已被复合索引覆盖的单列索引（减少写放大）
--    注意：确认复合索引已建好后再删
ALTER TABLE resource_snapshots DROP INDEX ix_resource_snapshots_snapshot_time;

-- 3) 更新统计信息
ANALYZE TABLE resource_snapshots, pod_metrics, slow_sql_records;

-- 4)【可选·重活】按天分区，清理改为 DROP PARTITION（秒级、无碎片）
--    需重建表，低峰执行；如不做，继续用每日 DELETE 清理（见下方保留期配置）
-- ALTER TABLE resource_snapshots
--   PARTITION BY RANGE (TO_DAYS(snapshot_time)) (
--     PARTITION p20260921 VALUES LESS THAN (TO_DAYS('2026-09-22')),
--     ... );
```

## 二、SQL 写法（治本）

把相关子查询改为「先聚合出每集群每类型的最新时间，再 JOIN」：

```sql
SELECT t.cluster_id, t.resource_type, COUNT(*)
FROM resource_snapshots t
JOIN (SELECT cluster_id, resource_type, MAX(snapshot_time) mt
      FROM resource_snapshots GROUP BY cluster_id, resource_type) m
  ON m.cluster_id = t.cluster_id AND m.resource_type = t.resource_type AND m.mt = t.snapshot_time
GROUP BY t.cluster_id, t.resource_type;
```

## 三、平台侧（已改，代码）

- `api/stats.py`：资源快照统计由 **N+1 查询**（每集群 max 后再逐条 count）改为
  「每集群每类型最新快照」**一次 JOIN 聚合**。
- 快照保留期**可配置**：系统设置 `snapshot_retention_days`（默认 7 天），
  维护循环每日按该值清理 resource_snapshots / quota_snapshots / pod_metrics / slow_sql_records。
- 慢 SQL 采集阈值：`slow_sql_threshold` 0.001s → **1.0s**（原值把建表 DDL 都当慢 SQL 收集）。

## 四、后续监控

- 观察 resource_snapshots 增长与慢 SQL；必要时降低快照写入频率或启用分区。

## 五、resource_snapshots 按天分区（2026-09-21 实施）

**背景**：每 60s 一轮写入，实施前 550 万行 / 数据 713MB + 索引 913MB；
`cleanup_old_snapshots` 用分批 `DELETE` 清理历史 → 代价高、且 **不回收磁盘空间**。

**方案**（迁移 `r1s2t3u4v5w6_partition_resource_snapshots.py`）：

1. 主键 `(id)` → `(id, snapshot_time)`：MySQL 要求分区列出现在所有**唯一键**中
   （表上除主键无其它 UNIQUE 键；`id` 仍为第一列，AUTO_INCREMENT 正常）。
2. `PARTITION BY RANGE COLUMNS(snapshot_time)`，**每天一个分区** `pYYYYMMDD`
   （边界为次日零点），末尾保留 `pmax VALUES LESS THAN (MAXVALUE)` 兜底，
   避免未来日期无分区导致插入报 `1526`。
3. 迁移时按现有 `MIN(snapshot_time)` 生成分区，并预留 `today + 3` 天。
4. 运行期维护：`resource_service.maintain_snapshot_partitions(retention_days, ahead_days=3)`
   - **DROP 过期分区**：`ALTER TABLE ... DROP PARTITION p20260914` —— 秒级完成且**回收磁盘**
   - **补齐未来分区**：`REORGANIZE PARTITION pmax INTO (新分区..., pmax)`
   - 挂载在 `main.py` 维护循环（每日，与 `snapshot_retention_days` 同源）
5. 分区后 `cleanup_old_snapshots` 自动跳过 `resource_snapshots` 的逐行 DELETE
   （其余表 quota_snapshots / pod_metrics / slow_sql_records 仍走 DELETE）。

**注意**：
- 该 ALTER 需整表重建（分区不支持 INPLACE），请在低峰执行；本次实测耗时见运行日志。
- 降级（`downgrade`）：`REMOVE PARTITIONING` + 主键还原 `(id)`。
- 若平台停机多日，数据会落在 `pmax`，重启后维护任务自动 `REORGANIZE` 出对应日分区。
