# 10 审批与执行链路安全审计（AutoHealOps）

- 审计对象：`/Users/wolf/Documents/project/autohealops`
- 审计方式：**只读**源码审计（未修改任何业务代码/配置/YAML/依赖，未安装依赖，未连生产，未做 git 写操作）
- 审计范围：审批 API/服务/模型、执行 API/服务（executions、batch、alert-actions、decisions、tasks）、autonomy_gate/action_registry/action_verify/decision_engine/command_guard/audit_service、k8s-executor gRPC 入口与 action 分发、RBAC 依赖、审批/执行相关前端页面
- 结论口径：所有结论带 `文件:行`；无法确认的写「暂时无法确认」并说明卡点

---

## 1. 结论摘要

1. **审批只覆盖「AI 生成修复计划」这一条链路，不是平台的强制闸门。** 同一个集群还有至少 8 类平行执行入口（资源变更 API、batch、alert-actions、MCP、custom-tasks、回滚、gRPC executor、agent pull）不经过审批，其中 5 类为 P0：资源变更 API、batch、MCP、gRPC executor、agent pull（`api/batch.py:14-31`、`api/mcp.py:87-110`、`k8s-executor/cmd/executor/main.go:95-109`、`api/clusters.py:403-426`）。
2. **审批人身份可被操作员自行改写。** `POST /approvals/{id}/add-approver` 与 `/transfer` 只要求 `require_operator`，不校验调用者是否为当前审批人/管理员，任何 operator 可把自己加签或转办给自己后批准（`api/approvals.py:137-154`、`services/approval_service.py:498-549`）。
3. **「双人审批」可退化为单人。** 双人节点 = 项目 manager + admin，判定用「每节点与 approved_by 求交集」，若同一人既是 admin 又是该项目 manager，一人即可同时满足两个节点（`services/approval_service.py:175-194`、`services/user_permission_service.py:123-142`）。审批单模型也没有申请人字段，无法做职责分离（`models/approval.py:16-42`）。
4. **审批与执行的绑定是「plan_id 弱绑定」。** 执行时重新从 DB 读 `plan_json`（`approval_service.py:889`），审批单不存计划摘要/哈希，也不校验告警是否已恢复；`execute_approved_plan` 只拦 `completed/executing`，不拦 `cancelled`（`approval_service.py:866-878`），因此已驳回/已取消的计划可被重新建审批单再执行。
5. **执行结果可能「失败被判成功」。** `plan_runner.run_plan` 不回传步骤失败（只写 progress），`execute_approved_plan` 仅按异常判断，步骤全失败也会把计划置 `completed`（`approval_service.py:896-929`、`services/plan_runner.py:100-160`）；无可验证指标的节点/DB 类告警则直接关单并标注「未验证」（`approval_service.py:1000-1016`）。
6. **审计覆盖面不足且链路不可靠。** 审计表只有 `trace_id + 5 段文本`，无 operator/action/target/result 结构化字段（`models/audit.py:11-26`）；batch、alert-actions、custom-tasks、MCP、多数资源变更端点完全不写审计；`update_audit_record` 只重算本行 hash 不更新后续行 `previous_hash`，任何一次更新后 `/audit/verify-chain` 必然失败（`services/audit_service.py:76-138`）。
7. **executor gRPC 无任何认证。** `grpc.NewServer()` 无拦截器/TLS（`k8s-executor/cmd/executor/main.go:95`），compose 直接把 `9090` 发布到宿主机所有网卡（`docker-compose.yml:20-23`）；调 `ExecuteCommand/ExecutePlan/DeleteResource/ScaleWorkload` 即可完全绕过 api-server 的审批与 RBAC。
8. **非生产默认「全自动」。** `system_settings.automation_mode` 缺省 `by_env`，非 prod 一律 `full`（`alert_auto_service.py:696-710`），full 下平台动作（回滚/探针/节点隔离 drain）直接执行不转审批（`alert_auto_service.py:3305-3311`、`2387-2400`）。

---

## 2. 审核模型（问题 1）

### 2.1 谁能发起

| 发起方 | 入口 | 权限 | 证据 |
|---|---|---|---|
| 告警自动链路（系统） | `alert_auto_service._process_one` → `create_approval(plan_id, approval_level, admin, alert_id=...)` | 无用户上下文，`current_user` 传第一个 admin | `services/alert_auto_service.py:3494-3512`（`create_approval` 调用段） |
| 处置台手动发起（approve 型动作） | `POST /api/v1/alerts/{id}/actions/{key}/dispatch` | `require_operator` | `api/alert_actions.py:35-42`、`services/alert_action_service.py:260-267` |
| 人工补建审批 | `POST /api/v1/approvals`（body `plan_id`） | `require_operator`，先 `evaluate_plan` 取级别，`<3` 直接拒绝 | `api/approvals.py:17-28` |
| 人工协助单（`kind=manual_assist`） | 已改为工单，列表统一排除 | — | `api/approvals.py:38-50` |

结论：**发起侧不区分「谁的计划」**，任何 operator 可对任意 `plan_id` 建审批单；模型无 `requested_by` 字段，事后无法判断申请人。

### 2.2 审批状态机

```
pending ──(approve_count 满足)──> approved ──(execute_mode=immediate)──> 执行
   │                                  └──(execute_mode=scheduled)──> apscheduler 定时执行
   ├──(任一人 reject)──> rejected（关联告警置 resolved、计划置 cancelled）
   └──(关联告警 self_healed/resolved，timeout_handler 扫描)──> cancelled
```

- 允许值：`pending / approved / rejected / cancelled`；模型注释里的 `timeout` 实际不使用（`models/approval.py:23`、`services/approval_service.py:767-783`）。
- **没有超时终态**：`timeout_handler` 到「提醒时间」只把 `current_node_index +1` 并重新通知，最后一个节点保持 pending 不再提醒（`approval_service.py:786-800`）。官方注释亦明确「审批没有超时一说」（`models/approval_flow.py:12-13`）。
- 超时/过期只存在于**数据库审核单**（`db-approvals`，界面有 `expired` 选项），与 K8s 风险审批不是同一套。

### 2.3 审批人如何判定

1. `create_approval` 时把流程节点**快照**写入 `approvals.flow_nodes`，并把各节点 `receiver_usernames` 并集写入 `approvers`（`approval_service.py:350-392`、`79-137`）。
2. `approve/reject` 用 `_check_approver_permission`：`username ∉ approvers → forbidden`；`admin` 只要在 approvers 内即可放行（不受当前节点限制）；非 admin 还必须在当前节点的 `receiver_usernames` 内（`approval_service.py:197-225`）。
3. `approvers` 为空（旧数据）→ **直接放行**并打 warning（`approval_service.py:207-210`）。
4. 级别：`decision_level >= 4` → 双人（`required_level=5`），否则单人（`required_level=3`）（`approval_service.py:357`）。

节点类型展开规则（`_expand_flow`）：`duty`=当天值班人（无值班人且 `on_empty=next` 跳过）、`group_all`=只把 admin 加入 approvers（`include_admin`）、`admin`=全部 admin、`user`=指定用户名；审批人数不得超过该节点接收人数（`approval_service.py:100-133`）。

**自审自批路径：**
- ① 加签/转办（P0，见 APP-01）；
- ② 流程展开后 `approvers` 为空时把 `current_user` 设为唯一审批人（`approval_service.py:135-137`）；
- ③ 双人审批同人跨节点（APP-03）。

---

## 3. 审批 → 执行关联与参数可信性（问题 3）

### 3.1 触发链

```
approve() 提交 approved
  ├─ execute_mode=immediate → asyncio.create_task(execute_approved_plan(plan_id))        approval_service.py:466
  └─ execute_mode=scheduled → scheduler.add_job(execute_approved_plan, DateTrigger)      approval_service.py:481-497
execute_approved_plan(plan_id)
  ├─ 读 RepairPlan；status ∈ {completed, executing} → 跳过                               :866-871
  ├─ 取该 plan 最新一张 Approval；status != approved → 跳过                              :872-878
  ├─ plan.status = executing；plan_json = plan.plan_json（DB 现值）                       :882-890
  └─ plan_runner.run_plan(cluster_id, plan_id, plan_json, trace_id)                       :896
```

- **执行参数来源 = 审批单里的 plan（DB 记录），不是审批请求体。** 这一点是正确的：`/approvals/{id}/decide` 只接受 `action/comment/execute_mode/scheduled_at`，不接受 steps（`api/approvals.py:176-194`）。
- **但审批单没有绑定计划内容摘要（无 hash/version）**，`plan_json` 在数据库中是可变的：`repair_recurrence_service.mark_plan_failed` 会整段重写 `plan_json`（`services/repair_recurrence_service.py:147-158`）；`alert_auto_service` 在审批前也会规范化回写（`alert_auto_service.py:3274-3282`）。任何后续代码/运维改库都会**静默改变已批准的执行内容**，无审计对比（EXE-23）。
- **审批通过后不校验告警/计划是否已作废**：`cancelled` 不在拦截集合内（`approval_service.py:869`），而 `_reject_risk_approval_alert` 会把被拒计划置 `cancelled`（`approval_service.py:612-618`）。因此「被拒 → 重新 POST /approvals → 再批准」即可执行一张本应作废的计划（APP-04）；定时任务在计划被僵尸清理置 `cancelled` 后到点仍会执行（APP-05）。
- **执行集群取自告警**（`alert.cluster_id`），审批单本身没有 cluster 字段；`/batch` 则可完全替换集群（EXE-01）。

---

## 4. 执行入口清单与是否强制审核（问题 4）

| # | 入口 | 鉴权 | 是否强制审批 | 审计 | 证据 |
|---|---|---|---|---|---|
| 1 | 审批通过 → `execute_approved_plan` | 审批人 | 是（仅此链路） | 部分（trace 常为空） | `approval_service.py:415-479,851-1034` |
| 2 | `POST /api/v1/batch` + `/{id}/execute` | `require_operator`，**无集群权限校验** | **否** | **无** | `api/batch.py:14-31`、`services/batch_service.py:42-125` |
| 3 | `POST /api/v1/executions/{plan_id}/rollback` | `require_operator`，无集群权限校验 | **否** | 有（rollback 明细） | `api/executions.py:59-114` |
| 4 | `POST /api/v1/alerts/{id}/actions/{key}/dispatch` | `require_operator` | 条件（autonomy_gate；auto 直接执行） | 仅 AlertComment | `api/alert_actions.py:35-42`、`alert_action_service.py:252-257` |
| 5 | `POST /api/v1/alerts/{id}/dispatch`（`action=ai`） | `require_operator` | 间接（转 `process_alert`，受模式约束） | AlertComment | `alert_action_service.py:270-311` |
| 6 | `POST /mcp` `tools/call` | `require_any_user`（含 viewer/developer） | **否** | **无** | `api/mcp.py:8,53-54,87-110` |
| 7 | gRPC `k8s-executor:9090`（ExecuteCommand/ExecutePlan/BatchExecute/RunScript/DeleteResource/ScaleWorkload…） | **无** | **否** | 仅 executor 侧建 Job/日志 | `k8s-executor/cmd/executor/main.go:95-109` |
| 8 | agent pull `:9100`（NodePort，ExecutorService 全量） | pull-token，**可为空=不校验** | **否** | 无 | `api/clusters.py:395-426`、`agentbridge/bridge.go:164-215` |
| 9 | `custom-tasks`（script/restart/scale + run-now/cron） | `require_operator` | **否** | 仅 CustomTaskLog | `api/custom_tasks.py:28-46,77-82`、`services/scheduler_service.py:45-107` |
| 10 | `resources.py` 变更端点（delete/scale/rollout/rollback/apply/probe/env/exec/node cordon/drain/isolate…） | `require_operator` + 项目级 `check_cluster_permission(operate)` | **否** | **仅 3 处写审计** | `api/resources.py:1138,1151,1270,1475,2816` |
| 11 | 容器终端 WS `terminal.py` / 主机终端 WS | JWT + operate | **否** | 主机终端有，Pod 终端无 | `api/terminal.py:38-55` |
| 12 | 资源治理批量 `batch-scale/batch-restart` | `require_operator` | **否** | 无 | `api/resource_governance_ctl.py:227-296` |
| 13 | 自愈规则 `force_auto` | `require_admin` 建规则 | **规则可强制免审** | 命中时写审计 | `api/self_heal_rules.py:39-60`、`alert_auto_service.py:3323-3331` |
| 14 | 内部回调 `POST /alerts/{id}/auto-process` | `X-Internal-Token == JWT_SECRET_KEY` | 否（进入自动链路） | 部分 | `api/alerts.py:532-546` |
| 15 | DB 审核单执行 | 审批人（admin/项目 manager） | 是（若策略开启） | 仅结果摘要 | `api/db_approvals.py:211-255` |

**判定结论：** 只有 #1 与 #15 是「强制审批」。其余 13 条均可直接改变集群/数据，其中 #2/#6/#7/#8 完全无审批且无审计，属 P0。

---

## 5. auto / approve 分界（问题 5）

### 5.1 真实规则（`services/autonomy_gate.py:107-225`）

判定顺序：
1. 动作不存在 → `manual`（L0）
2. `risk=forbidden` → `manual`（L1，任何配置不能放开）
3. 授权矩阵 `actions.{key}.{prod|test}` 命中 → `auto/approve/manual/forbidden`（L3/L5/L2/L1）
4. `risk=red` 且非 full → `approve`（L3）；full → `auto`
5. 置信度缺失/低于阈值（prod 0.8 / test 0.6）→ `manual`（full 跳过）
6. prod 且用户可感知 → `approve`（L4，full 跳过）
7. `risk=yellow` 且（非 prod 或无感知）→ `auto`（L5）
8. `risk=green` 且无感知 → `auto`（L5）

### 5.2 动作风险表（`services/action_registry.py:27-88`）

| 动作 | risk | prod+无感知 | prod+有感知 | 非 prod |
|---|---|---|---|---|
| rebuild_pod | green | auto | **auto**（规则 5 先命中：prod+有感知→approve，实际 approve） | auto |
| delete_stuck_pod | green | auto | approve（但 `alert_auto_service.py:653-656` 强制改 auto：**TerminatingStuck 免审批**） | auto |
| rolling_restart | yellow | auto | approve | auto |
| **rollback_deployment** | **yellow** | **auto** | approve | **auto** |
| fix_image_pull_secret | green | **auto（会改工作负载 spec）** | approve | auto |
| drain_traffic | green | **auto（摘流）** | approve | auto |

### 5.3 自动执行范围评估（是否过大）

- **是，偏大。** `rollback_deployment`（版本回滚）只是 `yellow`，在 prod 双副本 + RollingUpdate + readinessProbe 场景判为「无用户感知」→ **直接自动回滚、不经审批**（`action_registry.py:58-67`、`autonomy_gate.py:205-211`、`user_impact.py:10-22`）。版本级变更的风险等级与「重建单个 Pod」同为可自动，口径偏松。
- `full` 模式下**红线只剩 forbidden/动作不存在**：red（删除 deployment/secret、drain）也自动（`autonomy_gate.py:168-174`），节点隔离 `cordon+drain` 在 full 下直接执行（`alert_auto_service.py:2387-2404`）。
- `mode` 缺省 `by_env`，非 prod 即 `full`（`alert_auto_service.py:696-710`），所以「测试/UAT 全自动」是默认值；任何把 `env` 字段填错/缺失的集群也会落到 full。
- `automation_policy.mode` 可被 admin 一键设为 `full`，**同时写旧键 `automation_mode`**，等效于全局关闭审批（含 prod）（`api/settings.py:105-108`）。

---

## 6. 操作审计（问题 6）

| 维度 | 现状 | 证据 |
|---|---|---|
| 表结构 | `decision_id/trace_id/trigger_snapshot/ai_call_detail/decision_detail/execution_detail/verification_detail/hash/previous_hash`，**无 operator、action、target、result、before、after 列** | `models/audit.py:11-26` |
| who | 只在部分 `decision_detail` JSON 里（审批 `by=user_id`、回滚 `operator`）；审批创建记录**无操作者** | `approval_service.py:400-410,456-459`、`executions.py:104-108` |
| when | `created_at` | `models/audit.py:26` |
| what/target | 只在自由文本 JSON 中；审批记录无 cluster/namespace/resource | `approval_service.py:400-410` |
| result | 执行/验证结果写 `verification_detail`（auto 与审批路径都有） | `approval_service.py:1019-1024`、`alert_auto_service.py:2354-2375` |
| 失败是否记录 | 执行异常写 `exec_failed`；验证未通过写 `verdict=unchanged`；但**步骤级失败不记录**（见 EXE-11） | `approval_service.py:919-921,1019-1024` |
| 能否删除审计 | 应用层**无删除接口**，保留期清理明确排除 audit（合规） | `api/audit.py:11-31`、`services/data_retention_service.py:5` |
| 链完整性 | `update_audit_record` 只重算本行 hash，不更新后续行 `previous_hash` → 首次 upsert 后链即断 | `audit_service.py:103-113,116-138` |
| 覆盖缺口 | batch / alert-actions / custom-tasks / MCP / 资源变更 / Pod 终端 **零审计** | `api/batch.py`、`api/alert_actions.py`、`api/custom_tasks.py`、`api/mcp.py` 均无 `audit_service` 引用 |

补充：审批通过写审计时 `trace_id` 取 `approval.trace_id`，但 `Approval` 模型没有该字段，`hasattr` 恒 False → **trace_id=""**（`approval_service.py:456-458`、`models/approval.py:16-42`），审批动作在审计检索里无法按告警 trace 串起来。

---

## 7. 执行结果验证（问题 7）

- 审批路径验证：`verify_repair_outcome(alert)` → `_verify_auto_repair_ex`，判据为「Deployment 可用副本就绪 + 稳定性窗口（默认 20s）内重启次数不增长」，「已缩容至 0」单列（`alert_auto_service.py:1403-1451,1472-1490`）。
- 动作级验证：`action_verify.verify_action` 只在**自动路径**调用（`alert_auto_service.py:1317`），审批路径不调用。
- 失败处理：
  - 审批路径：验证 False → 计划 `failed`、告警 `pending + manual_required`、通知、群消息回写 `verify_failed`（`approval_service.py:1017-1034`）。
  - 验证 None（node/db/host/vm 无指标）→ **直接把告警置 resolved** 并标注「未验证」（`approval_service.py:1000-1016`）。
- **「执行失败但标记成功」确实可能：**
  1. `plan_runner.run_plan` 不抛异常，只返回带 `success=false` 的 progress；`execute_approved_plan` 只 `logger.info`，随后无条件 `plan.status="completed"`（`plan_runner.py:100-137`、`approval_service.py:896-929`）。
  2. 若随后验证因资源本身「就绪」（如探针改参失败但 Pod 本来就 Ready）判 True，则告警置 resolved，写「人工审批通过，已解决，验证通过」（`approval_service.py:978-996`）。
  3. `action_verify` 的 `pod_gone_or_recreated_120s` 分支把 `get_pod_status` **调用失败**（success=False）当作「Pod 已删除/重建」返回 True（`action_verify.py:76-78`）。

---

## 8. 并发 / 重复执行（问题 8）

| 场景 | 现状 | 结论 |
|---|---|---|
| 审批决策 | `select ... with_for_update()` + 幂等「重复审批」 | 单条安全（`approval_service.py:421-446`） |
| 审批 → 执行 | `execute_approved_plan` 先查 `plan.status` 再置 `executing`，两个事务间无锁/唯一约束 | **非原子**，可双跑（EXE-15） |
| 定时 + 立即 | apscheduler job id=`approval_exec_{plan_id}`，`replace_existing/max_instances=1` | 同进程内安全；跨副本不保证 |
| 批量执行 vs 审批执行 | batch 只锁 `lock:batch:{id}`，不锁 plan | 可并发执行同一 plan（`batch_service.py:44`） |
| 重复审批单 | `POST /approvals` 无去重；列表仅按 pod 显示去重（`api/approvals.py:63-75`） | 可产生多张；`execute_approved_plan` 取「最新一张」，新建 pending 单会**阻塞**已批准单的执行（`approval_service.py:872-878`） |
| 进程重启 | 通过后执行是 `asyncio.create_task`，无持久化任务/补偿 | 丢失即永不执行（EXE-14） |
| 告警幂等 | `process_alert` 单条 Redis 锁、建单前查 pending 去重 | 基本可用（`alert_auto_service.py:3521-3546,3493-3500`） |

---

## 9. gRPC executor 暴露面（问题 9）

- **无认证**：`grpc.NewServer()` 未加 `UnaryInterceptor/StreamInterceptor`，未启用 TLS（`k8s-executor/cmd/executor/main.go:95-109`）；`Server` 内所有 RPC 不读取 token/身份（`internal/grpc/server.go:32-77`，`traceID` 只取 trace）。
- **网络暴露**：
  - docker-compose：`ports: ["9090:9090"]` → 宿主机所有网卡可达（`docker-compose.yml:20-23`）。
  - K8s：Service 为 ClusterIP（`k8s/services.yaml:18-31`），有 NetworkPolicy 仅允许 `app=api-server` 入站（`k8s/networkpolicy.yaml:1-17`）——但依赖 CNI 实际执行，且同命名空间内任何带该 label 的 Pod 均可直连。
- **可绕过什么**：`ExecuteCommand`（白名单动词 get/describe/scale/rollout/delete/cordon/label/taint/logs/drain）、`ExecutePlan`（任意 plan_json）、`BatchExecute`、`RunScript`（busybox Job 任意 shell）、`DeleteResource`、`ScaleWorkload`、`ExecInPod`、`ApplyWorkloadYaml`、`UpdateProbe`、`RollbackWorkload`（`internal/grpc/server.go:128-230,622-710,740-766,956-992`）。**api-server 的 RBAC 与审批完全不在链路上。**
- **pull 模式 agent**：以 NodePort 暴露 `9100`（可选 `nodePort`），`pull-token` 为空时 `pullAuthOK` 直接放行（`agentbridge/bridge.go:230-236`）；未配证书时 gRPC 为明文。挂载的 ClusterRole 是 `apiGroups:["*"] resources:["*"] verbs:[get,list,watch,create,update,patch,delete]` + `impersonate`（`api/clusters.py:444-455`），等价集群管理员。任何能访问该 NodePort 的人可对全集群执行任意 RPC。

---

## 10. 功能状态清单

| 功能 | 状态 | 证据 | 备注 |
|---|---|---|---|
| 风险审批单创建（单人/双人） | 已实现（有代码依据） | `api/approvals.py:17-28`、`approval_service.py:350-412` | 无申请人字段 |
| 审批流程自定义（节点/定时提醒） | 已实现（有代码依据） | `api/approval_flows.py:87-162`、`approval_service.py:79-137` | 无「超时终态」；`group_all` 仅 admin 可批 |
| 平台内审批/批量审批 | 已实现（有代码依据） | `api/approvals.py:120-134,176-194` | 批量逐条开事务，无整体事务 |
| 加签 / 转办 | 已实现（有代码依据） | `approval_service.py:498-549` | **无操作人校验** |
| 审批权限校验 | 部分实现 | `approval_service.py:197-225` | admin 绕过节点；空 approvers 放行 |
| 审批 → 自动执行 | 已实现（有代码依据） | `approval_service.py:851-1034` | 无补偿、无内容绑定 |
| 定时执行 | 已实现（有代码依据） | `approval_service.py:481-497` | 到点不校验告警/计划有效性 |
| 审批超时升级 | 部分实现 | `approval_service.py:726-849` | 仅推进提醒，无终态 |
| 动作级验证（自动路径） | 部分实现 | `action_verify.py:54-94` | deadline 映射错位；查询失败当成功 |
| 修复结果验证（审批路径） | 已实现（有代码依据） | `approval_service.py:956-1016` | 无指标时直接关单 |
| 执行明细（步骤/日志） | 部分实现 | `api/executions.py:33-56` | 平台动作步骤不落 `execution_logs` |
| 一键回滚 | 已实现（有代码依据） | `api/executions.py:59-114` | 无审批、无集群权限校验 |
| 批量跨集群执行 | 有接口但业务逻辑不完整 | `batch_service.py:42-125` | 无审批/权限/审计；失败策略仅 continue |
| AI 处置台候选动作 | 已实现（有代码依据） | `alert_action_service.py:50-97` | 判定与执行同源 |
| AI 处置台一键执行 | 已实现（有代码依据） | `alert_action_service.py:183-267` | auto 直接执行；未校验集群权限 |
| autonomy_gate 三条件闸门 | 已实现（有代码依据） | `autonomy_gate.py:107-225` | full 模式几乎全放行 |
| 审计哈希链 | 部分实现 | `audit_service.py:48-138` | 更新即断链；字段不足 |
| 审计删除保护 | 已实现（有代码依据） | `api/audit.py:11-31` | 应用层无删除 |
| MCP 工具暴露 | 已实现（有代码依据） | `api/mcp.py:53-118` | 无写操作审批（注释自认） |
| executor gRPC 鉴权 | 完全未实现 | `cmd/executor/main.go:95-109` | 无 token/TLS |
| 数据库审核单（DB approval） | 已实现（有代码依据） | `api/db_approvals.py:112-255` | 可自批；默认策略全 false |

---

## 11. 问题清单

### APP（approval）

```
问题编号：APP-01
问题名称：加签/转办无操作人校验，任何 operator 可自加签或转办给自己后审批
问题等级：P0
所属模块：审批（approval）
问题描述：/approvals/{id}/add-approver 与 /{id}/transfer 只依赖 require_operator，service 层既不校验调用者是否为本单审批人/管理员，也不限制目标用户；approvers 是「并集 + 当前节点接收人」判定的唯一依据，因此任何 operator 可把自己追加为审批人（或把单据转给自己）再调用 /decide 通过，构成完整的自审自批链。前端对所有人展示「加签/转办」按钮，进一步降低利用门槛。
代码位置：api-server/app/api/approvals.py:137-154；api-server/app/services/approval_service.py:498-549；frontend/src/pages/ApprovalList.tsx:110-111
涉及函数或类：add_approver / transfer / Approval
调用链：POST /api/v1/approvals/{id}/add-approver → approval_service.add_approver → （approvers 追加）→ POST /decide → approve → _check_approver_permission（通过）→ execute_approved_plan
根本原因：把「谁能改审批人名单」当成普通操作员权限，未与「本单审批人/管理员」绑定；审批人名单无审计留痕。
可能影响：任意 operator 可批准任意高风险变更（含生产回滚/删除），审批机制形同虚设。
修改建议：加签/转办要求调用者 ∈ approvers 或 admin；转办目标不得为操作者本人；写审计（who→whom，前后 approvers）；前端按 can_approve 隐藏按钮。
验证方法：以非审批人 operator 调用 add-approver 加自己 → 期望 403；再 decide → 期望 403。
是否需要立即处理：是
```

```
问题编号：APP-02
问题名称：审批单无申请人字段，无法实施职责分离（申请≠审批）
问题等级：P1
所属模块：审批（approval）
问题描述：Approval 模型只有 plan_id/alert_id/approvers/approved_by，没有 requested_by/created_by；create_approval 也不记录发起人。因此无法判定「发起人不得批准自己的单」，也无法在审计中回答谁申请了这次变更。提示：create_approval 的 current_user 仅用于「approvers 为空时兜底」。
代码位置：api-server/app/models/approval.py:16-42；api-server/app/services/approval_service.py:350-412
涉及函数或类：Approval / create_approval
调用链：POST /approvals（operator）→ create_approval(plan_id, level, user) → Approval(...) 无 user 字段
根本原因：数据模型缺少申请人维度。
可能影响：值班人/操作员可发起并批准同一变更；事后追责只能依赖通知日志。
修改建议：新增 requested_by 列与迁移；approve 时校验 username != requested_by（可配置豁免）；审计写入申请人。
验证方法：同一账号 POST /approvals 再 decide → 期望被拒或需二次确认。
是否需要立即处理：是
```

```
问题编号：APP-03
问题名称：双人审批可被同一人同时满足（manager 与 admin 节点求交集）
问题等级：P1
所属模块：审批（approval）
问题描述：decision_level>=4 时节点为 manager(1 票) + admin(1 票)，_resolve_approve_status 对每个节点计算 len(set(approved_by) & receivers) >= approve_count，不要求跨节点不同人。若某 admin 同时是该项目 manager，approved_by=[该人] 即可同时满足两个节点，双人审批退化为单人；同时模型无申请人校验，可完全自审。
代码位置：api-server/app/services/approval_service.py:140-194；services/user_permission_service.py:123-142
涉及函数或类：_expand_dual_approvers / _resolve_approve_status
调用链：create_approval(level>=4) → _expand_dual_approvers → approve() → _resolve_approve_status
根本原因：用「每节点票数」近似「两人审批」，未做全局去重校验。
可能影响：高风险删除/回滚只需一人放行，违背双人复核设计。
修改建议：通过条件增加「len(set(approved_by)) >= 2」；manager 解析时排除 admin 角色或要求不同 user_id；单测覆盖同一人双角色。
验证方法：构造 admin 兼任 manager 的审批单，一人 decide → 期望仍 pending。
是否需要立即处理：是
```

```
问题编号：APP-04
问题名称：已驳回/已取消的计划可重新建审批单再次执行
问题等级：P1
所属模块：审批（approval）
问题描述：reject 会把 plan 置 cancelled；但 execute_approved_plan 只拦 status ∈ {completed, executing}，不拦 cancelled，也不校验告警状态；POST /approvals 允许任何 operator 对任意 plan_id 重新建单。于是「被拒 → 重新建单 → 批准」即可执行本应作废的计划。
代码位置：api-server/app/services/approval_service.py:596-628,866-878；api-server/app/api/approvals.py:17-28
涉及函数或类：_reject_risk_approval_alert / execute_approved_plan / create_approval
调用链：reject → plan=cancelled → POST /approvals(同 plan_id) → approve → execute_approved_plan（放行）
根本原因：执行前置校验只覆盖「重复执行」，未覆盖「计划/告警已作废」。
可能影响：被人工否决的危险变更可被再次执行；与告警已恢复的现实脱节。
修改建议：执行前置校验增加 plan.status == pending 且 alert.status ∈ {awaiting_approval, pending}；create_approval 拒绝 cancelled 计划；记录重建审批次数。
验证方法：reject 后建新审批并通过，观察是否执行。
是否需要立即处理：是
```

```
问题编号：APP-05
问题名称：定时执行到点不校验计划/告警当前状态
问题等级：P2
所属模块：审批（approval）
问题描述：scheduled 模式仅校验 scheduled_at 字符串格式，无「不得早于当前时间/最长提前量」限制；到点由 apscheduler 直接调 execute_approved_plan。若期间告警恢复、计划被 close_stale_repair_plans 置 cancelled（>30 分钟），任务仍会执行（因 cancelled 未被拦截），对已恢复业务重复变更。
代码位置：api-server/app/services/approval_service.py:481-497,866-878；services/repair_plan_service.py:34-60
涉及函数或类：_schedule_plan_execution / execute_approved_plan / close_stale_repair_plans
调用链：approve(execute_mode=scheduled) → add_job → 到点 execute_approved_plan
根本原因：定时任务与状态机解耦，无执行前二次校验。
可能影响：对已恢复/已关闭告警二次执行回滚或重启。
修改建议：定时任务触发时复用「APP-04 前置校验」；计划被取消时撤销 job；限制 scheduled_at 提前量。
验证方法：批准定时单后手动置告警 resolved，到点观察是否执行。
是否需要立即处理：否
```

```
问题编号：APP-06
问题名称：审批无超时终态，末节点永久 pending
问题等级：P2
所属模块：审批（approval）
问题描述：timeout_handler 到提醒时间只推进 current_node_index 并重发通知，最后一个节点不再提醒也不置终态；仅当关联告警自愈/解决才 cancelled。告警长期未恢复时审批单无限期挂起，占用「待审批」口径并持续通知。
代码位置：api-server/app/services/approval_service.py:726-849（786-800 的 continue 分支）；models/approval_flow.py:12-13
涉及函数或类：timeout_handler
调用链：后台循环（30s）→ timeout_handler → 只升级不终结
根本原因：产品口径明确取消超时，但缺少「挂起可见性/自动转人工」兜底。
可能影响：审批积压、值班交接丢失、指标失真。
修改建议：增加「N 小时未处理 → 标记 overdue/转工单」的可配置兜底，保留 pending 语义但可被统计识别。
验证方法：造一张末节点待审批单，观察 24h 状态与通知。
是否需要立即处理：否
```

```
问题编号：APP-07
问题名称：approvers 为空的旧数据跳过审批人校验
问题等级：P2
所属模块：审批（approval）
问题描述：_check_approver_permission 在 approvers 为空时直接返回 None（放行），只打 warning。历史数据/异常建单（流程展开后无接收人）会让任何 operator 可审批。
代码位置：api-server/app/services/approval_service.py:207-210
涉及函数或类：_check_approver_permission
调用链：approve/reject → _check_approver_permission → approvers==[] → 放行
根本原因：为兼容旧数据牺牲了安全默认值。
可能影响：低权限操作员可批准本应受控的单据。
修改建议：改为「仅 admin 放行」或拒绝并要求管理员修复单据；数据迁移回填 approvers。
验证方法：构造 approvers='[]' 的审批单，用普通 operator 审批。
是否需要立即处理：否
```

```
问题编号：APP-08
问题名称：审批列表/详情对所有登录用户可见（含 viewer/developer）
问题等级：P2
所属模块：审批（approval）
问题描述：GET /approvals、/approvals/{id} 使用 require_any_user，返回审批状态、审批人、计划摘要（前 200 字）、告警详情。viewer/developer 可见全平台审批与故障信息，与最小权限原则不符。
代码位置：api-server/app/api/approvals.py:31-91,157-173；middleware/permission.py:25
涉及函数或类：list_approvals / get_approval / require_any_user
调用链：GET /api/v1/approvals?status=pending
根本原因：读接口统一使用 require_any_user，未按项目/角色收窄（DB 审核单已按项目收窄，可对照）。
可能影响：横向信息泄露（集群/命名空间/资源/故障类型）。
修改建议：按 check_cluster_permission 或项目成员过滤；viewer 仅见统计。
验证方法：viewer 账号请求审批列表，检查返回条目。
是否需要立即处理：否
```

```
问题编号：APP-09
问题名称：group_all 节点语义与实现不一致（仅 admin 可批）
问题等级：P2
所属模块：审批（approval）
问题描述：节点类型 group_all 名义是「@群所有人」，但展开时只把 admin 放入 approvers，receiver_usernames 为空；非 admin 群成员即使被 @ 也无法审批（会被 forbidden）。若流程配置 group_all 且 include_admin=false，且此前无其他节点产生 approvers，则回落到 current_user 成为唯一审批人（自审）。
代码位置：api-server/app/services/approval_service.py:111-117,135-137
涉及函数或类：_expand_flow
调用链：create_approval → _expand_flow(group_all) → approvers 仅 admin / 空则 current_user
根本原因：通知范围与授权范围混为一谈。
可能影响：流程配置后无人可批（死锁）或发起人自批。
修改建议：group_all 明确「按群成员解析为审批人」或改名「admin 群通知」；approvers 为空时禁止创建并告警。
验证方法：配置仅 group_all 的流程，非 admin 群成员尝试审批。
是否需要立即处理：否
```

```
问题编号：APP-10
问题名称：审批审计 trace_id 恒为空，且创建记录无操作者
问题等级：P2
所属模块：审批（approval）/ 审计
问题描述：approve 写审计时取 approval.trace_id（模型无此字段，hasattr 恒 False）→ trace_id=""；create_approval 的审计 detail 只有 plan/alert/流程信息，没有发起人。审计检索（/audit/trace/{id}）无法把「审批」与「告警处理链」串起来。
代码位置：api-server/app/services/approval_service.py:396-410,456-459；models/approval.py:16-42
涉及函数或类：create_approval / approve / create_audit_record
调用链：POST /decide → approve → create_audit_record("", ...)
根本原因：模型缺 trace_id；审计字段设计缺少 actor。
可能影响：事后追责/复盘断链。
修改建议：审批单增 trace_id（建单时生成并复用）；审计写入 operator/approval_id/target。
验证方法：审批后按 alert trace 查审计，检查是否有 approve 记录。
是否需要立即处理：否
```

```
问题编号：APP-11
问题名称：数据库审核单允许申请人自批；策略默认全关闭（含删库）
问题等级：P1
所属模块：审批（DB approval）
问题描述：can_approve_db 只判断 admin 或项目 manager，未排除 requested_by；create_request 记录的 requested_by 从不参与审批校验。DbApprovalPolicy 无记录时 need_ddl/need_write 等默认全 false，即「建库/删库/DDL」默认免审，任何 operator 可直接执行（走 db_connections 直连执行路径）。
代码位置：api-server/app/api/db_approvals.py:39-50,211-255；services/db_approval_service.py:41-64,297-315
涉及函数或类：can_approve_db / create_request / execute_request / get_policy
调用链：POST /db-approvals（operator）→ approve（同一人）→ execute_request
根本原因：默认放行 + 缺少职责分离校验。
可能影响：误删/恶意删库无二次确认。
修改建议：requested_by == approver 时拒绝（除非 admin 且显式 force）；默认策略对 ddl/write 取 true；删库单独双人。
验证方法：项目 manager 提交 drop_db 并自批，观察是否执行。
是否需要立即处理：是
```

### EXE（execution）

```
问题编号：EXE-01
问题名称：/batch 任意 operator 可对任意集群执行任意计划（无审批/无集群权限/无审计）
问题等级：P0
所属模块：执行（batch）
问题描述：create_batch 接受任意 cluster_ids + plan_id，execute_batch 直接读 RepairPlan.plan_json 并对每个 cluster 调 executor execute_plan。全程没有审批状态校验（pending/cancelled 均可执行）、没有 check_cluster_permission、没有 audit 写入；execute 用 asyncio.create_task 异步，接口立即返回 running。_exec_item 在无 plan_json 时默认 success=True。
代码位置：api-server/app/api/batch.py:14-31；api-server/app/services/batch_service.py:42-125
涉及函数或类：create_batch / execute_batch / _run_strategy / _exec_item
调用链：POST /api/v1/batch → POST /{id}/execute → execute_batch → executor_client.execute_plan(cluster_id, plan_id, plan_json)
根本原因：批量能力被当作运维便利接口，未接入审批与 RBAC，也未做目标集群归属校验。
可能影响：越权对生产集群批量执行删除/回滚；把「待审批」计划提前执行；操作无留痕。
修改建议：要求 plan 对应审批 approved 且未执行；逐集群 check_cluster_permission；写审计（operator/targets/plan/result）；失败策略与结果回写。
验证方法：operator（无该项目权限）创建 batch 指向生产集群并执行，检查是否下发。
是否需要立即处理：是
```

```
问题编号：EXE-02
问题名称：MCP tools/call 不校验角色/集群权限/审批，任何登录用户可执行写工具
问题等级：P0
所属模块：执行（MCP）
问题描述：/mcp 使用 require_any_user（含 viewer/developer），tools/call 直接 td["handler"](args, context)，cluster_id 完全由调用方指定；exec_in_pod 的 permission=operate 但无任何拦截，且描述鼓励 apt-get/yum 安装与 sed -i 修改文件。模块 docstring 自认「如需审批控制，后续可在 tools/call 加拦截」。
代码位置：api-server/app/api/mcp.py:1-10,53-54,87-110；services/tool_registry.py:1301-1318,108-113
涉及函数或类：mcp_endpoint / _h_exec_in_pod / TOOL_REGISTRY
调用链：POST /mcp {method:"tools/call", name:"exec_in_pod"} → td["handler"] → executor_client.exec_in_pod
根本原因：MCP 作为「外部 AI 客户端入口」直接复用了 JWT 会话，未做能力降级。
可能影响：只读账号可在任意 Pod 内执行命令、安装软件、篡改配置；完全绕过审批与项目权限。
修改建议：tools/call 前按工具 permission 校验角色 + check_cluster_permission；operate 工具要求 operator 且写审计；默认只暴露 readonly 工具。
验证方法：viewer 账号通过 /mcp 调 exec_in_pod 在任意命名空间执行 `id`。
是否需要立即处理：是
```

```
问题编号：EXE-03
问题名称：k8s-executor gRPC 9090 无认证，compose 直接暴露到宿主机
问题等级：P0
所属模块：执行（executor）
问题描述：grpc.NewServer() 未配置任何拦截器/TLS/token 校验；所有 RPC（ExecuteCommand/ExecutePlan/BatchExecute/RunScript/DeleteResource/ScaleWorkload/ApplyWorkloadYaml/UpdateProbe/RollbackWorkload/ExecInPod）只要网络可达即可调用。docker-compose 将 9090 发布到宿主机全部网卡；K8s 侧仅靠 NetworkPolicy（CNI 不支持时无效）。
代码位置：k8s-executor/cmd/executor/main.go:95-109；k8s-executor/internal/grpc/server.go:32-77,128-230；docker-compose.yml:20-23；k8s/networkpolicy.yaml:1-17
涉及函数或类：NewServer / ExecuteCommand / ExecutePlan / RunScript / DeleteResource
调用链：任意客户端 → :9090 → ExecutorService（无身份）→ client-go → 集群
根本原因：服务间默认内网可信，认证只在 api-server 侧做。
可能影响：绕过全部审批/RBAC，直接删工作负载、执行任意 shell（RunScript 建 busybox Job）、排空节点。
修改建议：启用 mTLS 或共享 token 拦截器（metadata authorization）；compose 绑定 127.0.0.1；限制 RunScript/BatchExecute 白名单；NetworkPolicy 必配。
验证方法：从非 api-server 主机 grpcurl 调 ExecuteCommand `get nodes`。
是否需要立即处理：是
```

```
问题编号：EXE-04
问题名称：资源变更 API 大量端点只需 operator+项目权限，无审批（多数无审计）
问题等级：P0
所属模块：执行（K8s 资源操作）
问题描述：delete workload/object、scale（可降到 0）、rollout restart、rollback、recreate、apply YAML、env/mounts/probe 修改、exec in pod、node cordon/drain/isolate/labels/taints 等均只依赖 require_operator + _deny_cluster(operate)。审批机制只在「AI 计划」链路生效，人工直接改集群不受任何审批约束；resources.py 全文件仅 3 处写 audit。
代码位置：api-server/app/api/resources.py:591,745,770,1025,1138,1151,1270,1379,1398,1450,1475,2454,2816
涉及函数或类：delete_workload / scale_workload / rollout_workload / rollback_workload / update_probe / node_drain / exec_pod
调用链：前端集群管理 → PUT/DELETE /clusters/{id}/... → executor_client.*
根本原因：把「RBAC 有权限」等同于「可以直接变更」，审批只覆盖自动化链路。
可能影响：有 operate 权限的人可绕过审批直接删生产工作负载/排空节点，且无审计留痕。
修改建议：对高风险动词（delete/scale 0/drain/rollback/apply）引入与审批一致的决策引擎（或至少二次确认+审计）；统一写审计（operator/before/after）。
验证方法：operator 直接 DELETE /workloads/deployment/... 观察是否执行与是否有审计。
是否需要立即处理：是
```

```
问题编号：EXE-05
问题名称：agent pull 模式以 NodePort 暴露 9100，令牌可为空，权限等价 cluster-admin
问题等级：P0
所属模块：执行（agent 接入）
问题描述：安装模板为 pull 模式创建 type=NodePort 的 Service（可选 nodePort），agent 侧 pull-token 为空时 pullAuthOK 直接放行；未配置证书时 gRPC 明文。agent 挂载的 ClusterRole 是 apiGroups/resources 全量 + create/update/patch/delete + impersonate。bridge 暴露完整 ExecutorService（含 ExecuteCommand/ExecutePlan）。
代码位置：api-server/app/api/clusters.py:395-426,444-455；k8s-executor/internal/agentbridge/bridge.go:164-240；k8s-executor/cmd/agent/main.go:103,214-215
涉及函数或类：ServePull / pullUnaryAuth / pullAuthOK / Bridge.Call
调用链：外部 → NodePort 9100 → agent ExecutorService → 集群 API
根本原因：pull 模式为「内网调试」放宽了认证，但安装模板默认暴露 NodePort。
可能影响：集群被完全接管（任意 RPC + cluster-admin）。
修改建议：pull-token 强制非空且随机；默认 ClusterIP/TLS；文档与模板禁止空 token；限制 bridge 方法白名单。
验证方法：用空 token grpcurl 调 9100 的 ExecuteCommand。
是否需要立即处理：是
```

```
问题编号：EXE-06
问题名称：custom-tasks 可建任意 shell 任务并立即/定时执行，无审批无审计
问题等级：P1
所属模块：执行（定时任务）
问题描述：create_task 接受 script_content，run-now 异步执行；script 类型由 executor 创建 busybox Job 执行 `/bin/sh -c <script>`（无内容校验）。restart/scale 类型拼 kubectl 命令直接执行。仅写 CustomTaskLog，无 audit，无审批，无集群权限校验（create 时不校验 cluster 归属）。
代码位置：api-server/app/api/custom_tasks.py:28-46,77-82；services/scheduler_service.py:45-107；k8s-executor/internal/script/runner.go:32-88
涉及函数或类：create_task / run_now / execute_task / Runner.RunScript
调用链：POST /custom-tasks → POST /{id}/run-now → execute_task → run_script → 建 Job
根本原因：任务调度被当作「运维脚本平台」，未纳入变更管控。
可能影响：任意 shell（含挖矿、横向、删库脚本）在集群 default 命名空间执行；可持续化（cron）。
修改建议：script 类型需审批 + admin 审批；限制镜像/命名空间/超时；写审计；run-now 记操作者。
验证方法：operator 建 script 任务 `id; ls /` 并 run-now。
是否需要立即处理：是
```

```
问题编号：EXE-07
问题名称：一键回滚接口无审批、无集群权限校验
问题等级：P1
所属模块：执行（executions）
问题描述：POST /executions/{plan_id}/rollback 只要求 require_operator，直接取 plan.plan_json 的 rollback_command 交 executor 执行；不校验调用者对该集群的权限，不校验是否审批人，不要求二次确认（前端仅 Popconfirm）。回滚本身是变更操作。
代码位置：api-server/app/api/executions.py:59-114
涉及函数或类：rollback_execution / _rollback_steps
调用链：POST /executions/{plan_id}/rollback → executor_client.rollback_plan
根本原因：回滚被视为「恢复动作」而豁免管控。
可能影响：跨项目回滚他人集群；回滚到不健康版本造成二次故障。
修改建议：加 _deny_cluster(operate) 与审批（或 admin-only）；写审计含 before/after revision。
验证方法：无该项目权限的 operator 调用回滚。
是否需要立即处理：是
```

```
问题编号：EXE-08
问题名称：处置台 auto 路径直接执行业务变更（非生产默认 full）
问题等级：P1
所属模块：执行（alert-actions）
问题描述：dispatch_action 在 gate=auto 时创建 RepairPlan 后立即 asyncio.create_task(execute_approved_plan(plan_id))；由于没有审批单，execute_approved_plan 的审批校验被跳过（appr=None）。mode 缺省 by_env → 非 prod 即 full，几乎全部动作 auto。接口也未校验调用者对该告警集群的权限。
代码位置：api-server/app/services/alert_action_service.py:214-257；alert_auto_service.py:696-710
涉及函数或类：dispatch_action / execute_approved_plan / _automation_mode
调用链：POST /alerts/{id}/actions/{key}/dispatch → gate=auto → execute_approved_plan（无审批）
根本原因：把「AI 自主处置」的授权模型复用到「人手动点击」，但未加人工确认。
可能影响：测试/UAT 一键回滚、一键摘流即时生效；越权操作他人项目告警。
修改建议：手动发起至少 require approve（或显式确认参数）；加 check_cluster_permission；记录 operator 到审计。
验证方法：在 env=uat 集群点「回滚」候选动作，观察是否立即执行。
是否需要立即处理：是
```

```
问题编号：EXE-09
问题名称：autonomy_gate 自动执行范围偏大（rollback/摘流/改 spec 可无审批自动）
问题等级：P1
所属模块：执行（授权策略）
问题描述：rollback_deployment 仅标 yellow，prod 且「无用户感知」即 auto；fix_image_pull_secret（修改工作负载 imagePullSecrets）、drain_traffic（摘流）为 green，prod 无感知也 auto；delete_stuck_pod 在 alert_auto_service 被硬编码为永远 auto。yellow 的判定条件「非生产 或 无感知」使生产多副本服务成为默认可自动对象。
代码位置：api-server/app/services/action_registry.py:27-88；autonomy_gate.py:205-221；alert_auto_service.py:653-656；user_impact.py:10-22
涉及函数或类：evaluate / judge_user_impact / _evaluate_autonomy_gate
调用链：process_alert → _evaluate_autonomy_gate → gate=auto → _execute_auto_plan
根本原因：风险分级以「是否中断流量」为唯一维度，未考虑「变更可逆性/影响版本」。
可能影响：生产版本回滚、镜像凭据变更、摘流在无人确认下自动执行。
修改建议：把 rollback/镜像/摘流提升为 red 或强制 approve；「无感知」只用于扩缩容类；为 delete_stuck_pod 的免审加显式开关与审计。
验证方法：造 prod 双副本 Deployment 的 CrashLoop，观察是否自动 rollback。
是否需要立即处理：是
```

```
问题编号：EXE-10
问题名称：mode=full 可一键关闭全部审批（含生产），无二次确认/审计
问题等级：P1
所属模块：执行（授权策略）
问题描述：PUT /settings/automation-policy 接受 mode=full 并同时写旧键 automation_mode；full 下 red/低置信度/prod 用户可感知全部放行，节点 cordon+drain 也直接执行。接口只要求 admin，无二次确认、无变更审计、无生效范围提示。
代码位置：api-server/app/api/settings.py:64-110；autonomy_gate.py:168-174；alert_auto_service.py:3305-3311,2387-2404
涉及函数或类：update_automation_policy / evaluate / _process_node_alert
调用链：PUT /settings/automation-policy {mode:"full"} → 所有告警自动执行
根本原因：把「总开关」做成普通设置项。
可能影响：一次误操作使生产进入全自动自愈，含 drain 节点。
修改建议：full 仅允许在非 prod 或带有效期；变更写审计并通知；prod 强制 assist；提供「仅本次」临时开关。
验证方法：设为 full 后触发一条 red 动作告警，观察是否直接执行。
是否需要立即处理：是
```

```
问题编号：EXE-11
问题名称：执行步骤失败被吞，计划仍置 completed
问题等级：P1
所属模块：执行（execution）
问题描述：plan_runner.run_plan 对失败步骤只置 progress.success=false，不抛异常；execute_approved_plan 仅按异常判断，循环里只 logger.info，随后无条件 plan.status="completed"。平台动作失败会 break，但同样不抛。结果：执行失败的计划显示「已完成」，后续验证若因资源本就就绪而返回 True，则告警置 resolved 并写「验证通过」。
代码位置：api-server/app/services/approval_service.py:896-929；services/plan_runner.py:100-160
涉及函数或类：execute_approved_plan / run_plan
调用链：execute_approved_plan → plan_runner.run_plan → progress(success=false) → plan.status=completed
根本原因：执行结果只看异常，未消费 progress 的 success 聚合。
可能影响：假成功、掩盖真实故障、知识库沉淀错误方案。
修改建议：聚合 progress，任一失败则 plan=failed 并按失败路径处理；平台动作与 shell 步骤分开记录。
验证方法：构造必然失败的命令步骤（如不存在的资源），观察 plan 状态与告警结论。
是否需要立即处理：是
```

```
问题编号：EXE-12
问题名称：无可验证指标时直接关告警并标「未验证」（可能与失败混淆）
问题等级：P1
所属模块：执行（验证）
问题描述：verify_repair_outcome 对 node/db_connection/host/vm 或无法推断 Deployment 的资源返回 None；execute_approved_plan 在 repaired is None 时把告警置 resolved，message 追加「已执行；未验证」。这等于「无法证明成功」被当作「成功关单」；对节点 drain/DB 变更尤其危险。
代码位置：api-server/app/services/approval_service.py:1000-1016；alert_auto_service.py:1472-1490
涉及函数或类：execute_approved_plan / verify_repair_outcome
调用链：执行完成 → verify None → alert.status=resolved
根本原因：缺少「未验证」独立终态与人工复核队列。
可能影响：失败变更被静默关单，失去后续跟进。
修改建议：未验证置 `pending + manual_required(未验证)` 或独立 `unverified` 状态；对节点/DB 类动作定义替代验证指标。
验证方法：对 node 类告警走审批执行，检查告警终态。
是否需要立即处理：是
```

```
问题编号：EXE-13
问题名称：action_verify 验证口径映射错位，且把查询失败当成功
问题等级：P2
所属模块：执行（验证）
问题描述：verify_action 的 deadline 字典以「verify 口径」为 key（pod_ready_180s 等），但调用方传的是 action_key（rebuild_pod 等），实际恒取默认 180s；pod_gone_or_recreated 分支在 get_pod_status 返回 success=False（集群不可达/权限错误）时直接 return True「Pod 已删除/重建」，构成假成功。
代码位置：api-server/app/services/action_verify.py:62-68,73-78；alert_auto_service.py:1317
涉及函数或类：verify_action
调用链：_execute_auto_plan → verify_action(action_key, alert)
根本原因：两套标识（action key 与 verify spec）混用；失败语义未区分「NotFound」与「调用失败」。
可能影响：验证窗口不符预期；集群异常时误判修复成功。
修改建议：用 get_action(action_key)["verify"] 查 deadline；success=False 时区分 NotFound 与错误，错误返回 False。
验证方法：断开 executor 后触发验证，观察是否返回 True。
是否需要立即处理：否
```

```
问题编号：EXE-14
问题名称：审批通过后的执行无持久化/补偿，重启即丢失
问题等级：P2
所属模块：执行（execution）
问题描述：approve 后的执行是进程内 asyncio.create_task；无任务表、无重试、无启动补偿。进程重启/发布窗口内批准的单据会停在 approved + plan=pending，最终被 close_stale_repair_plans 置 cancelled，审批「已通过但永不执行」且无告警。
代码位置：api-server/app/services/approval_service.py:466,851-878；services/repair_plan_service.py:34-60
涉及函数或类：approve / execute_approved_plan / close_stale_repair_plans
调用链：approve → create_task → 进程退出 → 无恢复
根本原因：执行未做成可恢复的作业。
可能影响：关键修复丢失，值班以为已处理。
修改建议：新增「approved 且 plan=pending 超过 N 分钟」补偿循环（加锁+幂等）；或落任务表由 worker 消费。
验证方法：批准后立即重启 api-server，观察计划是否执行。
是否需要立即处理：否
```

```
问题编号：EXE-15
问题名称：执行幂等/并发控制不完整
问题等级：P2
所属模块：执行（execution）
问题描述：execute_approved_plan 的「查状态→置 executing」跨两个事务，无行锁/唯一约束，两个触发源可同时通过；batch 与审批执行可并发跑同一 plan（batch 只锁 batch id）；POST /approvals 无去重，多张单据时执行取「最新一张」，新建 pending 单会阻塞已 approved 单的执行。
代码位置：api-server/app/services/approval_service.py:860-892；services/batch_service.py:42-68
涉及函数或类：execute_approved_plan / execute_batch
调用链：approve(immediate) 与 batch execute 并发 → 双执行
根本原因：缺「执行中」原子抢占。
可能影响：同一变更执行两次（如重复回滚/重复删 Pod）。
修改建议：`UPDATE repair_plans SET status='executing' WHERE id=? AND status='pending'` 原子抢占；按 plan 加 Redis 锁；审批单按 plan 唯一约束（pending/approved 各一）。
验证方法：并发调用 approve 与 batch execute，检查 executor 侧调用次数。
是否需要立即处理：否
```

```
问题编号：EXE-16
问题名称：平台动作步骤不写 execution_logs，执行明细页空白
问题等级：P2
所属模块：执行（明细）
问题描述：只有 executor 的 GrayExecutor/RollbackPlan 会写 execution_logs；plan_runner 本地执行的 platform_action（update_probe、rollback_deployment）只返回 progress，不落库。前端 ExecutionDetail/ExecutionsPage 轮询 execution_logs，因此这类计划「无步骤、无输出」，回滚时也找不到 step 记录可更新。
代码位置：api-server/app/services/plan_runner.py:32-113；k8s-executor/internal/executor/gray_release.go:132-153；api/executions.py:33-56
涉及函数或类：run_plan / _run_platform_action / recordLog
调用链：execute_approved_plan → run_plan(platform_action) → 无 ExecutionLog 写入
根本原因：执行路径双轨（本地/远端），日志写入只在远端。
可能影响：无法复盘平台动作；前端展示与真实执行不一致。
修改建议：plan_runner 写 ExecutionLog（api-server 侧）；或在 execute_approved_plan 汇总落库。
验证方法：执行一条 update_probe 计划，查 execution_logs。
是否需要立即处理：否
```

```
问题编号：EXE-17
问题名称：审计哈希链在更新后断裂
问题等级：P2
所属模块：审计
问题描述：update_audit_record 修改字段后只重算本行 hash_value，不更新后续记录的 previous_hash（也无法更新，链式依赖单向）。因此第一次 upsert 之后，verify_audit_chain 必报 integrity_ok=False；而 alert_auto_service 大量使用 update 追加 execution_detail/decision_score，等于常态断链。
代码位置：api-server/app/services/audit_service.py:76-113,116-138
涉及函数或类：update_audit_record / verify_audit_chain
调用链：process_alert → update_audit_record → 链断 → POST /audit/verify-chain
根本原因：可变字段与不可变哈希链冲突（应 append-only）。
可能影响：防篡改能力失效（告警疲劳），无法用于合规证明。
修改建议：改为 append-only（每次状态变更新增一条 record，字段放 detail）；或引入版本号重算后续链。
验证方法：跑一次自动处置后执行 verify-chain。
是否需要立即处理：否
```

```
问题编号：EXE-18
问题名称：审计字段不足以回答 who/what/target/result/before-after
问题等级：P2
所属模块：审计
问题描述：AuditRecord 只有 5 段自由文本 + trace；没有 actor、action、cluster_id、namespace、resource、result、before/after 结构化列，查询只能在 Python 侧解析 trigger_snapshot。多路径（审批创建）不写 trigger_snapshot，导致业务维度筛选失效。
代码位置：api-server/app/models/audit.py:11-26；services/audit_service.py:13-28,158-220
涉及函数或类：AuditRecord / _parse_business / search_audit
调用链：/audit/search?cluster_id=... → Python 过滤
根本原因：审计表按「AI 决策链」设计，未按「操作审计」设计。
可能影响：无法按操作者/资源检索；合规审计成本高。
修改建议：新增列 actor/action/target_kind/target_name/cluster_id/namespace/result/before_hash/after_hash；旧字段兼容保留。
验证方法：尝试按 operator 检索审计（当前无入口）。
是否需要立即处理：否
```

```
问题编号：EXE-19
问题名称：审计覆盖缺口：batch / dispatch / custom-tasks / MCP / 资源变更不写审计
问题等级：P2
所属模块：审计
问题描述：全仓库 create/update_audit_record 仅出现在 alert_auto、host_*、approval、resources(3 处)、executions、alerts、sql_task、action_verify。batch、alert_actions、custom_tasks、mcp、terminal、resource_governance_ctl 均无审计调用，这些恰是「直接改变集群」的入口。
代码位置：api-server/app/api/batch.py、alert_actions.py、custom_tasks.py、mcp.py、terminal.py、resource_governance_ctl.py（grep 无 audit_service）
涉及函数或类：各入口 handler
调用链：任意上述入口 → executor_client
根本原因：审计按链路零散埋点，无统一中间件。
可能影响：越权/误操作无法追溯。
修改建议：在 executor_client 变更类方法统一埋点（或装饰器），确保 operator/target/result 必填。
验证方法：执行 batch 后查 audit_records 是否有对应记录。
是否需要立即处理：否
```

```
问题编号：EXE-20
问题名称：内部令牌复用 JWT 签名密钥
问题等级：P3
所属模块：执行（内部回调）
问题描述：/alerts/{id}/auto-process 与 /recovery-notify 的 X-Internal-Token 直接与 settings.JWT_SECRET_KEY 比较，密钥复用使「签发会话」与「内部触发」耦合；密钥泄露时攻击者可无限制触发自动处置，且无法独立轮换。
代码位置：api-server/app/api/alerts.py:532-560
涉及函数或类：auto_process_alert / recovery_notify_alert
调用链：k8s-executor watcher → POST /alerts/{id}/auto-process
根本原因：缺少独立服务间凭证。
可能影响：内部触发口无独立吊销手段。
修改建议：引入独立 INTERNAL_API_TOKEN（可轮换）；限制来源 IP/命名空间。
验证方法：用错误 token 调用，确认 401；轮换 JWT 密钥观察内部回调是否中断。
是否需要立即处理：否
```

```
问题编号：EXE-21
问题名称：Pod 终端 JWT 放在 URL query
问题等级：P3
所属模块：执行（终端）
问题描述：WebSocket 终端从 query_params 取 token，易被网关/代理访问日志、浏览器历史记录留存；终端本身是 operate 级能力。
代码位置：api-server/app/api/terminal.py:38-55
涉及函数或类：ws_exec_pod / _resolve_user
调用链：ws://.../exec?token=JWT
根本原因：浏览器 WebSocket 无法自定义 Header 的折中方案。
可能影响：令牌泄露导致会话劫持（可 exec 进容器）。
修改建议：改用一次性短期 ws-ticket（后端签发、单次使用）；日志脱敏。
验证方法：检查 nginx/uvicorn access log 是否出现 token。
是否需要立即处理：否
```

```
问题编号：EXE-22
问题名称：executor 作为第二个数据库写者，可绕过 API 层权限与审计
问题等级：P2
所属模块：执行（数据面）
问题描述：k8s-executor 直连 MySQL（GORM）并写 alerts/execution_logs，watcher 也会更新告警状态；这些写入不经过 api-server 的 ORM 钩子与审计。任何能调用 executor（见 EXE-03/05）的人可间接改库；schema 由 api-server alembic 管理，双写者存在漂移风险。
代码位置：k8s-executor/cmd/executor/main.go:45-47；internal/watcher/sink.go:196-215；internal/executor/gray_release.go:132-153
涉及函数或类：watcher sink / recordLog
调用链：executor → GORM → MySQL（alerts、execution_logs）
根本原因：为实时性引入第二写者，未做写入白名单。
可能影响：审计/权限边界被绕过；状态不一致。
修改建议：executor 仅保留 execution_logs 写入（或改回调 API）；告警写入回归 api-server。
验证方法：审计 executor 的 GORM 写入表清单并与 API 写路径对比。
是否需要立即处理：否
```

```
问题编号：EXE-23
问题名称：审批未绑定计划内容摘要，plan_json 变更不可检测
问题等级：P2
所属模块：执行（参数完整性）
问题描述：审批单只存 plan_id，不存 plan 的 hash/version/步骤快照；执行时读 DB 现值。repair_recurrence 等路径会整段重写 plan_json；运维改库亦可。批准的内容与实际执行内容可能不同，且审批详情页只展示 plan_json 前 200 字，人无法核对完整步骤。
代码位置：api-server/app/services/approval_service.py:350-412,882-896；services/repair_recurrence_service.py:147-158；api/approvals.py:157-173
涉及函数或类：create_approval / execute_approved_plan / get_approval
调用链：create_approval（无 hash）→ plan_json 被改写 → execute_approved_plan 执行新内容
根本原因：审批对象是「可变行」而非「不可变快照」。
可能影响：审批被绕过（批 A 执行 B）；事后无法证明执行内容。
修改建议：审批单存 plan_json 快照 + sha256；执行前比对，不一致则拒绝并要求重新审批；详情页展示完整步骤 diff。
验证方法：批准后手工改 plan_json 再执行，观察是否被拒。
是否需要立即处理：否
```

---

## 12. 无法确认事项

1. **NetworkPolicy 是否真正生效**：`k8s/networkpolicy.yaml` 只声明了 Ingress 白名单，实际取决于集群 CNI（Calico/Cilium 等）。源码无法确认生产是否部署了该策略、CNI 是否强制。
2. **pull 模式实际部署参数**：`pull-token`/证书是否为空取决于安装时是否传参（`api/clusters.py:395-409`）；生产实际值无法从源码确认（需查集群 Secret/Deployment args）。
3. **executor 的 MySQL 账号权限范围**：能写哪些表取决于 DSN 授权，源码只见 GORM 模型；是否可删审计表暂时无法确认。
4. **前端角色可见性**：`utils/auth.ts` 的 `isAdmin()` 仅影响按钮展示，后端才是边界；未逐一核对每个按钮的隐藏逻辑（不影响安全结论）。
5. **DB 连接直连执行路径**（`db_connections` 非审核单入口）是否对 write/ddl 做了独立的 `needs_approval` 拦截：本次只审了 db_approvals 分支，`api/db_connections.py` 内的执行入口未逐一验证，写 DDL 是否默认免审需二次确认。

---

## 13. 审计方法与覆盖范围

**已读源码（含行号定位）：**

- API：`api/approvals.py`、`approval_flows.py`、`db_approvals.py`、`executions.py`、`alert_actions.py`、`decisions.py`、`tasks.py`、`batch.py`、`audit.py`、`mcp.py`、`resources.py`（变更端点抽样）、`custom_tasks.py`、`terminal.py`、`alerts.py`（auto-process）、`settings.py`（automation-policy）、`clusters.py`（agent 安装模板）
- Service：`approval_service.py`（全量 1055 行）、`db_approval_service.py`、`action_registry.py`、`action_verify.py`、`autonomy_gate.py`、`decision_engine.py`、`command_guard.py`、`audit_service.py`、`alert_action_service.py`、`alert_auto_service.py`（关键段：600-760、900-1010、1270-1340、1470-1560、2340-2420、3255-3430、3490-3560）、`plan_runner.py`、`batch_service.py`、`scheduler_service.py`、`repair_plan_service.py`、`repair_recurrence_service.py`、`user_permission_service.py`、`user_impact.py`、`tool_registry.py`（注册段 + exec_in_pod）
- Model：`approval.py`、`approval_flow.py`、`execution.py`、`task.py`、`repair_plan.py`、`audit.py`、`decision.py`
- Middleware：`permission.py`
- Go：`k8s-executor/cmd/executor/main.go`、`internal/grpc/server.go`（RPC 清单与鉴权）、`internal/executor/execute.go`（白名单/危险命令）、`internal/agentbridge/bridge.go`（pull 鉴权）、`internal/script/runner.go`、`internal/executor/gray_release.go`、`rollback.go`
- 部署：`docker-compose.yml`、`k8s/services.yaml`、`k8s/deployments.yaml`、`k8s/networkpolicy.yaml`
- 前端：`pages/ApprovalList.tsx`、`AlertHandling.tsx`、`ExecutionsPage.tsx`、`ExecutionDetail.tsx`（扫读）、`components/AlertActionPanel.tsx`（扫读）
- 规范：`docs/architecture/audit/_AUDIT_TEMPLATE.md`、`04-system-architecture.md`

**未覆盖/未深入的：**

- `ai-service` 侧（诊断结果的置信度来源、是否可被 prompt 注入影响 gate 判定）
- `api/db_connections.py` 的 SQL 直连执行入口（见「无法确认事项 5」）
- 各 IM 渠道回调（钉钉/企微/飞书）的完整验签实现（只看了 webhooks 的验签存在性与 approval handle_callback 的映射逻辑）
- 前端全部按钮级权限（`utils/auth.ts`）
- 未运行任何服务、未执行任何接口请求（只读静态审计）
