# 慢 SQL 页面改版需求（对齐阿里云慢日志）

> 登记时间：2026-11-18 10:10
> 状态：**仅登记需求，暂不开发**（老板 2026-11-18 明确）
> 关联：R3.2 慢 SQL 时间维度（`d96cfad`）、R3.3 慢 SQL 优化建议、慢 SQL 逐条优化诊断（`7d6313d` / `0d3a269`）

## 1. 问题（现状）

1. 现在的「慢 SQL」Tab 是一张**混合表**：一行 = 一个 digest（`exec_count` 求和、平均耗时按次数加权），字段是 digest / SQL / 库 / 次数 / 平均 / 最大 / 命中规则建议 / 诊断。
2. **SQL 语句只显示一行**：`ellipsis: true` + Tooltip 悬浮才看全文，多行 SQL 被压成一行，读不了。
3. 表格下方没有「明细」概念：看不到某一次具体执行（时间、耗时、扫描行数、客户端）。
4. 老板目标形态：**和阿里云 RDS「慢日志」页一致**。

## 2. 目标形态（阿里云慢日志页对照）

阿里云 RDS 的慢日志分两块，我们也按这个拆：

### 2.1 慢日志统计（按 SQL 模板聚合）

字段（从左到右）：

| 列 | 来源 | 现状 |
|---|---|---|
| SQL 模板（含参数占位） | `slow_sql_records.sql_text` / `digest` | 已有（digest 列） |
| 执行次数 | `SUM(exec_count)` | 已有 |
| 平均耗时(ms) | 次数加权 | 已有 |
| 最大耗时(ms) | `MAX(max_latency_ms)` | 已有 |
| 平均扫描行数 | 需采集 `SUM_ROWS_EXAMINED / SUM_NO_INDEX_USED` 等 | **缺** |
| 平均返回行数 | 需采集 `SUM_ROWS_SENT` | **缺** |
| 平均锁等待时间 | `SUM_LOCK_TIME` | **缺** |
| 操作 | 趋势 / 诊断 / 明细 | 部分有 |

### 2.2 慢日志明细（单条执行）

字段：

| 列 | 来源 | 现状 |
|---|---|---|
| 执行开始时间 | 需采 `sampled_at` 精确到单次（或改为按次落库/仅保留 TOPN 明细） | **缺** |
| 执行耗时(ms) | 单次 `query_time` | **缺** |
| 执行状态 / 锁等待 | `events_statements_history*` | **缺** |
| 扫描行数 / 返回行数 | 单次 `rows_examined` / `rows_sent` | **缺** |
| 客户端 IP / 用户名 | `host` / `user` | **缺** |
| 数据库 | `schema_name` | 已有 |
| SQL 语句 | `sql_text`（**必须支持换行**） | 已有 |

## 3. 本次要求的 UI 细则（老板明确的两点 + 推导）

### 3.1 SQL 语句支持换行（硬性）

- SQL 列 **禁止** `ellipsis: true` + Tooltip 单行截断。
- 单元格用 `<pre>`/`white-space: pre-wrap; word-break: break-all; margin: 0`，保留原 SQL 的换行与缩进。
- 默认最多显示 5 行，超出折叠为「展开全部（共 N 行）」，点开在表格内展开（不弹窗）。
- 行高自适应；表格加 `size="small"` + `tableLayout="fixed"` 时给 SQL 列明确宽度（如 40%）。

### 3.2 列顺序（老板 2026-11-18 口径）

`慢 SQL（模板）` → `执行耗时` → `库名` → `平均耗时` → `最大耗时` → `连接/执行次数` → `SQL 语句` → `操作(详情/诊断)`

> 注：现状列顺序是 digest/SQL/库/次数/平均/最大，与老板口径不一致，改版时按上面的顺序重排。

### 3.3 筛选条（对齐阿里云）

顶部一行筛选：时间范围（预设：5 分钟/1 小时/6 小时/24 小时/7 天 + 自定义）｜ 数据库 ｜ 客户端 IP ｜ 关键字（SQL 文本模糊）｜ 最小耗时(ms) ｜ 排序（耗时/次数）｜ 刷新（自动刷新可关）。

### 3.4 详情展现

- 行点击/「详情」→ 右侧抽屉（Drawer，非居中弹窗）：
  - 完整 SQL（可换行、可复制）
  - 执行计划（复用现有 `explain` 数据，ALL 红色、filesort/临时表橙色）
  - 索引现状
  - 诊断结论（复用 `SlowSqlDiagnoseModal` 的内容，改成抽屉内 Tab：概览 / 执行计划 / 索引 / 建议）
- 现有「两时段对比」「digest 趋势」保留，入口放到筛选条右侧。

## 4. 后端补齐项（需要迁移/采集改动，评估后再做）

| 项 | 说明 | 风险 |
|---|---|---|
| 扫描行数 / 返回行数 / 锁等待 | `performance_schema.events_statements_summary_by_digest`：`SUM_ROWS_EXAMINED`、`SUM_ROWS_SENT`、`SUM_LOCK_TIME`（按次平均） | 低，只读；需确认实例 `performance_schema` 权限（现有只读账号 `read_lch` 已出现 1142 拒绝案例） |
| 客户端 IP / 用户名 | 同表 `HOST` / `USER` 字段 | 低，但需在 `slow_sql_records` 加列 |
| 慢日志明细（单次执行） | 方案 A：`events_statements_history_long`（内存表，重启丢、默认关闭需开 `performance_schema_consumer_events_statements_history_long`）；方案 B：只做「聚合 + TOPN 采样」，不做逐次 | 中：开 consumer 有性能/内存代价，需老板拍板 |
| Oracle / PG 对齐 | PG 用 `pg_stat_statements`（`rows`、`shared_blks_*`）；Oracle 走 AWR/`v$sql` | 中：字段口径不同，需要映射 |

## 5. 分阶段建议

| 阶段 | 内容 | 依赖 | 工作量 |
|---|---|---|---|
| **P0 纯前端** | SQL 换行 + 列顺序按老板口径 + 筛选条重排 + 行展开 + 详情抽屉（复用已有接口与诊断数据） | 无 | 0.5~1d |
| **P1 字段补齐** | `slow_sql_records` 加 `rows_examined/rows_sent/lock_time/client_host/user_name`；采集侧补 SQL；统计视图展示 | 迁移 + 采集改动 | 1~2d |
| **P2 明细视图** | 「慢日志明细」二级 Tab（逐次执行），需要先定 `events_statements_history_long` 是否开启 | 老板拍板采集方案 | 1~3d |

## 6. 验收口径

- P0：任一条多行 SQL 在表格里按原格式换行显示，超过 5 行折叠可展开；列顺序与第 3.2 节一致；无横向滚动条压住操作列；`npx tsc -b` + `npm run build` 通过。
- P1：慢日志统计列表能显示平均扫描行数/返回行数，且与实例上 `performance_schema` 查询结果一致（抽样核对）。
- P2：明细列表能按时间倒序显示单次执行记录，含客户端 IP、耗时、扫描/返回行数。

## 7. 待老板确认

1. 是否要「慢日志明细」（逐次执行）这一层？还是只做聚合统计 + 诊断就够（阿里云是两层都有）。
2. 采集侧开启 `events_statements_history_long` 有内存/性能代价，是否接受（仅 MySQL 5.7/8.0）。
3. P0 是否先做（纯前端，不动采集与库表）。
