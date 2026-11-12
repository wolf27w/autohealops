# 布局与功能改造需求（2026-11-11 老板拍板：按已确认 demo 落到现有系统）

> 依据：`docs/mockups/platform-demo.html`、`bigscreen-demo.html`（三版 demo 已确认）。
> 本期原则：**不新造功能**，把现有 22 个菜单项 / 现有页面重组进新布局；按钮同类只留一处。

## 一、已确认的布局决策

1. **平台外壳**：顶栏 + 左侧两级菜单 + 内容区；进集群后左侧**整条换成集群导航**（顶部「← 返回平台」）。
2. **顶栏不做环境/集群切换器**；顶栏只放：产品名、全局搜索、刷新、大屏入口、通知铃、主题、用户菜单。
3. **菜单最多两级**：现有「故障诊断 → 集群/应用/数据库」三级**合并成一项**，分类改为页内 Tab。
4. **列表行操作收进 `⋯`**；**详情一律整页跳转**（可直链/刷新），抽屉只用于快速预览。
5. **按钮去重**：刷新只在顶栏（全局一次）；每页只保留自己独有的主操作；跨模块入口只留左侧菜单一处。
6. **大屏**：三栏布局（集群监控+容器 Top / KPI+Deployment 矩阵+自愈 / 优先处理+实时告警+事件流），
   全屏集群按 namespace **左右排列**；**无曲线**（曲线留在集群详情页工作负载监控）；数据必须实时（SSE）。

## 二、现有功能 → 新位置（一个都不能丢）

### 平台级（左侧两级菜单）

| 新位置 | 对应现有页面/路由 |
|---|---|
| 监控中心 → 运行态势 | `pages/Dashboard.tsx` `/dashboard` |
| 监控中心 → 集群管理 | `pages/ClusterList.tsx` `/clusters` |
| 监控中心 → 僵尸服务 | `pages/ZombieServices.tsx` `/zombie-services` |
| 告警与工单 → 告警处理 | `pages/AlertList.tsx` + `AlertHandling.tsx` `/alert-handling` |
| 告警与工单 → 工单 | `pages/Tickets.tsx` `/tickets` |
| 告警与工单 → 告警屏蔽 | `pages/AlertSilences.tsx` `/alert-silences` |
| 告警与工单 → 值班排班 | `pages/OnDuty.tsx` `/on-duty` |
| 智能诊断 → 故障诊断 | `FaultDiagnosis.tsx`（cluster/app）+ `DiagnosticCenter.tsx`（数据库）→ **一页三 Tab** |
| 智能诊断 → 应用诊断助手 | `pages/AgentChat.tsx` `/agent` |
| 自动化运维 → 策略配置 | `pages/StrategyConfig.tsx` `/strategy` |
| 自动化运维 → 定时任务 | `pages/CustomTasks.tsx` `/custom-tasks` |
| 自动化运维 → 发布历史 | `pages/ReleaseHistory.tsx` `/release-history`（当前不在菜单，补进） |
| 自动化运维 → 主机/虚拟机 | `pages/HostManagement.tsx` `/host-management` |
| 自动化运维 → 堡垒机终端 | `pages/WebTerminal.tsx` `/web-terminal` |
| 自动化运维 → 资源治理 | `pages/ResourceGovernance.tsx` `/resource-governance`（当前不在菜单，补进，admin） |
| 知识配置 → AI 配置 | `pages/AIConfig.tsx` `/ai-models`（含模型/提示词模板/片段/路由/健康度） |
| 知识配置 → 提示词建议 | `pages/PromptSuggestions.tsx` `/prompt-suggestions` |
| 知识配置 → 知识库 | `pages/KnowledgeBase.tsx` `/knowledge` |
| 知识配置 → 日志源 | `pages/LogSources.tsx` `/log-sources` |
| 知识配置 → 通知配置 | `pages/NotificationConfig.tsx` `/notify-center` |
| 系统管理 → 用户与权限 | `pages/UserPermission.tsx` `/user-permission` |
| 系统管理 → 报表 | `pages/Reports.tsx` `/reports` |
| 系统管理 → 审计追溯 | `pages/AuditTrace.tsx` `/audit` |
| 系统管理 → 系统设置 | `pages/PlatformSettings.tsx` `/settings` |

### 集群内（`/clusters/:id/*`，左侧集群导航两级）

| 新位置 | 对应现有实现 |
|---|---|
| 概览 | `ClusterDetail.tsx` 顶部信息区 + 资源水位 |
| 节点 | `pages/NodeManagement.tsx`（当前独立页 → 并入集群内，按集群过滤） |
| 应用负载 → 工作负载（类型下拉：部署/有状态副本集/守护进程集/定时任务） | `ClusterDetail.tsx` 的 deployments/statefulsets/daemonsets/cronjobs Tab |
| 应用负载 → 服务 | `ClusterDetail.tsx` 的 services/ingress Tab |
| 应用负载 → 容器组 | `ClusterDetail.tsx` 的 pods Tab（含监控/日志/进容器/JVM/隔离/YAML/删除） |
| 存储 | `ClusterDetail.tsx` 的 pvc/storageclass Tab |
| 配置 | `ClusterDetail.tsx` 的 configmap/secret Tab |
| 监控告警 | `monitoring` 相关（集群聚合视图） |
| 巡检与诊断 | 集群巡检入口 + 诊断跳转 |
| 工作负载详情（整页） | `pages/ServiceDetail.tsx` 已有能力（概览/副本/探针/回滚/重启/Pod 列表/监控）→ 改为规范整页 + 页签（含「监控」） |

## 三、按钮去重清单

| 功能 | 现状 | 改为 |
|---|---|---|
| 刷新 | 顶栏无、各页各一个 | **顶栏一个**（刷新当前路由数据） |
| 大屏入口 | 顶栏按钮 | 保留顶栏一个（图标化） |
| 集群入口 | 左侧菜单 + 首页卡片「集群管理 ›」 | 只留左侧菜单；首页卡片点击直接进集群 |
| 创建/添加 | 各页都有（集群/主机/工单…） | 各页只留自己那一个，语义明确（添加集群/添加主机…） |
| 详情入口 | 部分用抽屉、部分整页 | 统一整页；抽屉只保留「快速预览」（如通知） |

## 四、实施阶段与文件边界

### P0 骨架（2d）
- `components/MainLayout.tsx`：顶栏重构（搜索/刷新/大屏/铃/主题/用户），菜单合并三级→两级
- `App.tsx`：新增 `/clusters/:id/*` 路由；`/fault-diagnosis/*` 合并为 `/fault-diagnosis` + Tab
- 新增 `components/GlobalRefreshContext.tsx`（顶栏刷新广播，各页订阅）
- **验收**：所有现有页面仍可达；菜单无三级；顶栏刷新能触发当前页重载

### P1 集群内重构（3~4d，可并行）
- 新增 `pages/cluster/ClusterLayout.tsx`（左导航 + 返回平台 + 类型下拉）
- 新增 `pages/cluster/WorkloadsList.tsx`（kind 由路由/下拉决定）
- 新增 `pages/cluster/WorkloadDetail.tsx`（整页，页签：概览/资源状态/元数据/环境变量/事件/监控）
- 新增 `pages/cluster/PodsList.tsx`、`ServicesList.tsx`、`StorageConfig.tsx`、`NodesPage.tsx`
- 改造 `ClusterDetail.tsx` → 拆空（保留概览部分）后下线
- 迁移 `ServiceDetail.tsx` 的能力进 `WorkloadDetail.tsx`
- **验收**：日志/进容器/JVM/隔离/YAML/删除/发布/回滚/伸缩 全部可用；直链可刷新

### P2 首页（1~2d）
- 按 demo A（决策优先）+ B 的概览卡合并：顶栏健康分 → 4~5 KPI → 趋势/构成 → 待办队列 → 集群水位/DB 健康
- `pages/Dashboard.tsx` 重写（保留现有接口，不改后端）

### P3 大屏实时（2~3d）
- 后端：`services/screen_bus.py` + `GET /api/v1/stats/screen/stream`（SSE）
- 事件挂点：告警创建/合并、自愈动作完成、工单建单/关单
- 前端：`public/monitor-bigscreen.html` 换 SSE 消费 + 局部刷新；绿块依次闪烁
- 详见 `docs/BIGSCREEN-REALTIME-2026-11-11.md`

### P4 清理（0.5d）
- 删除废弃路由/页面（`ClusterDetail.tsx` 拆分后残留、`stats`/`batch`/`prompts` 等重定向保留）
- 菜单权限矩阵复核（admin/operator/viewer）

## 五、不做（本期）

- 不做 demo 页面进生产（demo 仅 `docs/mockups/` + `frontend/public/*.html` 保留参考）
- 不改后端数据模型（布局改造只动前端；大屏 SSE 除外）
- 不改已有业务口径（告警/工单/诊断闭环逻辑不动）

## 六、总估时与顺序

P0 2d → P1 3~4d → P2 1~2d → P3 2~3d → P4 0.5d ≈ **9~12 人日**。
建议顺序：**P0 → P1 → P2 → P3**（P0/P1 是老板最在意的「布局」，先落地）。

---

## 进度与剩余（2026-11-11 晚更新）

### ✅ 已完成（P1 集群内重构）
- 独立整页 `/clusters/:id/*`（点集群开新页，不套平台 MainLayout；顶部「← 返回平台」+ 集群名 + 刷新，整页白底统一）
- 集群左导航两级：概览 / 节点 / 应用负载(工作负载·服务·容器组) / 存储 / 配置 / 监控告警 / 巡检诊断
- 工作负载列表（类型用页内 Segmented，URL 联动；状态筛选在状态列头；命名空间+搜索一行）
- 工作负载详情整页（6 页签，含监控）
- 容器组（状态列可筛选）、服务/路由、存储/配置、节点
- commit：`ddd87ba` `fae417a` `99c3523` `9772cd8` `3af555c` `2bc7832` `bd18642` `386eaee` `681033b` `74313c1`

### ⏳ 剩余（按顺序）
| # | 事项 | 估时 | 依赖 |
|---|---|---|---|
| R32-P0 | 平台外壳：顶栏重构（去掉环境/集群切换、全局搜索、刷新、大屏、通知、主题、用户）+ 菜单三级合并为两级（故障诊断合一 + 页内 Tab）+ 全局刷新广播 | 2d | 无（纯前端） |
| R32-P2 | 首页重写（A+B 合并：健康分→KPI→趋势/构成→待办队列→水位/DB） | 1~2d | 无 |
| R32-P3 | 大屏实时：SSE + 事件挂点 + 前端局部刷新 + 绿块闪烁 | 2~3d | 后端需联网验证 |
| R32-P4 | 清理：ClusterDetail 拆后残留、权限矩阵复核、废弃路由 | 0.5d | P0/P1 |
| R31 | 诊断会话共享（默认私有，可共享到同 namespace） | 1d | 迁移需联网 |
| 小项1 | 后端字段缺口：节点 IP/创建时间、ConfigMap/Secret/PVC 创建时间、工作负载创建时间（executor 有、api-server 映射漏） | 0.5d | 需集群验证 |
| 小项2 | Pod 行「监控」从工作负载级抽屉换成 Pod 级（getPodMetrics 已有） | 0.5d | 无 |
| 小项3 | 集群内「监控告警 / 巡检诊断」两个占位页接真实内容 | 1~2d | P2/P3 |
| 小项4 | 首页 A/B 版最终选型（建议 A+B 合并） | 待拍板 | — |

### 离线可做（老板回家期间）
P0、P2、P3 前端、R31 代码（迁移不执行）、小项2 —— 详见 `docs/OFFLINE-DEV-2026-11-11.md`。

---

## 2026-11-12 进度更新（本轮并行开发完成）

### ✅ 已完成并提交（4 笔）
- **R32-P0 平台外壳**（`a558019`）：顶栏重构（全局搜索 AutoComplete + 全局刷新按钮）、菜单三级→两级（故障诊断合一为页内三 Tab）、新增 `GlobalRefreshContext.tsx` + `FaultDiagnosisHub.tsx`。
- **R32-P2 首页重写**（`7527b8d`）：`Dashboard.tsx` 重写为决策优先+概览卡合并版（健康分→5 KPI→趋势/构成→待办队列→集群水位/DB 健康），仅用现有接口、保留 5s 轮询。
- **R32-P3 大屏 SSE 后端**（`1e59bb8`）：`screen_bus.py`(发布订阅+去重) + `screens.py`(SSE 端点) + 单测；父会话接线 `main.py` 路由 + 工单建单/关单、自愈完成 publish 挂点。
- **R31 诊断会话共享**（`e315a2d`）：模型加 `shared/shared_at/shared_by` + 迁移 `a9b8c7d6e5f4`（**待 upgrade**）+ sessions 可见性/messages 权限/share 接口 + `AgentChat.tsx` 共享开关。
- **小项2 Pod 级监控**：复核后发现已在 `99c3523` 完成（`PodMetricsDrawer` 用 `getPodMetrics`），冗余，无需改动。

### 验证
- 后端：`py_compile` 全过；单测 **23 个全绿**（screen_bus 11 + session_share 12）。
- 前端：`npx tsc -b` **exit 0**（无类型错误）。

### ⏳ 剩余未开发
| 事项 | 状态 | 依赖 |
|---|---|---|
| R32-P3 大屏**前端**换 SSE 消费（monitor-bigscreen.html 局部刷新+绿块闪烁） | 待做 | 后端契约已就绪，可离线 |
| R32-P4 清理（ClusterDetail 残留 / 权限矩阵复核 / 废弃路由） | 待做 | 需老板确认删除范围 |
| 小项1 后端字段缺口（节点/CM/Secret/PVC/工作负载创建时间映射） | 待做 | 需集群验证 |
| 小项3 集群内「监控告警/巡检诊断」占位页接真实内容 | 待做 | 部分需 P2/P3 |
| R31 迁移 `a9b8c7d6e5f4` 升级 | 待执行 | 需 MySQL |
| 大屏 SSE 端到端联调（≤1s 上屏） | 待联调 | 需后端+DB 起服务 |
| 告警创建/合并 publish 挂点 | 待确认 | 告警源在 Go watcher 侧，api-server 无创建路径 |
| P2-2 R2 平台多副本拆网关 | 待开发 | 需集群，方案见 `docs/R2-MULTI-REPLICA.md` |
