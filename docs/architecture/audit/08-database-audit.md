# 08 数据库审计报告（AutoHealOps）

- 审计对象：`/Users/wolf/Documents/project/autohealops`
- 审计方式：**只读**源码审计（未改任何代码/配置/依赖，未安装依赖，未连任何数据库或生产环境，未执行 SQL，未做 git 写操作）
- 审计范围：`api-server/app/api/db_*.py`、`oracle_clients.py`、`api-server/app/services/db_*.py` + `sql_task_service.py` + `slow_sql_fulltext_service.py` + `command_guard.py` + `action_registry.py` + `services/dialects/*` + `models/db_connection.py`/`db_approval.py`/`slow_sql*.py`/`sql_task.py`、`k8s-executor/internal/agentdb/*.go`、`frontend/src/pages/{DbDetail,DbResourceList,SqlTerminal,ApprovalList}.tsx`
- 结论口径：所有结论带 `文件:行`；跨库语义（MySQL/PG 服务端行为）标注为「服务端语义，需实测确认」，无法从源码确认的写「暂时无法确认」

---

## 1. 结论摘要

1. **支持 3 类数据库的「平台直连」与 2 类的「agent 代理」**：直连方言 mysql / pg / oracle（`dialects/__init__.py:12-17`）；agent 侧配置只允许 `mysql`/`pg`（`agentdb/config.go:112-114`），但 agent 侧的 DDL/备份/导入/对象导出**实现上只支持 mysql**（`agentdb/objects.go:35-37` 直接拒绝非 mysql），PG 仅「查询 + 元数据 + 健康采集」。
2. **密码 AES-256-GCM 加密落库、接口不回显**：`password_encrypted` / `readonly_password_encrypted` 两套凭据（`models/db_connection.py:25-28`），`_serialize()` 只回 `has_readonly_password` 布尔（`api/db_connections.py:59-65`），全库 grep 无任何接口返回明文密码；密钥来自环境变量 `ENCRYPTION_KEY`，无默认值（`core/config.py:31`、`core/security.py:20-27`）。
3. **DB 相关接口共 66 个**（`db_connections.py` 47 + `db_approvals.py` 8 + `db_diagnosis.py` 5 + `db_diagnose_batch.py` 1 + `oracle_clients.py` 5），其中**可真实写目标库的入口有 7 条**（exec / import / import-sql / import-chunk / 建库 / 删库 / 表结构变更），**只有 3 条**（exec、import-sql、table-alter/alter-db）会读审核策略。
4. **只读限制全部是「SQL 首词白名单」，仓库内 0 处数据库级只读**（无 `SET SESSION TRANSACTION READ ONLY`、无只读事务、无 `sql_safe_updates`，grep 无命中）；且**未配置只读账号时会静默回落主账号**（`dialects/mysql.py:41-44`、`pg.py:76-80`、`oracle.py:76-80`），因此「只读」在多数现场配置下等于「用业务账号跑没被首词拦住的任意语句」。
5. **审核机制存在结构性绕过**：审核单的 `kind`（query/write/ddl/export）与 `action`（sql/import/create_db/drop_db）**由请求方自填且不与 SQL 内容校验**（`db_approvals.py:120-155`），执行时先看 `action`（`db_approval_service.py:155-179`）；同时**申请人可自己审批**（`db_approvals.py:270-298` 无 requested_by≠approver 校验）→ 单账号即可完成「提交 DDL → 自批 → 执行」全链路。
6. **审计只覆盖 8 类操作、且有 5 处缺口**：`_audit_sql` 11 个调用点（`db_connections.py:382,752,825,922,1042,1067,1207,1344,1377,1976,1978`），但**建库、删库、备份、对象导出、审批单执行（`db_approval_service.execute_request`）都不写 audit_records 哈希链**；SQL 文本只留前 500 字符（`db_connections.py:490-494`）。
7. **直连执行没有语句超时、也没有服务端 LIMIT**：`_run_sql` 的 `timeout` 参数只作用于 agent 路径（`db_connections.py:431-441`），直连走 `db_direct_service` 时被丢弃；MySQL 用默认缓冲游标（`mysql.py:381-392`），`fetchmany(limit)` 只是「返回前 N 行」，**整份结果集已先读进 api-server 内存**；MySQL/PG 连接均无 `read_timeout`/`command_timeout`（`mysql.py:374-379`、`pg.py:123-131`）。
8. **慢 SQL 采集有两套、常态开销明确**：摘要级（`events_statements_summary_by_digest`，按 `collect_interval` 默认 5 分钟，`db_collector.py:26-55`）+ 明细级（`events_statements_history_long`，**每 30 秒一轮**，`main.py:376-378`），明细采集每轮还要额外跑 `information_schema.COLUMNS` 与 `setup_consumers` 探测（`db_slow_event_service.py:243-256`）；agent 侧另每 10 秒一连接采集（`cmd/agent/main.go:615-640`）。

---

## 2. 证据与实现细节

### 2.1 支持的数据库类型与连接方式

| 类型 | 平台直连（source=direct） | agent 代理（source=agent） |
|---|---|---|
| MySQL（含 TiDB/OceanBase 等兼容库） | 全能力：query/exec/schema/ddl/建库删库/导出/导入/备份（`mysql.py:365-372`） | 全能力：Query/Schema/DDL/Export/Import/Backup/CreateDB/DropDB/ImportSqlRaw（`agentdb/*.go`） |
| PostgreSQL | query/exec/schema/ddl/list_databases；**无 export/import/backup**（`pg.py:116-120`） | 仅 Query/Schema/健康采集；DDL/Export/Import 直接返回「暂不支持」（`agentdb/dialect.go:159-169`、`objects.go:35-37`） |
| Oracle | query/exec/schema/ddl/建库删库/导出/导入/备份，支持 service_name / sid / tns 与 sysdba/sysoper（`oracle.py:620-642`） | **不支持**（`agentdb/config.go:112-114` 拒绝） |

驱动：MySQL → `aiomysql`（api-server，`mysql.py:1-14`）/ `go-sql-driver/mysql`（agent，`query.go:13`）；PG → `asyncpg`（`pg.py:1-20`）/ `pgx/v5 stdlib`（`pg.go:16`）；Oracle → `oracledb`（thin/thick 可选，`oracle.py:626-642`、`db_collector.py:360-400`）。

通道：直连 = api-server 直接 TCP 连库（凭据在平台，解密后使用）；agent = 平台下发 `{key, sql}` 经 gRPC 隧道到集群内 agent，agent 用本地配置文件里的账密连库，**凭据不出集群**（`db_agent_service.py:24-31`、`agentdb/collect.go:72-108`）。

### 2.2 连接信息存储与加密

- 模型：`db_connections`，字段 `host/port/username/password_encrypted/readonly_username/readonly_password_encrypted`，`source`（direct/agent）+ `cluster_id` + `agent_db_key`（`models/db_connection.py:18-60`）。
- 加密：`encrypt_aes256_gcm` = AES-256-GCM，随机 12B IV，输出 `base64(IV+ciphertext+tag)`（`core/security.py:20-27`）；写入口在 `create_connection`（`db_connections.py:307-311`）与 `update_connection`（`1599-1603`）。
- 回显：`_serialize()` 不含任何密码字段，只有 `has_readonly_password` 布尔（`db_connections.py:52-86`）；`db-approvals` 的 `_req_dict` 也只回 `download_token`（`db_approvals.py:92-109`）。
- 结论：**API 不回显密码，已实现**。风险点在于密钥单点（`ENCRYPTION_KEY` 无默认值，但全平台共用一把钥匙，解密后明文出现在 api-server 进程内存，见 `mysql.py:38-52`）。

### 2.3 连接池 / 超时 / 最大行数 / 结果集限制

| 项 | 目标库直连 | agent 代理 | 位置 |
|---|---|---|---|
| 连接池 | **无池**，每次执行 `aiomysql.connect` / `asyncpg.connect` / `oracledb.connect` 新建、用完即关 | 无池，每次 `sql.Open` + `SetMaxOpenConns(1)`，用完 Close | `mysql.py:374-379,381-392`；`query.go:94-99`；`objects.go:38-45` |
| 连接超时 | MySQL `connect_timeout=8`；PG `timeout=8`；Oracle 无显式超时 | DSN `timeout=10s`；ctx 30s | `mysql.py:378`；`pg.py:129`；`query.go:85,101` |
| 读/执行超时 | **无**（aiomysql 无 `read_timeout`，asyncpg 无 `command_timeout`，oracledb 无 call timeout） | `readTimeout=30s` + `context.WithTimeout(30s)`；备份 30min；导入单条 60s/总 30min | `query.go:85,101`；`backup.go:50`；`import_sql.go:12-17` |
| 默认返回行数 | 200（`db_direct_service.query` 默认；API `/query` 也 200） | 200 | `db_connections.py:430`；`query.go:29-32` |
| 硬上限 | 5000（`MAX_ROWS`），`limit` 被 `min(...,5000)` 夹紧 | `hardMaxRows=5000` | `mysql.py:31,382`；`query.go:30-32,76-82` |
| 结果下载上限 | `/query/download` 5000；`/export-result` 默认 10000、最大 50000 | 同左（agent 走同一 `_run_sql`） | `db_connections.py:1372`；`db_result_export_service.py:52` |
| 备份上限 | MySQL 逻辑备份 50MB（agent 侧 `maxBackupBytes`）；直连侧无上限（流式） | 50MB | `backup.go:13,59-61`；`db_connections.py:1300-1305` |
| 平台自身 DB 池 | `pool_size=20, max_overflow=40, pool_recycle=1800`，未开 pre_ping（`core/database.py:20-27`） | — | — |

> 关键点：`_run_sql(c, sql, limit=..., allow_write=..., timeout=60)` 的 `timeout` **只传给 agent 分支**（`db_connections.py:431-441`），直连分支 `db_direct_service.query(c, sql, limit, database)` 无超时参数，`timeout` 被静默丢弃。

### 2.4 SQL 执行链路

```
浏览器(SqlTerminal) 
  ├─ 读语句 → POST /db-connections/{id}/query        (require_any_user + db 权限 + 可选 query 审核)
  ├─ 写语句 → POST /db-connections/{id}/exec         (require_admin + confirm=true + classify 审核)
  ├─ 下载   → POST /db-connections/{id}/query/download / /export-result
  ├─ 导入   → POST /import | /import-sql | /import-chunk
  └─ 结构   → POST /table-alter | /databases/{n}/alter | POST/DELETE /databases
                     ↓
        _run_sql()  ── agent 库 ──→ agent_gateway.call(cluster, "QueryDatabaseByKey", {key,sql,limit,allow_write})
                     │                                        ↓ 集群内 agent → agentdb.QueryByKey → HandleQuery
                     └─ 直连库 ──→ db_direct_service → dialects.{mysql,pg,oracle}.query/exec_sql
                     ↓
        审计 _audit_sql()（audit_records，SQL 前 500 字符）
```

- 谁发起：任何对该连接有 read 权限的登录用户可发只读查询（`user_permission_service.check_db_permission`，`user_permission_service.py:91-120`）；写操作仅 admin（`/exec`）或 operator/项目管理员（导入、DDL）。
- 是否审核：见 2.6。
- 谁执行：agent 库由集群内 agent 执行（平台不持凭据）；直连库由 api-server 进程执行。
- 结果返回：`{columns, rows, truncated}`，逐行 `_norm` 归一化；写操作返回 `affected_rows/last_insert_id`（`mysql.py:394-403`）。

### 2.5 只读限制如何实现 / 能否绕过

实现共 **6 处互不一致的白名单**：

| 位置 | 允许的首词 | 备注 |
|---|---|---|
| `db_connections.py:352-353`（/query） | select, show, describe, desc, explain | **漏了 `with`** |
| `db_connections.py:1356-1357`（/query/download） | 上述 + with | |
| `db_connections.py:1404,1424-1431`（SQL 定时任务） | + with | |
| `db_result_export_service.py:42-44` | + with，且允许前导 `(` | |
| `db_approval_service.py:36-38,67-76` | query 头含 with；**其余一律按 write 处理** | 用于审核分类 |
| `mysql.py:30` `READ_HEADS` | 定义了但 **query() 里未使用**（死常量） | |
| `agentdb/query.go:35-46` `isWriteSQL` | 首词不在白名单即「写」；允许 with | 与 `allow_write` 联动 |

**没有任何数据库级只读**：

- `grep -rn "READ ONLY|read_only|SET SESSION|sql_safe_updates|max_execution_time"` 在 `dialects/*.py`、`db_collector.py`、`agentdb/*.go` 中 **0 命中**。
- `connect(readonly=True)` 只是「优先取只读账号」（`mysql.py:38-44`）：`readonly_username or username`、`readonly_password or password`。**未配置只读账号时，读路径就是用主账号**，而主账号通常是业务读写账号。

**可绕过面（首词检查看不到的语句类型）**：

1. `WITH cte AS (...) DELETE FROM t` / `WITH ... UPDATE`：首词 `with` → 判为只读。MySQL 8 与 PG 均支持 CTE 修饰 DELETE/UPDATE（服务端语义，需按版本实测）。`/query`、`/query/download`、`/export-result`、`sql_task`、agent `isWriteSQL` 全部按只读放行。
2. `SELECT ... INTO OUTFILE '/path'` / `SELECT ... INTO DUMPFILE`：合法 SELECT 首词，会在**数据库服务器上写文件**（需 FILE 权限）。
3. `SELECT ... FOR UPDATE` / `FOR SHARE`：读语句产生行锁，可阻塞业务写入（可用作可用性攻击）。
4. `EXPLAIN ANALYZE <stmt>`：PG 的 `EXPLAIN ANALYZE` 会**真实执行**被解释语句（含 INSERT/UPDATE/DELETE，服务端语义）；MySQL 8.0.18+ 的 `EXPLAIN ANALYZE` 亦会执行（对 UPDATE/DELETE 的支持按版本，**暂时无法确认**具体版本边界）。
5. `SELECT load_file('/etc/passwd')`、`SELECT ... FROM mysql.user`（只读账号权限过大时）→ 数据外带。
6. 多语句：MySQL `aiomysql` 未开 `CLIENT.MULTI_STATEMENTS`，`go-sql-driver` 未开 `multiStatements`（`query.go:85` DSN 无该参数），`;` 拼接会被服务端报语法错 → **该绕过面目前不成立**（PG `asyncpg conn.execute` 支持多语句，但只在 admin 的 `/exec` 路径可达）。

### 2.6 数据库审核机制

- 策略：`db_approval_policies` 一连接一行，四个布尔 `need_query/need_write/need_ddl/need_export`，**缺省全 false = 不审核**（`models/db_approval.py:15-27`、`db_approval_service.py:41-55`）。
- 需要审核的操作：`/query`(query)、`/exec`(classify_sql 结果)、`/import-sql`(classify_sql_batch)、`/table-alter`(ddl)、`/databases/{n}/alter`(ddl)、`/export`(export)、`/export-result`(export，且还需实例开关 `require_download_approval`)。命中策略时返回 `code=428` 信封，前端弹「提交审核」（`api.ts:46-47`、`SqlTerminal.tsx:462-468,701-730`）。
- 审批人：admin，或该库挂在某项目且用户是该项目 `manager`（`db_approval_service.py:297-314`）。
- 参数是否可篡改：审核单 payload 落库后无更新接口，审批执行的是**库里的 payload**（`execute_request` 重新读取，`db_approval_service.py:137-152`），所以「提交后再改」不成立；**但 `kind` 与内容不校验、`action` 优先于 `kind`**，等价于「提交时就能选一个不触发策略的壳」。
- 是否存在绕过审核的直接执行入口：**有 6 处**（见 DB-05/06/07）。

### 2.7 SQL 风险判断规则清单

- 唯一「风险分类」是首词映射：`_QUERY_HEADS` / `_DDL_HEADS`(create,alter,drop,truncate,rename) / `_WRITE_HEADS`(insert,update,delete,replace)，**未识别按 write 从严**（`db_approval_service.py:36-38,67-76`）；批量脚本用词边界正则，DDL 优先级最高（`79-91`）。
- 没有高危函数黑名单（`load_file`、`sleep`、`benchmark`、`pg_read_file`、`dblink`、`xp_cmdshell` 等全无拦截），没有 `;`/注释/存储过程语义分析。
- 非安全类的「SQL 规则」是**性能建议**（SELECT *、前置 %LIKE、函数包列、大 IN、无 WHERE、ORDER BY 无 LIMIT、GROUP BY、OR、JOIN 无 ON，`db_anomaly_service.py:270-320`），不参与放行决策。
- 漏判：见 2.5 全部绕过面 + `kind` 自填（2.6）。误判：`/query` 拒绝合法 CTE（`with`），导致功能不可用（DB-15）。

### 2.8 执行记录与审计

- `audit_records` 哈希链：`hash_value = SHA256(previous_hash + 字段)`，行锁防并发分叉（`audit_service.py:48-73`、`models/audit.py:10-27`）。
- DB 侧写入字段：`trigger_snapshot`（mode/source/cluster_id/agent_db_key/db 名）+ `execution_detail`（user、`sql[:500]`、elapsed_s、rows/affected、ok、`error[:200]`）（`db_connections.py:481-499`；query 路由内联同一口径 `379-395`）。
- **不记录**：邮箱收件人（sql_task 只记 rows/ok）、导出文件内容、备份产物、审批单执行结果明细。
- 失败是否记录：**执行阶段的失败会记**（`ok=false` + 错误前 200 字符）；**前置拒绝不记**（400/403/404/428，例如无权限访问、命中审核被拦），这些在安全视角恰是最需要留痕的事件。
- 覆盖缺口：建库(`930-969`)、删库(`971-1010`)、备份(`1242-1310`)、对象导出(`832-877`)、`execute_request`（`db_approval_service.py:130-256`）均无 `audit_records` 写入。

### 2.9 慢 SQL 采集与诊断

| 采集器 | 数据源 | 频率 | 凭据 | 只读性 | 开销 |
|---|---|---|---|---|---|
| `db_collector.collect_connection_slow_sql` | `performance_schema.events_statements_summary_by_digest`（按 SCHEMA_NAME 过滤 + `NOT REGEXP '^(ALTER|CREATE|...)'` + `ORDER BY SUM_TIMER_WAIT DESC LIMIT 200`）；PG `pg_stat_statements`；Oracle `v$sqlstats` | 每连接 `collect_interval`（默认 5 分钟，`models/db_connection.py:29`） | **主账号**（`db_collector.py:212-213`） | 只读 SQL | 实例级内存表全量聚合 + REGEXP + 排序，digest 多（万级）时有 CPU 开销 |
| `db_slow_event_service.collect_connection_slow_events` | `performance_schema.events_statements_history_long` → 退化 `events_statements_history`，JOIN `performance_schema.threads`，`TIMER_WAIT > 阈值 AND TIMER_START > 水位 ORDER BY TIMER_START LIMIT 500` | **每 30 秒一轮**（`main.py:376-378`） | 只读账号优先（`db_slow_event_service.py:377-383`） | 只读 SQL | 每轮 3~4 条元数据查询（`information_schema.COLUMNS`、`setup_consumers`、`global_status`）+ 环形缓冲有界扫描 500 行；连接多时是**常态负载** |
| `db_collector.collect_db_health` | `SHOW GLOBAL STATUS`、`information_schema`、`data_lock_waits`、`memory_summary_global_by_event_name` | 每 `collect_interval` | 只读账号优先（`db_collector.py:1206`） | 只读 | 中等（`SHOW GLOBAL STATUS` 数百变量） |
| agent `CollectOnce` | `SHOW GLOBAL STATUS` + `data_lock_waits` 计数 + 变量；withSlow 时加表空间 TOP10 + 慢 SQL TOP5 | **每 10 秒**（`cmd/agent/main.go:620-636`），慢段 60 秒 | agent 本地账号（单一账号） | 只读 | 每库每 10s 新建/关闭一次连接（连接 churn），生产实例上属持续小幅负载 |

诊断：`db_diagnosis_service.diagnose_db` 采集健康指标 + 慢 SQL → 汇总文本给 ai-service `/db-diagnose`，**只出方案不执行**（`db_diagnosis_service.py:83-120`，文件头注释明确红线）；慢 SQL 优化 `sql_optimize_service` 只跑 `EXPLAIN`（`653-655`）、`SHOW INDEX`、`pg_indexes`，`_explain` 对非 select/with 直接跳过（`509-514`），Oracle 跳过。

### 2.10 备份 / 导入 SQL 的危险面

- 备份（`/backup`，`require_any_user` + 连接 read 权限）：导全库结构+数据，MySQL/agent 均支持；agent 侧先 `SHOW CREATE TABLE` 再 `SELECT * FROM` 逐表全量（`backup.go:108-148`），50MB 上限，**无审批、无审计、无行数/表数限制**；直连侧 `StreamingResponse` 边生成边发（`db_connections.py:1300-1305`）→ 等于「任何有读权限的人可整库拖走数据」。
- 导入（三入口）：
  - `/import`（admin + confirm，≤5MB）：SQL 或 CSV。CSV 走参数化 INSERT（`objects.go:305-345`），SQL 走 `splitSQL` 逐条 `ExecContext`（无超时、遇错即停）。
  - `/import-sql`（operator + `classify_sql_batch` 审核，不限大小）：直连逐条执行，`asyncio.wait_for` 60s/条、10MB/条、200 条/批（`mysql.py:662-696`）；agent 侧上限 200MB 总、10MB/条、30min 总（`import_sql.go:12-17`）。
  - `/import-chunk`（admin + confirm，Redis 幂等）：**不查审核策略**。
- 危险点：导入是「任意 SQL 执行器」，只要拿到 operator 就能跑任意 DDL/DML（仅受 `classify_sql_batch` 一个正则首层限制，且该函数只看关键词出现，不看上下文）；`mode=skip` 会「跳过错误继续执行」，部分失败可留下半完成状态且无事务（MySQL 逐条 autocommit，`mysql.py:374-379`）。

### 2.11 前后端能力对齐

| 能力 | 后端 | 前端 | 是否对齐 |
|---|---|---|---|
| CTE（`with ...`）查询 | `/query` 白名单**不含 with** → 400 | `READ_HEADS` 含 with（`SqlTerminal.tsx:73`）→ 走 `/query` | **不对齐（功能坏）** |
| 写操作入口 | `/exec` 仅 admin | 不按角色隐藏，任何用户可点「执行」，非 admin 才 403（`SqlTerminal.tsx:454`） | 不对齐（体验/误导） |
| PG 导出/导入/备份 | 后端 `UnsupportedError` → 400 文案 | 按钮仍展示 | 不对齐（提示兜底） |
| agent 库 PG 的 DDL/导出/导入 | agent 返回「暂不支持 db_type=pg（当前仅 mysql）」 | 与直连同样展示入口 | 不对齐 |
| 审核 428 | 5 个入口会返回 428 | 统一弹「提交审核」弹窗 | 对齐 |
| SQL 定时任务行数 | 直连 5000 行（`MAX_ROWS`）；agent 默认 200 行（`agent_query` 未传 limit） | — | **不对齐（同功能不同结果）** |
| 结果集下载开关 | `/export-result` 校验 `allow_result_download` / `require_download_approval`；`/query/download` **不校验** | 下载按钮走 `/export-result` | 后端双通道不一致 |

---

## 3. 功能状态清单

| 功能 | 状态 | 证据（文件:行） | 备注 |
|---|---|---|---|
| 数据源管理（增删改查、分组、环境标签） | 已实现（有代码依据） | `api/db_connections.py:287-341,1549-1612` | admin 建，operator 可管 |
| 连接密码加密存储 / 不回显 | 已实现（有代码依据） | `models/db_connection.py:25-28`；`core/security.py:20-27`；`api/db_connections.py:59-65` | 单密钥全平台共用 |
| 独立只读账号 | 部分实现 | `mysql.py:38-44`、`db_collector.py:337-352` | 未配置时静默回落主账号；慢 SQL 摘要采集固定用主账号 |
| 只读查询（agent / 直连） | 部分实现 | `db_connections.py:344-400`；`query.go:35-120` | 仅首词白名单，可绕过 |
| SQL 执行（DML/DDL） | 已实现（有代码依据） | `db_connections.py:1312-1349` | admin + confirm |
| 数据库操作审核（策略/审核单/审批/定时执行） | 已实现（有代码依据），但可绕过 | `db_approvals.py:53-336`、`db_approval_service.py:41-279` | 见 DB-01/02/05/06/07 |
| 审核单导出结果一次性下载 | 已实现（有代码依据） | `db_approvals.py:319-336`、`db_approval_service.py:195-248` | token 24h、一次性 |
| 表结构设计器（改表 / 改库字符集） | 已实现（有代码依据） | `db_connections.py:717-830` | 标识符/类型白名单校验 |
| 库/表/数据导出（SQL 文本） | 已实现（有代码依据） | `db_connections.py:832-877`、`objects.go:142-268` | 单表 ≤5000 行 |
| 整库备份 | 已实现（有代码依据） | `db_connections.py:1242-1310`、`backup.go:20-85` | 无审批无审计 |
| 导入（SQL / CSV / 分片 / import-sql） | 已实现（有代码依据） | `db_connections.py:878-1077,1119-1241` | 审核覆盖不全 |
| 查询结果集下载（CSV） | 已实现（有代码依据） | `db_connections.py:1350-1401,1932-1985` | 两通道策略不一致 |
| SQL 定时任务（cron + 邮件） | 已实现（有代码依据） | `sql_task_service.py:59-320` | 无审核、可任意外发 |
| 慢 SQL 摘要采集（digest） | 已实现（有代码依据） | `db_collector.py:83-280` | 主账号 |
| 慢 SQL 明细采集（history_long） | 已实现（有代码依据） | `db_slow_event_service.py:366-452` | 仅 MySQL 直连，30s 一轮 |
| 慢 SQL 诊断 / 优化建议（EXPLAIN + AI） | 已实现（有代码依据） | `sql_optimize_service.py:462-655`、`db_diagnosis_service.py:83-160` | 只出方案不执行 |
| 数据库健康指标采集与异常判定 | 已实现（有代码依据） | `db_collector.py:487-1190`、`db_anomaly_service.py:74-260` | agent/直连双口径 |
| Oracle Instant Client 管理 | 已实现（有代码依据） | `api/oracle_clients.py:23-130` | admin；自定义 URL 下载 |
| 数据库操作审计（哈希链） | 部分实现 | `api/db_connections.py:481-499` | 5 类操作无审计 |
| 语句级超时 / 资源组限制 | 完全未实现 | `mysql.py:381-392`（无 timeout） | 仅 agent 路径有 30s/60s |
| 数据库级只读（只读事务/会话） | 完全未实现 | grep 0 命中 | — |

---

## 4. 问题清单

```
问题编号：DB-01
问题名称：审核单 kind/action 由请求方自填且不与 SQL 内容校验，可用 query 壳执行写操作
问题等级：P0
所属模块：数据库审核（db_approvals / db_approval_service）
问题描述：创建审核单时 kind 直接取自请求体（只校验枚举合法），action 也由请求方指定；执行审核单时先按 action 分支（import/create_db/drop_db），再按 kind 分支（query/write/ddl 决定 allow_write）。因此 operator 可提交 {kind:"query", action:"sql", sql:"delete from t"} 或 {kind:"query", action:"import", sql:"DROP TABLE x"}：策略只按 need_query 判断（默认 false 时甚至无需审核），执行时分别以 query 路径（dialect.query 仍会真实执行并 autocommit 提交）或 import 路径执行写操作。
代码位置：api-server/app/api/db_approvals.py:120-155；api-server/app/services/db_approval_service.py:155-179,180-194
涉及函数或类：create_db_approval、db_approval_service.execute_request、classify_sql
调用链：POST /api/v1/db-approvals → create_request(payload 原样落库) →（审批）→ execute_request → action/kind 分支 → _run_sql(allow_write=(kind!="query")) 或 db_direct_service.import_sql
根本原因：kind 被当作「用户声明」而不是「由 SQL 推导的事实」；execute 分支对 kind 与 action/SQL 的一致性没有交叉校验。
可能影响：可绕过 need_write/need_ddl 策略，对生产库执行任意 DML/DDL；与 DB-02 组合后单人即可完成全链路。
修改建议：（1）kind 一律由服务端 classify（对 SQL 做 AST/词法判定，多语句取最高风险）推导，忽略客户端传值；（2）action 与 kind 强绑定（import→ddl/write 至少其一）；（3）执行前再次校验 payload 的 SQL 风险级别与 kind 匹配，不匹配拒绝；（4）query 路径只允许「不含写语句」的 SQL，否则强制走 exec 且必须 allow_write。
验证方法：以 operator 身份提交 kind=query + sql="delete from <测试表> where 1=1"，审批（或策略全关时直接）执行后检查目标表行数是否变化；预期应被拒绝。
是否需要立即处理：是
```

```
问题编号：DB-02
问题名称：审核无四眼原则，申请人可自审自批（项目管理员）
问题等级：P0
所属单位：数据库审核（db_approvals）
所属模块：数据库审核
问题描述：审批接口只校验 can_approve_db(user, conn_id)（admin 或该库所属项目的 manager），没有校验 approved_by ≠ requested_by；create 接口要求 operator，项目的 manager 通常同时具备 operator 角色，因此「自己提单、自己批准、立即执行」可闭环。
代码位置：api-server/app/api/db_approvals.py:270-298；api-server/app/services/db_approval_service.py:297-314
涉及函数或类：approve_db_approval、can_approve_db
调用链：POST /db-approvals（requested_by=当前用户）→ POST /db-approvals/{id}/approve（同一用户）→ execute_mode=immediate → execute_request
根本原因：审批权限只看「能不能批这个库」，没有「能不能批这张单」的职责分离校验。
可能影响：审核机制形同自证；对 P0 级写操作失去第二人复核，合规审计（谁批的、是否独立）无法成立。
修改建议：approve/reject 增加 requested_by != 当前用户 的强制校验（admin 也不例外，或至少需要显式双人开关）；审核单记录 approver≠requester 的校验结果；对 admin 自批增加二次确认 + 审计告警。
验证方法：同一账号（非 admin 的项目 manager）提交并被拒绝审批自己的单，预期返回 403。
是否需要立即处理：是
```

```
问题编号：DB-03
问题名称：只读限制仅靠 SQL 首词白名单，缺少语句类型校验与数据库级只读，可被 WITH/INTO OUTFILE/FOR UPDATE 等绕过
问题等级：P0
所属模块：SQL 执行（db_connections / dialects / agentdb）
问题描述：所有「只读」入口都只取第一个词比对白名单（select/show/describe/desc/explain[/with]），既不做语法/AST 校验，也不设置只读事务或只读会话；仓库内不存在任何数据库级只读保障。首词为 select/with 但语义为写的语句因此可通行：WITH ... DELETE/UPDATE、SELECT ... INTO OUTFILE/DUMPFILE、SELECT ... FOR UPDATE（行锁阻塞）、EXPLAIN ANALYZE（PG 会真实执行）。直连时 query 走 dialect.query()，其内部 `cur.execute(sql)` + autocommit=True 会真实提交。
代码位置：api-server/app/api/db_connections.py:352-353,1356-1357,1404；api-server/app/services/dialects/mysql.py:374-392；api-server/app/services/dialects/pg.py:134-152；k8s-executor/internal/agentdb/query.go:35-46,105-120
涉及函数或类：query_connection、download_query、is_query_sql、MySQLDialect.query、PGDialect.query、isWriteSQL/HandleQuery
调用链：POST /db-connections/{id}/query → head 白名单 → _run_sql(allow_write=False) → dialect.query → cur.execute(用户 SQL)
根本原因：把「首词」当成「语句类别」，且把只读责任完全放在应用层文本检查上。
可能影响：任意有 read 权限的用户都可能对生产库造成写/锁/落盘副作用（例如 WITH ... DELETE 删数据、FOR UPDATE 阻塞业务、INTO OUTFILE 在 DB 主机留文件）。
修改建议：（1）只读路径改用数据库级保障：MySQL `SET SESSION TRANSACTION READ ONLY` + 只读事务包住整条语句；PG `SET TRANSACTION READ ONLY`；Oracle `SET TRANSACTION READ ONLY`；agent 侧同样在连接后设置。（2）再加一层语句解析（sqlglot/sqlparse 或方言级 lexer）判定 statement type，禁止 INTO OUTFILE/DUMPFILE、FOR UPDATE、CALL/DO、EXPLAIN ANALYZE。（3）要求配置专用只读账号并在未配置时拒绝只读入口（见 DB-04）。
验证方法：对测试库执行 `WITH x AS (SELECT 1) DELETE FROM t`、`SELECT * FROM t FOR UPDATE`，预期 400/拒绝；同时 `SHOW SESSION TRANSACTION ISOLATION LEVEL` 之外用 `SELECT @@tx_read_only` 验证会话只读已生效。
是否需要立即处理：是
```

```
问题编号：DB-04
问题名称：未配置只读账号时「只读」路径静默回落主账号（业务读写账号）
问题等级：P1
所属模块：数据源/执行链路
问题描述：_info(readonly=True) 取 `readonly_username or username` / `readonly_password or password`；_db_ready 只要求「有任一密码」。因此只填主账号时，所有查询、导出、备份、EXPLAIN、慢 SQL 采集都用业务账号执行，只读完全依赖 DB-03 的首词检查。慢 SQL 摘要采集甚至固定用主账号（db_collector.py:212-213），与健康采集（只读优先）不一致。
代码位置：api-server/app/services/dialects/mysql.py:38-52；pg.py:72-88；oracle.py:76-80；api-server/app/api/db_connections.py:407-418；api-server/app/services/db_collector.py:212-213
涉及函数或类：_info、_db_ready、collect_connection_slow_sql
调用链：query/export/backup → connect(readonly=True) → _info → 回落主账号
根本原因：把「没有只读账号」当成可接受状态，用主账号兜底；平台侧没有「只读账号必须配置」的强约束。
可能影响：最小权限原则失效；一旦首词检查被绕过（DB-03），影响范围从「只读」直接变成「业务账号全权限」；Oracle 场景下若主账号是 SYS 且 role=sysdba，等于把 SYS 交给任意有 read 权限的用户（见 DB-14）。
修改建议：只读类操作强制要求 readonly_username/readonly_password，缺失时返回明确错误并提示配置；慢 SQL 摘要采集改用只读账号；在连接表单上把只读账号标为「生产环境必填」。
验证方法：创建一个只填主账号的连接，调用 /query 预期返回「未配置只读账号」而不是成功。
是否需要立即处理：是
```

```
问题编号：DB-05
问题名称：整库备份接口任意读权限用户可用，无审批、无审计、无行数限制（拖库通道）
问题等级：P1
所属模块：数据源/备份
问题描述：GET /db-connections/{id}/backup 仅要求 require_any_user + 连接 read 权限，直接流式返回「全库结构 + 全部数据」的 SQL 文本；不检查 need_export 策略、不写 audit_records、不限表数量。agent 侧对每张表执行 `SELECT *`（backup.go:120-148），直连侧同样全量导出。
代码位置：api-server/app/api/db_connections.py:1242-1310；k8s-executor/internal/agentdb/backup.go:20-148
涉及函数或类：backup_database、BackupByKey、dumpTable
调用链：GET /db-connections/{id}/backup → （agent）_agent_call("Backup")/(直连) db_direct_service.backup_sql → StreamingResponse
根本原因：备份被当作「只读展示功能」，未纳入导出类操作的管控口径（审批 + 审计 + 开关）。
可能影响：任何有该库读权限的账号（含只读运营人员）可一次性导出生产全库数据，形成绕过 need_export 与 allow_result_download 的外带通道，且事后无审计可追。
修改建议：（1）备份按 export 类操作处理：检查 need_export、`allow_result_download`、`require_download_approval`；（2）强制写审计（操作人/库/表数/字节数/耗时）；（3）增加表/字节/耗时上限与并发限制；（4）考虑只允许 admin 或项目 manager 发起。
验证方法：以仅有 read 权限的账号调用 /backup，预期 403 或 428；并检查 audit_records 是否生成记录。
是否需要立即处理：是
```

```
问题编号：DB-06
问题名称：结果集下载存在两条通道，/query/download 绕过实例开关与下载审批
问题等级：P1
所属模块：数据源/导出
问题描述：/export-result 会校验 allow_result_download 与 require_download_approval 并写审计；而 /query/download（同样返回 CSV 全量结果）只做首词白名单 + read 权限，不校验这两个开关、不查 need_export、不写审计。
代码位置：api-server/app/api/db_connections.py:1350-1401（对比 1932-1985）
涉及函数或类：download_query、export_query_result
调用链：POST /db-connections/{id}/query/download → _run_sql(limit=5000) → CSV Response
根本原因：同一业务能力两套实现，策略只落在新通道上，旧通道未收口。
可能影响：实例上「禁止下载结果集」或「下载需审批」的配置可被旧接口完全绕过；导出行为无审计。
修改建议：两条通道合并到一个实现，统一走 export_result + 策略校验 + 审计；或在 /query/download 前置同样的开关与 need_export 判断。
验证方法：把某连接 allow_result_download=false，调用 /query/download 预期 403。
是否需要立即处理：是
```

```
问题编号：DB-07
问题名称：多个写入口不查审核策略（/import、/import-chunk、agent 库建库/删库）
问题等级：P1
所属模块：数据源/导入与 DDL
问题描述：（1）/import（admin）与 /import-chunk（admin）直接执行导入，全程无 _check_approval；（2）create_database / drop_database 的 agent 分支在 _check_approval 之前就 return，agent 库的建库/删库永不触发 ddl 审核；（3）execute_request 也以 action 优先，绕过 kind 审核（见 DB-01）。
代码位置：api-server/app/api/db_connections.py:878-928、1119-1241、944-969、983-1009
涉及函数或类：import_objects、import_chunk、create_database、drop_database
调用链：POST /import → db_direct_service.import_content / agent "DbImport" → 直接执行
根本原因：审核检查按入口逐个手写，新增入口/新增分支（agent 分支提前 return）时漏加。
可能影响：admin 可无视 need_ddl/need_write；agent 库的删库操作（不可恢复）无任何审核环节。
修改建议：把审核校验下沉到「执行器」层（db_direct_service.exec_sql / import_*、agent 对应方法入口），按入口不重复判断；agent 分支把 _check_approval 移到分支之前。
验证方法：开启 need_ddl 后，对 agent 库调用 DELETE /databases/{name}，预期 428。
是否需要立即处理：是
```

```
问题编号：DB-08
问题名称：直连执行无语句超时、无服务端 LIMIT，MySQL 缓冲游标会把整份结果集读进平台内存
问题等级：P1
所属模块：SQL 执行（dialects）
问题描述：_run_sql 的 timeout 只传给 agent 分支；直连分支进入 dialect.query() 后：MySQL 用 aiomysql 默认（缓冲）Cursor，`cur.execute(sql)` 在服务端返回后把全部行读入内存，`fetchmany(limit)` 只是从中取前 N 行；PG 用 portal cursor + forward/fetch（有界，较好）；Oracle 用 fetchmany（按 arraysize 分批，较好）。MySQL/PG/Oracle 均未设置语句级超时（aiomysql 无 read_timeout、asyncpg 无 command_timeout）。
代码位置：api-server/app/api/db_connections.py:430-448；api-server/app/services/dialects/mysql.py:374-392
涉及函数或类：_run_sql、MySQLDialect.query/connect
调用链：/query|/query/download → _run_sql → dialect.query → aiomysql.connect(autocommit=True) → cur.execute（无超时、全量缓冲）
根本原因：把「返回行数」当成「结果集大小」；未使用 SSCursor/服务端 LIMIT 重写；超时参数未贯通到直连路径。
可能影响：一条 `SELECT * FROM <亿级表>` 或 `SELECT SLEEP(3600)` 即可：① 打满 api-server 内存（单进程 OOM 影响整个平台）；② 长时间占用目标库会话与 CPU；③ 无超时可无限挂起，运维只能重启；④ /query 对任意有 read 权限用户开放，属低门槛 DoS。
修改建议：（1）直连路径统一施加语句超时：MySQL `SET SESSION max_execution_time`、PG `statement_timeout`、Oracle `call_timeout`；（2）MySQL 改 SSCursor 或强制包一层 `SELECT * FROM (sql) t LIMIT n+1`；（3) 把 _run_sql 的 timeout 真正用于直连（asyncio.wait_for + 连接级 kill）；（4）对 /query 增加用户级/连接级并发与 QPS 限制。
验证方法：对测试库执行 `SELECT SLEEP(600)`（预期 30~60s 内被中断）与 `SELECT * FROM <大表>`（观察 api-server RSS 是否随行数线性上涨）。
是否需要立即处理：是
```

```
问题编号：DB-09
问题名称：审批执行与 5 类 DB 操作不写审计哈希链；拒绝类事件也不留痕
问题等级：P1
所属模块：审计（audit_records）
问题描述：db_approval_service.execute_request 执行审核单后只更新 db_approval_requests.result_summary，不写 audit_records；建库、删库、对象导出、整库备份同样无 _audit_sql；400/403/404/428 等前置拒绝一律不记。SQL 文本只保留 500 字符，长脚本/批量导入的真实内容不入审计（只有 "IMPORT ... (N bytes)" 摘要）。
代码位置：api-server/app/services/db_approval_service.py:130-256；api-server/app/api/db_connections.py:481-499、930-1010、1242-1310、832-877
涉及函数或类：execute_request、_audit_sql、create_database、drop_database、export_objects、backup_database
调用链：approve → execute_request → _run_sql/import_sql（无 audit_service 调用）
根本原因：审计散落在各路由，未做成执行器的横切能力；把「审批单本身」当成审计凭证。
可能影响：哈希链审计无法回答「谁在什么时候通过审批执行了什么 SQL」；删除/建库这类破坏性操作无 tamper-evident 记录；越权尝试无迹可查，事后取证与合规审计不成立。
修改建议：（1）在 _run_sql / import_* 这类唯一执行点统一写审计（含来源：直接入口 or 审核单 id）；（2）execute_request 写 audit_records 并带上 req_id、approver；（3）鉴权失败与审核拦截写「拒绝事件」；（4）SQL 长度上限提到 4KB 并对超长内容写入对象存储/Redis 存引用；（5）建库/删库/备份/导出补审计。
验证方法：走一次审批执行后查 audit_records 是否新增记录且 execution_detail 含 approval_req_id；再制造一次 403 请求看是否留痕。
是否需要立即处理：是
```

```
问题编号：DB-10
问题名称：SQL 定时任务无审核、可发任意收件邮箱，构成合规的数据外带通道
问题等级：P1
所属模块：SQL 定时任务（sql_task_service）
问题描述：任务创建仅需 admin（/sql-tasks POST），执行时不检查 need_query 策略；recipients 支持任意邮箱字符串（`@` 即视为邮箱，`_resolve_recipients`），SMTP 走平台全局配置；SQL 只做首词只读校验（因此同样可被 WITH... 绕过），结果 CSV 直接作为附件外发。执行时使用直连/agent 的只读账号（agent 侧默认 limit 200）。
代码位置：api-server/app/services/sql_task_service.py:26、217-320（241-258 校验/执行、272-290 发信）；api-server/app/api/db_connections.py:1451-1537
涉及函数或类：execute_task、_resolve_recipients、_send_mail_sync、create_sql_task
调用链：cron/手动 run → execute_task → split_sql + 首词校验 → db_direct_service.query / agent_query → CSV → SMTP 发送
根本原因：定时任务被当作「报表」而非「数据导出」，未纳入导出类管控（审批、收件人白名单、审计内容）。
可能影响：admin 账号一旦被借用，即可设置每 5 分钟把生产库任意查询结果发到外部邮箱，形成持久化外带通道；且外发行为只记 rows/ok，无收件人与查询内容审计。
修改建议：（1）创建/修改任务校验 need_export/need_query，并对「收件人在平台用户邮箱白名单内」做限制（禁止任意外部域名，或需管理员二次确认）；（2）任务执行写审计（含收件人列表、SQL 摘要、行数）；（3）对导出结果做敏感列/行数上限与脱敏；（4）agent 路径补 limit（对齐 5000）。
验证方法：创建 recipients=外部邮箱的任务并 dry_run=false 执行，观察是否被策略拦截与是否留审计。
是否需要立即处理：是
```

```
问题编号：DB-11
问题名称：慢 SQL 摘要采集固定使用主账号，与健康采集的只读口径不一致
问题等级：P2
所属模块：数据源/采集
问题描述：collect_connection_slow_sql 手工拼 conn_info 用 `c.username` / `c.password_encrypted`（主账号），而 collect_db_health 用 `_build_conn_info(c, readonly=True)`（只读优先）。
代码位置：api-server/app/services/db_collector.py:212-213（对比 337-352、1206）
涉及函数或类：collect_connection_slow_sql、_build_conn_info、collect_db_health
调用链：scan_all_connections（每 5 分钟）→ collect_connection_slow_sql → _mysql_collect(主账号)
根本原因：早期实现遗留，未统一凭据选择逻辑。
可能影响：定时任务用高权限账号连生产库（扩大凭据暴露面与误操作面）；最小权限原则不成立。
修改建议：统一改用 `_build_conn_info(c, readonly=True)`，并在缺少只读账号时记录告警（配合 DB-04）。
验证方法：配置只读账号后执行采集，在 DB 侧 `performance_schema.threads` / 审计日志确认连接用户为只读账号。
是否需要立即处理：否
```

```
问题编号：DB-12
问题名称：慢 SQL 明细每 30 秒一轮 + 每轮元数据探测，实例多时对生产库形成常态负载
问题等级：P2
所属模块：数据源/慢 SQL
问题描述：collect_all_slow_events 由 30s 循环驱动，每个 MySQL 直连连接每轮执行：`information_schema.COLUMNS`（history_long）、`setup_consumers`、`global_status(Uptime)`、history_long/history 两条查询（各带 JOIN threads、TIMER_WAIT 过滤、ORDER BY LIMIT 500）。这些是实例级系统表查询，没有缓存（列探测每轮重复）。
代码位置：api-server/app/main.py:376-378；api-server/app/services/db_slow_event_service.py:243-256、366-452、100-135
涉及函数或类：collect_all_slow_events、collect_connection_slow_events、_table_columns、_consumer_enabled
调用链：_interval_worker("慢SQL明细采集", 30) → collect_all_slow_events → 逐连接 4~5 条系统查询
根本原因：列探测/consumer 探测未缓存；采集周期按「最坏情况」固定 30s，没有按连接数或实例负载自适应。
可能影响：50 个 MySQL 实例时约 250 次系统表查询/分钟；`information_schema.COLUMNS` 在 MySQL 5.7 大实例上可能产生元数据锁与 CPU 抖动；history_long 的 ORDER BY 扫描在缓冲满时每轮 1 万行。
修改建议：（1）`_table_columns`/`_consumer_enabled` 结果按连接缓存 1 小时；（2）采集周期改为可配置并按实例规模退避（如 30s→120s）；（3）history_long 查询优先用 TIMER_START 索引式扫描 + 只 select 必要列；（4）在生产实例上做基线测量后再定阈值。
验证方法：在压测库上开启 30s 采集，用 `performance_schema.events_statements_summary_by_digest` 观察这几条系统查询的 SUM_TIMER_WAIT 占比与实例 CPU 变化。
是否需要立即处理：否
```

```
问题编号：DB-13
问题名称：object-ddl 与 table-compare 的目标连接缺少连接级权限校验
问题等级：P2
所属模块：数据源/元数据
问题描述：/db-connections/{id}/object-ddl 只判断连接存在与 _db_ready，**没有** check_db_permission；任何登录用户遍历 conn_id 即可读取任意实例的表/视图/存储过程/事件创建语句（含列名、默认值、注释、索引）。table-compare 的 target_conn_id 也未校验用户对目标库的权限。
代码位置：api-server/app/api/db_connections.py:542-575、658-716
涉及函数或类：get_object_ddl、compare_table
调用链：GET /db-connections/{id}/object-ddl → agent "DbObjectDDL" / db_direct_service.ddl
根本原因：该接口后期新增（R37 前），未套用其它只读接口的权限模板。
可能影响：横向越权读取其它项目数据库的结构信息（表名/字段名常被视为敏感资产）；配合 DB-03 可进一步定位高价值表。
修改建议：补 `check_db_permission(user, conn_id, "read")`；compare_table 对 target_conn_id 同样校验 read 权限；这两个接口补审计（可选）。
验证方法：用无权限用户请求他人连接的 object-ddl，预期 403。
是否需要立即处理：否
```

```
问题编号：DB-14
问题名称：Oracle 支持 sysdba/sysoper 角色且「只读」路径沿用该高权账号
问题等级：P2
所属模块：数据源/Oracle 方言
问题描述：连接可配置 oracle_role=sysdba/sysoper，_connect_sync 用 AUTH_MODE_SYSDBA 登录并 autocommit=True；query 路径传 readonly=True 但只是「优先 readonly 账号」——未配置只读账号时即用 SYS 登录执行任意 SELECT（含 v$ 视图、DBA_ 视图），且 autocommit 使任何被放行的写语句立即生效。
代码位置：api-server/app/services/dialects/oracle.py:620-667（626-642 连接、644-655 查询）；api-server/app/api/db_connections.py:113-140（oracle_role 校验仅枚举）
涉及函数或类：OracleDialect._connect_sync/_query_sync、_validate_conn_payload
调用链：/query → dialect.query → _connect_sync(readonly=True) → oracledb.connect(mode=SYSDBA)
根本原因：为了运维便利（启停、数据字典查询）开放了 SYSDBA，但没有把它限定为「仅诊断用、仅 admin、单独审计」的特权通道。
可能影响：SYS 权限通过只读界面暴露给任意有 read 权限的用户；一旦首词检查被绕（DB-03），等价于 SYS 任意 DDL/DML。
修改建议：（1）sysdba/sysoper 连接只允许 admin 使用，其它角色的请求直接拒绝；（2）强制该类连接必须配置只读账号，且禁止 readonly 回落；（3）对 SYSDBA 连接的每次使用单独审计并告警；（4）考虑将该类连接限定在诊断页、不出现在 SQL 终端下拉里。
验证方法：用 operator 账号对 sysdba 连接调用 /query，预期 403。
是否需要立即处理：否
```

```
问题编号：DB-15
问题名称：/query 白名单漏 with，导致 CTE 查询在 SQL 终端不可用
问题等级：P2
所属模块：SQL 执行/前端
问题描述：/query 的 head 白名单为 (select, show, describe, desc, explain)，不含 with；前端 READ_HEADS 含 with，因此 `WITH ... SELECT` 会被发到 /query 并被 400 拒绝，提示「只允许 select / show / describe / explain」。同一仓库其它三处白名单都含 with，说明是遗漏。
代码位置：api-server/app/api/db_connections.py:352-353（对比 1356-1357、1404；frontend/src/pages/SqlTerminal.tsx:73）
涉及函数或类：query_connection、isReadStmt
调用链：前端 isReadStmt→true → POST /query → head="with" → 400
根本原因：多处白名单复制粘贴后漂移，没有单一来源。
可能影响：MySQL 8 / PG 常用的 CTE 写法在当前版本不可用（功能缺陷，用户会误认为平台不支持 CTE）。
修改建议：把「只读首词集合」抽成单一常量（建议放 services/db_risk.py），四个入口 + 前端从同一口径取值；同时按 DB-03 用语句解析替代首词。
验证方法：在终端执行 `with t as (select 1) select * from t`，预期返回结果而不是 400。
是否需要立即处理：否
```

```
问题编号：DB-16
问题名称：审核单缺少过期/失效机制，且 download_token 在列表接口回显给所有可列表用户
问题等级：P2
所属模块：数据库审核
问题描述：pending 审核单无 TTL，可长期滞留；连接被改（host/账号/库）或被删除后，已批准的 scheduled 单仍会在后台 30s 循环执行（execute_request 只判连接是否存在，不校验连接是否变更）。列表接口 _req_dict(include_token=True) 把 download_token 与过期时间返回给所有能看到该行的人。
代码位置：api-server/app/services/db_approval_service.py:259-279、130-152；api-server/app/api/db_approvals.py:92-109、255-263；api-server/app/main.py:365
涉及函数或类：run_due_scheduled、execute_request、_req_dict、list_db_approvals
调用链：30s 循环 → run_due_scheduled → execute_request → _run_sql
根本原因：审核单被当作静态记录，没有「绑定提交时的连接快照 + 时效」概念。
可能影响：陈旧授权被延后利用（scheduled 单在连接变更后执行到新库）；下载令牌横向扩散（同项目成员可代下载他人导出结果）。
修改建议：（1）pending 超时自动 expired（如 7 天），scheduled 单限制最长延后时间；（2）审核单记录连接指纹（host/port/db/user 哈希），执行前比对，不一致则失败并提示重新提交；（3）download_token 只在下载时由申请人/审批人单独获取，列表只回 download_ready。
验证方法：构造一张 scheduled 单，修改连接 host 后再等到点，预期执行失败并记录「连接已变更」。
是否需要立即处理：否
```

```
问题编号：DB-17
问题名称：agent 库 PG 的能力声明与实现不一致，前端入口照常展示
问题等级：P3
所属模块：agent 数据库能力
问题描述：agent 配置允许 type=pg，Query/Schema/健康 已支持；但对象 DDL/导出/导入/备份都走 openByKey，而 openByKey 硬性拒绝非 mysql（"暂不支持 db_type=%s（当前仅 mysql）"），CreateDatabase/DropDatabase 同样。前端对 agent 库与直连库展示同一套入口。
代码位置：k8s-executor/internal/agentdb/objects.go:20-47；ddl.go:1；dialect.go:159-169
涉及函数或类：openByKey、ImportByKey、BackupByKey、CreateDatabaseByKey、pgDialect
调用链：前端点「导出」→ /export → agent "DbExport" → openByKey → 报错
根本原因：方言抽象只做到 query 层，写/导出/元数据仍直连 MySQL 实现。
可能影响：agent 接入的 PG 实例在 UI 上「看起来支持」但操作必失败（体验问题，非安全问题）。
修改建议：后端把能力矩阵暴露给前端（每个 conn 回 capabilities），前端按能力隐藏/禁用入口；或在 openByKey 里对 pg 给出明确「该能力暂不支持」文案。
验证方法：agent 接入 PG 后点导出，确认提示文案清晰且入口置灰。
是否需要立即处理：否
```

```
问题编号：DB-18
问题名称：导出/优化路径存在未校验标识符直接拼接 SQL 的注入面
问题等级：P3
所属模块：SQL 构造
问题描述：（1）/import 的 `db` 取自 payload，未经 _IDENT_RE 校验就进 `USE \`{db}\``（mysql.py:667）；（2）_mysql_indexes 用 schema/table 拼 `SHOW INDEX FROM \`{sch}\`.\`{tbl}\``（schema_name 来自 payload，未转义反引号）；（3）_pg_indexes 用 `_sql_str` 只转义单引号。当前因 MySQL 未开多语句、且这些语句结果只读，实际可利用性有限，但属持续隐患（一旦开启 multiStatements 或换驱动即变可用）。
代码位置：api-server/app/services/dialects/mysql.py:662-672；api-server/app/services/sql_optimize_service.py:537-560、563-579
涉及函数或类：MySQLDialect.import_sql、_mysql_indexes、_pg_indexes
调用链：/import 或 /slow-sql/{digest}/inspect → 标识符拼接 → cur.execute
根本原因：标识符校验只在部分入口做（check_ident），执行层不做兜底。
可能影响：目前为低可利用性注入面；与 DB-03 组合可能放大（在数据库服务端语义差异下）。
修改建议：执行层统一对库/表/列名做 `check_ident` 或驱动级 quoting（`conn.escape`/`Identifier`），禁止 f-string 拼标识符；import 的 db 参数补 _IDENT_RE。
验证方法：向 /import 传 db="x`;select 1;--" 预期 400。
是否需要立即处理：否
```

```
问题编号：DB-19
问题名称：Oracle 客户端安装支持自定义 URL 服务端下载（SSRF）+ 解压落盘到 api-server 主机
问题等级：P3
所属模块：Oracle 客户端管理
问题描述：POST /oracle-clients/download 接受任意 url，api-server 用 httpx 直接 GET（follow_redirects=True、timeout=None、trust_env=False）并落盘到 /tmp，再解压安装到 clients/<name>。仅有 admin 可调用，且解压走 _safe_extract_zip/_safe_extract_tar（有路径穿越防护），但 URL 未做协议/域名白名单。
代码位置：api-server/app/api/oracle_clients.py:64-108；api-server/app/services/oracle_client_service.py:328-358
涉及函数或类：download_client、install_package、_safe_extract_zip
调用链：POST /oracle-clients/download{url} → httpx.stream GET → install_package → 解压
根本原因：为方便内网镜像下载开放了任意 URL。
可能影响：admin 账号被借用时可让 api-server 访问内网任意 HTTP 服务（SSRF 探测、读取内网元数据服务），并写入 600MB 级文件（磁盘耗尽）。
修改建议：URL 白名单（仅 https + Oracle 官方域或配置的内网镜像域）；限制协议为 http(s)；timeout 设上限；落盘前校验 Content-Length。
验证方法：传入 http://169.254.169.254/ 预期被拒绝。
是否需要立即处理：否
```

```
问题编号：DB-20
问题名称：前端不按角色隐藏写操作入口，非 admin 写完才吃 403
问题等级：P3
所属模块：前端 SQL 终端
问题描述：SqlTerminal 依据 isReadStmt 决定调 /query 还是 /exec，不判断当前用户角色；非 admin 输入 UPDATE/DELETE 会走到 /exec 并收到 403（后端拦住了，但交互上先让用户写完再报错）。
代码位置：frontend/src/pages/SqlTerminal.tsx:437-470（454 处分支）
涉及函数或类：handleRun、isReadStmt
调用链：执行 → isReadStmt=false → execDBConnection → 403
根本原因：权限判断只在后端，前端未消费用户角色能力。
可能影响：误导性交互（用户以为可以写），且产生大量 403 噪音日志（当前不留审计，见 DB-09）。
修改建议：前端按当前用户角色/连接授权禁用「执行写操作」入口或标注「需管理员」，并在提交前给出提示。
验证方法：用 operator 登录，写一条 UPDATE，确认按钮/提示已按角色区分。
是否需要立即处理：否
```

```
问题编号：DB-21
问题名称：command_guard 的 DROP DATABASE/TABLE 规则只作用于主机 SSH 终端，SQL 通道无任何对应拦截
问题等级：P3
所属模块：命令拦截
问题描述：DANGEROUS_RULES 包含 `drop database` / `drop table`（high 级），但 check_command 仅在 host_terminal 的输入行组装处调用（host_terminal.py:253），数据库 SQL 链路完全不经过它；因此同一条 DROP TABLE 走主机终端会被拦，走平台 SQL 终端只受审核策略（默认关）约束。规则本身也易被注释/换行/大小写拼接绕过（`drop/**/table`、`DROP\nTABLE` 命中与否取决于正则 `\b` 与 `\s`）。
代码位置：api-server/app/services/command_guard.py:18-56；api-server/app/api/host_terminal.py:253
涉及函数或类：DANGEROUS_RULES、check_command
调用链：SSH 终端输入 → LineAssembler → check_command；SQL 终端 → 无调用
根本原因：两套执行面各自为政，没有统一的「高危语句判定」服务。
可能影响：给 DBA 造成「有拦截」的错觉；实际 DB 侧保护依赖于默认关闭的审核策略。
修改建议：把「高危 SQL 判定」抽成服务（识别 DROP/TRUNCATE/无 WHERE 的 DELETE/UPDATE、GRANT 等），SQL 入口统一调用，命中则强制审核或 admin + 二次确认；command_guard 的 SQL 规则可从同一来源生成。
验证方法：在 SQL 终端执行 `drop table x`，在策略全关时应至少命中「高危」提示/强确认，而不是静默执行。
是否需要立即处理：否
```

---

## 5. 无法确认事项

1. **`WITH ... DELETE/UPDATE`、`EXPLAIN ANALYZE` 的具体版本行为**：属 MySQL/PG 服务端语义，不在本仓库代码内。报告中按「首词为 with/explain 即放行」的代码事实定性；实际能否写成功取决于目标库版本与权限（MySQL 8.0 起支持 CTE 修饰 DELETE/UPDATE；PG 的 EXPLAIN ANALYZE 会执行语句）。需在测试实例实测确认版本边界。
2. **生产实际只读账号配置率**：代码允许只填主账号；是否所有生产连接都配了 `readonly_username` 无法从源码判断（需查 `db_connections` 表的 `readonly_password_encrypted` 非空比例，属运行数据，本次未连库）。
3. **aiomysql 缓冲游标的内存上限实测值**：结论基于 pymysql/aiomysql 默认 Cursor 语义（execute 时 `_do_get_result()` 读取全部行），仓库未改用 SSCursor。具体 OOM 阈值依赖单行大小与行数，未做压测。
4. **`agent_gateway.call` 的 timeout 是否真能中断底层线程**：`asyncio.to_thread(..., timeout)` 只放弃等待，不保证终止正在执行的 gRPC 调用；实际是否会在 agent 侧继续执行完，需要看 `agent_gateway.py` 与 gRPC 取消传播（本次未展开审计第 5 章之外的隧道实现）。
5. **SMTP 外发是否受出口管控**：sql_task 通过平台全局 SMTP 发信，是否部署侧限制外域邮箱无法从源码确认。
6. **`db_approval_requests` 是否还有其它写入方**（如告警自动处理链路创建 DB 审核单）：本次仅审了 DB 相关文件，未全仓追踪 create_request 的调用者。

---

## 6. 审计方法与覆盖范围

**已读（全文或分段精读）**：

- API 层：`api-server/app/api/db_connections.py`（2106 行，逐路由）、`db_approvals.py`（336）、`db_diagnosis.py`（前 80 行 + 权限函数）、`db_diagnose_batch.py`（22，全文）、`oracle_clients.py`（130）
- 服务层：`db_direct_service.py`（72，全文）、`db_approval_service.py`（421，全文）、`db_diagnosis_service.py`（前 120）、`db_collector.py`（关键段：慢 SQL 采集、凭据构造、健康采集入口、`collect_db_health`）、`db_slow_event_service.py`（采集段 100-452 + 查询/清理）、`db_agent_service.py`（前 80）、`db_result_export_service.py`（相关段）、`sql_task_service.py`（全文 320 行）、`sql_optimize_service.py`（EXPLAIN/索引/风险规则相关 415-660、908-960）、`slow_sql_fulltext_service.py`（关键段）、`db_anomaly_service.py`（`sql_suggestions` 段）、`db_schema_diff.py`（函数清单）、`command_guard.py`（全文）、`action_registry.py`（全文，确认无 DB 动作）、`oracle_client_service.py`（`install_package` 段）
- 方言：`dialects/base.py`（全文 312 行，含 `split_sql_statements`）、`dialects/mysql.py`（连接/查询/执行/导入/DDL 生成关键段）、`dialects/pg.py`（连接/查询/执行）、`dialects/oracle.py`（OracleDialect 连接/查询/执行/能力集）、`dialects/__init__.py`（全文）
- 模型：`db_connection.py`、`db_approval.py`、`sql_task.py`（全文）；`slow_sql*.py`（字段级）；`audit.py`（全文）
- Go agentdb：`config.go`、`query.go`、`import_sql.go`、`backup.go`、`ddl.go`、`dialect.go`（全文）、`objects.go`（openByKey/导出/导入/quoteIdent/sqlValue）、`schema.go`（能力判断）、`collect.go`（QueryByKey/CollectOne）、`health.go`（采集 SQL 与频率注释）；`cmd/agent/main.go:615-640`（推送周期）
- 其它：`core/security.py`（加解密）、`core/database.py`（平台连接池）、`core/config.py`（ENCRYPTION_KEY）、`audit_service.py:48-73`、`main.py:355-380,436`（后台循环）、`user_permission_service.py:91-120`、`host_terminal.py:253`（command_guard 唯一调用点）
- 前端：`pages/SqlTerminal.tsx`（执行/多语句/审核弹窗/READ_HEADS 段，共 1244 行中约 1/3）、`pages/DbResourceList.tsx`（类型下拉/表单）、`pages/DbDetail.tsx`（接口面）、`pages/ApprovalList.tsx`（审核单展示）、`services/api.ts`（DB 相关 715-1098 段）

**未读 / 未覆盖**：

- `db_alert_service.py`（313 行）、`db_anomaly_service.py` 的阈值判定主体、`db_slow_tag_service.py`、`db_diagnose_batch_service.py` 主体、`db_collector.py` 中 MySQL/PG/Oracle 健康采集的具体 SQL 全文（只读了入口与少数字段）——这些偏「监控展示」逻辑，与执行/权限链路无关，按风险优先级跳过。
- `agent_gateway.py`（隧道实现）、`api/host_terminal.py` 其余逻辑：属 04 报告的范围。
- 未运行任何代码/测试，未连库，未做动态验证；所有「可绕过」结论来自代码路径推导 + 数据库服务端语义（已标注）。

**统计命令（可复现）**：

```bash
grep -c '^@router' api-server/app/api/db_{connections,approvals,diagnosis,diagnose_batch}.py api-server/app/api/oracle_clients.py
grep -rn 'READ ONLY\|read_only\|SET SESSION\|sql_safe_updates\|max_execution_time' \
     api-server/app/services/dialects api-server/app/services/db_collector.py k8s-executor/internal/agentdb
grep -n '_audit_sql\|create_audit_record' api-server/app/api/db_connections.py
```
