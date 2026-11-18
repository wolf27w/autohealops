# 慢 SQL 页面改版需求（对齐阿里云慢日志）

> 登记时间：2026-11-18 10:10 ｜ 更新：2026-11-18 10:13（老板给出阿里云列清单）
> 状态：**仅登记需求，暂不开发**
> 关联：R3.2 慢 SQL 时间维度（`d96cfad`）、R3.3 慢 SQL 优化建议、慢 SQL 逐条优化诊断（`7d6313d` / `0d3a269`）

## 1. 目标

把「慢 SQL」Tab 改成阿里云 RDS「慢日志」的样子：**一行 = 一次慢查询执行**（明细列表），列清单按老板 2026-11-18 10:13 的口径，并在末尾加「诊断」。

现状问题：
1. 现在的表是 **digest 聚合**（同一 SQL 只一行，次数累加），看不到「某一次执行」。
2. SQL 语句被 `ellipsis: true` 压成一行，多行 SQL 读不了。
3. 缺执行耗时/锁等待/返回行数/扫描行数/访问来源/用户/线程等阿里云已有字段。

## 2. 列清单（老板口径，顺序固定）

| # | 列 | 含义 | 数据来源（阿里云） | 我们现在有没有 |
|---|---|---|---|---|
| 1 | 执行开始时间 | 该次慢查询开始时刻 | 慢日志 `start_time` | 部分（只有 `sampled_at` 采集时间） |
| 2 | SQL ID | SQL 模板唯一标识 | `DIGEST` / `DIGEST_TEXT` 摘要 | 有（我们叫 `digest`，展示前 16 位） |
| 3 | SQL 展开 | 可展开看完整 SQL（**必须支持换行**） | `SQL_TEXT` | 有文本，但前端单行截断 |
| 4 | 数据库 | 库名 | `CURRENT_SCHEMA` / 慢日志 `db` | 有（`schema_name`） |
| 5 | 执行耗时(秒) | 单次执行耗时 | `TIMER_WAIT` / `Query_time` | **缺**（现在只有 digest 平均/最大） |
| 6 | 锁等待耗时(秒) | 单次锁等待 | `LOCK_TIME` | **缺** |
| 7 | 返回行数 | 返回给客户端的行数 | `ROWS_SENT` | **缺** |
| 8 | 扫描行数 | 扫描的行数 | `ROWS_EXAMINED` | **缺** |
| 9 | 访问来源 | 客户端 IP / 来源地址 | `HOST` | **缺** |
| 10 | 用户 | 执行账号 | `USER` | **缺** |
| 11 | 线程 | 会话线程 ID | `THREAD_ID` / `CONNECTION_ID` | **缺** |
| 12 | 表名 | 涉及的表 | SQL 解析 | 可解析（现有 `_extract_tables`） |
| 13 | 标签 | 自定义标签（如「核心链路」/「可忽略」） | 平台自定义 | **缺**（需要一张标签表 + 打标入口） |
| 14 | 操作 | 阿里云是「查看详情/优化建议」；**我们加「诊断」** | — | 诊断已有（`/slow-sql/{digest}/optimize`） |

**操作列口径**：`详情` ｜ **`诊断`**（点开现有诊断抽屉：结论 + 执行计划 + 索引 + 建议，只读不执行）。

## 3. UI 细则

### 3.1 SQL 展开（硬性）

- 第 3 列 `SQL` 禁止 `ellipsis` + Tooltip 单行截断。
- 单元格 `<pre style="white-space: pre-wrap; word-break: break-all; margin: 0">`，保留原换行与缩进。
- 默认最多显示 2 行，超出显示「展开」（点击后该行在表格内增高显示全文，不弹窗）；列宽建议 30~35%。

### 3.2 其它

- 表格 `size="small"`、`scroll={{ x: 'max-content' }}`，操作列 `fixed: 'right'`。
- 数值列右对齐；执行耗时 > 1s 标红（与现有口径一致）。
- 顶部筛选条：时间范围（预设 5 分钟/1 小时/6 小时/24 小时/7 天 + 自定义）、数据库、关键字（SQL 文本）、最小执行耗时(秒)、访问来源、用户、排序（时间倒序 / 耗时倒序）、自动刷新开关。
- 顶部右侧保留现有入口：`两时段对比`、`慢 SQL 趋势`。
- 行点击或「详情」→ 右侧抽屉：完整 SQL、执行计划、索引、诊断结论（Tab 切换）。
- 分页：默认 50 条/页，支持 20/50/100/200。

## 4. 数据采集缺口（开发前必须先定）

阿里云这些列本质来自 **MySQL 慢查询日志 / performance_schema 逐次执行记录**，我们现在只采了 `events_statements_summary_by_digest`（**只有聚合，没有单次**）。三条可选路径：

| 方案 | 做法 | 优点 | 代价/风险 |
|---|---|---|---|
| **A. `performance_schema.events_statements_history_long`** | 打开 consumer：`performance_schema_consumer_events_statements_history_long=ON`，定时捞 `TIMER_WAIT > 阈值` 的行 | 不改库表结构，字段最全（TIMER_WAIT/LOCK_TIME/ROWS_SENT/ROWS_EXAMINED/HOST/USER/THREAD_ID/CURRENT_SCHEMA 全有） | 内存环形缓冲，重启丢；有性能开销（需老板拍板）；部分云托管实例可能不允许改参数 |
| **B. `mysql.slow_log` 表** | `slow_query_log=ON` + `log_output=TABLE` + `long_query_time`，直接 `select * from mysql.slow_log` | 就是慢日志原文，字段含 `start_time / user_host / query_time / lock_time / rows_sent / rows_examined / db / sql_text / thread_id` | 需要 `SELECT` on `mysql.slow_log` 权限；写表有额外写入开销；托管实例可能禁用 |
| **C. 只做聚合 + 采样** | 维持现状 digest 聚合，「执行开始时间」用采集时间近似，其余留空 | 零风险、零采集改动 | 达不到阿里云明细效果，等于只做 UI 换皮 |

> 结论口径：**要真做到老板给的列清单，必须走 A 或 B**（C 只能做出半成品）。建议**方案 A**（按阈值捞，内存表可控），先只在测试实例验证开销。

### 4.1 落库设计（草案）

新增表 `slow_sql_events`（逐次执行）：

```
id, db_connection_id, digest, sql_text, schema_name,
start_time, exec_seconds, lock_seconds, rows_sent, rows_examined,
client_host, db_user, thread_id, table_names, tag,
created_at
索引：(db_connection_id, start_time desc)
```

`tag`（标签）：平台侧打标，存 `slow_sql_tags(digest, tag, note, created_by)`，列表左连接展示；支持「忽略该 SQL」（诊断时提示为已知问题）。保留期：默认 7 天，超期清理任务（避免又一张几百万行表）。

## 5. 分阶段建议

| 阶段 | 内容 | 依赖 | 工作量 |
|---|---|---|---|
| **P0 前端** | 表结构按第 2 节列清单重排 + SQL 换行/展开 + 筛选条 + 详情抽屉（字段先用现有能给的，缺的显示 `-`） | 无 | 0.5~1d |
| **P1 采集** | 方案 A 打通：采集 `events_statements_history_long` → `slow_sql_events` → 列表切到明细数据源（含清理任务） | 老板拍板方案 + 测试实例验证开销 | 2~3d |
| **P2 标签** | 标签表 + 列表打标 + 「忽略」语义 | P1 | 1d |
| **P3 其它库** | PG（`pg_stat_statements` 无逐次，需 `log_min_duration_statement` + 日志解析）、Oracle（AWR/`v$sql`） | 按需 | 各 1~2d |

## 6. 验收口径

- P0：列顺序与第 2 节完全一致（14 列），SQL 多行原样换行、可展开；诊断入口可用；`npx tsc -b` + `npm run build` 通过。
- P1：明细列表按执行开始时间倒序，任取一条与实例 `performance_schema` 查询结果核对：耗时、锁等待、返回行数、扫描行数、用户、来源一致；日期范围筛选准确。
- P2：给某 SQL 打标签后列表显示，可「忽略」。

## 7. 待老板确认

1. **采集方案选 A 还是 B**（A = performance_schema 逐次记录，B = `mysql.slow_log` 表）。这决定能不能真做到阿里云这套列。
2. `events_statements_history_long` 的性能开销能接受吗（仅 MySQL 5.7/8.0，环形缓冲，重启丢）。
3. 明细保留期（建议 7 天）与采集阈值（建议沿用连接的 `slow_sql_threshold`，默认 1s）。
4. 「标签」是纯人工打标，还是要自动打（如按库/按业务线）？
5. P0（纯前端换皮）是否先做。
