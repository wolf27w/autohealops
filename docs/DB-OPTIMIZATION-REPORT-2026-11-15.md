# 数据库优化分析报告（2026-11-15）

> 老板口径：**只优化不动**——只做索引/查询层优化，不做分区、不做去重存、不迁时序、不换硬件。
> 数据来源：`performance_schema`（digest / index usage）、`information_schema`、`mysql.innodb_index_stats`；实例 `192.168.6.99`、库 `autohealops`、MySQL 8.0.33、HDD、7G RAM、buffer pool 1G。
> **本报告只出建议，未执行任何 DDL。**

## 一、现状

| 表 | 行数 | 数据 MB | 索引 MB |
|---|---|---|---|
| resource_snapshots | 6,547,997 | 883.9 | **1,461.6** |
| pod_metrics | 809,471 | 82.8 | **356.8** |
| pod_metrics_5m | 746,504 | 87.1 | 170.3 |
| db_metric_snapshots | 1,049,386 | 81.6 | 126.9 |
| k8s_events | 44,911 | 15.3 | 10.5 |

`resource_snapshots` 索引是数据的 1.65 倍；`pod_metrics` 索引是数据的 4.3 倍。

## 二、可删索引（冗余，收益明确）

「冗余」= 该索引的列序被另一个索引的**前缀**完全覆盖，优化器可改用后者。

| 表 | 索引 | 列 | 被谁覆盖 | 大小 |
|---|---|---|---|---|
| pod_metrics_5m | `ix_pm5m_pod` | (cluster_id,namespace,pod_name) | `uk_pm5m` (cluster_id,namespace,pod_name,bucket_at) | **73.6 MB** |
| resource_snapshots | `idx_cluster_namespace` | (cluster_id,namespace) | `idx_cluster_ns_type_name` (cluster_id,namespace,resource_type,resource_name,id) | 估 ~200 MB |
| db_metric_snapshots | `ix_db_metric_snapshots_db_connection_id` | (db_connection_id) | `ix_dbms_conn_sampled` (db_connection_id,sampled_at) | **17.5 MB** |
| pod_metrics | `ix_pod_metrics_cluster_id` | (cluster_id) | `uk_pod_metric_sample` (cluster_id,namespace,pod_name,sampled_at) | 估 ~10 MB |

**合计约 300 MB 索引空间**，可直接回收。

⚠️ 注意 `idx_cluster_namespace` 的读取次数 1.46 亿（热点），删除后查询会改走 `idx_cluster_ns_type_name`，**需观察 1~2 天**再删下一个。建议顺序：先删 `ix_pm5m_pod`、`ix_db_metric_snapshots_db_connection_id`、`ix_pod_metrics_cluster_id`（低风险），观察后再评估 `idx_cluster_namespace`。

DDL（待你确认后执行，逐条、避开业务高峰）：
```sql
ALTER TABLE pod_metrics_5m DROP INDEX ix_pm5m_pod;
ALTER TABLE db_metric_snapshots DROP INDEX ix_db_metric_snapshots_db_connection_id;
ALTER TABLE pod_metrics DROP INDEX ix_pod_metrics_cluster_id;
-- 观察 1~2 天后再评估
-- ALTER TABLE resource_snapshots DROP INDEX idx_cluster_namespace;
```

## 三、近完全不用的索引（count_star≈0，需业务确认后再删）

- `resource_snapshots.ix_resource_snapshots_snapshot_time`：仅 1,184 次读（对比主键 907 万次）
- 全库共 **89 个**索引 `count_star=0`（本次采样窗口内未使用），例如
  `alerts.ix_alerts_manual_required`、`diagnosis_failures.*`、`custom_tasks.ix_custom_tasks_cluster_id`、`hosts.ix_hosts_cluster_id`、`host_command_audits.ix_host_command_audits_user_id` 等。
  > 说明：count_star 只反映实例启动以来的使用情况，且低频功能（报表/节假日/批量任务）平时本来不跑，**不能直接一刀切删**。建议只处理「表很大 + 有冗余前缀」的，其它保持。

## 四、查询热点（按总耗时排序，Top 6）

| 查询 | 次数 | 总耗时 | 单次均 | 观察 |
|---|---|---|---|---|
| `INSERT INTO resource_snapshots ...` | 245 万 | 1561s | 636ms | 逐行插入，建议按 cluster 批量 executemany（一次 100~500 行） |
| `SELECT alerts ...`（两条不同 where） | 113 万 / 97 万 | 1186s / 598s | 1050ms / 620ms | **每次扫 62 万 / 94 万行**，缺复合索引；需按实际 where 补 `(status, created_at)` 之类 |
| `INSERT INTO pod_metrics_5m ...`（聚合插入） | 132 | 506s | **3.8s/次**，扫 5096 万行 | INSERT…SELECT 聚合全表扫，建议先在 `pod_metrics(sampled_at)` 上加时间窗或用增量 bucket |
| `UPDATE hosts SET last_seen ...` | 11,583 | 409s | **35s/次** | 单行更新不该 35s，疑似锁等待/表锁；需单独查 `innodb_trx` 与该表索引 |
| `DELETE pod_metrics a JOIN pod_metrics b ...`（去重） | 1 | 291s | — | 一次性清理任务，建议改成按时间窗分批 + 用 `id > last` 游标 |
| `SELECT ... COUNT(DISTINCTROW ...) FROM resource_snapshots` | 8,337 | 126s | 15s，排序 2405 万行 | `DISTINCT` + `GROUP BY` 无合适覆盖索引；建议在应用层用「最新快照时间窗」再聚合（已有类似口径） |

**这条 `UPDATE hosts` 35s 是最可疑的**：`hosts` 表很小，正常应 <1ms。建议单独排查（锁等待、`innodb_lock_wait_timeout`、是否有长事务持有行锁）。

## 五、建议执行顺序

1. **零风险**：删除 3 个前缀冗余索引（省 ~100 MB，立即见效）
2. **高收益**：批量化 `resource_snapshots` 插入（636ms/次 × 245 万次是本库最大写放大）
3. **高收益**：查 `hosts.last_seen` 的 35s 源头（可能是长事务/锁等待，不是缺索引）
4. **中收益**：给 alerts 列表查询补复合索引（要先拿到真实 SQL 的 where 子句）
5. **观察后**：`resource_snapshots.idx_cluster_namespace` 是否可删
6. **（暂不做）** 分区 / 去重存 / 换 SSD / 时序外迁 —— 按老板「只优化不动」

## 六、附：本次未执行的操作

- 任何 `ALTER TABLE`、`DROP INDEX`、`ANALYZE TABLE` 均未执行
- 未修改任何实例参数
- 建议 2 与 3 需要改代码（批量插入 / 排查锁等待），属开发任务，可另开

---

## 七、执行记录（2026-11-16）

| 项 | 状态 | 说明 |
|---|---|---|
| 3 个低风险冗余索引（ix_pm5m_pod / ix_db_metric_snapshots_db_connection_id / ix_pod_metrics_cluster_id） | 已不在库中 | 现 `pod_metrics_5m` / `pod_metrics` / `db_metric_snapshots` 已无这三个索引 |
| `INSERT resource_snapshots` 批量写 | ✅ B17-2（`8010a2b`） | add_all → insert() 字典批量，200 行 19.7s → 0.31s |
| `UPDATE hosts SET last_seen` 35s | ✅ B17-2（`8010a2b`） | 改为短事务读取 + SSH 探测期间不持事务 + 整轮一次批量 update 单次 commit |
| `INSERT pod_metrics_5m` 聚合（3.8s/次、扫全表） | ✅ B17-3（`cae0e5f`） | 以 `MAX(bucket_at)` 为水位增量推进，正常只聚合 2~3 个桶；实测 2h 窗口 791ms → 15 分钟窗口 104ms |
| 告警列表 `SELECT alerts ...`（1s+/次、扫 62 万行） | ✅ B17-3（`cae0e5f`） | `group=true` 改为 SQL 侧 GROUP BY 取 MAX(id)+COUNT(*)，只回一页完整行；实测分组/翻页口径一致 |
| `COUNT(DISTINCT) FROM resource_snapshots`（15s/次） | ✅ B17-3（`cae0e5f`） | 仪表盘「每集群每类型最新快照」加 24h 时间窗；实测该查询 7ms（走 `ix_rs_cluster_type_time` loose index scan） |
| `DELETE pod_metrics a JOIN b` 去重（一次性 291s） | 未做 | 已是一次性任务；如再需要，按 `id > ?` 游标 + LIMIT 分批 |
| 补 alerts 复合索引（status, created_at） | 待批 | 当前 `alerts` 仅 348 行，暂无收益，等量级上来再加（DDL 需评审） |
| 删 `resource_snapshots.idx_cluster_namespace`（读 1.46 亿次） | 待观察 | 观察期未满，且是热点索引，未动 |
| 分区 / 换 SSD / 时序外迁 | 暂缓 | 按老板「只优化不动」口径 |
