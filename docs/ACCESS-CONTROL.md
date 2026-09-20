# 权限与角色方案：资源级授权 + 开发角色 + 告警订阅

> 状态：**方案确认中**（2026-08-20 老板讨论定稿，待开发）
> 关联：`DIAGNOSIS-CENTER.md`（诊断中心，权限部分引用本文档）
> 定位：平台使用对象 = 运维 + 开发；权限从「全局三角色」升级为「资源级授权 + 角色细分」

## 老板拍板的决策（2026-08-20）

1. **使用对象**：运维 + 开发两类用户；开发不需要值班
2. **资源级授权**：独立授权表，admin 全量，其他按授权可见
3. **开发角色**：诊断自己负责的应用（只诊断不修复——应用问题是代码/启动参数/基础镜像，平台改不了）；订阅自己项目的告警通知（环境告警 + 日志告警）
4. **告警订阅**：namespace 粒度；配置直接生效无需审核；纯通知无审批流；不做个人消息推送，发到项目对应的群 webhook
5. **环境标签**：集群创建时打 env_tag（qa/release/prod），admin 配置，一个集群一个环境

## 一、角色体系（4 角色）

| 角色 | 定位 | 值班 | 审批 | 修复操作 | 诊断 | 数据可见 |
|---|---|---|---|---|---|---|
| **admin** | 全量管理 | 兜底 | 兜底 | ✅ | ✅ | 全部 |
| **operator** | 运维操作 | ✅ 参与 | ✅ | 授权范围 | ✅ | 授权范围 |
| **viewer** | 只读观察 | ❌ | ❌ | ❌ | ✅ 发起 | 授权范围 |
| **developer** | 开发排查 | ❌ | ❌ | ❌（默认） | ✅ 核心 | 授权范围 |

### 开发角色（developer）核心场景

- 查看自己服务的状态/日志/指标
- **发起应用诊断任务**（诊断中心主要用户——定位自己应用问题）
- 查看诊断报告、授权数据库连接的慢 SQL
- **订阅自己项目的告警通知**（环境告警 + 日志告警）
- 不参与值班、不处理审批
- 修复操作默认禁止（如需可 admin 额外授权）

## 二、资源级授权（独立授权表）

```sql
-- 集群授权（集群 + namespace 组合粒度）
user_cluster_permissions:
  id, user_id, cluster_id, namespace,  -- namespace 可空 = 该集群全部
  permission(read|operate)             -- operate = 该 ns 下可创建/删除/操作

-- 数据库连接授权
user_db_connections:
  id, user_id, db_connection_id, permission(read|manage)
  -- manage = 可改配置/删除；read = 只看慢SQL/报告
```

- **admin**：隐式全量，跳过授权检查
- **operator/viewer/developer**：按授权可见，未授权资源不可见（权限收敛）
- 诊断任务：创建诊断任务需对目标资源有权限
- 授权管理：admin 在用户管理里分配（集群+namespace+权限 / 数据库连接+权限）
- 数据库连接创建：仅 admin；创建后授权给对应 operator 管理

## 三、告警通知订阅（用户级）

```sql
alert_subscriptions:
  id, user_id, cluster_id, namespace,   -- namespace 粒度（项目）
  subscribe_type(env|log|both),         -- 环境告警 / 日志告警
  enabled, created_at
```

- 开发在平台上自己配置订阅（选集群 + namespace + 类型），**直接生效无需审核**
- 该 namespace 范围内产生告警（pod 故障/节点异常/日志关键词命中）→ 通知订阅者
- **通知方式**：不做个人消息推送；通知渠道绑定 namespace（项目），告警按所属 namespace 路由到对应项目的群 webhook（钉钉/企微/飞书群）
- 纯通知，无审批流

### 通知渠道绑定 namespace

- 现有 `notification_configs`（全局渠道）扩展：支持绑定 `cluster_id + namespace`（项目群）
- 路由逻辑：告警产生 → 查该 namespace 的订阅 + 绑定渠道 → 推送对应群
- 未绑定渠道的 namespace：走全局默认渠道兜底

## 四、值班规则

- 值班人（on_duty 排班）**只从 admin/operator 角色选择**（viewer/developer 不可选）
- 审批超时升级链（见 DECISION-BOUNDARY.md）：值班人轮转 → admin 兜底

## 五、仪表盘视角（按环境 + 按权限）

- 集群打 env_tag 后，仪表盘顶部环境切换器：[全部] [QA] [Release] [Prod]
- 所有数据按当前环境过滤 + **按当前用户授权范围过滤**
- 角色视角差异：
  - 运维（admin/operator）：全局视角（集群/告警/审批/成本/自愈统计）
  - 开发（developer）：自己的服务视角（状态/告警/诊断入口），不显示审批/值班/成本
  - viewer：只读全局视角（授权范围）
- 环境切换器只显示用户有权限的环境

## 六、数据模型变更汇总

1. `clusters` 加 `env_tag`（qa/release/prod）
2. `user_cluster_permissions` 集群授权表（新）
3. `user_db_connections` 数据库连接授权表（新）
4. `alert_subscriptions` 告警订阅表（新）
5. `notification_configs` 加 `cluster_id/namespace` 绑定字段（项目群）
6. `users` role 支持 developer
7. on_duty 排班限定 admin/operator

## 七、待确认项（不阻塞）

- 通知渠道绑定 namespace 后，是否保留"全局默认渠道"兜底（建议保留）
