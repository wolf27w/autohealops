# 12 · 多项目隔离审计（Project Isolation Audit）

> 审计类型：**只读**静态审计。未修改代码/配置/依赖，未装依赖，未连生产库，未发真实请求，未执行 git 写操作。
> 仓库：`/Users/wolf/Documents/project/autohealops`　分支：`main`　审计时点：2026-11-27
> 覆盖：`models/project.py`、`models/permission.py`、`models/host.py`、`services/user_permission_service.py`、`services/ticket_service.py`、`services/trace_service.py`、`services/notification_service.py`、`services/rbac_service.py`、`services/db_approval_service.py`、`api/projects.py`、`api/permissions.py`、`api/clusters.py`、`api/resources.py`、`api/alerts.py`、`alert_actions.py`、`repair_records.py`、`executions.py`、`tasks.py`、`tickets.py`、`approvals.py`、`audit.py`、`fault_diagnosis.py`、`diagnostic.py`、`db_connections.py`、`db_approvals.py`、`db_diagnosis.py`、`hosts.py`、`host_terminal.py`、`stats.py`、`release.py`、`monitoring.py`、`logs.py`、`inspections.py`、`api-server/app/api/ai_proxy.py` + `ai-service/app/api/aux_api.py`、`frontend/src/pages/ProjectManagement.tsx`、`frontend/src/pages/ApprovalList.tsx`。

---

## 1. 结论摘要

1. **「项目」在本系统里是「授权代理」，不是「数据分区」**：`Project` 只聚合 `project_cluster_ns` / `project_db_connections` / `user_project_permissions` 三张关系表（`models/project.py:16-70`），**任何业务表都不带 `project_id`**。87 个 ORM 模型里只有 **3 张**有 `project_id`（且全是关联表），**26 张**有 `cluster_id`，**58 张**两者都没有（其中一部分可经父表 join 推导）。因此「按项目过滤列表」在数据模型上**做不到**，只能退化为「按 cluster/namespace 过滤」。
2. **项目关联能力是够的**：一个项目可关联**多集群 × 多 namespace**（`ProjectClusterNS` 无唯一约束，`projects.py:141-165` 反复插入），可关联**多个数据库实例**（`ProjectDBConnection`），**不能关联主机/VM**（`Host` 只有 `group_id`/`cluster_id`，`models/host.py:18-50`）。项目也不落任何 K8s 资源快照/告警，全靠在**读时**用 `(cluster_id, namespace)` 反查项目（`notification_service.py:140-146`）。
3. **用户-项目-角色表结构支持「一人多项目不同角色」**（`user_project_permissions` 唯一性靠应用层判重，`projects.py:257-266`），代码也确实按 `(user_id, project_id)` 取角色（`user_permission_service.py:72-87`、`db_approval_service.py:305-360`、`resolve_project_managers:123-142`）。**但 `manager` 角色没有自助能力**：项目成员/关联的全部写接口都写死 `require_admin`（`projects.py:142,201,251,276,320`），平台管理员才能加人——与「项目管理员管成员」的目标相反。
4. **隔离落点极不均匀**：做得对的有 **工单**（`ticket_service._scope_conditions` 按可见集群 ∪ 本人创建 ∪ 本人被指派 ∪ 有权限的库，`ticket_service.py:221-238`）、**K8s 资源**（79 条路由里 77 条调 `_deny_cluster`，`resources.py:31-34`）、**集群列表**（`get_visible_cluster_ids`，`clusters.py:750-762`）、**主机**（按组，`hosts.py:99-113`）、**巡检**（`inspections.py:31-53`）、**Agent 轨迹**（`trace_service.py:792-812`）、**数据库审核单**（`db_approvals.py:189-240`）。**完全没做**的有：**告警全域、处理记录、审批、执行明细、任务、诊断报告、审计检索、批量操作、仪表盘聚合**——这些接口都是 `require_any_user` + 无任何范围条件，等于「任何登录用户 = 全平台视图」。
5. **4 个可静态证实的越权链**（详见 §5）：
   - **ISO-01** 任意登录用户 `GET /api/v1/alerts` 拿到**所有集群**的告警（含故障原因、诊断结论、修复命令）——告警表是平台核心数据；
   - **ISO-02** 只被授权 1 个 namespace 的用户，把 `namespace` 参数传空即可读**整个集群所有 namespace** 的 Pod/ConfigMap/Secret 名/PVC（`resources.py:605-611,706-720`，因为 `check_cluster_permission` 在 `namespace=""` 时退化为集群级判断，`user_permission_service.py:47-70`）；
   - **ISO-03** 任意 operator 可 `POST /api/v1/executions/{plan_id}/rollback` **回滚别人项目集群的修复**（`executions.py:59-83`，无 `check_cluster_permission`）；
   - **ISO-04** 任意登录用户可读任意数据库连接的 **DDL/建表语句**、**定时 SQL 与收件邮箱**（`db_connections.py:543,1442`，这两个端点漏了 `check_db_permission`）。
6. **读接口漏校验比写接口多**：47 条 DB 路由里 44 条有 `check_db_permission`，漏的 3 条都是读或探测类（`object-ddl`、`sql-tasks` 列表、`test`）；79 条 K8s 路由只有 2 条漏（`registry/tags`、`registry/parse`，属镜像仓库解析，与集群无关）。
7. **namespace 维度的写绕过**：`PUT /clusters/{id}/objects/{kind}/yaml` 用**逐行文本扫描**找 `namespace:`（`resources.py:754-760`），flow-style YAML 或 JSON 正文（`{"metadata":{"namespace":"prod"}}`）不会被识别 → `ns=""` → 授权按「集群级」放行 → 写入落到 YAML 里写的任意 namespace（ISO-07）。
8. **跨租户日志读取**：`POST /api/v1/ai/logs/search` 经 `ai_proxy` 转发到 ai-service，直接按 `namespace`/`pod` 查 ES（`ai-service/app/api/aux_api.py:421-428`），**没有 cluster_id 概念、没有权限校验**——一个项目的用户可捞另一个项目的日志（ISO-06）。
9. **前端确实把 `project_id` 当普通筛选参数传**（`ApprovalList.tsx:219`、`ProjectManagement.tsx:134-136`），后端 `stats.dashboard` 也直接收 `project_id`/`cluster_id` 做聚合（`stats.py:1424-1435`，`_user` 参数未使用）→ 手工构造请求即可看别的项目/集群的聚合指标（ISO-11，影响限于统计口径而非明细）。
10. **与未来目标（LDAP + 多项目多角色 + 项目管理员管成员）的差距是「权限模型层」的，不是「补几个 where」能解决的**：缺少 `project_id` 列、缺少「项目管理员」授权中间件、缺少「资源→项目」的反查索引；告警/工单/审批/知识库/告警策略/日志源这些**全局对象**在当前模型里根本无法归属项目（ISO-09）。

---

## 2. 项目模型的真实能力

### 2.1 表结构

```python
# api-server/app/models/project.py:16-70（节选）
class Project(Base):                       # id, name(unique), description,
    notify_config_id / approval_flow_id     # 通知配置、审批流（按项目路由）
    created_by, created_at

class ProjectClusterNS(Base):              # 项目 × 集群 × namespace（无唯一约束）
    project_id, cluster_id, namespace

class ProjectDBConnection(Base):           # 项目 × 数据库实例
    project_id, db_connection_id

class UserProjectPermission(Base):         # 用户 × 项目 × 三级权限
    user_id, project_id, permission        # read / operate / manager
```

### 2.2 能力矩阵

| 能力 | 是否支持 | 证据 |
|---|---|---|
| 一个项目关联**多集群** | ✅ | `ProjectClusterNS` 每次插入一行，`projects.py:141-165`；`GET /projects/{id}/cluster-ns` 返回列表 |
| 一个项目关联**同集群多 namespace** | ✅ | 同上，无 `(project_id, cluster_id)` 唯一约束 |
| 一个项目关联**多数据库** | ✅ | `ProjectDBConnection` + `projects.py:200-218` |
| 一个项目关联**多 VM / 主机** | ❌ | `Host` 无 `project_id`（`models/host.py:18-50`），只有 `group_id`/`cluster_id`/`env_tag`；全仓无 `project_host` 表 |
| 一个项目关联**K8s 资源对象**（具体 Deployment/Pod） | ❌（按 namespace 全量） | 只到 namespace 粒度，无法只授权某个 Deployment |
| 项目级通知路由 | ✅ | `notification_service.py:140-146` 按 `(cluster_id, namespace)` 反查 `ProjectClusterNS` → `Project.notify_config_id` |
| 项目级审批流 | ✅ | `Project.approval_flow_id`（`models/project.py:27`），`approval_service.py:162` 生成 `manager` 节点 |
| 项目级 K8s RBAC 下发 | ✅ | `rbac_service.py:19-40`：项目角色 → `autohealops-read/operate/manager` ClusterRole + group `autohealops:proj:{id}:{role}`，impersonation |
| 项目级资源用量/配额 | ⚠️ 部分 | `resource_governance` 按 `cluster_id + namespace`（`models/resource_governance.py`），未按项目聚合 |

### 2.3 API 清单（15 条，全部在 `api/projects.py`）

| 方法 | 路径 | 依赖 | 备注 |
|---|---|---|---|
| GET | `/projects` | `require_any_user` | admin 全量；其他人**只看自己被授权**的项目（`projects.py:55-63`）✅ |
| POST | `/projects` | `require_admin` | — |
| PUT/DELETE | `/projects/{id}` | `require_admin` | 删除时级联清三张关联表（`109-121`） |
| GET | `/projects/{id}/cluster-ns` | `require_any_user` | **无成员校验**（ISO-05）|
| POST | `/projects/{id}/cluster-ns` | `require_admin` | 触发 `reconcile_cluster` |
| DELETE | `/projects/{id}/cluster-ns/{item_id}` | `require_admin` | 校验 `item.project_id == project_id` ✅ |
| GET | `/projects/{id}/db` | `require_any_user` | **无成员校验**（ISO-05）|
| POST/DELETE | `/projects/{id}/db[/{item_id}]` | `require_admin` | — |
| GET | `/projects/{id}/users` | `require_any_user` | **无成员校验**，泄露成员用户名+角色（ISO-05）|
| POST | `/projects/{id}/users`、`/users/batch` | `require_admin` | 去重后 upsert，触发 `reconcile_project_clusters` |
| DELETE | `/projects/{id}/users/{perm_id}` | `require_admin` | 校验 `p.project_id == project_id` ✅ |
| GET | `/permissions/{user_id}/projects` | `require_admin` | 反向查某人项目 |

---

## 3. 用户 - 项目 - 角色

### 3.1 表结构支持一人多项目不同角色

`user_project_permissions` 的主键是自增 `id`，`(user_id, project_id)` **没有唯一约束**（`models/project.py:63-70`）。应用层在 `add_project_user` 里先查后写（`projects.py:256-267`），批量接口也做了去重（`projects.py:284-311`）→ 实际能保证「一人一项目一条」。**结论：结构上支持一个用户在 N 个项目分别持有 read/operate/manager。**

### 3.2 代码是否真按 `(user, project)` 取角色

| 调用点 | 是否按 (user, project) | 证据 |
|---|---|---|
| `check_cluster_permission` | ✅ 用 `user_id` join `ProjectClusterNS`，但**只在 namespace 精确匹配时才带 namespace 条件** | `user_permission_service.py:72-87` |
| `check_db_permission` | ✅ 同上（join `ProjectDBConnection`） | `:107-119` |
| `get_visible_cluster_ids` | ✅ 直接授权 ∪ 项目授权 | `:24-44` |
| `resolve_project_managers` | ✅ 只取 `permission=="manager"` 且项目关联了该 cluster+ns | `:123-142` |
| `db_approval_service.manager_project_ids` | ✅ | `db_approval_service.py:305-360` |
| `rbac_service` 下发 K8s group | ✅ `autohealops:proj:{project_id}:{role}` | `rbac_service.py:31-38` |
| **项目成员管理接口** | ❌ 全部 `require_admin`，不看 manager | `projects.py:251,276,320` |
| **项目 CRUD/关联接口** | ❌ 全部 `require_admin` | `projects.py:73,91,110,142,201` |

### 3.3 结论

- 「多项目多角色」**数据层已就绪、判定层已落地、管理层缺失**。
- `manager` 目前只有两个真实用途：① 高风险双人审批的第二审批人来源（`resolve_project_managers` → `approval_service.py:162,182,217`）；② DB 审核单的可见范围（`db_approvals.py:211-219`）。
- **`_PERM_WEIGHT` 让「项目 manager」直接等于集群 `operate`**（`user_permission_service.py:21,86`）：项目管理员在其项目 namespace 内可执行一切写操作，这与「manager 只是管成员」的直觉不同，需要产品确认口径（当前是「manager ⊃ operate」，符合注释）。

---

## 4. 项目隔离的落点（逐域判定）

> 判定标准：**列表/详情接口是否按「当前用户可见范围」收敛**。`require_any_user` 只代表「登录即可」，不算隔离。

| # | 资源域 | 代表接口 | 角色依赖 | 是否有范围过滤 | 判定 |
|---|---|---|---|---|---|
| 1 | K8s 资源（Pod/Workload/Service/Ingress/ConfigMap/Secret/PVC/SA/HPA/节点/YAML/日志/终端） | `/resources/clusters/{id}/**`（79 条） | `require_any_user` / `require_operator` | **有** `_deny_cluster(user, cluster_id, namespace, required)`，77/79 条覆盖 | ✅ 已隔离（**但 namespace 为空时退化为集群级 → ISO-02**） |
| 2 | 集群列表/详情 | `GET /clusters`、`GET /clusters/{id}` | `require_any_user` | **有** `get_visible_cluster_ids`（`clusters.py:750-762`）、`check_cluster_permission`（`:787-789`） | ✅ |
| 3 | 告警 | `GET /alerts`、`/alerts/{id}`、`/{id}/diagnosis`、`/describe`、`/comments` | `require_any_user` | **无**：`list_alerts` 的 where 只有用户传入的筛选条件（`alerts.py:80-131`）；`get_alert` 直接 `session.get`（`:226`） | ❌ 全量（**ISO-01**） |
| 4 | 告警处置台 | `/alerts/{id}/candidate-actions`、`/dispatch`、`/actions/{key}/dispatch` | `require_operator` | **无** `check_cluster_permission`（`alert_actions.py:14-45`） | ❌ 可跨项目**写**（**ISO-01 附带**） |
| 5 | 处理记录 | `GET /repair-records` | `require_any_user` | **无**：`select(Alert)` 全表分页（`repair_records.py:41-44`），返回 `plan_steps` + 执行输出 | ❌（**ISO-08**） |
| 6 | 审批 | `GET /approvals`、`/approvals/{id}` | `require_any_user` | **无**：`select(Approval)` 全表（`approvals.py:45-52`），详情带 plan 摘要（`:158-173`） | ❌（**ISO-08**）；`/decide` 校验审批人（✅ `:184-186`） |
| 7 | 执行明细 | `GET /executions/{plan_id}`、`POST /{plan_id}/rollback` | `require_any_user` / `require_operator` | **无**归属校验（`executions.py:33-56,59-83`） | ❌ 读+写（**ISO-03**） |
| 8 | 任务（Task） | `GET /tasks`、`/tasks/{id}`、状态流转 | `require_any_user` / `require_operator` | **无**：`select(Task)` 全表（`tasks.py:21-35`） | ❌（**ISO-08**） |
| 9 | 诊断会话/报告 | `GET /diagnostic-tasks`（✅ 按 created_by）、`GET /diagnostic-reports`（❌ 全量）、`GET /fault-diagnoses`（❌ 全量）、`POST /ai/diagnose` | `require_any_user` | 任务列表按 `created_by` 过滤（`diagnostic.py:53-58`）；**报告列表与故障诊断台账无过滤**（`diagnostic.py:88-100`、`fault_diagnosis.py:102-150`） | ⚠️ 半隔离（**ISO-08**） |
| 10 | 数据库连接/慢 SQL/DDL | `/db-connections/**`（47 条） | 混合 | **44/47 有** `check_db_permission`；`object-ddl`、`sql-tasks`(GET)、`test` 漏（`db_connections.py:543,1442,1614`） | ⚠️ 3 个缺口（**ISO-04**） |
| 11 | DB 审核单 | `GET /db-approvals` | `require_any_user` | **有**：admin 全量 / manager 只看自己项目 / 其他只看自己提交（`db_approvals.py:189-240`） | ✅ **全平台最完整的项目级隔离** |
| 12 | 主机 / VM | `/hosts/**`、`/host-terminal`、`/host-sessions`、`/host-audits` | `require_any_user` / `require_host_admin` | **有**：按 `HostGroup` 成员角色（`hosts.py:99-113`、`host_group_service.host_access:52-58`） | ✅（与「项目」无关，是另一套授权轴） |
| 13 | 工单 | `/tickets/**`（11 条） | `require_any_user` / `require_operator` | **有**：`_scope_conditions` + `_check_visible`（`ticket_service.py:221-253`） | ✅ |
| 14 | 审计 | `GET /audit/search`、`/audit/trace/{id}` | `require_any_user` | **无**：`audit_service.search_audit` 不带 user（`audit.py:17-25`），可全库检索（全平台所有集群的操作与命令） | ❌（**ISO-08**） |
| 15 | Agent 轨迹 | `/agent-traces` | `require_any_user` | **有**：`visible_cluster_ids` 收敛（`trace_service.py:792-812,847-865,903-914`） | ✅ |
| 16 | 仪表盘/大屏统计 | `/stats/dashboard`、`/ai-*`、`/repair-quality`、`/platform-health` | `require_any_user` | **无**：聚合全表；`dashboard` 接受任意 `cluster_id`/`project_id`（`stats.py:1424-1435`） | ❌ 聚合全量（**ISO-11**） |
| 17 | 批量操作 | `/batch`、`/batch/{id}`、`/batch/{id}/execute` | `require_operator` / `require_any_user` | **无**：`cluster_ids` 由请求体任意指定，`execute_batch` 不校验（`batch.py:14-31`） | ❌ 可跨项目批量执行（**ISO-01 附带**） |
| 18 | 巡检报告 | `/clusters/{id}/inspection-reports`、`/inspection-reports/{id}/download` | `require_any_user` | **有**（`inspections.py:31-53` 的 `_can_see`） | ✅ |
| 19 | 发布（Jenkins） | `/release/**`（20 条） | 混合 | 写操作有 `check_cluster_permission(operate)`（`release.py:480,564,618`）；列表类未见过滤（`release_mappings` 有 `cluster_id`，列表未收敛） | ⚠️ 部分 |
| 20 | 日志源 / 日志告警 / 知识库 / 告警策略 / 静默 / 值班 | `/log-sources`、`/log-alert-rules`、`/knowledge`、`/alert-policies`、`/alert-silences`、`/on-duty` | `require_admin` 或 `require_any_user` | **全局对象，无项目归属** | ❌ 无法隔离（模型缺失，**ISO-09**） |
| 21 | AI 代理 | `ANY /api/v1/ai/**` | 仅登录 | **无**（见 AUTH-03）；其中 `/ai/logs/search` 按 namespace 查 ES 无集群概念 | ❌（**ISO-06**） |

**统计口径**：有范围过滤的域 **9 个**（K8s 资源、集群、工单、DB 审核单、主机、巡检、Agent 轨迹、DB 连接〔3 缺口〕、告警处置〔部分〕）；无过滤的域 **8 个**（告警读、处理记录、审批、执行、任务、审计、批量、统计）；模型不支持过滤的 **1 类**（全局配置对象）。

---

## 5. 重点漏洞验证（静态推演 + 调用链）

### 5.1 ISO-01：告警域完全无范围过滤（读 + 写）

**读**：
```
攻击者条件：任意有效账号（viewer 即可），无需任何集群授权。
请求：GET /api/v1/alerts?status=all&page_size=1000
代码路径：
  api/alerts.py:61  list_alerts(..., user=Depends(require_any_user))
  api/alerts.py:80-131  conditions 只由 query 参数构造（cluster_id/severity/status/fault_type/env_tag/manual_required）
                        —— 没有任何 `user` 相关条件，也没有 get_visible_cluster_ids
  api/alerts.py:199-210  items 里带 cluster_name / namespace / fault_type / message / resolved_by / assigned_to
返回：全平台所有集群的告警（含故障描述、命名空间、资源名、指派与处理人）。
```
同一模式还有：`GET /alerts/{id}`（`:226`）、`/{id}/diagnosis`（`:570`，返回 AI 根因与修复建议全文）、`/{id}/describe`（`:641`）、`/{id}/comments`（`:502`）、`GET /repair-records`（`repair_records.py:38`，带 `plan_steps` 命令与 `execution_steps` 输出）。

**写**（更严重）：
```
攻击者条件：operator 角色（默认 seed 会创建 operator 账号）+ 任意告警 id。
请求：POST /api/v1/alerts/123/actions/restart_deployment/dispatch
代码路径：
  api/alert_actions.py:36  dispatch_specific_action(..., user=Depends(require_operator))
  api/alert_actions.py:38-45 直接 alert_action_service.dispatch_action(alert_id, action_key, operator)
                              —— 没有 check_cluster_permission（本文件连 import 都没有）
  services/alert_action_service.py → 触发诊断/执行（auto 直执，approve 建单）
为什么没拦住：告警对象本身不带任何「谁能操作它」的判定，路由层只判了角色，没判资源归属。
影响：跨项目执行变更（重启/扩容/删除）——这是隔离审计里最严重的一条。
```
同类无归属写：`POST /batch`（`cluster_ids` 由请求体任意指定，`batch.py:14-25`）、`POST /batch/{id}/execute`（`:27-31`）。

### 5.2 ISO-02：只授权一个 namespace，却可读整个集群的其它 namespace

```python
# services/user_permission_service.py:56-70
required_w = _PERM_WEIGHT.get(required, 1)
perms = [UserClusterPermission where user_id & cluster_id]
for p in perms:
    if namespace and p.namespace and p.namespace != namespace:   # ← namespace 为空时此判断整体失效
        continue
    if _PERM_WEIGHT.get(p.permission, 0) >= required_w: return True
...
if namespace:                                                    # ← 项目授权同理
    query = query.where(ProjectClusterNS.namespace == namespace)
```

**调用链**：
```
攻击者条件：被授权 cluster 1 的 namespace "dev"（read 或 operate），无其它授权。
请求 1：GET /api/v1/resources/clusters/1/misc-resources            （namespace 缺省 = ""）
  api/resources.py:706  misc_resources(cluster_id, namespace: str = "", ...)
  api/resources.py:708  _deny_cluster(user, cluster_id, "")         → check_cluster_permission(..."")
  user_permission_service.py:67  namespace 为空 → 不跳过任何条目 → 命中 dev 的授权 → True
  api/resources.py:710  executor_client.list_misc_resources(cluster_id, "")
  → 返回 **全部 namespace** 的 services / ingresses / configmaps / **secret 名称** / pvcs

请求 2：GET /api/v1/resources/clusters/1/pods                       （namespace 缺省 = ""）
  api/resources.py:605-611 同理 → 全部 namespace 的 Pod（含镜像、节点、重启次数）
请求 3：GET /api/v1/resources/clusters/1/workloads?namespace=        （同理，`:1247`）
请求 4：GET /api/v1/resources/clusters/1/workload-events?namespace=   （同理，`:269`）
```
**为什么没拦住**：把「namespace 是否为空」当成「要不要做 namespace 过滤」的开关，而不是「用户有没有全 namespace 权限」的判断。正确语义应是：`namespace==""` 时要求该用户**拥有集群级授权**（存在 `user_cluster_permissions.namespace == ""` 的条目，或项目里覆盖了该集群全部 ns）。

**影响**：namespace 是这套系统事实上的租户边界（项目按 ns 划分）。该缺口把「ns 级隔离」降级为「cluster 级隔离」——同一集群里 A 项目的用户可以列出 B 项目的 Secret 名称、ConfigMap 名称、Service/Ingress 域名（进而做内网探测）。

### 5.3 ISO-03：执行明细与回滚无归属校验

```python
# api/executions.py:33-56
@router.get("/{plan_id}")
async def get_execution(plan_id: int, since_step: int = 0, user=Depends(require_any_user)):
    logs = [ExecutionLog where plan_id]        # ← 只按 plan_id 查，无任何 user 条件
    plan = session.get(RepairPlan, plan_id)
    return {"steps":[{"command","output"}...], ...}

# api/executions.py:59-83
@router.post("/{plan_id}/rollback")
async def rollback_execution(plan_id: int, user=Depends(require_operator)):
    plan = session.get(RepairPlan, plan_id)
    alert = session.get(Alert, plan.alert_id)
    cluster_id = alert.cluster_id              # ← 拿到集群 id 后**没有** check_cluster_permission
    executor_client.rollback_plan(cluster_id, plan_id, plan_json, ...)
```
**攻击链**：operator 遍历 `plan_id`（自增，可枚举）→ `GET /executions/{id}` 读到**其它项目**的修复命令与执行输出（可能含 `kubectl exec` 命令、密码参数、内网地址）→ 对已完成的计划 `POST /{id}/rollback` 触发**在别人集群里执行回滚命令**（`rollback_command` 来自 plan JSON，由 AI 生成，通常等价于反向变更）。
**为什么没拦住**：`RepairPlan` 只有 `alert_id`，路径里没有 cluster_id，开发时只按「计划是否存在 + 状态是否可回滚」做判断，遗漏了归属。修复方式：`alert = ...; await check_cluster_permission(user, alert.cluster_id, alert.namespace, "operate")`。

### 5.4 ISO-04：数据库连接 3 个端点漏 `check_db_permission`

| 端点 | 位置 | 泄露/影响 |
|---|---|---|
| `GET /db-connections/{conn_id}/object-ddl?kind=table&name=x` | `db_connections.py:543-560` | 返回 `SHOW CREATE TABLE` → **完整表结构**（字段、索引、注释），可推断业务与敏感字段；未授权 conn_id 直接可读 |
| `GET /db-connections/{conn_id}/sql-tasks` | `:1442-1450` | 返回 `_task_dict(t)`：**SQL 全文**、cron、**收件邮箱**（`sql_task_service` 字段）→ 可拿到「谁在定时跑什么 SQL、发给谁」 |
| `POST /db-connections/{conn_id}/test` | `:1614-1624` | 用平台保存的凭据做连通性测试 → 未授权用户可探测任意纳管库的**可达性/错误信息**（凭据爆破的旁路探测） |

对比：同文件 `/{conn_id}/query`（`:344-361`）、`/anomalies`（`:1627`）、`/slow-sql`（`:1640`）都写了 `if user.role != "admin" and not await check_db_permission(...)`。**说明不是设计豁免，是遗漏。**

### 5.5 ISO-05：项目详情是「登录即可枚举」

```
GET /api/v1/projects/{id}/users     → [{id, user_id, username, permission}]   ← 全平台人员名单 + 项目角色
GET /api/v1/projects/{id}/cluster-ns→ [{cluster_id, cluster_name, namespace}] ← 全平台集群拓扑（还额外 select(Cluster) 全表取名字）
GET /api/v1/projects/{id}/db        → [{db_connection_id, db_name}]           ← 全平台数据库清单
```
三处依赖都是 `require_any_user`（`projects.py:127,186,235`），**没有「我是该项目成员」判定**。
**攻击者条件**：任意登录账号；**请求**：`GET /api/v1/projects/1/users` 遍历 id（1..N）。
**影响**：组织架构与系统拓扑泄露（谁负责哪个项目、哪个项目跑在哪个集群/库），为后续社工与横向移动提供地图。虽不含凭据，但属典型的 IDOR（越权对象引用）。

### 5.6 ISO-06：AI 代理跨租户读日志

```
请求：POST /api/v1/ai/logs/search   {"namespace":"prod-payment","pod":"","tail":200}
代码：api_proxy.py:27-28（仅登录）→ ai-service aux_api.py:421-428
      es.search_logs(namespace, pod, tail)   ← 只有 namespace/pod，没有 cluster_id，也没有用户上下文
```
ES 里日志按 namespace 索引（`es_client.py`），**不同集群的同名 namespace 会混在一起**；且完全不做权限判定。
→ 一个项目的 viewer 可以拉取任意 namespace 的日志（含生产库连接串、报错堆栈）。

### 5.7 ISO-07：YAML 写入的 namespace 判定可绕过

```python
# api/resources.py:754-761
ns = ""
for line in yaml_content.splitlines():
    if line.strip().startswith("namespace:"):
        ns = line.split(":", 1)[1].strip().strip('"\'')
        break
if denied := await _deny_cluster(user, cluster_id, ns, required="operate"):
    return denied
r = executor_client.apply_workload_yaml(cluster_id, kind, ns, "", yaml_content)
```
**绕过方式**（任一即可）：
```yaml
# ① flow style
apiVersion: v1
kind: ConfigMap
metadata: {name: evil, namespace: prod}
# ② JSON 正文（YAML 超集，payload.yaml 传 JSON 字符串）
{"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":"evil","namespace":"prod"},"data":{}}
# ③ 注释/缩进变体（"  namespace : prod" → strip 后 "namespace : prod" 不以 "namespace:" 开头）
```
三种情况下 `ns=""` → `_deny_cluster(..., "", "operate")` 在有**任意** namespace operate 授权时通过（见 ISO-02 的语义缺陷）→ executor 按 YAML 内真实 namespace 落盘 → **跨 namespace 写**。
`POST /objects/{kind}/apply`（`:912-936`）用 `metadata.namespace` 正确取值，**没有**该问题 —— 说明修法明确：把 `apply_object_yaml` 改成先 `yaml.safe_load` 再取 `metadata.namespace`。

---

## 6. 前端是否把 `project_id` 当可信输入

| 位置 | 用法 | 后端是否信任 |
|---|---|---|
| `ProjectManagement.tsx:134-136` | 项目详情三个 Tab 都用 URL 里的 `projectId` | 后端只查不判成员（ISO-05）→ **信任** |
| `ApprovalList.tsx:219` | `listDbApprovals({project_id: projectFilter})` | 后端 `db_approvals.py:230-240` 会**再按 manager 项目过滤**（先 scoped 再过滤）→ **不信任（好）** |
| `stats.dashboard(cluster_id| project_id)` | 前端 Dashboard 目前**没有**传 project_id（未实装项目筛选） | 后端 `stats.py:1424-1435` 直接收下并参与聚合，`_user` 参数未使用 → 手工构造请求可看任意项目/集群聚合（ISO-11） |
| 创建类接口 | 平台内**没有**「创建资源时带 project_id」的接口；创建时只带 `cluster_id`/`db_connection_id`，由后端按 cluster/ns 反查项目 | 不存在「传别人 project_id 绑定成功」的路径（**该问的答案是：没有这种接口，因此也无此漏洞**） |

结论：**除 §5.5 的项目详情三接口外，后端整体上不把前端传的 project_id 当授权依据**（因为业务表根本没有 project_id 列）；真正的风险是「有些接口连 cluster/ns 都不判」。

---

## 7. 隔离缺口清单（模型层）

### 7.1 统计

| 分类 | 数量 | 说明 |
|---|---:|---|
| ORM 模型类（`class X(Base)`） | **87** | `api-server/app/models/*.py` |
| 含 `project_id` | **3** | `ProjectClusterNS`、`ProjectDBConnection`、`UserProjectPermission`（全是关联表） |
| 含 `cluster_id` | **26** | Alert、Ticket、Host、DBConnection、DiagnosisSession、CustomTask、ReleaseMapping/Build、PodMetric、K8sEvent、ResourceSnapshot、QuotaSnapshot、ResourceAlertRule、ResourceGovernance、SelfHealRule、LogAlertRule、MonitorSource、UserClusterPermission、AlertSubscription、AlertInhibition、BatchOperationItem、DiagnosticTask/Report、DiagnosisFailure、DBConnection… |
| 两者都没有 | **58** | 其中 **28 张可经父表 join 推导**（有 `alert_id`/`plan_id`/`ticket_id`/`task_id`/`conn_id`/`host_id`/`session_id`） |

### 7.2 真正「无归属锚点」的租户业务对象（无法做行级隔离）

| 表/模型 | 位置 | 为什么是缺口 |
|---|---|---|
| `alert_policies` / `alert_silences` | `models/alert_policy.py`、`alert_silence.py` | **全局告警策略与静默**：一个项目的管理员静默某 fault_type，会静默所有项目 |
| `log_sources` / `log_alert_rules` | `models/log_source.py` | ES 日志源含**凭据**，全局共享；无项目隔离 |
| `knowledge_entries` | `models/knowledge.py` | 知识库全局；A 项目的故障处理经验会被 B 项目的诊断直接复用（可能是期望，但无开关） |
| `host_groups` / `hosts` | `models/host.py` | 主机授权走「组」这条平行轴，**与项目无关**；无法表达「这台 VM 属于项目 X」 |
| `db_groups` | `models/db_connection.py` | DB 分组是展示维度，不是授权维度 |
| `approval_flows` | `models/approval_flow.py` | 审批流模板全局，项目只引用 id（`Project.approval_flow_id`） |
| `report_schedules` | `models/report_schedule.py` | 报表订阅全局，无项目/集群范围 |
| `audit_records` | `models/audit.py` | 审计记录有 `cluster_id`（部分），但**无 project_id**，检索接口也不过滤 |
| `in_app_messages` / `notification_*` | `models/notification.py` | 站内信无租户概念（按 user 投递，可接受） |
| `jenkins_instances` / `user_jenkins_tokens` | `models/release.py` | Jenkins 实例全局；发布映射有 cluster_id 但实例无归属 |

### 7.3 子表可推导但容易漏（读接口若只按子表查就会越权）

`alert_comments`(alert_id)、`execution_logs`(plan_id)、`repair_plans`(alert_id)、`repair_verifications`(plan_id)、`task_audit_logs`(task_id)、`ticket_comments`(ticket_id)、`ai_diagnosis_results`(alert_id)、`ai_feedback`(alert_id)、`decision_scores`(alert_id,plan_id)、`host_session*`(host_id)、`slow_sql_*`(db_connection_id)、`db_metric_snapshots`(db_connection_id)。
**实测漏的例子**：`GET /executions/{plan_id}`（§5.3）、`GET /alerts/{id}/comments`（`alerts.py:502`）、`GET /tickets/{id}/comments`（✅ 走了 `_check_visible`）。

---

## 8. 与未来目标的差距

| 目标 | 当前状态 | 差距（逐条） | 工作量 |
|---|---|---|---|
| **LDAP 统一身份** | 已实现但无限流、无组同步 | ① LDAP 组 → 平台项目角色**无映射**（只有 `ldap_default_role` 一个全局默认），无法「LDAP 组=项目成员」；② 无 LDAP 用户定期同步/离职禁用（`is_active` 只能手改）；③ 每次登录现建用户（`auth.py:137-147`），无属性同步 | 中 |
| **一人多项目多角色** | 表结构与判定已就绪（§3） | 只差「管理入口」（见下一行）；另需给 `user_project_permissions` 加 `(user_id, project_id)` 唯一索引，防并发重复 | 小 |
| **项目管理员管成员** | ❌ 全部 `require_admin` | 需要新增依赖 `require_project_manager(project_id)`（读路径参数或 body 的 project_id → 查 `UserProjectPermission.permission=="manager"`，允许 admin 直通），并把 `projects.py` 的 members/cluster-ns/db 写接口从 `require_admin` 换成它；同时**限制 manager 不能给自己提权**（不能改自己的 permission、不能加 admin） | 中 |
| **按项目过滤的列表** | 部分（工单/DB审核单/巡检/轨迹 有；告警/审批/执行/任务/审计 没有） | 三条路可选：① 加 `project_id` 列（需要 123 个迁移文件之后的新迁移 + 回填 + 写入侧改造，成本高但彻底）；② 不加列，统一在查询处 join `ProjectClusterNS`（现状做法，需逐接口补 where，容易漏，本报告已列出 8 个域）；③ 折中：给 `alerts`/`tickets`/`approvals` 增加 `project_id`（这三个是要求最强烈的），其余 join | 中大 |
| **项目级告警策略/静默** | ❌ 全局对象 | 需给 `alert_policies`/`alert_silences`/`log_alert_rules` 加 `project_id`（或 `cluster_id+namespace`），并改造匹配逻辑（`alert_service` 的 policy 查找） | 中 |
| **项目级主机/VM** | ❌ 无关联 | 需新增 `project_hosts` 表或在 `hosts` 加 `project_id`；但要与现有 `host_groups` 授权轴对齐（两套授权会打架，需产品决策哪个优先） | 中 |
| **项目级审计可见性** | ❌ 全量 | `audit_records` 需加 `project_id`/`cluster_id`（现部分有 cluster_id），`/audit/search` 需注入 visible 过滤 | 小 |
| **项目级 K8s RBAC** | ✅ 已实现（`rbac_service` 下发 `autohealops:proj:{id}:{role}`） | 差距：`reconcile` 是 `asyncio.create_task` 旁路（`projects.py:164,271,315`），失败只进日志，无重试/无状态回显 → 授权变更可能不同步 | 小 |

---

## 9. 问题清单

```
问题编号：ISO-01
问题名称：告警域（读 + 处置写）完全无项目/集群范围过滤
问题等级：P1
所属模块：告警 / 项目隔离
问题描述：告警列表、详情、诊断结果、描述、评论、处理记录六类读接口全部 require_any_user 且无任何
          user 相关 where 条件，任意登录账号（含 viewer）可枚举全平台所有集群的告警；
          更严重的是告警处置台（alert_actions.py）与批量操作（batch.py）在 require_operator 之后
          直接按 alert_id/cluster_ids 执行变更，没有 check_cluster_permission，
          任意 operator 可对**不属于自己项目**的集群发起重启/扩容/回滚等写操作。
代码位置：api-server/app/api/alerts.py:61（list_alerts）、80-131（conditions 无 user 条件）、199-210（返回 cluster_name 等）
          api-server/app/api/alerts.py:226（get_alert）、502（comments）、570（diagnosis）、641（describe）
          api-server/app/api/alert_actions.py:14-45（三个处置端点，全文件无 check_cluster_permission import）
          api-server/app/api/repair_records.py:37-44（select(Alert) 全表）
          api-server/app/api/batch.py:14-31（cluster_ids 由 body 指定）
涉及函数或类：list_alerts / get_alert / dispatch_action / dispatch_specific_action / list_records / create_batch / execute_batch
调用链（写）：
  ① operator 登录 → GET /api/v1/alerts?status=all 拿到全平台 alert_id 与 cluster_id
  ② POST /api/v1/alerts/{alert_id}/actions/{action_key}/dispatch
  ③ alert_action_service.dispatch_action → 决策/审批/执行（对目标集群真实变更）
调用链（读）：
  GET /api/v1/alerts?status=all&page_size=1000 → 全平台告警（含 message 里的异常堆栈、namespace、服务名）
根本原因：告警对象在设计上是「平台级事件」，从未引入归属维度；路由层只做角色判定，资源归属判定缺失。
可能影响：跨项目数据泄露（核心业务数据）+ 跨项目变更（P1 最高风险：一个项目的 operator 能重启另一个项目的生产服务）。
修改建议：1) 列表处注入范围：非 admin 加 `Alert.cluster_id.in_(await get_visible_cluster_ids(user))`
          （`trace_service.py:792-812` 已有现成写法可抄）；
          2) 单条处统一加 `_check_alert_visible(user, alert)`（按 cluster+namespace 调 check_cluster_permission）；
          3) alert_actions / batch 的写操作必须 `check_cluster_permission(cluster_id, ns, "operate")`；
          4) 补回归测试：operator 访问未授权集群的 alert → 403。
验证方法：用仅授权 cluster A 的账号请求 GET /alerts?cluster_id=B，当前返回 B 的告警；POST 处置返回 200。
是否需要立即处理：是
```

```
问题编号：ISO-02
问题名称：namespace 级授权在 namespace 参数为空时失效，导致跨 namespace 读
问题等级：P1
所属模块：授权判定 / K8s 资源
问题描述：check_cluster_permission(user, cluster_id, namespace="", ...) 把「namespace 为空」解释为
          「不做 namespace 过滤」（user_permission_service.py:67 与 :81 的 `if namespace:` 分支），
          于是只要用户在该集群有**任意一个** namespace 的授权，集群级判断即通过。
          而 resources.py 中有 5+ 个端点把 namespace 定义为可选参数（缺省 ""，语义=全部 namespace），
          并在调用 _deny_cluster 时原样传入空串 → 授权退化为「集群级」。
代码位置：api-server/app/services/user_permission_service.py:47-70（第 67、81 行是关键分支）
          api-server/app/api/resources.py:31-34（_deny_cluster）
          api-server/app/api/resources.py:605-611（cluster_pods，namespace 可选）
          api-server/app/api/resources.py:706-720（misc_resources：services/ingresses/configmaps/secrets/pvcs）
          api-server/app/api/resources.py:269-272（workload_events）、1247-1250（list_workloads）
涉及函数或类：check_cluster_permission / _deny_cluster / cluster_pods / misc_resources / list_workloads
调用链：
  授权：user_cluster_permissions(user=u, cluster=1, namespace="dev", permission="read")
  请求：GET /api/v1/resources/clusters/1/misc-resources
  → _deny_cluster(u, 1, "") → check_cluster_permission(..., namespace="")
  → 第 67 行 `if namespace and ...` 短路 → 命中 dev 授权 → True
  → executor.list_misc_resources(1, "") → 全部 namespace 的 ConfigMap/Secret 名称/PVC/Ingress
根本原因：把「查询参数为空」与「权限覆盖全集群」混为一谈；缺一个「该用户是否有集群级授权」的判定函数。
可能影响：namespace 是项目的事实边界，该缺口使 A 项目用户可枚举同集群 B 项目的 Secret/ConfigMap 名称、
          Service/Ingress 域名、PVC 名称、全部 Pod 与镜像（情报收集，为进一步攻击铺路）。
修改建议：新增 `has_cluster_wide_permission(user, cluster_id, required)`：要求存在
          `user_cluster_permissions.namespace == ""` 的条目，或项目授权覆盖了该集群全部 ns；
          `_deny_cluster` 在 namespace=="" 时调用它而不是 check_cluster_permission。
验证方法：用只授权 ns=dev 的账号请求 misc-resources（不带 namespace 参数），当前返回全部命名空间数据。
是否需要立即处理：是
```

```
问题编号：ISO-03
问题名称：执行明细/回滚接口无归属校验（读他人执行输出 + 在他人集群回滚）
问题等级：P1
所属模块：执行 / 项目隔离
问题描述：GET /api/v1/executions/{plan_id} 与 POST /{plan_id}/rollback 都只按 plan_id 查库，
          前者无任何权限判定（require_any_user），后者只判 operator 角色，拿到 alert 后
          没有对 alert.cluster_id 做 check_cluster_permission，直接调 executor.RollbackPlan。
代码位置：api-server/app/api/executions.py:33-56（读）、59-83（写，第 78 行取 cluster_id 后未校验）
涉及函数或类：get_execution / rollback_execution
调用链（写）：
  operator → POST /api/v1/executions/1234/rollback
  → plan = RepairPlan(1234); alert = Alert(plan.alert_id); cluster_id = alert.cluster_id
  → executor_client.rollback_plan(cluster_id, plan_id, plan_json)   ← 跨集群真实执行
调用链（读）：
  viewer → GET /api/v1/executions/1234 → steps[].command + output（含 kubectl 命令、可能的密码/内网地址）
根本原因：路径里没有 cluster_id，开发时只校验「计划存在 + 状态可回滚」，遗漏归属。
可能影响：跨项目执行变更（P1）；修复命令与输出泄露（其中可能出现数据库连接串、token）。
修改建议：两处都加 `await check_cluster_permission(user, alert.cluster_id, alert.namespace, required)`；
          列表接口（若有）按 visible_cluster_ids 过滤。
验证方法：operator 对未授权集群的 plan 调 rollback，当前返回 200 并开始执行。
是否需要立即处理：是
```

```
问题编号：ISO-04
问题名称：数据库连接 3 个端点漏 check_db_permission（DDL / 定时 SQL / 连通性探测）
问题等级：P1
所属模块：数据库 / 项目隔离
问题描述：db_connections.py 的 47 条路由中 44 条有 check_db_permission，遗漏三条：
          ① GET /{conn_id}/object-ddl（返回 SHOW CREATE 全文）；
          ② GET /{conn_id}/sql-tasks（返回 SQL 全文 + cron + 收件邮箱）；
          ③ POST /{conn_id}/test（用平台凭据探测连通性并回显错误）。
          三者都是 require_any_user，任意登录用户可对任意 conn_id 调用。
代码位置：api-server/app/api/db_connections.py:543-560（object-ddl）
          api-server/app/api/db_connections.py:1442-1450（sql-tasks 列表）
          api-server/app/api/db_connections.py:1614-1624（test）
          对照正确写法：:344-361（query）、:1627（anomalies）、:1640（slow-sql）
涉及函数或类：get_object_ddl / list_sql_tasks / test_connection
调用链：viewer → GET /api/v1/db-connections/7/object-ddl?kind=table&name=orders
        → session.get(DBConnection, 7)（不判权限）→ agent_gateway/直连执行 SHOW CREATE TABLE → 返回表结构
根本原因：三个端点是不同时期补的，未走统一的 _deny_db 辅助（该文件有 29 处 check_db_permission 但无统一装饰器）。
可能影响：数据库结构泄露（业务模型推断）、定时任务 SQL 与收件人泄露（社会工程）、纳管库可达性探测。
修改建议：抽 `async def _deny_db(user, conn_id, required="read")` 并在这三处调用；或写成 APIRouter 级依赖。
验证方法：用无该库授权的账号请求 object-ddl，当前返回 200 + DDL。
是否需要立即处理：是
```

```
问题编号：ISO-05
问题名称：项目详情三接口缺少成员校验（IDOR：枚举任意项目的成员/集群/库）
问题等级：P1
所属模块：项目管理
问题描述：GET /projects/{id}/users、/cluster-ns、/db 只 require_any_user，不判断「我是否该项目成员」，
         也不判断 admin。返回内容含成员用户名+项目角色、集群名+namespace、数据库实例名，
          且 cluster-ns/db 两个接口还会 select(Cluster)/select(DBConnection) 全表取名字。
代码位置：api-server/app/api/projects.py:126-138（cluster-ns，:133 全表取集群名）
          api-server/app/api/projects.py:185-197（db，:192 全表取库名）
          api-server/app/api/projects.py:234-247（users，:241 全表取用户）
          （对照：GET /projects 列表本身是收敛的，:55-63 —— 说明作者知道要收敛，只是详情忘了）
涉及函数或类：list_project_cluster_ns / list_project_db / list_project_users
调用链：任意登录账号 → for id in 1..N: GET /api/v1/projects/{id}/users → 全平台「人-项目-角色」矩阵
根本原因：详情接口默认「能进项目页就是成员」，但后端没有这一前提。
可能影响：组织架构/系统拓扑泄露（为社工与横向移动提供地图）；不含凭据，定 P1 偏保守可降 P2。
修改建议：加 `_check_project_member(user, project_id)`（admin 直通；否则要求存在
          UserProjectPermission(user_id, project_id)）；users 列表对非 manager 只返回自己一条。
验证方法：用无任何项目授权的 viewer 请求 /projects/1/users，当前返回完整成员列表。
是否需要立即处理：是
```

```
问题编号：ISO-06
问题名称：AI 代理 /ai/logs/search 无集群/项目维度，跨租户读 ES 日志
问题等级：P1
所属模块：日志 / AI 代理
问题描述：api_proxy 把 /api/v1/ai/** 全量转发（只校验登录），ai-service 的 /logs/search
          只接收 namespace/pod/tail，直接调 ESClient.search_logs，没有 cluster_id、没有用户上下文。
          不同集群的同名 namespace 在 ES 里混在一起，故即使传对 namespace 也会跨集群返回。
代码位置：api-server/app/api/ai_proxy.py:27-28
          ai-service/app/api/aux_api.py:421-428
          ai-service/app/services/es_client.py（search_logs 实现）
涉及函数或类：ai_proxy / search_logs
调用链：viewer → POST /api/v1/ai/logs/search {"namespace":"prod","tail":500} → 代理注入内部令牌
        → ai-service search_logs → ES 查询 → 返回其它项目/集群日志
根本原因：日志查询被下沉到 ai-service（只有服务身份，没有用户身份），边界处未做资源授权。
可能影响：日志中的连接串、密码、内部地址、业务数据泄露（横向移动的关键情报）。
修改建议：① 该端点移回 api-server（用现有 `logs.py` 的 check_cluster_permission 逻辑）；
          ② 或强制要求 cluster_id 并在 api-server 侧校验后再转发（同时把 cluster 传给 ES 查询条件）；
          ③ 更彻底：ai_proxy 建立路径→(角色, 资源参数) 的映射表（与 AUTH-03 一并修）。
验证方法：无集群授权的账号 POST /api/v1/ai/logs/search，当前返回日志。
是否需要立即处理：是
```

```
问题编号：ISO-07
问题名称：配置类对象 YAML 写入的 namespace 用文本扫描提取，flow-style/JSON 可绕过授权
问题等级：P2
所属模块：K8s 资源写操作
问题描述：apply_object_yaml 逐行找以 "namespace:" 开头的行来取 ns，取不到则为 ""，
          然后用空 namespace 调 _deny_cluster（在 ISO-02 的语义缺陷下 = 集群级放行），
          再把原始 yaml 交给 executor 按 YAML 内的真实 namespace 落盘。
代码位置：api-server/app/api/resources.py:744-768（:754-760 是扫描逻辑）
涉及函数或类：apply_object_yaml
调用链：operator（仅有 ns=dev 的 operate）→ PUT /api/v1/resources/clusters/1/objects/configmap/yaml
        body: {"yaml": "{\"apiVersion\":\"v1\",\"kind\":\"ConfigMap\",\"metadata\":{\"name\":\"x\",\"namespace\":\"prod\"},\"data\":{}}"}
        → ns="" → 授权通过 → apply_workload_yaml(cluster, "configmap", "", "", yaml) → 落到 prod/x
根本原因：用正则/文本启发式解析结构化数据（YAML），而不是解析器。
可能影响：跨 namespace 写入（篡改生产 ConfigMap/Secret，等价于 RCE 的常见前置）。
修改建议：`doc = yaml.safe_load(yaml_content); ns = (doc.get("metadata") or {}).get("namespace","")`，
          与 `object_apply`（:912-936）保持一致；同时按 ISO-02 修复空 namespace 的授权语义。
验证方法：用 flow-style YAML 对未授权 namespace 写入，当前成功。
是否需要立即处理：是（成本极低）
```

```
问题编号：ISO-08
问题名称：审批/任务/审计/诊断报告/处理记录等 6 类列表接口无范围过滤
问题等级：P2
所属模块：项目隔离（读）
问题描述：以下接口 require_any_user + 全表查询，无 visible_cluster_ids / 成员判断：
          GET /approvals（+ /approvals/{id} 带 plan 摘要）、GET /tasks（+ 详情时间线）、
          GET /audit/search（全平台操作与命令记录）、GET /diagnostic-reports（含 raw_output 前 2000 字）、
          GET /fault-diagnoses（故障台账 + AI 诊断结论）、GET /repair-records（含执行步骤与输出）。
代码位置：api-server/app/api/approvals.py:32,45-52,158-173
          api-server/app/api/tasks.py:21-35,39-56
          api-server/app/api/audit.py:17-25
          api-server/app/api/diagnostic.py:88-100
          api-server/app/api/fault_diagnosis.py:102-150
          api-server/app/api/repair_records.py:37-44
涉及函数或类：list_approvals / get_approval / list_tasks / search / list_reports / list_fault_diagnoses / list_records
调用链：viewer → GET /api/v1/audit/search?page_size=200 → 全平台所有集群的 kubectl 命令与执行结果
根本原因：与 ISO-01 同源（缺统一的「可见范围」注入）。
可能影响：审计/审批/诊断信息跨项目泄露；审计数据尤其敏感（含命令原文）。
修改建议：把 `get_visible_cluster_ids` 收敛抽成 `scope_condition(model, user)` 公共函数，
          在这些列表处统一注入（有 cluster_id 的直接 in_；无的经 join 父表）。
验证方法：无授权账号访问上述接口，均返回全量。
是否需要立即处理：否（P2，但审计检索建议优先，信息量最大）
```

```
问题编号：ISO-09
问题名称：数据模型层缺 project_id，全局对象（告警策略/静默/日志源/知识库/主机组）无法归属项目
问题等级：P2
所属模块：数据模型 / 隔离能力
问题描述：87 个 ORM 模型里只有 3 张（全是关联表）有 project_id；业务表最多只有 cluster_id。
          告警策略、告警静默、日志源（含凭据）、知识库、主机/主机组、DB 分组、审批流、报表订阅
          都是全局对象，无法表达「属于某个项目」。
代码位置：api-server/app/models/（全目录）
          api-server/app/models/project.py:32-70（只有三张关联表带 project_id）
          api-server/app/models/host.py:18-50（Host 无 project_id）
          models/alert_policy.py、models/alert_silence.py、models/log_source.py、models/knowledge.py
涉及函数或类：全部 ORM 模型
调用链：不适用（这是模型能力缺口，不是单点漏洞）
根本原因：项目从「授权代理」起步，未做「数据分区」设计。
可能影响：无法实现真正的项目级配置隔离（例：A 项目静默告警会影响 B 项目）；
          按项目统计/报表只能靠 join 反查，易漏。
修改建议：分阶段：① 先给 alerts/tickets/approvals 加 project_id（写入时按 cluster+ns 反查填充，保留可空兼容历史）；
          ② 再给 alert_policies/alert_silences/log_alert_rules 加 project_id（NULL=全局）；
          ③ 主机引入 project_hosts；④ 最后统一列表查询走 project_id。
          每步都要配数据回填脚本 + 双写过渡期。
验证方法：`grep -c project_id app/models/*.py` → 仅 3 个文件命中。
是否需要立即处理：否（架构级改造，需单独立项）
```

```
问题编号：ISO-10
问题名称：项目管理员（manager）无任何管理入口，成员/关联全靠平台 admin
问题等级：P2
所属模块：项目权限管理
问题描述：project manager 在数据层已存在（read/operate/manager），但 projects.py 中
          members / cluster-ns / db 的写接口全部 require_admin，manager 无法加人、无法关联环境；
          现有 manager 能力只剩「审批第二人」和「DB 审核单可见范围」。
代码位置：api-server/app/api/projects.py:142,201,251,276,320（全部 require_admin）
          api-server/app/services/user_permission_service.py:123-142（resolve_project_managers）
          api-server/app/services/db_approval_service.py:305-360
涉及函数或类：add_project_cluster_ns / add_project_db / add_project_user / add_project_users_batch / delete_project_user
调用原因：成员管理接口从「平台用户管理」直接复制，未做项目维度的权限下沉。
可能影响：与「项目管理员管成员」目标相反；平台 admin 成为瓶颈；无法规模化。
修改建议：新增 `require_project_manager(project_id)` 依赖（admin 直通；否则查 UserProjectPermission.permission=="manager"），
          替换上述 5 处；并加约束：manager 不能修改自己的 permission、不能授予 manager/admin 给他人（防提权链）。
验证方法：以 manager 身份 POST /projects/{id}/users，当前 403。
是否需要立即处理：否
```

```
问题编号：ISO-11
问题名称：仪表盘聚合接口接受任意 cluster_id/project_id，不校验成员关系
问题等级：P3
所属模块：统计 / 项目隔离
问题描述：GET /stats/dashboard 接受 cluster_id、project_id、env_tag 作为筛选（stats.py:1424-1435），
          但 dashboard_cached/dashboard_data 都不接收 user（`_user` 参数声明了但未使用），
          且 core 区块（cluster_total、alert_trend 等）本身就是全表聚合。
          手工构造请求即可查看任意项目/集群的聚合指标（不含明细）。
代码位置：api-server/app/api/stats.py:1424-1435（签名与调用）
          api-server/app/api/stats.py:260-274（缓存键含 project_id）
涉及函数或类：dashboard / dashboard_cached / dashboard_data
调用链：viewer → GET /api/v1/stats/dashboard?project_id=3 → 返回该项目关联集群的健康分/告警趋势/DB 健康
根本原因：统计层从「全局大屏」演化而来，未引入可见范围。
可能影响：项目经营指标泄露（严重度低，但违背隔离预期）。
修改建议：非 admin 时用 visible_cluster_ids 强制 intersect；project_id 需校验成员关系；核心区块也按可见集群聚合。
验证方法：无授权账号带 project_id 请求，当前返回该项目指标。
是否需要立即处理：否
```

---

## 10. 无法确认事项

1. **告警是否真的在多项目间共享同一张表且无 project 标记**：已确认 `alerts` 表无 `project_id`（模型扫描），但生产数据是否存在「同一 cluster+ns 被多个项目共用」的实际情况无法从代码确认 → 若共用，则 §4/§5.1 的爆炸半径更大。
2. **ES 日志索引是否按 cluster 隔离**：`es_client.py` 的 index 命名策略未逐行展开，若 index 名含 cluster 前缀，则 ISO-06 的跨集群混合会减轻（但「无权限校验」这一点仍成立）。
3. **`alert_action_service.dispatch_action` 内部是否隐含权限校验**：本报告只确认路由层与 `alert_actions.py` 无 `check_cluster_permission`；service 内部是否另有判定未逐行核对（grep 未见），标为「暂时无法确认」但倾向于无。
4. **`resource_governance` / `zombie` 是否按项目**：`resource_governance_ctl.py` 无任何 `check_*` 调用（见 AUTH 报告 §7 的 unscoped 统计），但其作用域是 `cluster_id + namespace` 级配置，是否属有意为 admin-only 未确认（路由用了 `require_any_user`/`require_operator`，未见 admin 限制）。
5. **release 列表是否收敛**：`release.py` 20 条路由中只有 3 处 `check_cluster_permission`（均在写操作），列表类接口未逐一核对 → 标为「部分」。

---

## 11. 审计方法与覆盖范围

**读了什么**（只读）：
- 模型：`models/project.py`、`permission.py`、`host.py`、`user.py`、`alert*.py`、`ticket.py`、`db_connection.py`、`audit.py`、`release.py`、`knowledge.py`、`log_source.py`（**全 87 个模型类做了 project_id/cluster_id 归属扫描**，不是抽样）。
- 授权核心：`services/user_permission_service.py`（全文）、`ticket_service.py:221-253,1007-1050`、`trace_service.py:760-920`、`host_group_service.py:52-71`、`db_approval_service.py:300-360`、`notification_service.py:140-146`、`rbac_service.py:19-40`。
- 接口：`api/projects.py`（全文）、`permissions.py`（全文）、`resources.py`（79 条路由逐条统计 + 关键 8 条细读）、`alerts.py`、`alert_actions.py`、`repair_records.py`、`executions.py`、`tasks.py`、`tickets.py`、`approvals.py`、`audit.py`、`fault_diagnosis.py`、`diagnostic.py`、`db_connections.py`（47 条逐条统计 + 5 条细读）、`db_approvals.py`、`hosts.py`、`host_terminal.py`、`stats.py:260-274,1380-1440`、`release.py`、`monitoring.py`、`logs.py`、`inspections.py`、`batch.py`、`ai_proxy.py`。
- 跨服务：`ai-service/app/api/aux_api.py:415-440`。
- 前端：`ProjectManagement.tsx`、`ApprovalList.tsx`、`constants/nav.ts`。

**统计命令（可复现）**：
```bash
cd api-server
grep -rn "check_cluster_permission\|get_visible_cluster_ids\|check_db_permission" app/api/*.py | wc -l   # 80
grep -rn "project_id" app/api/*.py | wc -l            # 81（其中 projects.py 占绝大多数）
grep -rln "project_id" app/api/*.py                   # 仅 db_approvals/db_diagnosis/projects/stats 四个文件
python3 - <<'EOF'  # 模型归属扫描：87 类 / 3 带 project_id / 26 带 cluster_id / 58 两者皆无
EOF
```

**没做**：未启动服务、未发真实请求、未连库、未枚举生产数据（所有「当前返回 X」均为**静态推演**结论，基于代码路径，未经运行时验证——但每条都给了完整调用链与行号，可手工复现）。
