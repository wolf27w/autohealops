# 03 · 模块清单（Module Inventory）

> 只读审计。LOC 均为 `find … | xargs wc -l` 实测（含注释/空行，约数已在表内标注口径）。
> 状态标签只用模板定义：`已实现（有代码依据）` / `部分实现` / `仅前端实现` / `仅后端接口实现` / `有接口但业务逻辑不完整` / `完全未实现` / `暂时无法确认`。

---

## 1. 结论摘要

1. **18 个业务域全部有后端实现**，其中 17 个可判 `已实现（有代码依据）`，只有**主机/VM** 因「只有 SSH 纳管、无虚拟化生命周期」判 `部分实现`（证据：`api-server/app/models/host.py:39` `kind` 仅 `host|vm` 标记，全仓无 vmware/proxmox/libvirt 调用）。
2. **代码集中在 3 个巨型文件**：`resources.py` 2883 行 / 79 路由、`db_connections.py` 2106 行 / 47 路由、`stats.py` 1648 行，三者合计 6637 行，占 `app/api` 的 35%。
3. **权限是「后端强校验 + 前端弱守卫」**：后端有 475 处路由级 `Depends(require_*)` 角色依赖（`grep -rn "Depends(require" app/api/*.py | wc -l` → 475），前端仅按 `minRoleForPath()` 做跳转（`frontend/src/App.tsx:86-88`），且 `/platform/audit`、`/platform/reports` 两条路由未加 guard（`App.tsx:187-188`）——后端 `audit.py` 用 `require_any_user`（`api/audit.py:12,20`），属有意放宽，非漏洞但前后端口径不一致。
4. **契约层缺失**：`app/` 下**没有 `schemas/` 目录**，`response_model` 引用数 **0**，请求体普遍写 `payload: dict`（190 处）。接口靠 dict 直出，OpenAPI 文档与 CI 的「OpenAPI 漂移检查」只能覆盖路径/方法，覆盖不到字段。
5. **测试与代码量不匹配**：`api-server` 97 个测试文件覆盖 88k 行；`k8s-executor` 12 个测试文件覆盖 28.7k 行；前端 0 测试。
6. **AI 能力双层**：api-server 侧 `agent_loop.py` 负责 ReAct 循环与工具编排，ai-service 侧 `diagnosis_service.py`（847 行）负责提示词与解析；模型调用统一走 `model_adapter.py` 降级链。

---

## 2. 模块总表

> LOC 口径：该行「关键文件」实际 `wc -l` 之和（Python 端一般含 api+service+model；Go 端为包内全部 .go；前端为相关 tsx 估算）。「约」表示跨目录估算。

| # | 模块 | 代码路径 | 主要职责（基于代码证据） | 关键文件 | LOC（约） | 对外接口 | 状态 |
|---|---|---|---|---|---:|---|---|
| 1 | 认证权限 | `api-server/app/api/auth.py`、`users.py`、`permissions.py`、`app/middleware/`、`app/services/rbac_service.py` | 本地/LDAP 登录发 JWT（含登录防爆破 5 次/300s）、用户 CRUD、集群/项目/DB 细粒度授权，并向被管集群同步 K8s RBAC（impersonation groups + RoleBinding reconcile） | `auth.py`(170)、`permissions.py`(162)、`middleware/auth.py`、`middleware/permission.py`、`rbac_service.py`、`models/user.py`、`models/permission.py` | 921 | `/api/v1/auth`、`/api/v1/users`、`/api/v1/permissions`；`POST /auth/login` | 已实现（有代码依据） |
| 2 | 项目管理 | `api-server/app/api/projects.py`、`models/project.py` | 项目维度聚合集群+命名空间+数据库连接，并维护项目成员权限（UserProjectPermission） | `projects.py`(348)、`models/project.py`(4 类) | 418 | `/api/v1/projects`（15 路由） | 已实现（有代码依据） |
| 3 | 集群接入 | `api-server/app/api/clusters.py`、`service_accounts.py`、`services/cluster_credential_service.py`、`agent_pki.py` | kubeconfig 导入/校验/加密存储、多集群批量导入、Agent 一键接入（签发证书+渲染 install.yaml）、ServiceAccount 与凭据管理 | `clusters.py`(979, 21 路由)、`cluster_credential_service.py`、`agent_pki.py`、`models/cluster.py` | 1,745 | `/api/v1/clusters`、`/api/v1/clusters/{id}/agent/*` | 已实现（有代码依据） |
| 4 | K8s 资源查询 | `api-server/app/api/resources.py`、`workload_logs.py`、`services/resource_service.py`、`k8s-executor/internal/executor/*` | 工作负载/Pod/节点/Service/Ingress/存储/ConfigMap/Secret/SA/HPA 列表与详情、YAML 读取、Pod 日志、指标曲线；缓存与快照落库 | `resources.py`(2,883, 79 路由)、`workload_logs.py`(309)、`resource_service.py`、`internal/executor/pod_status.go`/`node_list.go`/`misc_resources.go` | 3,882 + Go 6,190 | `/api/v1/resources/*`（79 路由）；gRPC `ListPods`/`GetPodDetail`/`ListWorkloads`/`ListMiscResources`/`GetPodEvents` 等 | 已实现（有代码依据） |
| 5 | K8s 操作执行 | `k8s-executor/internal/executor/`、`api-server/app/api/executions.py`、`services/command_guard.py`、`action_verify.py`、`platform_actions.py` | 扩缩容/滚动重启/回滚/探针修改/删除/驱逐清理/HPA 增删、灰度执行、熔断、执行后验证；命令白名单校验 | `internal/executor/execute.go`、`gray_release.go`、`rollback.go`、`revision_ops.go`、`command_guard.py`、`action_verify.py` | 6,190(Go) + 约 700(Python) | gRPC `ExecuteCommand`/`ExecutePlan`(stream)/`RollbackPlan`/`BatchExecute`/`ScaleWorkload`/`RolloutWorkload`/`RollbackWorkload`/`ApplyPodYaml`/`DeleteResource` | 已实现（有代码依据） |
| 6 | 告警 | `api-server/app/api/alerts.py`、`alert_actions.py`、`alert_policies.py`、`alert_silences.py`、`services/alert_*.py`、`autonomy_gate.py`、`k8s-executor/internal/watcher/` | Pod/Node 故障检测→去重聚合→入库限流→诊断→处置（auto/approve/人工）→恢复复查；告警策略、静默、抑制、协作评论 | `alerts.py`(650, 14 路由)、`alert_auto_service.py`、`alert_grouping.py`、`autonomy_gate.py`、`internal/watcher/pod_watcher.go`/`node_watcher.go`/`sink.go` | 6,418 + Go 1,683 | `/api/v1/alerts`、`/alerts/{id}/diagnose`、`/alerts/{id}/actions`、`/alert-policies`、`/alert-silences`；gRPC `StartWatch`/`StopWatch` | 已实现（有代码依据） |
| 7 | 诊断（AI） | `ai-service/app/services/diagnosis_service.py`、`plan_service.py`、`api-server/app/api/diagnostic.py`、`fault_diagnosis.py`、`services/agent_loop.py`、`diagnostic_service.py`、`proactive_diagnosis_service.py` | 单次/多模型协同诊断、修复方案生成、修复验证、ReAct 多轮工具循环、故障分类、诊断失败兜底、诊断会话存储 | `diagnosis_service.py`(847)、`agent_loop.py`、`diagnostic_service.py`、`model_adapter.py`(276)、`fault_taxonomy.py`、`models/diagnosis.py` | 3,340 + 1,800(AI 侧诊断相关) | ai: `POST /api/v1/ai/diagnose`、`/multi-diagnose`、`/generate-plan`、`/verify-repair`、`/agent/decide`、`/agent/diagnose`；api: `/api/v1/diagnoses`、`/fault-diagnoses` | 已实现（有代码依据） |
| 8 | Agent 网关 | `api-server/app/api/agent.py`、`app/agent_gateway.py`、`agent_gateway_service.py`、`k8s-executor/cmd/agent/`、`internal/agentbridge/` | 被管集群 Agent 通过 mTLS 反向隧道接入，网关按 cluster_id 路由 gRPC 调用到对应 agent；支持 inproc / 独立 remote 两种模式 | `agent.py`(618, 12 路由)、`agent_gateway.py`、`agent_service.py`、`agentbridge/bridge.go`、`proto/agent/agent.proto` | 3,730 | `/api/v1/agent/*`；gRPC `AgentService.Tunnel`(双向流，:9095) | 已实现（有代码依据） |
| 9 | 主机 / VM | `api-server/app/api/hosts.py`、`host_terminal.py`、`host_sessions.py`、`host_audits.py`、`services/host_ssh.py`、`host_monitor_service.py` | SSH 纳管（密码/私钥，AES 加密）、主机分组、进程采集、Web 终端（录像会话+命令审计）、主机监控 | `hosts.py`(362, 8 路由)、`host_terminal.py`(761, 14 路由)、`host_ssh.py`(asyncssh)、`host_session_record_service.py`、`models/host*.py` | 3,081 | `/api/v1/hosts`、`/hosts/{id}/terminal`(WS)、`/host-sessions`、`/host-audits`、`/host-groups` | **部分实现**（SSH 纳管已落地；`kind` 仅区分 host/vm 标签，无 VM 创建/迁移/快照等虚拟化生命周期，全仓无 vmware/proxmox/libvirt） |
| 10 | 数据库 | `api-server/app/api/db_connections.py`、`db_diagnosis.py`、`db_approvals.py`、`services/db_*.py`、`dialects/`、`k8s-executor/internal/agentdb/` | MySQL/PG/Oracle 连接纳管、库表/索引/会话查询、慢 SQL 采集与优化建议、库巡检与异常检测、SQL 审核单与定时执行、表结构对比与设计器 | `db_connections.py`(2,106, 47 路由)、`db_diagnosis_service.py`、`slow_sql_fulltext_service.py`、`dialects/`、`internal/agentdb/query.go`/`ddl.go`/`import_sql.go` | 8,215 + Go 2,153 | `/api/v1/db-connections/*`（47 路由）、`/db-diagnosis`、`/db-approvals`；gRPC（agentdb 经隧道） | 已实现（有代码依据） |
| 11 | 工单 | `api-server/app/api/tickets.py`、`services/ticket_service.py`、`models/ticket.py` | 告警/审批转工单、指派、评论、关闭校验（含故障是否已恢复的复查）、工单统计 | `tickets.py`(133, 11 路由)、`ticket_service.py`、`models/ticket.py`(Ticket/TicketComment) | 1,372 | `/api/v1/tickets/*`、`/tickets/from-alert/{id}`、`/tickets/from-approval/{id}` | 已实现（有代码依据） |
| 12 | 审核审批 | `api-server/app/api/approvals.py`、`approval_flows.py`、`db_approvals.py`、`services/approval_service.py`、`db_approval_service.py` | 单人/双人审批、审批流配置（场景路由）、超时自动升级、钉钉/企微/飞书交互审批（验签）、DB 变更审核单（含定时执行） | `approvals.py`(194, 9 路由)、`approval_flows.py`(186)、`db_approvals.py`(336)、`approval_service.py`、`models/approval*.py` | 1,537 | `/api/v1/approvals`、`/approval-flows`、`/db-approvals`、`/webhooks/*`（IM 回调） | 已实现（有代码依据） |
| 13 | 审计 | `api-server/app/api/audit.py`、`services/audit_service.py`、`models/audit.py`、`models/host_command_audit.py` | 全链路审计记录，SHA256 前序哈希链防篡改，按 trace_id 追溯/搜索，链完整性校验（admin） | `audit.py`(31, 3 路由)、`audit_service.py`(31-33 `_hash`、116 `verify_audit_chain`)、`models/audit.py` | 308 | `/api/v1/audit/trace/{trace_id}`、`/audit/search`、`/audit/verify-chain` | 已实现（有代码依据） |
| 14 | 通知 | `api-server/app/api/notifications.py`、`services/notification_service.py`、`feishu_service.py`、`messaging.py`、`long_conn_client.py` | 钉钉/企业微信/飞书三通道（含 Stream 长连接）、severity 路由、重试与限频、站内信、通知配置与日志 | `notifications.py`(217, 9 路由)、`notification_service.py`、`feishu_service.py`、`messaging.py`、`models/notification.py` | 2,449 | `/api/v1/notifications/*`、`/notifications/config`、`/in-app-messages` | 已实现（有代码依据） |
| 15 | 知识库 | `api-server/app/api/knowledge.py`、`services/knowledge_service.py`、`ai-service/app/services/rag_service.py`、`embedding_service.py` | 修复经验沉淀、向量嵌入 + RAG 检索、去重、导入导出、条目启停 | `knowledge.py`(142, 10 路由)、`knowledge_service.py`、`rag_service.py`(330)、`embedding_service.py`(157) | 262 + 487(AI 侧) | `/api/v1/knowledge/*`；ai: `/api/v1/ai/rag/search`、`/rag/add`、`/embed` | 已实现（有代码依据，但 embedding 依赖外部 API，未配置时 503，见 `ai-service/app/main.py:52-55`） |
| 16 | 日志 | `api-server/app/api/logs.py`、`log_sources.py`、`log_alert_rules.py`、`workload_logs.py`、`services/es_client.py`、`log_alert_service.py`、`logstream_client.py`、`k8s-executor/internal/logstream/` | ES 多日志源查询/下载、日志告警规则、Pod 日志历史与实时跟随（executor 9099 流） | `logs.py`(183)、`log_sources.py`(194)、`log_alert_rules.py`、`es_client.py`、`internal/logstream/server.go`/`watch.go` | 1,358 + Go 390 | `/api/v1/logs`、`/log-sources`、`/log-alert-rules`、`/resources/{...}/logs/stream`；HTTP :9099 | 已实现（有代码依据） |
| 17 | 监控 / 大屏 | `api-server/app/api/stats.py`、`screens.py`、`monitoring.py`、`services/prometheus_service.py`、`metrics_rollup.py`、`screen_bus.py`、`frontend/public/monitor-bigscreen.html` | 仪表盘聚合统计、AI 效果/成本统计、平台健康度、大屏 SSE 实时推送、Prometheus 指标查询与 Pod 指标分层存储 | `stats.py`(1,648, 8 路由)、`screens.py`(73, SSE)、`prometheus_service.py`、`metrics_rollup.py`、`screen_bus.py` | 2,687 | `/api/v1/stats/*`、`/stats/screen/stream`(SSE)、`/stats/dashboard/stream`、`/monitoring` | 已实现（有代码依据） |
| 18 | 系统设置 | `api-server/app/api/settings.py`、`services/system_setting_service.py`、`data_retention_service.py`、`models/system_setting.py` | 平台参数（ES/Prometheus/保留期/快照天数/会话保留）读写、凭据加密、数据保留清理作业 | `settings.py`(149, 6 路由)、`system_setting_service.py`、`data_retention_service.py` | 422 | `/api/v1/settings`、`/system-settings` | 已实现（有代码依据） |
| + | 发布（Jenkins） | `api-server/app/api/release.py`、`services/jenkins_client.py`、`release_change_service.py` | Jenkins 实例/构建触发、发布映射、批量发布、发布历史与回滚记录 | `release.py`(36KB)、`jenkins_client.py`、`models/release.py` | 约 1,400 | `/api/v1/release/*` | 已实现（有代码依据） |
| + | 巡检 / 定时任务 | `api-server/app/api/inspections.py`、`custom_tasks.py`、`report_schedules.py`、`scheduler_service.py`、`inspection_service.py` | 集群巡检报告生成与保留期清理、自定义定时任务（每集群独立 Cron + Redis 锁）、报表订阅 | `inspections.py`、`custom_tasks.py`、`scheduler_service.py` | 约 800 | `/api/v1/inspection-reports`、`/custom-tasks`、`/report-schedules` | 已实现（有代码依据） |

---

## 3. 功能状态清单（细粒度）

| 功能 | 状态 | 证据（文件:行） | 备注 |
|---|---|---|---|
| JWT 登录 + LDAP | 已实现（有代码依据） | `api-server/app/api/auth.py:21-60`、`_ldap_login` | 登录失败 5 次/300s 锁定 |
| 登出黑名单 | 已实现（有代码依据） | `app/middleware/auth.py:31-40` | Redis 故障降级放行 |
| 三角色 RBAC | 已实现（有代码依据） | `app/middleware/permission.py:9-26`、475 处 `Depends(require_*)` | developer 角色也存在（`require_any_user`） |
| 集群内 K8s RBAC 同步 | 已实现（有代码依据） | `app/services/rbac_service.py:101-130`、gRPC `SetupRBAC`/`ReconcileRoleBindings`（`proto/executor.proto:76,78`） | impersonation groups |
| kubeconfig 加密存储 | 已实现（有代码依据） | `app/services/cluster_credential_service.py`、`app/core/security.py`（AES-256-GCM）+ Go `internal/k8sclient/crypto.go` 互测 | `make crypto-test` |
| Agent 接入（一键 install.yaml + mTLS） | 已实现（有代码依据） | `api/clusters.py:213 agent_onboard`、`agent_pki.py`、`.env.example:64-78` | 证书 SAN 变更需重启 api-server |
| 工作负载列表/详情/YAML | 已实现（有代码依据） | `api/resources.py`（79 路由）、`internal/executor/workload_ops.go` | — |
| 扩缩容/重启/回滚/灰度 | 已实现（有代码依据） | `internal/executor/execute.go`、`gray_release.go`、`rollback.go`；`services/command_guard.py` 白名单 | 高危命令强制人工由决策引擎判 |
| 命令白名单 | 已实现（有代码依据） | `services/command_guard.py`、`.env.example:55` | 默认含 delete/exec，靠决策层兜 |
| 告警去重/聚合/限流 | 已实现（有代码依据） | `services/alert_merge.py`、`alert_grouping.py`、`alert_auto_service.py` | 5 分钟窗口（README:46） |
| 告警→诊断→处置闭环 | 已实现（有代码依据） | `api/alert_actions.py`(3 路由)、`services/alert_action_service.py`、`autonomy_gate.py`、提交 `caa29bf/d1fff96` | auto 直执 / approve 建单后执 |
| AI 多模型降级链 | 已实现（有代码依据） | `ai-service/app/services/model_adapter.py:116-133`、`AIModelConfig.priority` | severity 分级选模型 |
| ReAct 多轮工具循环 | 已实现（有代码依据） | `api-server/app/services/agent_loop.py`、`app/core/config.py:109-112`（`AGENT_REACT_LOOP_ENABLED=True`） | 默认 ≤8 步 / 90s |
| 诊断报告三段折叠 | 已实现（有代码依据） | 前端 `components/DiagnosisSections.tsx`、提交 `b3b9f03` | — |
| Agent 反向隧道 | 已实现（有代码依据） | `app/agent_gateway.py`、`k8s-executor/cmd/agent/main.go`、`internal/agentbridge/bridge.go` | 单副本约束见 `k8s/README.md:31-34` |
| 主机 SSH 终端 + 会话录像 | 已实现（有代码依据） | `api/host_terminal.py:1-761`、`host_session_record_service.py`、`models/host_session.py` | xterm 前端 |
| 主机命令审计 | 已实现（有代码依据） | `api/host_audits.py`、`models/host_command_audit.py` | — |
| VM 生命周期（创建/快照/迁移） | 完全未实现 | 全仓 grep `vmware\|proxmox\|libvirt` 无命中；`models/host.py:39` 仅有 `kind` 标签 | 只有「把 VM 当 SSH 主机纳管」 |
| 数据库多方言连接 | 已实现（有代码依据） | `services/dialects/`、`internal/agentdb/dialect.go`（mysql/pg/oracle） | — |
| 慢 SQL 采集与优化建议 | 已实现（有代码依据） | `db_collector.py`、`slow_sql_fulltext_service.py`、`sql_optimize_service.py`、`main.py:93-100`（每 1 分钟） | — |
| SQL 审核单 + 定时执行 | 已实现（有代码依据） | `db_approvals.py`、`db_approval_service.run_due_scheduled`（`main.py:74-82`，每 30s） | — |
| 工单流转 | 已实现（有代码依据） | `api/tickets.py`（11 路由）、`ticket_service.py` | 关闭前复查故障恢复（提交 `c64e65f`） |
| 双人审批 / 超时升级 | 已实现（有代码依据） | `approval_service.py`、`models/approval.py`(escalation 字段)、迁移 `5c6d7e8f9a0b` | — |
| IM 交互审批 | 已实现（有代码依据） | `api/webhooks.py`、`feishu_service.py`、`messaging.py` | 验签 |
| 审计哈希链 | 已实现（有代码依据） | `audit_service.py:31-33,116-140` | `verify-chain` 仅 admin |
| 通知三通道 + 限频重试 | 已实现（有代码依据） | `notification_service.py`、`feishu_service.py`、`long_conn_client.py` | 钉钉/企微/飞书 |
| 知识库 RAG | 已实现（有代码依据） | `knowledge_service.py`、`rag_service.py`、`embedding_service.py` | embedding 依赖外部服务 |
| ES 多日志源 | 已实现（有代码依据） | `services/es_client.py`、`log_sources.py`、`models/log_source.py` | 每源独立凭据加密 |
| Pod 日志实时流 | 已实现（有代码依据） | `internal/logstream/server.go`、`logstream_client.py`、`api/workload_logs.py` | 依赖 :9099（见 OPS-01） |
| 仪表盘 / 大屏 SSE | 已实现（有代码依据） | `api/stats.py:1423-1505`、`api/screens.py:26-73`、`screen_bus.py` | 大屏为静态 HTML iframe |
| 系统设置热生效 | 已实现（有代码依据） | `system_setting_service.get_setting`（60s 缓存，`es_client.py:19`） | — |
| 前端页面级角色守卫 | 仅前端实现（弱） | `App.tsx:86-88` `guard()`、`constants/nav.ts` `minRoleForPath` | `/platform/audit`、`/platform/reports` 未加 guard（`App.tsx:187-188`） |

---

## 4. 问题清单

```
问题编号：MOD-01
问题名称：资源中心单文件 2883 行 / 79 路由，承载几乎全部 K8s 查询
问题等级：P2
所属模块：K8s 资源查询
问题描述：app/api/resources.py 单文件 2883 行、注册 79 个路由，同时包含 Pod/节点/工作负载/存储/ConfigMap/Secret/Ingress/HPA/YAML/日志等多个资源域，且直接内联业务逻辑（非薄 controller）。同目录还有 workload_logs.py 复用同一 prefix /api/v1/resources。任何资源域改动都在这一个文件里叠加，合并冲突与回归风险高。
代码位置：api-server/app/api/resources.py:24（router 定义）～2883；api-server/app/api/workload_logs.py:34
涉及函数或类：resources.router 下 79 个 handler
调用链：前端 pages/cluster/* → /api/v1/resources/* → executor gRPC
根本原因：按「功能」而非「资源域」组织路由，未拆分 router 模块
可能影响：可维护性差、审查困难、单人改动阻塞并行开发
修改建议：按资源域拆成 resources_pods.py / resources_nodes.py / resources_storage.py 等子 router，统一在主 router 下 include
验证方法：拆分后 `grep -c "@router." app/api/resources*.py` 总和应仍为 79；跑 97 个单测 + 前端手点
是否需要立即处理：否
```

```
问题编号：MOD-02
问题名称：数据库模块单文件 2106 行 / 47 路由，且跨 6 个 service 相互耦合
问题等级：P2
所属模块：数据库
问题描述：db_connections.py 2106 行 47 路由，同时处理连接 CRUD、库表浏览、SQL 执行/导出、表结构设计、会话/进程、健康指标；并直接 import 至少 db_direct_service、db_agent_service、db_collector、db_result_export_service、db_schema_diff 等，形成网状依赖。
代码位置：api-server/app/api/db_connections.py:28（router）～2106
涉及函数或类：db_connections router handlers
调用链：前端 DbDetail/DbWorkbench → /api/v1/db-connections/* → db_direct_service / agentdb(gRPC)
根本原因：数据库能力按迭代堆叠在单文件，未做应用服务层切分
可能影响：改动一处 SQL 执行逻辑影响全部入口；测试只能覆盖少量（tests 中 db_* 测试 10 个文件）
修改建议：按「连接管理 / 查询执行 / 元数据与结构 / 导出」拆子路由与 service
验证方法：拆分后路由总数不变，`tests/test_db_*.py` 全绿
是否需要立即处理：否
```

```
问题编号：MOD-03
问题名称：接口契约层缺失——无 schemas 目录、0 个 response_model、190 处 payload: dict
问题等级：P2
所属模块：全局架构
问题描述：api-server/app 下没有 schemas/；全仓 `response_model` 出现 0 次；请求体普遍声明为 `payload: dict`（190 处）。响应统一 `{"code","message","data"}` dict 直出。后果：OpenAPI 只能描述路径与方法，字段级契约无法生成；CI 的 OpenAPI 漂移检查（scripts/ci_check.sh:36-38）只能发现路径增删；前端类型靠手写 interface，字段改名后端不会报错。
代码位置：api-server/app/api/auth.py:22（`payload: dict` 典型）、全目录 `grep -c "response_model" app/api/*.py` → 0
涉及函数或类：几乎所有 handler
调用链：前端 services/api.ts → /api/v1/*
根本原因：早期快速迭代选择了 dict 透传，未引入 pydantic 请求/响应模型
可能影响：前后端字段漂移发现晚；文档与实现不一致；参数校验靠手写（可能漏校验）
修改建议：至少为高频写接口补 pydantic 模型（登录、审批、告警处置、DB 审核单），渐进式引入 response_model
验证方法：`/openapi.json` 中目标接口出现 requestBody schema；CI 漂移检查能捕获字段删除
是否需要立即处理：否
```

```
问题编号：MOD-04
问题名称：前端零测试 + 后端权限校验与前端守卫口径不一致
问题等级：P2
所属模块：前端 / 权限
问题描述：frontend/package.json 无测试框架与 test 脚本，69 个页面文件全靠人工回归；同时前端 guard() 只对部分路由生效，`/platform/audit`（App.tsx:187）、`/platform/reports`（:188）未加 guard，而同类页面 `/platform/users` 加了 admin 守卫。后端 audit 路由用 require_any_user（api/audit.py:12,20），所以不构成越权，但「哪些页面给 viewer 看」在前端无统一表达。
代码位置：frontend/package.json:6-10、frontend/src/App.tsx:184-189、api-server/app/api/audit.py:12,20
涉及函数或类：guard()、minRoleForPath()
调用链：浏览器路由 → 页面组件 → /api/v1/audit/*
根本原因：页面准入规则分散在 App.tsx 与 nav.ts，且未强制「每条路由必须声明 minRole」
可能影响：权限口径日后不一致时易出现「前端能进、后端 403」或反之；无自动化回归
修改建议：路由表由 nav.ts 单一数据源生成并强制 minRole 必填；引入 vitest + 至少覆盖权限/AI 处置关键组件
验证方法：遍历 App.tsx 所有 Route，断言每条都有 minRole 或显式 allowAny
是否需要立即处理：否
```

```
问题编号：MOD-05
问题名称：测试密度与代码规模严重不匹配（executor 28.7k 行 12 个测试文件）
问题等级：P2
所属模块：k8s-executor / 质量保障
问题描述：Go 侧 78 个源文件 28,750 行，仅 12 个 `_test.go`（覆盖 crypto/retry/revision/exec_in_pod/watcher 等少数包）；`internal/executor` 6,190 行只有 `exec_in_pod_test.go`、`revision_ops_test.go` 两个测试文件。高危操作（灰度、回滚、YAML apply、删除）缺少单测，回归依赖真实集群手测。
代码位置：k8s-executor/internal/executor/（6,190 行，2 个测试文件）；`find . -name "*_test.go" | wc -l` → 12
涉及函数或类：executor 包各操作函数
调用链：api-server → gRPC ExecutePlan → executor 操作
根本原因：K8s 操作需 fake clientset 才有意义，历史未投入
可能影响：执行类改动回归成本高，误删/误改风险靠代码评审
修改建议：优先给 execute/gray_release/rollback 补 fake clientset 单测；CI 加 `go test ./... -race`
验证方法：`go test ./internal/executor/ -race` 有实质覆盖率提升（当前几乎为空）
是否需要立即处理：否
```

```
问题编号：MOD-06
问题名称：主机/VM 模块只有 SSH 纳管，VM 仅作为标签
问题等级：P3
所属模块：主机 / VM
问题描述：Host 模型有 `kind: host|vm`（models/host.py:39），但无任何虚拟化平台对接（无 vmware/proxmox/libvirt/openstack 调用）。VM 的生命周期（创建/快照/迁移/关机）无接口；实际能力等价于「SSH 可达即可纳管」。若产品口径要求 VM 管理，属需求缺口而非缺陷。
代码位置：api-server/app/models/host.py:39；`grep -rl "vmware|proxmox|libvirt"` 无命中
涉及函数或类：Host、host_ssh.py
调用链：无（无虚拟化调用链）
根本原因：需求范围原本就是「主机纳管」，VM 只是分类标签
可能影响：用户预期偏差
修改建议：文档明确「VM 支持 = SSH 纳管」；如需虚拟化能力单独立项
验证方法：对照 docs/ROADMAP.md 的主机需求条目
是否需要立即处理：否
```

---

## 5. 无法确认事项

| 事项 | 卡点 |
|---|---|
| 各模块线上实际使用率 | 需要访问统计/日志数据，只读审计不连生产 |
| 通知三通道是否都真实可用 | 依赖外部 webhook 配置（`.env.example:25-29` 为空），代码路径完整但未联调 |
| 知识库 embedding 实际供应商与维度 | 由 `ai_model_configs`/embedding 配置决定，未连接运行环境 |
| 发布（Jenkins）是否在用 | `JenkinsInstance`/`ReleaseBuild` 模型与 36KB 路由存在，但需实例配置 |
| 大屏 `monitor-bigscreen.html` 与后端 SSE 的实际联调 | 静态文件存在（`frontend/public/monitor-bigscreen.html`），未运行验证 |

---

## 6. 覆盖范围

**逐模块读过的文件**：`app/api/` 下 66 个路由模块的文件名/前缀/路由数（`grep -n "@router\."` 逐文件统计）、`app/models/` 87 个类、`app/services/` 114 个模块清单与关键文件（audit_service/es_client/rbac_service/model_adapter/command_guard 等）、`app/middleware/{auth,permission}.py`、`app/main.py`、`k8s-executor/internal/*` 包清单与 LOC、`k8s-executor/proto/*.proto`、`frontend/src/App.tsx`、`frontend/src/pages|components|services` 清单、`frontend/package.json`。

**统计命令**（可复现）：

```bash
# 每模块 LOC
wc -l api-server/app/api/resources.py api-server/app/services/resource_service.py ...
# 路由数
grep -c "@router\." api-server/app/api/<file>.py
# Go 包 LOC
find k8s-executor/internal/executor -name '*.go' | xargs cat | wc -l
# 权限校验数
grep -rn "Depends(require" api-server/app/api/*.py | wc -l   # 475
# 契约缺失
grep -rn "response_model" api-server/app/api/*.py | wc -l    # 0
grep -rn "payload: dict" api-server/app/api/*.py | wc -l     # 190
```

**没读**：各 service 的业务算法细节（只读接口与职责）、前端 69 个页面组件内部实现、AI 提示词全文（`prompt_engine.py` 389 行只读类结构）。
