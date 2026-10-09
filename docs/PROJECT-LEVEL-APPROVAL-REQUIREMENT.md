# 项目级审批（审核机制补项目维度）—— 需求

> 状态：**草案（待老板评审）**
> 日期：2026-10-09
> 关联：`notification_service.py`（通知路由）、`approval_service.py`（审批）、`alert_policies`、`NETDATA-MONITORING-REQUIREMENTS.md`

---

## 一、背景

平台「告警路由」与「审核机制」目前**维度不对称**：

| | 现状 | 项目/namespace 维度 |
|---|---|---|
| **告警通知路由** | `notify_alert`：先按 `(cluster_id, namespace)` 反查项目 → `project.notify_config_id` → 项目组群；未归属项目再用 `scene=alert` 配置的 `namespaces` 兜底 | ✅ **已有** |
| **审核机制（审批）** | `approval_flows` **全局选择**：`_load_default_flow()` 取 `is_default` 的一个默认流程；节点 `duty`/`group_all`/`admin`/`user` 均**全局** | ❌ **缺失** |

> 唯一的项目维度：高危**双人审批**的 `manager` 节点（`resolve_project_managers(cluster_id, namespace)`）——已按项目解析项目管理员。

**老板结论（2026-10-09）**：告警路由是有的，只是**审核机制没有**——本需求即补齐审核机制的项目维度。

---

## 二、目标

让审核机制**对称**告警通知路由：支持**项目级审批流程**，即
**不同项目 / namespace 的告警审批，走各自绑定的审批流程（不同审核人、不同节点、不同票数）**；
未绑定项目的告警走默认流程。**通知路由行为不变。**

---

## 三、方案（推荐 A：项目绑流程）

> 与通知的「项目挂 `notify_config_id`」完全对称——**在项目上挂一条审批流程**，而不是给流程加 namespace 字段。

### 3.1 实现清单

| # | 落点 | 改动 |
|---|---|---|
| 1 | `projects` 表 | **新增 `approval_flow_id`**（Integer, nullable）—— 对称 `notify_config_id` |
| 2 | `approval_service._load_default_flow()` | 改为：按 `(alert.cluster_id, alert.namespace)` 反查项目 → 项目的 `approval_flow_id` → 加载该流程；**无则回退**现有默认流程 |
| 3 | 前端（项目编辑页） | 新增「审批流程」下拉 —— 对称「通知配置」下拉 |

> `_load_default_flow` 目前是纯全局取默认，**只需改这一处解析逻辑**（创建审批时持有 alert，`cluster_id`/`namespace` 可得）。

### 3.2 备选 B（不推荐）

给 `approval_flows` 加 `cluster_id`/`namespaces` 匹配选流程 —— 会导致**按项目复制多条流程**，维护翻倍。

---

## 四、边界

- **通知路由不动**（`notify_alert` 的项目路由 / scene 兜底保持原样）。
- **决策引擎不动**（`risk_policy` 四维评分 → 是否需审核，仍全局）。
- 本需求只改「**审核走哪套流程**」，不改「是否要审核」。
- `manager` 节点（双人审批）现状保留（它已按项目）。

---

## 五、验收标准

1. 给**项目 A** 绑「流程 1」、**项目 B** 绑「流程 2」→ A/B 下 namespace 的告警审批，**分别走各自流程与审核人**。
2. **未绑定**项目的告警 → 走**默认流程**（现状行为不变）。
3. 告警**通知路由行为零变化**（回归）。
4. 项目编辑页可配置/更换「审批流程」。

---

## 六、待确认

1. 项目绑的是「审批流程」还是也可以绑「是否走审批」？（本需求默认只绑流程）
2. 是否需要「按集群」区分（不归属项目时）？—— 当前只提 `(cluster, namespace)→项目` 一条链。
