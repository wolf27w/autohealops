# 04 · 巨型文件、复杂函数、重复代码与结构问题

> **代码基线**：`main @ b3b9f03`　**方式**：`wc -l` + Python AST 内联统计（只读，未落地脚本）+ grep + 逐文件阅读
> **路径约定**：相对项目根 `/Users/wolf/Documents/project/autohealops/`；缩写同 03（`A/`=api-server/app、`I/`=ai-service/app、`F/`=frontend/src、`G/`=k8s-executor）
> **排除**：生成代码（`G/proto/executor.pb.go` 9,888 行、`executor_grpc.pb.go` 2,030 行、`A/proto/executor_pb2_grpc.py` 2,203 行）不计入手写巨型文件
> **标注**：【事实】/【推断】/【待验证】

---

## 0. 结论摘要

1. 【事实】**15 个手写源码文件超过 1,000 行**（Python 12 / TS-TSX 2 / Go 1）；其中 2 个函数超过 1,000 行：`A/services/alert_auto_service.py:2464 _process_one`（1,053 行）与 `A/api/stats.py:327 _compute_dashboard`（1,058 行）。
2. 【事实】30+ 个 Python 函数超过 100 行；`_process_one` 内部同时做通知、DB 写入、executor 调用、诊断、决策、执行、验证、知识沉淀。
3. 【事实】重复代码集中在 5 类：资源级权限校验（5 份复制）、连接/会话样板、异常吞掉（单文件 97 处 `except Exception`）、前后端枚举、通知分发。
4. 【事实】数据层无 `ForeignKey`/`relationship`（models 全目录与全部迁移零匹配），87 张表靠裸整型 id + 手写 join 关联；`ai-service` 重复定义 14 张表。
5. 【事实】测试 1,290 个 Python 用例 + 28 个 Go 用例，但**审批→执行、审计哈希链、RBAC、AI 修复验证、Agent 隧道（Python 侧）均 0 覆盖**，前端 0 测试。

---

## 1. 超过 1,000 行的手写文件【事实】

| 语言 | 文件 | 行数 |
|---|---|---:|
| Python | `A/services/alert_auto_service.py` | 3,867 |
| Python | `A/api/resources.py` | 2,883 |
| Python | `A/api/db_connections.py` | 2,106 |
| Python | `A/services/tool_registry.py` | 1,832 |
| Python | `A/api/stats.py` | 1,648 |
| Python | `A/services/notification_service.py` | 1,636 |
| Python | `A/clients/executor_client.py` | 1,407 |
| Python | `A/services/db_collector.py` | 1,264 |
| Python | `A/services/ticket_service.py` | 1,177 |
| Python | `A/services/agent_loop.py` | 1,149 |
| Python | `A/services/dialects/oracle.py` | 1,055 |
| Python | `A/services/approval_service.py` | 1,055 |
| TS/TSX | `F/services/api.ts` | 1,455 |
| TS/TSX | `F/pages/SqlTerminal.tsx` | 1,244 |
| Go | `G/internal/grpc/server.go` | 1,126 |

【事实】`A/api/stats.py` 实测总行数 1,648（此前审计报告记为 1,058 行的 `_compute_dashboard` 是函数行数，二者不同）。

---

## 2. 超过 100 行的复杂函数

### 2.1 Python（AST 精确统计）【事实】

| 文件:行号 | 函数 | 行数 |
|---|---|---:|
| `A/services/alert_auto_service.py:2464-3516` | `_process_one` | **1,053** |
| `A/api/stats.py:327-1384` | `_compute_dashboard` | **1,058** |
| `A/main.py:294-540` | `startup_tasks` | 247 |
| `A/services/db_collector.py:918-1164` / `:487-692` / `:695-900` | 三段采集函数 | 247 / 206 / 206 |
| `A/api/clusters.py:260-503` | agent 接入渲染 | 244 |
| `A/services/agent_service.py:294-532` | `run_agent_turn` | 239 |
| `A/api/host_terminal.py:140-371` | 终端会话 | 232 |
| `A/services/trace_service.py:168-389` | trace 构造 | 222 |
| `A/services/approval_service.py:851-1033` | `execute_approved_plan` | 183 |
| `A/services/notification_service.py:1191-1338` | 通知渲染/分发 | 148 |
| `A/services/tool_registry.py:1147-1289` | 工具 handler | 143 |
| `A/api/resources.py:1720-1848` | workload YAML/环境变量 | 129 |
| `A/api/db_connections.py:1120-1239` | 导入/执行 | 120 |
| `I/api/aux_api.py:276-419` | Prompt 预览 | 144 |
| `I/services/diagnosis_service.py:132-255` | `diagnose_alert` | 124 |

### 2.2 前端 / Go（粗估）【推断】

- `F/pages/SqlTerminal.tsx:352-1245` 单块约 894 行（锚点法估算，含 JSX，误差较大）。
- `F/services/api.ts` 是 473 个 `export const` 薄方法，问题在**文件级**而非函数级。
- `G/internal/grpc/server.go:993-1126 ExecPodStream` 约 134 行；55 个 `func` 全部挂在单一 `*Server` 类型上。

### 2.3 `_process_one` 实测开头【事实】

`A/services/alert_auto_service.py:2464` 的函数注释即写明「知识库优先 → AI 诊断 → AI 修复 → 转人工」；`:2473` 资源阈值告警早退、`:2477` DB 告警早退、`:2482` PodEvicted 分支内 `:2485` 直接调 `notification_service.send_alert_notification`、`:2488` 直接 `async_session()` 改 `Alert.message`、`:2499` 直接 `executor_client.acall(cleanup_evicted)`。即**一个函数横跨 4 层**。

---

## 3. 重点文件逐一剖析

> 每项按「①职责 ②内部子域 ③强关联函数 ④是否拆 ⑤建议模块 ⑥兼容策略」六点回答。

### 3.1 `A/services/alert_auto_service.py`（3,867 行）

① 告警自愈全链路编排；② 10 个子域：自愈编排(`:2464`)、知识库(`:246/:349`)、根因/自主闸门(`:541/:569/:624`)、工单冷却(`:1000/:1016`)、循环抑制(`:2030`)、恢复扫描(`:3557/:3639`)、审批(`:2109`)、决策评分(`:909`)、动作诊断(`:1590`)、镜像修复(`:1915`)；③ `_process_one` 顺序调用 `check_pod_recovered` / `_close_related_alerts` / `_mark_alert_recovered` / `_mark_manual_and_notify` / `record_action_diagnosis` / `_process_node_alert` / `_auto_force_delete_terminating`，共同读写 `Alert.status/message`；④ **应拆**；⑤ `alert_orchestrator.py`（编排）、`alert_gates.py`（闸门/置信度/环境）、`alert_recovery.py`、`alert_kb.py`、`alert_ticket.py`；⑥ 保留原文件作**门面再导出**：跨模块引用的 `process_alert` / `auto_process_alerts` / `verify_repair_outcome`（`db_alert_service.py:236`、`ticket_service.py:936`）、`_dedup_knowledge`（`action_verify.py:118`）、`_gate_workload_spec` / `_automation_mode`（`alert_action_service.py:53`）、`infer_deployment_name`(:116) 全部保持原路径可导入，import 一行不改。【待验证】行为等价需回归 `A/main.py:353/:420` 的定时链路。

### 3.2 `A/api/stats.py`（1,648 行）

① 仪表盘/统计/大屏/AI 报表；② 缓存(`:260-325`)、口径纯函数(`:150/:155/:170/:182`)、大屏聚合(`:327`)、AI 报表(`:1507/:1568/:1613`)；③ `_compute_dashboard` 内 `:355-442` 连发 20+ `select/text` SQL，与 `_severity_level:150`、`_disposal_label:155` 的口径强耦合；④ **应拆**；⑤ `stats_dashboard_repo.py`（SQL）、`stats_metrics.py`（纯函数）、`dashboard_aggregator.py`；⑥ 保留 `dashboard` / `dashboard_cached` / `ai_dashboard` 路由不变，`_compute_dashboard` 再导出。

### 3.3 `A/api/resources.py`（2,883 行）

① K8s 资源与节点操作 API；② 7 域：资源查询、节点、HPA、日志/文件、网络隔离、工作负载变更、调度；③ `:1705 _load_workload_doc → :1926 _apply_doc → upsert_env/replace_env/delete_env` 是一条强顺序链；`:31 _deny_cluster` 被 70+ 端点重复调用；④ **应拆**；⑤ `resources/k8s_read.py`、`workload_ops.py`、`node_ops.py`、`hpa_ops.py`、`log_files.py`、`isolation.py`；⑥ `api/resources.py` 保留为 router 聚合 + `_deny_cluster` 兼容层，所有 URL 与响应结构不动。

### 3.4 `F/services/api.ts`（1,455 行）

① 全站 HTTP 客户端与全部接口函数 + 大量 `interface/type`；② 按注释分 20+ 业务域（`:81` 认证、`:87` 集群、`:123` 告警、`:266` 审批……）；③ 473 个薄方法共享同一 `api` 实例与拦截器(`:31/:40`)，改拦截器会影响全部域；④ **应拆**；⑤ `api/http.ts` + `api/auth.ts` / `clusters.ts` / `alerts.ts` 等领域文件 + `types.ts`；⑥ 新建 `index.ts` 桶文件 `export *`，保持现有全部具名导入路径不变（若历史导入为 `../services/api`，可让 `api.ts` 改为 `export * from './api/index'`）。

### 3.5 `A/api/db_connections.py`（2,106 行）

① 数据库连接全生命周期；② 7 域：CRUD、SQL 执行、DDL 设计器、导入导出、备份、定时任务、慢 SQL；③ `:430 _run_sql` / `:451 _agent_call` / `:597 _rows` 三处同构连接逻辑；④ **应拆**；⑤ `db_conn_crud.py`、`db_sql_exec.py`、`db_import_export.py`、`db_slow_sql.py`，共享 `_check_approval` 与 `_run_sql` 到 `db_gateway.py`；⑥ 路由函数保留原签名，428 信封结构不变。

### 3.6 `A/clients/executor_client.py`（1,407 行）

① 所有 K8s/执行器 RPC 的唯一客户端；② 4 种传输(`:94/:131/:413/:466`) + 能力协商(`:197`) + 50+ RPC 包装(`:544-1310`) + protobuf 转换；③ `_TunnelAwareStub.__getattr__:249-373` 动态反代把所有方法绑到 4 条路径；④ **应拆（但优先级靠后）**；⑤ `executor_transport.py`、`executor_converters.py`，`ExecutorClient` 作门面；⑥ 所有 `executor_client.xxx()` 调用点不改（44 个文件引用，见 02）。

### 3.7 `A/services/tool_registry.py`（1,832 行）

① AI 工具注册表 + 30 余个 handler；② 注册/校验(`:1294/:1297/:1809/:1811`) + K8s/JVM/发布/jar/HPA/env 工具；③ `execute_tool:1795` 是所有 handler 的唯一入口，`allow_operate:1817` 是唯一权限点；④ **应拆**；⑤ `tools/k8s_tools.py`、`release_tools.py`、`jar_tools.py` + `tool_registry.py` 作注册表门面；⑥ `list_tools:1759` / `execute_tool:1795` 签名不变，工具名字符串不变（工具名进入 LLM 提示词，属于外部契约）。

### 3.8 `A/services/notification_service.py`（1,636 行）

① 三通道通知与渲染；② 渠道发送、策略、七字段渲染、审批/恢复/汇总；③ `:858` 与 `:941` 两个同名内部 `_dispatch` 几乎同构；`:56-83` 六个 `send_*` 是一行转发；④ **部分应拆**；⑤ `notify_channels.py`、`notify_policy.py`、`notify_render.py`，合并两个 `_dispatch`；⑥ 六个 `send_*` 是兼容门面，**不建议再抽**（一行转发本身有价值）。

### 3.9 `F/pages/SqlTerminal.tsx`（1,244 行）

① 数据库工作台单页组件；② 连接选择、SQL 编辑、结果集、导入导出、备份、DDL、审批 7 类交互；③ 共用一份连接状态与结果集 state；④ **应拆**；⑤ `useSqlConnection` / `useSqlResult` hooks + `SqlEditor` / `ResultTable` / `ApprovalPanel` 子组件；⑥ 路由 `/sql-terminal` 与页面级导出不变。【推断】TSX 行数含大量 JSX，拆分收益主要是可测试性而非行数。

### 3.10 `G/internal/grpc/server.go`（1,126 行）

① 全部约 50 个 RPC 的服务端实现；② Pod/工作负载/HPA/JVM/日志/发布/计划执行；③ 55 个方法挂同一 `*Server`，共享 6 个注入依赖（`server.go:39-58 NewServer`）；④ **应拆（优先级最低）**；⑤ 按 pod/workload/hpa/jvm/log 拆多个 service 注册，共用 `NewServer` 装配；⑥ `RegisterExecutorServiceServer` 与 proto 契约不变，`cmd/executor/main.go:96` 与 `agentbridge/bridge.go:116-127` 两个装配点不改。

---

## 4. 重复代码与抽象问题

| 类别 | 具体位置【事实】 | 是否值得抽象【推断】 |
|---|---|---|
| 权限校验复制 | `A/api/resources.py:31`、`service_accounts.py:16`、`resource_governance.py:23`、`jvm.py:122`、`db_diagnosis.py:23` 各自定义 `_deny/_deny_cluster`；底层统一走 `user_permission_service.py:47` | **值得**：改为共享 `Depends(cluster_guard)`，可删 4 份复制，且消除「新增路由忘记鉴权」的结构性风险 |
| 资源连接重复 | `A/api/db_connections.py:430-444 _run_sql` 与 `:451-472 _agent_call` 同构 `asyncio.to_thread(agent_gateway.call,...)`；`report_schedules.py:29`、`custom_tasks.py:17`、`knowledge.py:23`、`host_sessions.py:109` 重复 `async with async_session()+select()` | **部分值得**：连接调用应合并；CRUD 样板不建议抽 ORM 基类（收益低、易隐藏事务边界） |
| 异常吞掉 | `alert_auto_service.py` 含 97 处 `except Exception`（`:2486/:2502` 仅 `logger.warning` 后继续），`:111/:210/:2667` 为 `except: pass`；同类 `trace_service.py:277/542`、`tool_registry.py:86`、`executor_client.py:494`、`host_terminal.py:66` | **值得统一分类**：JSON 容错可保留，但主流程的宽捕获应换成可观测的失败类型。【待验证】哪些宽捕获掩盖了修复失败需逐条审 |
| 前后端枚举重复 | 前端 `F/constants.ts:11`（critical→严重）、`F/utils/env.ts:5-8`、`F/components/StatusTag.tsx:5`、`F/pages/AutomationPolicy.tsx:140/147`、`F/pages/AIModelConfig.tsx:175`；后端 `A/api/stats.py:150/155`、`A/services/alert_merge.py:29`、`autonomy_gate.py:24/64/73`、`decision_engine.py:72/227`、`decision_score.py:105/222`、`models/cluster.py:24` | **值得**：后端收敛到单一 `env_policy.py` + `/config` 下发前端；【待验证】两套枚举是否已漂移 |
| 通知分发重复 | `A/services/notification_service.py:858` 与 `:941` 两个同名 `_dispatch` 同构 | **值得合并**；但 `:56-83` 的六个一行转发**不值得再抽** |
| 过度/不必要抽象 | `A/clients/executor_client.py:249-373` 动态反代叠加 4 套传输；`A/api/resources.py:100-167` 的 `_rt_store/_load/_refresh/_cached` 四件套 | 前者分支偏多，建议收敛为 transport 策略；后者是合理缓存抽象，**不建议为减行数改动** |

---

## 5. 数据模型与业务边界

### 5.1 八组模型【事实】

| 组 | 主要模型（位置） |
|---|---|
| 用户与角色 | `User`(`A/models/user.py:11-31`)、`Role` 仅预留(`:34-42`，注释「RBAC 主用硬编码三角色」) |
| 项目与授权 | `Project` / `ProjectClusterNS` / `ProjectDBConnection` / `UserProjectPermission`(`project.py:16-71`)；`UserClusterPermission` / `UserDBConnection` / `AlertSubscription`(`permission.py:11-51`) 两套并行 |
| 集群与 Namespace | 仅 `Cluster` 单表 40+ 字段(`cluster.py:11-68`)；**无 Namespace 表**，namespace 是 16 个模型里的 String 列 |
| Agent 与任务 | `DiagnosisSession/Message`(`agent_session.py:11-60`)、`Task/TaskAuditLog`(`task.py:11-37`)、`CustomTask`、`BatchOperation`、`SqlTask`、`HostSession/Chunk`、`ExecutionLog`(`execution.py:11-23`) |
| 告警与工单 | `Alert`(`alert.py:11-73`)、`AlertComment`、`AlertSilence/Inhibition`、`AlertPolicy`、`Ticket/TicketComment`(`ticket.py:14-62`) |
| AI 诊断与证据 | `AIDiagnosisResult/DiagnosisFailure`(`diagnosis.py:11-50`)、`DiagnosticTask/Report`、`DecisionPolicy/Result`(`decision.py:11-45`)、`DecisionScore`、`AICallLog` |
| 审批与执行记录 | `Approval`(`approval.py:11-42`)、`ApprovalFlow`、`DbApprovalRequest`、`RepairPlan`(`repair_plan.py:11-23`)、`RepairVerification`(`verification.py:11-23`)、`AuditRecord`(`audit.py:11-26`，SHA256 哈希链) |
| 知识库 | `KnowledgeEntry`(`knowledge.py:11-41`)、`PromptTemplate/PromptVersion/PromptSuggestion`(`prompt.py:11,39,61`) |

### 5.2 关系与边界问题【事实】

- `ForeignKey` / `relationship` / `back_populates` 在 `models/` 全目录与全部 123 个迁移中**零匹配**；一律裸 `Mapped[int]` 外键位 + 手写 join（`user_permission_service.py:39,75,110,132-133`）。
- `Alert` 同时有 `dedup_key`(STORED，`alert.py:57`)、`fault_signature`(`:42`)、`diag_category`(STORED，`:60`)，另有 `fault_domain` property 实时推导(`:71-73`)；分类规则既在迁移 SQL（`j3k4l5m6n7o8:26-52`）又在 Python（`A/services/diag_category.py`）各一份。
- 生成列 / `JSON_EXTRACT` / `ON DUPLICATE KEY UPDATE` / 分区规则将 schema 强绑 MySQL（`d7e8f9a0b1c2:30-44`、`q0r1s2t3u4v5:46-55`、`r1s2t3u4v5w6:14`）。
- `ai-service` 重复定义 14 张表（`I/app/models/__init__.py:2` 及 `:24,34,53,70,95,115,127,141,154,167,183,195,223,237`），无 alembic。
- 职责混杂：`Cluster` 一张表塞 kubeconfig(`:20`)、Agent 隧道(`:32-51`)、RBAC(`:55-57`)、巡检(`:62,66`)、自愈(`:64`)、维护窗口(`:53`)；`Alert` 混故障 + 协作(`:49-54`) + 诊断域(`:58-60`) + 去重(`:55-57`)。
- 状态机分散：字符串散落 `ticket_service.py:455,547,701,807-810,865,967`、`notification_service.py:1550-1556`、`db_approval_service.py:119,143,251,264`，无枚举/常量，且与 STORED 列中的状态字面量构成跨层隐式契约。
- 审计字段不统一：无 `deleted_at/is_deleted`；仅 7 个 `is_active`；`ExecutionLog` 只有 `started_at`、`RepairPlan` 只有 `created_at`、`AuditRecord` 只有 `created_at`，而 `User` 两者齐全。
- `TicketComment.username` 冗余字段，注释明言「避免每次列表 join 用户表」(`ticket.py:58-59`)——这是「无 relationship」的直接代价。

【推断】模型层最大的结构风险是**「无约束 + 双份定义」**：加字段需要同步 4 处（api-server 模型、ai-service ORM、迁移、Go 侧 GORM 结构体），且没有任何编译期或测试期保证。

---

## 6. 测试结构【事实】

| 范围 | 测试文件 | 用例数 |
|---|---:|---:|
| `api-server/tests` | 97 | 1,190 |
| `ai-service/tests` | 14 | 100 |
| `k8s-executor` | 12 个 `*_test.go` | 28 |
| `frontend` | 0 | 0（`F/../package.json:6-9` 只有 dev/build/preview，无 test 框架） |

按域分布：告警聚合/恢复 `test_alert_grouping.py`、`test_alert_merge.py`、`test_alert_silence_service.py`、`test_log_alert_recovery.py`；诊断证据 `test_diagnosis_*.py`、`test_evidence_gap.py`、`test_oom_evidence.py`；DB 方言 `test_dialect_ddl.py`、`test_mysql_delimiter.py`、`test_pg_dialect.py`、`test_db_direct_dialects.py`；加密 `test_crypto.py`、`test_host_credentials.py`；权限 `test_permissions.py`、`test_command_guard.py`；提示词 `test_prompt_*.py`。

**真集成极少**：仅 `test_host_credentials.py:19,123-162` 用真实 `async_session`/`engine` 写删；`G/internal/watcher/sink_live_test.go:20-35` 连真实 MySQL 但默认 skip；`test_permissions.py:103-110` 虽用 TestClient，`:100` 注释说明「依赖解析阶段即拒绝，不触库」。其余多为纯函数 / mock 单测（`test_agent_loop.py:363`、`I/../test_model_retry.py:63` 均 patch `async_session`）。

**核心流程覆盖空白【事实】**（grep 口径）：审计哈希链 0（`AuditRecord|hash_value|previous_hash` 零命中）、AI 修复验证 0、RBAC 0、Agent 隧道 Python 侧 0；审批→执行仅间接（`test_manual_assist_migration.py`、`test_platform_action_risk.py`）；ReAct 有 `test_react_deterministic.py`、`test_agent_loop.py`（26 用例）。109 个 service 中 **37 个模块名在 tests 中 0 次提及**。

**难测原因【事实/推断】**：① 同步 gRPC + goroutine（`G/internal/agentbridge/bridge.go:126,133,169,314`）；② 全局可变状态（`eval_service.py:172` `threading.Lock`、`system_setting_service.py:19` `_cache`）；③ 69 个 service 直接 `async with async_session()`，测试须逐模块 patch；④ services 内 `datetime.now()/time.time()` 共 150 处时间依赖；⑤ 前端无测试框架且大屏是不进构建的原生 HTML。

测试-模块对应最薄弱 5 个模块【推断】：`audit_service`、`approval_service` + `repair_plan_service`、`rbac_service` + `user_permission_service`、`knowledge_service` + `action_verify`、`diagnostic_service` + `db_diagnosis_service`。【待验证】这些模块可能经路由层测试间接覆盖，本次仅做静态检索。

---

## 7. 结构问题优先级表

| 编号 | 问题 | 影响面 | 证据 | 建议阶段 |
|---|---|---|---|---|
| Q-01 | `_process_one` 1,053 行横跨 4 层 | 告警自愈主链路 | `alert_auto_service.py:2464-3516` | 第一优先 |
| Q-02 | `_compute_dashboard` 1,058 行 + 20+ SQL | 仪表盘/大屏 | `stats.py:327-1384` | 第一批 |
| Q-03 | 5 份 `_deny_cluster` 复制 | 全部资源类路由 | `resources.py:31` 等 | 第一批（收益高、风险低） |
| Q-04 | `api.ts` 1,455 行 / 473 导出 | 全站前端 | `F/services/api.ts` | 第一批 |
| Q-05 | 前后端枚举双份 | 风险/环境/状态判定 | `constants.ts:11` vs `decision_engine.py:227` | 第一批 |
| Q-06 | api 层 49 文件写 SQL / 12 文件直调 executor | 分层边界 | 见 02 §2 | 第二批 |
| Q-07 | 无 FK + 87 张表 + 双份 ORM | 数据一致性 | `models/`、`I/models/__init__.py` | 第二批（需回归网） |
| Q-08 | 两组循环依赖 | 告警/审批/验证 | 见 02 §3 | 与 Q-01 同批 |
| Q-09 | 核心链路 0 测试 + 前端 0 测试 | 重构安全性 | 见 §6 | **必须先于 Q-01 落地回归网** |
| Q-10 | 生成码混放、契约双份、孤儿页面、遗留网关三文件 | 可维护性 | 见 01 §6 | 第三批（清理） |

---

## 8. 待验证事项

1. `SqlTerminal.tsx` 与 Go 方法的粗估行数需精确 AST/解析器复核（本次为锚点法）。
2. 97 处宽 `except Exception` 中哪些会掩盖修复失败，需逐条运行期审。
3. 37 个「0 次提及」的 service 是否被路由层测试间接覆盖。
4. 前后端枚举是否已在运行期漂移（需对比实际取值，本次仅确认双份存在）。
