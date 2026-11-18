# Agent 运行轨迹 + 诊断/自愈 Agent 开发需求（2026-11-18 定稿）

> 老板 2026-11-18 14:16：「使用这个 demo，并把 agent 的开发需求加入到需求里」
> **状态：仅登记需求，暂不开发**（老板 2026-11-18 14:19：「写入到需求里，不开发」）——P0 起的代码开发一律不启动，等排期。
> Demo：`frontend/public/agent-trace-demo.html`（可访问 `/agent-trace-demo.html`；6 条示例轨迹：应用层 OOM 自愈 / 代码 panic 回滚转单 / 慢 SQL 加索引审批闭环 / 点查 IO 只给方案 / 探针配错自愈 / 镜像 401 自愈）
> 关联：R41 三域 Agent 架构、R42 Agent trace 可视化、慢 SQL 优化诊断（`sql_optimize_service`）、数据库审核（`db_approvals`）

## 0. 定位与边界（先定清楚，别和诊断助手混淆）

| | 应用诊断助手（现有 AgentChat） | 诊断/自愈 Agent（本需求） |
|---|---|---|
| 触发 | 人主动发起、会话追问 | 事件驱动：告警 / 巡检 / 定时 / 慢 SQL 超阈值 |
| 形态 | 对话 | 任务（跑完出结论 + 动作 + 验证） |
| 权限 | 只读工具 | **可执行**（按动作矩阵分级：自动 / 审批 / 只给方案） |
| 结束 | 人满意 | **指标验证**（Pod Ready、重启归零、慢 SQL 耗时下降） |
| 兜底 | 换问法 | 回滚 / 转工单（带证据包） |
| 留存 | 会话记录 | trace（可回放）+ 案例沉淀知识库 |

两者**共用同一套工具、模型出口、知识库**（R41：一个 Runtime + N Domain Profile）；Agent 转人工时，助手从同一 trace 接手。

## 1. 页面需求（以 demo 为准）

### 1.1 列表页（轨迹索引）

- 顶部统计：今日诊断数 / 自动闭环数（含占比）/ 走审批单数 / 平均轮数与耗时 / 转人工工单数
- 列：时间、Agent 域（应用层 / 数据库）、目标（集群+ns+workload ｜ 数据库实例+库）、触发方式（告警 / 手动 / 定时 / 巡检）、结论、动作类型（自动执行 / 审批后执行 / 仅给方案）、轮数、耗时、token 成本
- 筛选：时间范围、域、目标、结论、是否走审批、是否转工单

### 1.2 详情页（时间轴，核心）

按 seq 展开，每步显示：**时间偏移 + 步骤类型 + 标题 + 入参/出参摘要 + 耗时**

步骤类型（颜色与 demo 一致）：
`think` 推理（灰）｜`tool` 工具调用（青）｜`decide` 决策（紫，含风险等级与免审/需审）｜`exec` 执行（蓝）｜`approve` 审批（粉，含审批人）｜`ok` 验证通过（绿）｜`err` 失败/转单（红）

- 决策步必须显示：**风险等级、阈值判定、动作清单、回滚语句（若有）**
- 工具步必须显示：工具名、**入参（脱敏）**、出参摘要（截断到 N 字符）、耗时、成功/失败
- 执行步必须显示：实际下发对象与 diff（如 `limits.memory 1Gi → 2Gi`）
- 验证步必须显示：验证指标与前后值（Pod Ready / 重启次数 / 慢 SQL 平均耗时与扫描行数）
- 底部：**证据包**（日志片段 / jstack / heapdump / 执行计划 / 慢 SQL 明细 / 变更 diff）、trace_id、关联工单与审批单
- 操作：重放（同参数重跑，评估用）、导出证据、跳转关联单

### 1.3 统计视图（P2）

成功率、自动闭环率、平均轮数、工具失败率、审批通过率、误判率（人工标注）、按域/按故障类型分布。

## 2. 数据模型（新增两张表）

```sql
agent_traces (
  id, trace_id(uniq), domain(app|db), target_type(cluster|db),
  cluster_id, namespace, workload, db_connection_id,
  trigger(alert|manual|cron|inspect), alert_id, task_id,
  model, tokens_in, tokens_out, cost,
  result(ok|partial|failed|ticket), action(auto|approval|plan_only),
  risk_level, approval_id, ticket_id,
  started_at, ended_at, duration_ms,
  summary_json      -- 判定/动作/结论三行摘要
)

agent_trace_steps (
  id, trace_id, seq, at_ms,
  kind(think|tool|decide|exec|approve|verify),
  title, tool_name,
  args_json,        -- 脱敏后
  result_summary,   -- 截断存储，全文进证据包
  duration_ms, status(ok|error|skip), error,
  evidence_refs_json
)
索引：(trace_id, seq)、(started_at)、(domain, started_at)
```

**采集方式**：ai-service 每轮 tool call / 决策打点；api-server 的执行（patch/scale/rollback/DDL）与审批动作打点；统一 `trace_id` 头透传（现有 `trace_id` 机制复用）。

## 3. 动作矩阵（这是 Agent 的权力边界，必须先定）

### 3.1 应用层

| 故障 | 判定依据 | 动作 | 执行方式 |
|---|---|---|---|
| OOMKilled / 资源不足 | describe + metrics + 退出码 137 | 调 requests/limits、扩容 | 自动（低风险）或审批（超阈值幅度） |
| 探针配错 | probe 返回码 vs 实际健康路径 | 修 probe | 自动 |
| 镜像拉取失败 | events 401/403、imagePullSecrets 缺失 | 补 imagePullSecrets / 换 registry | 自动 |
| 调度失败 | events FailedScheduling | 放宽亲和/清污点/扩容节点 | 审批 |
| ConfigMap/Secret 缺 key | 容器启动日志 + 引用检查 | 补 key 引用 | 自动（仅引用）/ 审批（改值） |
| 滚动升级卡住 | 新 RS 不就绪 + 超时 | **回滚上一版本** | 自动（保留现场） |
| Terminating 卡死 | finalizer / 节点失联 | 强删 | 自动（既有口径） |
| **代码/镜像缺陷**（启动即 panic、ENTRYPOINT 错） | 栈 + 首启失败 | 回滚 + 摘流 + **建工单** | 回滚自动、工单必建；**不尝试改代码** |
| 下游依赖故障 | 连接错误 + 依赖侧健康 | 摘流 + 建单 | 只用方案 + 工单 |

### 3.2 数据库层

| 故障 | 判定依据 | 动作 | 执行方式 |
|---|---|---|---|
| 缺索引 / filesort / 临时表 | EXPLAIN + 索引现状 + 扫描行数 | `CREATE INDEX ... ALGORITHM=INPLACE, LOCK=NONE` | **审批后执行**（kind=ddl） |
| 冗余/未用索引 | `sys.schema_unused_indexes` + 写负载 | `DROP INDEX` | 审批（高风险双人） |
| 动态参数（会话/全局可动态生效） | 指标偏离 + 参数现状 | `SET GLOBAL ...` | 审批 |
| 静态参数（需重启，如 buffer_pool） | 同上 | **只给方案 + 工单** | 不自动执行 |
| SQL 改写（业务语义） | 执行计划 + SQL 结构 | **只给方案** | 交开发 |
| 数据修正 / 结构调整 | — | **禁止自动** | 人工 |

**红线**：不改业务数据（DML）、不代替开发改 SQL、需重启的参数不自动改。

### 3.3 执行安全

- 大表 DDL：表大小阈值（建议 >20GB 只给方案）+ 低峰窗口（scheduled 审核单已有）+ 超时与锁等待保护
- 每条 DDL 必须**预生成回滚语句**并展示
- 索引类动作要评估**写放大**（写 QPS、现有索引数上限）
- 执行后自动验证；无效提示回滚，**变差自动回滚**（需老板确认，默认建议只强提示）

## 4. 阶段拆分

| 阶段 | 内容 | 依赖 | 工作量 |
|---|---|---|---|
| **P0** | trace 表 + 打点接入（app/ai-service/executor）+ **列表页 + 时间轴页**（先只读展示，不接管执行） | 无 | 3~4d |
| **P1** | 动作矩阵接入应用层（自动/审批两档）+ 审批联动（复用 `db_approvals` 与风险评分）+ 验证与回滚 | P0 | 4~5d |
| **P2** | 数据库域闭环（索引 DDL → 审批 → 执行 → 前后对比验证 → 回滚脚本）+ 统计视图 + 重放 | P1、`sql_optimize_service` | 4~5d |
| **P3** | 评估体系（误判率标注、回归集）、知识库自动沉淀 | P2 | 2~3d |

## 5. 验收标准

1. 任一告警触发的 agent 任务，在列表能看到、能打开时间轴，步骤完整（think/tool/decide/exec/verify 齐全）
2. 时间轴里工具入参**无明文敏感信息**（密码/私钥/连接串/Token 全打码）
3. 走审批的任务，审批单可从 trace 跳转；审批通过后执行动作与 trace 记录一致
4. 应用层「代码缺陷」场景：能自动回滚 + 建工单，工单里带 panic 栈/镜像 tag/变更 diff
5. 数据库「缺索引」场景：诊断 → 审批 → 低峰执行 → 自动前后对比（同 digest 平均/最大耗时、扫描行数）
6. 全量 trace 保留 7 天、摘要 90 天，超期清理任务存在
7. 页面 1920 宽无横向滚动；时间轴 20 步内不卡顿

## 6. 拍板结论（老板 2026-11-18 14:18：按建议执行）

| # | 事项 | 结论 |
|---|---|---|
| 1 | 变差自动回滚 | **应用层开启**（执行后验证变差 → 自动回滚上一版本）；**数据库层不自动回滚**，只强提示并提供预生成回滚语句 |
| 2 | trace 保留期 | **全量 7 天 + 摘要 90 天**；大字段（日志片段/计划/栈）存文件只留引用 |
| 3 | 数据库大表 DDL | 表 > **20GB** 只给方案不执行；执行窗口 **02:00–05:00**（后续可按 `slow_sql_events` 低峰自动选） |
| 4 | 误判标注 | 在 trace 详情页直接点「判定正确 / 判定错误（选原因）」即可，不单独做标注任务 |

## 7. P0 实现方案（已设计验证，未落地；排期后可直接按此开发）

> 2026-11-18 曾开工 P0 并按老板口径回滚，代码不保留；以下为实现要点，避免后期重做设计。

**表与索引**：`agent_traces`（`started_at` 单列索引 + `(domain, started_at)` 复合索引）、`agent_trace_steps`（`trace_id` 单列索引 + `(trace_id, seq)` 复合索引）。

**服务层（`agent_trace_service.py`）**
- `new_trace()`：uuid4 hex，支持复用外部传入 trace_id（与 ai-service / executor 的 trace_id 对齐）
- `add_step()`：`seq` 用 Redis `agent_trace_seq:{tid}` 自增，`max(seq)+1` 兜底；`at_ms` = 距 started_at 的毫秒（时钟回拨钳位为 0）
- `redact()`：覆盖 `password/passwd/pwd/secret/token/authorization`（JSON 键值与 `=`/`:` 两种写法）、`bearer xxx`、PEM 私钥块、`mysql://` / `postgres://` 连接串 → 值替换 `***`
- `result_summary` 截断 4000 字符（全文进证据包）
- `finish_trace()`：回填 `duration_ms/ended_at/summary_json`
- `list_traces()`：筛选/分页/按 started_at 倒序；`target` 由 Cluster / DBConnection 名称拼，取不到回退 id
- `purge_old_traces(days=90)`

**API**
- `GET /api/v1/agent-traces`（require_any_user；非 admin 按可见集群 + 可见数据库收敛）
- `GET /api/v1/agent-traces/{trace_id}`
- `POST /api/v1/internal/agent-traces`、`.../{trace_id}/steps`、`.../{trace_id}/finish`（`X-Internal-Token == settings.JWT_SECRET_KEY`）

**接入点（启用时）**
1. `main.py`：`from app.api import agent_traces` + `app.include_router(agent_traces.router)`
2. 打点：ai-service 每轮 tool call / 决策；api-server 的执行动作（patch/scale/rollback/DDL）与审批动作

**验证基线**：`py_compile` 6 文件通过；`unittest tests.test_agent_traces` **36 用例**（脱敏 12 条规则、seq/at_ms 递增、截断、finish 回填、列表筛选分页排序、详情顺序、内部令牌校验、purge）。
