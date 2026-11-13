# 资源中心与菜单归位需求（R34）

> 依据：2026-11-13 老板拍板 —— 「数据库正式做成资源中心的第三个入口，还有菜单归位」。
> 前置：`docs/LAYOUT-REFACTOR-2026-11-11.md`（R32，顶栏 + 两级菜单骨架已实现）。
> 本期性质：**以信息架构重组为主，数据库模块是唯一的"新页面组"**（后端能力已齐，前端有存量可复用）。
> 状态：待开发。

---

## 一、目标

1. 把「资源」从流程阶段里抽出来，成为**独立一级域**：K8s 集群 / 主机与虚拟机 / 数据库 三类资源平级。
2. 数据库成为资源中心**第三个正式入口**：纳管、监控、慢 SQL、SQL 工作台、诊断都能从资源进入。
3. 菜单归位：消除"有路由没入口"和"一个功能两处入口"，一级菜单从 6 组收敛到 **概览 + 3 业务模块 + 1 平台底座**。
4. 不丢功能：现有页面**一个都不能少**（下线只在明确写了"下线"的地方）。

### 不做什么

- 不改后端接口（本需求已核实：DB 相关后端能力全部就绪，见第五节 5.7）。
- 不做视觉改版（沿用 R32 已实现的顶栏 + 两级菜单外壳）。
- 不动集群内导航（`/clusters/:id/*`）的结构，只做入口归位（节点管理下线一条例外）。

---

## 二、现状体检（证据）

| 项 | 现状 | 问题 |
|---|---|---|
| 一级菜单 | 6 组 / 22 项：监控中心、告警与工单、智能诊断、自动化运维、知识配置、系统管理 | 分组顺序其实是对的（采集→告警→诊断→自愈），但混进了"资源"这个横向维度 |
| K8s 集群 | 在「监控中心 → 集群管理」 | 资源对象被放进监控域 |
| 主机/虚拟机 | 在「自动化运维 → 主机/虚拟机」 | 资源对象被放进自动化域 |
| **数据库** | **只存在于「智能诊断 → 故障诊断 → 数据库诊断」Tab 内** | 资源能力藏在诊断页里，没有任何资源入口 |
| `DiagnosticCenter.tsx` | 一个页面同时做：DB 纳管 CRUD（`createDBConnection` / `testDBConnection` / `deleteDBConnection`）+ 监控（内嵌 `DBMonitor`）+ SQL 工作台（`DbWorkbench`）+ 诊断报告 + 趋势 | 职责四合一，是本次要拆的主体 |
| `AlertHandling.tsx` | 5 个 Tab：告警列表 / **故障诊断** / 审批列表 / 处理记录 / 批量处理 | 「故障诊断」Tab 与 `/fault-diagnosis` 重复入口 |
| `StrategyConfig.tsx` | 3 个 Tab：告警策略 / 自愈规则 / 审批流程 | 横跨"告警"和"自愈"两个模块，必须拆 |
| 有路由无菜单入口 | `/node-management`（NodeManagement 422 行）、`/resource-governance`（392 行）、`/release-history`（102 行） | 只能手输 URL 才能到 |
| 真孤儿页面（无人 import，含动态 import 校验） | `ComingSoon` / `DecisionPolicies` / `ResourceQuota` / `TaskDetail` / `TaskList` | 死代码，清理 |
| URL | `/host-management`、`/web-terminal`、`/zombie-services`、`/alert-handling` 平铺，与模块无对应关系 | 新增模块还要再补 Navigate |
| 历史重定向 | `App.tsx` 里 20+ 条 `<Navigate>` 兼容旧路径 | 说明 URL 一直是自然生成的，本次一次性归位 |

> 注：上一轮沟通里"10 个死代码页面"的说法不准确，实际用 import 图校验后只有 **5 个**真孤儿；`DBMonitor` / `DiagnosticCenter` / `AICallLogs` 等都是被合并页 Tab 引用的活代码。

---

## 三、目标信息架构

### 3.1 四条原则

1. **一级菜单只放名词**（资源 / 告警 / 工单 / 知识），流程阶段放二级。例外：「告警处理」「工单」这类**队列型作业面**自身就是列表，可以占一级。
2. **同一对象只有一个列表页**。资源的监控 / 告警 / 诊断全部做成**详情页 Tab**，不在别处再开一个同类入口。
3. **配置项跟着它服务的对象走**。日志源跟采集走、通知跟告警走、AI 与知识库跟诊断走，不设"配置大杂烩"组。
4. **菜单最多两级**，第三层交给详情页 Tab；URL 前缀与一级模块对齐。

### 3.2 菜单树（推荐方案 A）

```
[概览]  运行态势                    /dashboard          ← 单独置顶，不占模块名额

资源中心
├─ K8s 集群                        /resources/clusters      → 详情 /clusters/:id/*
├─ 主机与虚拟机                     /resources/hosts         ← HostManagement
├─ 数据库                          /resources/databases     ← 新建（拆 DiagnosticCenter）
├─ 堡垒机终端                       /resources/terminal      ← WebTerminal（跨主机统一入口）
└─ 资源治理（admin）                 /resources/governance    ← ResourceGovernance + ZombieServices + NodeManagement 下线

告警监控
├─ 告警处理                        /alerts/handling         ← AlertHandling（去掉「故障诊断」Tab）
├─ 工单                            /alerts/tickets          ← Tickets
├─ 告警策略                         /alerts/policies         ← AlertPolicies（从 StrategyConfig 拆出）
├─ 告警屏蔽 / 静默                   /alerts/silences         ← AlertSilences
├─ 通知配置                        /alerts/notifications    ← NotificationCenter（渠道 + 消息群）
├─ 值班排班                        /alerts/on-duty          ← OnDuty
└─ 采集与日志源                     /alerts/sources          ← LogSources（从「知识配置」移入）

诊断与自愈
├─ 故障诊断（应用/集群/数据库三 Tab）  /diagnose/fault          ← FaultDiagnosisHub（DB Tab 改只读聚合）
├─ 应用诊断助手                      /diagnose/agent          ← AgentChat
├─ 自愈规则与审批                     /diagnose/strategy       ← SelfHealRules + ApprovalFlows + DecisionPolicies
├─ 定时与批量任务                     /diagnose/tasks          ← CustomTasks + BatchOperation
├─ 发布历史                          /diagnose/release        ← ReleaseHistory（补入口）
└─ 执行与审批记录                     /diagnose/executions     ← ExecutionDetail 列表 + ApprovalList

平台
├─ 用户与权限                        /platform/users          ← UserPermission
├─ AI 配置与知识库                    /platform/ai             ← AIConfig（模型/提示词/成本）+ KnowledgeBase + PromptSuggestions
├─ 审计追溯                          /platform/audit          ← AuditTrace
├─ 报表                              /platform/reports        ← Reports
└─ 系统设置                          /platform/settings       ← PlatformSettings
```

### 3.3 备选方案 B（保守版，只做最小改动）

保留「自动化运维」组不做拆分，仅把「主机/虚拟机」「资源治理」移入资源中心、数据库进资源中心、日志源移入告警监控。

- **差别只有一处**：`StrategyConfig`（策略配置）和 `CustomTasks`/`ReleaseHistory` 是否拆出去。
- **代价**：`StrategyConfig` 不拆 = 「告警策略」继续留在"自动化运维"里，配置项跟着对象走的原则被破坏。
- **建议选 A**：拆 `StrategyConfig` 只有约 **0.5 人日**（页面已经是 3 个 Tab 拼装，拆开就是路由 + 菜单两处改动），换来分类轴干净。

---

## 四、现状 → 新位置完整映射（一个都不能丢）

| 现有页面 / 路由 | 行数 | 新位置 | 处理动作 |
|---|---|---|---|
| `Dashboard` `/dashboard` | 420 | 概览 | 不动 |
| `ClusterList` `/clusters` | 478 | 资源中心 → K8s 集群 | 换前缀 `/resources/clusters`，旧地址重定向 |
| `ClusterDetail` + `pages/cluster/*` | 11 文件 | 资源中心 → K8s 集群详情 | 不动（集群内导航保持） |
| `HostManagement` `/host-management` | 498 | 资源中心 → 主机与虚拟机 | 换前缀 |
| `NodeManagement` `/node-management` | 422 | **下线**（并入 `/clusters/:id/nodes` 已有实现） | 先核对差异化能力，再删路由 + 页面 |
| `WebTerminal` `/web-terminal` | 525 | 资源中心 → 堡垒机终端 | 换前缀；主机详情里保留入口 |
| `HostSessionReplay` `/host-sessions/:id/replay` | 158 | 主机详情 Tab「会话回放」或资源治理下 | 保留路由（直链） |
| `ResourceGovernance` `/resource-governance` | 392 | 资源中心 → 资源治理 | **补菜单入口**（原来没有） |
| `ZombieServices` `/zombie-services` | 163 | 资源中心 → 资源治理（Tab） | 从「监控中心」移入 |
| `DiagnosticCenter` `/fault-diagnosis?tab=db` | — | **拆分**：DbResource（新）/ DbDiagnosisTab（改造） | 见第五节 |
| `DBMonitor`（被 DiagnosticCenter 内嵌） | 388 | 数据库详情 → 监控 Tab | 改成按 connId 固定，去掉顶部切换器 |
| `DbWorkbench`（component） | — | 数据库详情 → SQL 工作台 Tab | 不动 |
| `AlertHandling` `/alert-handling` | 23 | 告警监控 → 告警处理 | 删「故障诊断」Tab；Tab 保留告警列表/审批/处理记录/批量 |
| `AlertList` | — | 同上 Tab | 不动 |
| `FaultDiagnosis` | — | 诊断 → 故障诊断（应用/集群） | 从 AlertHandling 移除，只留 Hub 一处 |
| `ApprovalList` | — | 诊断 → 执行与审批记录 | 移出 AlertHandling |
| `ProcessingRecords` | — | 告警处理 Tab 保留 | 不动 |
| `BatchOperation` | — | 告警处理 Tab 保留 | 不动 |
| `Tickets` `/tickets` | 359 | 告警监控 → 工单 | 换前缀 |
| `AlertSilences` `/alert-silences` | 99 | 告警监控 → 告警屏蔽 | 换前缀 |
| `OnDuty` `/on-duty` | 400 | 告警监控 → 值班排班 | 换前缀 |
| `NotificationCenter` `/notify-center` | 17 | 告警监控 → 通知配置 | 换前缀 |
| `LogSources` `/log-sources` | 124 | 告警监控 → 采集与日志源 | 从「知识配置」移出 |
| `StrategyConfig` `/strategy` | 24 | **拆分**：AlertPolicies → `/alerts/policies`；SelfHealRules + ApprovalFlows → `/diagnose/strategy` | 拆路由 |
| `FaultDiagnosisHub` `/fault-diagnosis` | 23 | 诊断 → 故障诊断 | DB Tab 改只读聚合 |
| `AgentChat` `/agent` | 545 | 诊断 → 应用诊断助手 | 换前缀 |
| `CustomTasks` `/custom-tasks` | 99 | 诊断 → 定时与批量任务 | 从「自动化运维」移入 |
| `ReleaseHistory` `/release-history` | 102 | 诊断 → 发布历史 | **补菜单入口** |
| `ExecutionDetail` `/executions/:planId` | 119 | 诊断 → 执行与审批记录 详情 | 保留直链 |
| `UserPermission` `/user-permission` | 18 | 平台 → 用户与权限 | 换前缀 |
| `AIConfig` `/ai-models` | 19 | 平台 → AI 配置与知识库 | 换前缀 |
| `PromptSuggestions` `/prompt-suggestions` | 145 | 平台 → AI 配置与知识库（Tab） | 合并进 AIConfig 页 |
| `KnowledgeBase` `/knowledge` | 217 | 平台 → AI 配置与知识库（Tab） | 合并进 AIConfig 页 |
| `AuditTrace` `/audit` | 131 | 平台 → 审计追溯 | 换前缀 |
| `Reports` `/reports` | 127 | 平台 → 报表 | 换前缀 |
| `PlatformSettings` `/settings` | 368 | 平台 → 系统设置 | 换前缀 |
| `Login` `/login` | 88 | — | 不动 |
| `ComingSoon` / `DecisionPolicies` / `ResourceQuota` / `TaskDetail` / `TaskList` | 298 合计 | — | **删除**（DecisionPolicies 若要有归属，放诊断 → 自愈策略 Tab） |

---

## 五、数据库资源模块需求（本次唯一的"新页面组"）

### 5.1 列表页 `/resources/databases`

**信息粒度**：一行一个数据库实例（`db_connections` 表一条记录）。

**列**：名称 / 类型（mysql·pg·oracle…）/ 来源（手动纳管·集群 Agent 自动发现）/ 分组 / 环境标签 / 状态（在线·离线·未知）/ 采集（开关 + 间隔）/ 巡检（开关 + cron）/ 最近采集时间 / 操作

**筛选**：关键词、类型、分组、环境标签、状态、采集开关。

**操作**：
- 顶部：`+ 添加数据库`、批量导入（沿用主机批量导入的交互形态）、刷新
- 行内 `⋯`：编辑 / 测试连接 / 立即采集 / 查看监控（跳详情）/ 删除（二次确认 + 显示影响条数）

**权限**：
- 列表口径沿用后端现成逻辑：admin 全量，其他用户按 `check_db_permission` 过滤（后端已实现，前端不要重复过滤）。
- 增 / 删 / 改 / 采集：admin（`RequireRole role="admin"`）。
- 只读用户只能看列表和详情监控。

**数据来源接口**（全部已存在，见 5.7）：
`GET /db-connections`、`POST /db-connections`、`PUT /db-connections/{id}`、`DELETE /db-connections/{id}`、`POST /db-connections/{id}/test`、`POST /db-connections/{id}/collect`、`GET /db-connections/groups` 系列。

### 5.2 详情页 `/resources/databases/:id`（整页，可直链/刷新）

顶部：名称 + 类型标签 + 状态 + 所属集群/项目 + 右上主操作（立即采集 / 一键诊断 / 更多 `⋯`）。

Tab 骨架（与 K8s 集群详情保持一致的"对象详情"范式）：

| Tab | 内容 | 现有实现 | 工作量 |
|---|---|---|---|
| 概览 | 连接信息（密码打码）、采集/巡检配置、关联集群与项目、最近异常摘要 | 新建（字段都在 `_serialize`） | 小 |
| 监控 | 指标按类型分组（连接/吞吐/缓存/InnoDB/锁等待/复制…）+ 趋势图 | `DBMonitor.tsx` 复用，去掉顶部库切换器 | 中 |
| 慢 SQL | 慢 SQL 列表（命中规则 + 建议）+ digest 趋势 + 时段对比 | `getSlowSQL` / `getSlowSQLDigestTrend` / `compareSlowSQL` | 小（搬运） |
| 异常 | 阈值异常列表（级别/指标/当前值/阈值/建议） | `getDBAnomalies` | 小（搬运） |
| SQL 工作台 | 查询、执行、导出、导入、查看 DDL、Schema 树、定时 SQL 任务 | `components/DbWorkbench` | 小（搬运） |
| 诊断 | 一键诊断 + 进度 + 报告列表 + 指标趋势 | `runDbDiagnosis` / `getDbDiagnosisProgress` / `getDbDiagnosisReports` / `getDbTrends` | 中（从 DiagnosticCenter 搬） |

**原则**：`DBMonitor` 与 `DbWorkbench` **只搬运不重写**，避免功能回退。

### 5.3 与「诊断」模块的边界（必须写清，否则又是重复入口）

- `/diagnose/fault` 的「数据库诊断」Tab **只做聚合视图**：跨库展示诊断报告列表 + 手动触发诊断 + 跳转到 `/resources/databases/:id?tab=diagnosis`。
- **口径**：`资源详情 = 针对这一个库干活`；`诊断中心 = 跨库看结果、批量发起`。纳管 CRUD、监控、SQL 工作台**只允许存在于资源详情**，诊断中心不再出现。

### 5.4 与其他模块的联动

- 告警详情 / 工单里的"关联数据库"链接 → `/resources/databases/:id`（目前若指向 `/fault-diagnosis?tab=db` 需一并改）。
- 权限管理里的"按数据库授权"（`getUserDBPerms`）继续在平台 → 用户与权限，不在资源页重复。
- 项目详情里的 DB 关联（`getProjectDBs`）保持现状。

### 5.5 空态与引导

首次进入无任何 DB 连接时，显示引导卡：说明"Agent 自动发现"与"手动纳管"两种方式 + 「添加数据库」按钮，不放空白表格。

### 5.6 验收

- 添加一条 mysql 连接 → 测试连接成功 → 立即采集 → 监控 Tab 有数据 → 慢 SQL 有列表 → 一键诊断出报告，全链路无报错。
- 非 admin 用户看不到"添加/编辑/删除/采集"按钮。
- 直链刷新 `/resources/databases/3?tab=slow-sql` 能直接落在该 Tab。

### 5.7 后端现状核实（**本期不需要后端改动**）

已确认存在：`api-server/app/api/db_connections.py`（连接 CRUD、分组、测试、查询/执行、Schema、DDL、导入导出、导出下载、定时 SQL 任务、异常、慢 SQL、慢 SQL 对比、慢 SQL digest 趋势、采集触发）、`api-server/app/api/db_diagnosis.py`（诊断发起、报告列表、指标、进度、趋势）。
唯一可能需要补的：列表页若需要"异常数/报告数"聚合展示，优先前端对 `/db-connections` 返回值计算，**不为展示新增接口**。

---

## 六、菜单归位需求

### 6.1 一级分组变更

| 旧 | 新 | 说明 |
|---|---|---|
| 监控中心 | **撤销** | 仪表盘 → 概览；集群 → 资源中心；僵尸服务 → 资源治理 |
| 告警与工单 | 告警监控 | 吸收「告警策略」「通知配置」「日志源」「值班」 |
| 智能诊断 | 诊断与自愈 | 吸收「自愈规则/审批」「定时任务」「发布历史」「执行记录」 |
| 自动化运维 | **撤销** | 主机→资源；终端→资源；策略→拆两处；任务/发布→诊断与自愈 |
| 知识配置 | **撤销** | 日志源→告警监控；AI/知识库/提示词→平台 |
| 系统管理 | 平台 | 报表归入平台（业务产出，不是系统配置，但无更合适归属） |
| — | **资源中心（新增）** | K8s / 主机与虚拟机 / 数据库 / 堡垒机终端 / 资源治理 |

### 6.2 三个"有路由没入口"的处理

| 页面 | 处理 |
|---|---|
| `ResourceGovernance` | 进菜单（资源中心 → 资源治理，admin） |
| `ReleaseHistory` | 进菜单（诊断与自愈 → 发布历史） |
| `NodeManagement` | **下线**：先逐项比对 `/clusters/:id/nodes`（`NodesPage.tsx`）与它的能力差异，确认无独有能力后删页面 + 删路由；有差异则把差异能力补进 `NodesPage` |

### 6.3 三处重复入口的收敛

| 重复点 | 处理 |
|---|---|
| `AlertHandling` 的「故障诊断」Tab vs `/fault-diagnosis` | 从 `AlertHandling` 删该 Tab，告警行操作保留"发起诊断"直接跳 `/diagnose/fault`（带告警 id） |
| `StrategyConfig` 三合一 Tab | 拆成 `/alerts/policies` 与 `/diagnose/strategy` |
| `ZombieServices` 独立菜单 vs 资源治理 | 合并为资源治理下的 Tab |

### 6.4 角色过滤

`MainLayout.tsx` 的 `ADMIN_ONLY_KEYS` 按新 key 重写（admin 专属：`/resources/governance`、`/alerts/policies`、`/alerts/notifications`、`/alerts/sources`、`/diagnose/strategy`、`/diagnose/tasks`、`/platform/users`、`/platform/ai`、`/platform/settings`）。
**要求**：菜单过滤与 `App.tsx` 的 `RequireRole` 守卫必须同源（现在两处各写一份，容易漂）。落点建议：抽一个 `constants/nav.ts` 导出菜单树 + 每个 key 的最低角色。

### 6.5 URL 归位与重定向

新前缀：`/resources/*`、`/alerts/*`、`/diagnose/*`、`/platform/*`。

旧地址**全部保留 301 语义的重定向**（`<Navigate replace>`），至少覆盖：
`/dashboard`、`/clusters`、`/host-management`、`/web-terminal`、`/resource-governance`、`/zombie-services`、`/alert-handling`、`/tickets`、`/alert-silences`、`/on-duty`、`/notify-center`、`/log-sources`、`/fault-diagnosis`、`/agent`、`/custom-tasks`、`/release-history`、`/user-permission`、`/ai-models`、`/knowledge`、`/prompt-suggestions`、`/audit`、`/reports`、`/settings`、`/strategy`。

**建议**：`/dashboard` 这次不动（顶栏 Logo 和大量代码都指向它，收益小风险大），其余按上表迁移。

### 6.6 死代码清理

删除 `pages/ComingSoon.tsx`、`pages/ResourceQuota.tsx`、`pages/TaskDetail.tsx`、`pages/TaskList.tsx`；`pages/DecisionPolicies.tsx` 若确认要保留风险评分配置，则挂到 `/diagnose/strategy` 的 Tab，否则一并删除。

### 6.7 全局搜索

`flattenMenu` 已从菜单树派生搜索项，菜单改完自动跟着变；**注意**：搜索项 key 变成新 URL 后，历史收藏/分享链接要靠 6.5 的重定向兜底。

---

## 七、验收标准（可测）

1. **可达性**：菜单树里每一项点开都有内容（无白屏、无 404）；所有详情页可直链刷新。
2. **层级**：菜单最多两级（`menuItems` 里无第三层）。
3. **无重复入口**：同一功能在菜单里只出现一次（`AlertHandling` 不再有故障诊断；策略不再三合一）。
4. **重定向**：第六节 6.5 列出的旧 URL 逐个访问，均落到对应新页面。
5. **构建**：`npx tsc -b` 0 错、`npx eslint src` 0 error、`npm run build`（vite）通过。
6. **冒烟**：用 headless Chrome + CDP 逐项点开菜单（约 25 项），全部渲染出 `<h1>` 级标题与表格/卡片，0 报错。
7. **数据库全链路**：见 5.6。
8. **权限**：admin / operator / viewer 三种角色分别登录，菜单项与按钮符合 6.4 表。

---

## 八、分期与文件边界（供并行开发）

> 遵循项目既有并行工作法：按**文件边界**拆，不按功能拆；公共接线文件由父会话统一收口。

### P0 骨架（0.5~1d）— 单人，先做完再并行

- `frontend/src/components/MainLayout.tsx`：新菜单树（4 模块 + 概览）
- `frontend/src/App.tsx`：全部路由前缀归位 + 旧地址重定向表
- `frontend/src/constants/nav.ts`（新建）：菜单树 + 角色守卫的唯一来源
- 验收：所有页面仍可达（此时不要求页面内容变化）

### P1 数据库资源模块（2~3d）— 可与 P2 并行

| 任务 | 文件白名单 | 依赖 |
|---|---|---|
| DB 列表页 | `pages/DbResourceList.tsx`（新建） | P0 |
| DB 详情页骨架 + 概览 | `pages/DbDetail.tsx`（新建） | P0 |
| 监控 Tab | `components/db/DbMonitorPanel.tsx`（由 `DBMonitor.tsx` 改造，保留原文件直到切换完） | 详情页骨架 |
| 慢 SQL / 异常 Tab | `components/db/DbSlowSqlPanel.tsx`、`DbAnomalyPanel.tsx`（新建，从 DiagnosticCenter 搬逻辑） | 详情页骨架 |
| SQL 工作台 Tab | 复用 `components/DbWorkbench.tsx`，仅做 props 适配 | 详情页骨架 |
| 诊断 Tab | `components/db/DbDiagnosisPanel.tsx`（从 DiagnosticCenter 搬） | 详情页骨架 |
| 诊断中心瘦身 | `pages/DiagnosticCenter.tsx`（改成跨库聚合视图） | 上面全完成 |

### P2 菜单归位配套（1~2d）— 与 P1 并行（文件不重叠）

| 任务 | 文件白名单 |
|---|---|
| 策略拆分 | `pages/AlertPoliciesRoute.tsx`、`pages/StrategyConfig.tsx`（改）、`pages/AlertHandling.tsx`（删诊断 Tab） |
| 知识配置拆散 | `pages/PlatformAI.tsx`（新建，合并 AIConfig + KnowledgeBase + PromptSuggestions） |
| 资源治理合并 | `pages/ResourceGovernance.tsx`（加僵尸服务 Tab） |
| 节点管理下线 | `pages/NodeManagement.tsx`（删）、`pages/cluster/NodesPage.tsx`（补差异能力） |

### P3 清理与验证（0.5d）

- 删 4~5 个孤儿页面；`npx tsc -b` / `eslint` / `build`；CDP 菜单冒烟；三角色权限回归。

**合计约 4~6 人日**（P0 1 + P1 3 + P2 2 + P3 0.5，含并行压缩）。

---

## 九、待确认

1. **方案 A 还是 B**（第六节 3.3）：是否拆掉「自动化运维」组，把策略/任务/发布并入「诊断与自愈」。**推荐 A**。
2. **节点管理**：`NodeManagement.tsx`（422 行）是否有 `/clusters/:id/nodes` 不具备的能力（如跨集群节点总览、批量打标/隔离）？若有，是保留跨集群视图还是并入集群内？
3. **报表**放「平台」是否可接受（它更像业务产出）。若不可接受，可选做成顶栏独立入口。
4. **`/dashboard` 是否一起迁到 `/overview`**（建议不迁，风险大于收益）。
