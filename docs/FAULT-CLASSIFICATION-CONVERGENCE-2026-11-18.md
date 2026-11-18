# 故障分类收敛 + Agent 输出契约（需求）

> 登记：2026-11-18 15:08（老板「把这个需求加入到需求里」）
> **状态：仅登记需求，暂不开发**
> 背景来源：2026-11-18 14:04–15:07 关于「应用层 Agent / 数据库 Agent / 故障分类是否还有意义」的讨论结论

## 0. 背景（带现状数字）

平台已按 Agent 方式做诊断与自愈，**分类不再承担「决策路由」职责**，但现在仍有三处负担：

| 现状 | 数字（2026-11-18 实测） |
|---|---|
| `alerts.fault_type` 实际取值 | **19 类**：TerminatingStuck(114)、CrashLoopBackOff(69)、PodRestartProbeFail(42)、PendingTimeout(31)、ImagePullBackOff(30)、DBConnFailed(26)、OOMKilled(22)、PodRestartOOM(8)、DiskPressure(6)、NodeNotReady(5)、NodeCordoned(4)、CreateContainerError(3)、ContainerMemHigh(2)、ErrImagePull(1)、CreateContainerConfigError(1)、MemoryPressure(1)、DBAuthFailed(1)、InvalidImageName(1)、PodEvicted(1) |
| `prompt_templates` | 12 条（`pod_crash_diagnosis` / `node_pressure_diagnosis` / `application_diagnosis` / `db_diagnose` / `sql_optimize` / `repair_plan_generation` / `risk_assessment` / `repair_verification` / `cluster_diagnosis` / `default_diagnosis` / `diagnosis_uncertain` / `sql_optimization`） |
| `self_heal_rules` | **0 条**（表结构里是 `fault_type_match`，字段按类精确匹配） |
| `knowledge_entries.fault_type` | 11 类 |

问题：分类粒度细、逐类维护成本高；`OOMKilled` 与 `PodRestartOOM`、`ImagePullBackOff` 与 `ErrImagePull` 这类本质同族却分列；还有「未分类 → 人工」的兜底把 agent 的判断权让出去了。

## 1. 目标

1. **分类收敛为两层固定枚举**
   - **L1 域（5）**：`app` 应用 / `db` 数据库 / `node` 节点 / `network` 网络 / `release` 发布
   - **L2 故障族（10）**：`resource` 资源不足 / `config` 配置错误 / `image` 镜像 / `schedule` 调度 / `probe` 探针 / `dependency` 下游依赖 / `code` 代码缺陷 / `capacity` 容量 / `lock` 锁与慢查询 / `network` 网络
   - **L3 具体原因**（自由文本，不限）：由 agent 生成，写进 `root_cause`，**不进策略表**
2. **`fault_type` 保留为「标签」**：继续用于统计报表、知识库检索过滤、通知路由、效果评估；**不再用于硬路由**（不再「识别成 A 类才走 A 流程」）。
3. **自愈规则按 L2 族配「允许动作集」**：`self_heal_rules.fault_type_match` → `fault_family_match`（L2），具体动作由 Agent 在允许集内决策；高风险动作仍走审批与风险评分。
4. **提示词模板收敛为域级 + 证据契约**：不再按 fault_type 一类一套；保留 12 条里的「角色类」模板（plan/risk/verify），把「诊断类」收敛成 `diagnosis_app` / `diagnosis_db` / `diagnosis_node` 三条，并固定证据字段契约。
5. **Agent 输出固定三字段**：`category`(L2)、`confidence`(0~1)、`evidence_refs`(列表)。`confidence < 阈值` → 转人工工单，且工单必须携带 `evidence_refs`。

## 2. 范围

**改**
- 分类枚举定义（新增 L1/L2 常量 + 现有 19 类 → L2 的映射表）
- `self_heal_rules` 匹配口径（fault_type → fault_family）
- 提示词模板归类（诊断类收敛、角色类保留）
- Agent 输出解析（新增三字段 + 低置信度分流）
- 报表/知识库检索：`fault_type` 查询维度补 L1/L2

**不改**
- 告警阈值与采集链路
- 工单流转与关闭闭环
- 审批机制与风险评分（仍按「动作」分级）
- 权限模型（命令审计/会话录制仅 admin 的口径不动）

## 3. 口径

### 3.1 现有 19 类 → L2 映射（草案）

| L2 族 | 现有 fault_type |
|---|---|
| `resource` 资源不足 | OOMKilled、PodRestartOOM、ContainerMemHigh、PodEvicted |
| `config` 配置错误 | CreateContainerConfigError、CreateContainerError |
| `image` 镜像 | ImagePullBackOff、ErrImagePull、InvalidImageName |
| `schedule` 调度 | PendingTimeout |
| `probe` 探针 | PodRestartProbeFail |
| `dependency` 下游依赖 | DBConnFailed、DBAuthFailed |
| `code` 代码缺陷 | CrashLoopBackOff（栈/首启失败时归此类） |
| `capacity` 容量 | DiskPressure、MemoryPressure |
| `lock` 锁与慢查询 | （DB 域新增：慢 SQL / 锁等待） |
| `network` 网络 | PodEvicted（网络类驱逐时归此类，或留 resource，见待确认） |
| 待归类 | TerminatingStuck、NodeNotReady、NodeCordoned（节点域，建议 L1=node + L2=node_health 单列或并入 capacity） |

### 3.2 阈值与配置

- `confidence` 阈值放系统设置，**默认 0.6**，运行两周后按分布再调
- 新增 L2 族必须回答：它驱动了「统计 / 检索 / 路由 / 评估 / 审批」中的哪一项，答不出来不加

### 3.3 存量兼容

- 存量告警/工单/知识库的 `fault_type` **不迁移**，查询时按映射表换算 L2
- `self_heal_rules` 当前 0 条，无迁移成本；后续新建规则直接按 L2 配

## 4. 验收

1. 告警、工单、诊断报告、知识库四处都能按 **L1/L2** 聚合出报表（且存量数据不用改库）
2. 知识库检索改用 L2 过滤后，抽样 20 条人工判定相关性提升
3. 任一 Agent 任务可查 `category` / `confidence` / `evidence_refs` 三个字段
4. 构造一条低置信度样本（`confidence=0.3`）→ 自动转人工工单，且工单带证据引用
5. 原有「按 fault_type 硬路由」逻辑下线后，无功能回归（用现有 19 类逐条回归）
6. 「未分类 → 人工」兜底移除，改为「低置信度 → 人工」

## 5. 待确认（老板）

1. L2 是否就这 10 类（多了应归 L3 自由文本）
2. `TerminatingStuck` / `NodeNotReady` / `NodeCordoned` 归到 L1=node 的哪个 L2（建议单列 `node_health`，即 L2 变 11 类）
3. `confidence` 阈值先用 0.6 还是要跑数据再定
4. 诊断类提示词模板收敛后，旧的 12 条是停用（`is_active=0`）还是保留可回滚

## 6. 分期建议（排期后执行）

| 阶段 | 内容 | 工作量 |
|---|---|---|
| P1 | 枚举 + 映射表 + 报表/检索按 L1/L2 聚合（不动 agent） | 1~1.5d |
| P2 | Agent 输出三字段 + 低置信度转人工 | 1.5~2d |
| P3 | `self_heal_rules` 改按 L2 允许动作集 + 提示词模板收敛 | 2d |
| P4 | 下线硬路由与「未分类」兜底 + 回归 | 1d |

> 注：P2/P3 依赖 Agent 运行轨迹（`AGENT-TRACE-REQUIREMENTS-2026-11-18.md`）落地后才有「confidence」可落库。
