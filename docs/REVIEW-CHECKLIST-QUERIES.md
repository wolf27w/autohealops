# 查询 Review 检查单（时序 / 大表）

> 由来：R27-4（2026-11-10）。历史上踩过的坑全部是「查询没带时间窗 + 索引顺序不对」，
> 与写入量关系不大。新增/修改查询时对照本单自查，Review 时逐条勾。

## 一、适用表（会持续增长）

| 表 | 量级（2026-11-10） | 保留策略 |
|---|---|---|
| `resource_snapshots` | 700 万+ 行 | 按天分区，默认 7 天 |
| `quota_snapshots` | — | 7 天 |
| `pod_metrics` | 100 万+ 行 | 按天分区 + 原始 24h（`POD_METRICS_RAW_RETENTION_HOURS`） |
| `pod_metrics_5m` | 70 万+ 行 | 7 天（降采样） |
| `db_metric_snapshots` | 87 万+ 行 | — |
| `slow_sql_records` | — | 7 天 |
| `k8s_events` | — | 7 天 |

## 二、铁律

1. **必须带时间下限**：`where <时间列> >= :since`。
   - 实时/详情类默认 30~60 分钟（如 `NS_LOOKBACK_MIN`）；
   - 历史类接口用显式参数（`hours`），并钳制上限（本项目统一 `min(hours, 24*30)`）；
   - 兜底全量扫描只允许作为「窗口查不到时的降级路径」，且必须注释说明。
2. **禁止 `LIMIT` 当时间窗**：`limit(200)` 只是结果截断，扫描量仍然按索引范围走。
3. **取「每个 X 的最新一条」**用 `(key, max(time))` 等值 JOIN（或 `MAX(id)` + 等值 JOIN），
   禁止 `xxx IN (SELECT max(...))` 与「全历史 `DISTINCT` / `GROUP BY`」。
4. **索引顺序与查询顺序对齐**：`where`/`group by` 的列顺序要和索引前缀一致（例：
   `db_metric_snapshots(metric_name, db_connection_id, id)` 对应「按 metric 分组取最新」）。
5. **`DISTINCT` / `GROUP BY` 大表前先 EXPLAIN**：`rows` 超过 10 万就要重新设计（加窗口、拆查询、走缓存）。
6. **PR 里贴 EXPLAIN**：新增或修改上述表的查询，PR 描述附 `EXPLAIN` 的 `type/rows` 两列即可。

## 三、真实反例（都实测过）

| 场景 | 病根 | 修法 | 效果 |
|---|---|---|---|
| 仪表盘命名空间去重 | JOIN 只带 `(cluster_id, snapshot_time)` → 行相乘 + `DISTINCT` 600 万行 | 元组精确过滤 + `COUNT(DISTINCT)` | 14.8s → 1.17s |
| `namespace-list` | 无时间窗的 `DISTINCT` | `snapshot_time >= now-60min` + 缓存 | 6.3s → 0.06s |
| 每集群最新 CPU/内存 | `sampled_at IN (各集群最新值)` | `(cluster_id, sampled_at)` 等值 JOIN + Redis 15s 缓存 | 235ms → 6.6ms |
| 每库每指标最新值 | `GROUP BY` 顺序与索引不符 | 对齐索引 + `MAX(id)` JOIN | 1.22s → 0.10s |
| 事件批量 upsert | 冗余二级索引写放大 | 删被前缀覆盖的索引 | 写放大下降 |

## 四、Review 勾选清单

- [ ] 查询涉及第一节的任一张表？
- [ ] 带了时间下限（或明确注释为降级兜底）？
- [ ] 「取最新一条」用的是等值 JOIN，不是 `IN (子查询)` / 全历史 `GROUP BY`？
- [ ] `DISTINCT` / `GROUP BY` 已 EXPLAIN，`rows` 在 10 万以内？
- [ ] 索引前缀与 `where`/`group by` 顺序一致？
- [ ] 结果是否需要缓存（仪表盘/大屏类，TTL 10~45s + last-good 兜底）？
- [ ] 分页参数没有替代时间窗？
