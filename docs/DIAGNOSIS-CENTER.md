# 诊断中心方案：应用诊断 + 数据库诊断

> 状态：✅ 已实现（2026-11-18 代码核实，见 docs/VERIFY-2026-08-BATCH-2026-11-18.md）
> 关联：`AGENT-DIAGNOSIS.md`（8-19 会话式 AI 诊断助手，本方案将其并入统一诊断框架）
> 定位：平台现有「集群/资源层」诊断自愈（executor watch + AI 闭环）；本方案补齐「应用层（容器内）」+「数据库」两个诊断域

## 老板拍板的决策（2026-08-20）

1. **诊断两个域一起做**：应用诊断（Pod/服务）+ 数据库诊断（慢 SQL），统一为「诊断中心」
2. **不做自动触发诊断**：边界不清，后面再讨论；只有**用户显式下达诊断任务**（含定时计划）
3. **任务式为主**：发现资源/性能问题 → 下诊断任务（选目标 + 时间窗 + 执行时间，如业务低谷凌晨跑）→ 定时执行 → 出报告落库可查
4. **会话式保留**：8-19 方案（对话式实时诊断）保留，与任务式共用同一套诊断能力
5. **数据库范围**：MySQL 系 + PG 系 + Oracle 三类

## 诊断域总览

| 诊断域 | 目标 | 数据源 | 输出 |
|---|---|---|---|
| 应用诊断 | Pod/服务（容器内） | ExecInPod 只读命令 + ES 日志 + describe events + pod_metrics 指标 | 根因 + 处理/优化建议 |
| 数据库诊断 | MySQL/PG/Oracle | 系统视图慢 SQL + EXPLAIN + 表结构/索引 | 慢 SQL 清单 + 执行时长 + 优化建议 |

## 统一诊断框架

```
用户下达诊断任务（类型/目标/时间窗/定时计划）
   ↓ api-server 定时调度
执行诊断（应用：executor 采集；数据库：直连采集）
   ↓ ai-service 统一 AI 出口
诊断报告（落库，前端可查历史）
   ↓ 确认闭环后（可选）
知识库沉淀（复用现有 rag）
```

### 诊断任务模型（核心）

- **诊断任务表**：`id / 类型(app|db) / 目标(集群+ns+pod | 数据库连接) / 时间窗 / 定时计划(cron) / 模型(model_config_id，可空=系统自动) / 状态(待执行|执行中|成功|失败) / 创建人 / 创建时间`
- **执行形态**：用户创建任务（可指定 cron，如每天凌晨 2 点）→ 到点执行 → 生成报告
- **手动/定时两用**：不填 cron = 立即执行一次；填 cron = 定时重复

### 模型选择（2026-08-20 老板定）

- 会话式诊断 / 诊断任务 / 数据库 AI 建议：发起时**选一次模型**（会话/任务级）
- 下拉列出所有激活模型（供应商/模型名 + 每千 token 成本标注）
- 默认值 = **自动（系统推荐）**：走现有 priority 降级链，兼容老行为
- 指定模型后：**指定模型优先，失败仍走降级链兜底**
- 全部模型对所有用户开放（不限制角色）

### 诊断报告

- **报告表**：`id / 任务id / 诊断域 / 结论 / 根因 / 建议 / AI原始输出 / 时间`
- 前端报告详情页：诊断结论 + 建议 + 原始数据（日志摘要/慢SQL列表/指标趋势）

## 一、应用诊断

### 工具集（与 8-19 方案一致）

1. **ExecInPod**（executor 新增 gRPC，client-go remotecommand 等价 kubectl exec）：容器内只读命令（ps/env/cat 配置/ss·netstat/df 等，白名单 + 强制只读）
2. **ES 应用日志**（按时间窗查询）
3. **Pod describe + events**（现有能力）
4. **资源指标**（pod_metrics 复用，按时间窗）

### 入口

- **任务式**（新增）：选集群/命名空间/Pod + 时间窗（如过去 24h）+ 执行计划（立即 / cron）→ 定时执行 → 报告
- **会话式**（保留 8-19）：对话页实时多轮诊断

### 明确不做

- 系统级自动触发诊断（资源异常自动诊断等）——边界未定，后续讨论

## 二、数据库诊断（数据库监控）

### 连接管理

- 平台配置数据库连接：`类型(MySQL系|PG系|Oracle) / 名称 / 地址 / 端口 / 库名 / 账号 / 密码`
- 密码用 ENCRYPTION_KEY 加密存储（复用 kubeconfig 加密方案）
- 建议专用**只读账号**（最小权限）
- 连接可用性检查（测试连接按钮）
- **每连接独立配置**（创建连接时设置）：
  - 慢 SQL 阈值（long_query_time，默认 1s，按 digest 平均耗时过滤）
  - 采集频率（默认 5 分钟）

### 连接权限（资源级 RBAC，2026-08-20 老板定）

平台权限从「全局三角色」升级为「资源级授权」（独立授权表）：

- **admin**：最大权限，全部集群 + 全部数据库连接（隐式全量，跳过授权检查）
- **operator**：按授权分配——指定集群 + 指定 namespace 可创建/删除/操作；指定数据库连接可配置管理
- **viewer**：按授权查看（无创建/删除/配置权限）
- 未授权的资源不可见（viewer/operator 都按授权看，权限收敛）

#### 授权表设计

```sql
-- 集群授权（集群 + namespace 组合粒度）
user_cluster_permissions:
  id, user_id, cluster_id, namespace,  -- namespace 可空 = 该集群全部
  permission(read|operate)             -- operate = 该 ns 下可创建/删除/操作

-- 数据库连接授权
user_db_connections:
  id, user_id, db_connection_id, permission(read|manage)
  -- manage = 可改配置(阈值/频率)/删除；read = 只看慢SQL/报告
```

#### 权限检查逻辑

- 集群操作（executor 相关）：校验 user 对该 cluster 的 namespace 有 operate 权限（admin 直接过）
- 数据库连接：manage 才能改配置/删除，read 才能看；admin 全量
- 诊断任务：创建诊断任务需对目标资源有权限
- 授权管理：admin 在用户管理里分配（集群+namespace+权限 / 数据库连接+权限）
- 数据库连接创建：仅 admin；创建后授权给对应 operator 管理

### 慢 SQL 采集

| 类型 | 数据源 | 说明 |
|---|---|---|
| MySQL 系 | `performance_schema.events_statements_summary_by_digest` | 按 digest 聚合：执行次数/平均耗时/最大耗时/锁等待 |
| PG 系 | `pg_stat_statements` | 同上 |
| Oracle | `v$sql` / AWR | 单独适配 |

- 采集频率可配（默认每 5 分钟）
- 慢 SQL 阈值可配（long_query_time，默认如 1s，按 digest 平均耗时过滤）
- 记录：`慢SQL表：db连接 / digest / SQL样本 / 执行次数 / 平均耗时 / 最大耗时 / 采集时间`
- 执行时长趋势：按 digest 时间序列聚合（图表）

### AI 优化建议

- 触发：新慢 SQL digest 出现 / 手动对某条慢 SQL 发起诊断
- 输入：SQL 文本 + EXPLAIN 执行计划 + 相关表结构/索引信息
- 输出：优化建议（加索引 / SQL 改写 / 参数调优 / 分页优化等），落库可回溯
- 产出即报告（复用统一报告模型），可选沉淀知识库

## 数据模型（新增表，api-server alembic 管理）

1. `diagnostic_tasks` 诊断任务
2. `diagnostic_reports` 诊断报告
3. `db_connections` 数据库连接（密码加密）
4. `slow_sql_records` 慢 SQL 统计
5. `user_cluster_permissions` 集群授权（集群+namespace 组合）
6. `user_db_connections` 数据库连接授权
7. （复用）`knowledge_entries` 知识库沉淀

## 组件改动

| 组件 | 改动 |
|---|---|
| k8s-executor | 新增 `ExecInPod` gRPC（只读命令白名单，应用诊断用） |
| ai-service | 会话式诊断接口（8-19 已定）；数据库诊断接口（慢 SQL + explain + 表结构 → 建议） |
| api-server | 诊断任务 API + 定时调度；数据库连接 CRUD（仅 admin 创建，授权 operator 管理）；慢 SQL 采集器（MySQL/PG/Oracle 三类适配）；资源级授权中间件（集群+namespace / 数据库连接） |
| frontend | 「诊断中心」页：应用诊断 + 数据库监控两个 Tab；任务列表 + 报告详情；用户管理加授权分配 |

## 安全

- 数据库密码加密存储（ENCRYPTION_KEY）
- 数据库专用只读账号（最小权限，平台侧文档说明）
- ExecInPod 命令白名单 + 强制只读 + 全量审计（谁/何时/哪个 pod/什么命令）
- 镜像无 shell 兜底：退化为日志 + describe 诊断

## 开发顺序（建议）

1. **executor ExecInPod** + 命令白名单（Go）
2. **诊断任务框架**：diagnostic_tasks 表 + api-server 定时调度 + 报告落库
3. **应用诊断任务式**：选 pod + 时间窗 + 定时执行 → 报告
4. **会话式诊断**（8-19 方案，复用工具集）
5. **数据库连接管理**：CRUD + 加密 + 测试连接
6. **MySQL 系慢 SQL 采集** + 执行时长趋势
7. **PG 系 + Oracle 采集适配**
8. **AI 优化建议**（慢 SQL → 建议落库）
9. **前端诊断中心**（两个 Tab + 任务/报告页）
10. 知识库闭环联调

## 待确认项（不阻塞方案）

- 数据库只读账号：平台提供 SQL 模板，管理员在库上执行（模板可配）
- 诊断报告保留时长：**平台可配**（默认 90 天，管理员在平台修改）
