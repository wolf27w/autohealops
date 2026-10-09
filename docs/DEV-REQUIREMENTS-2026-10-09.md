# 开发需求清单（2026-10-09 讨论产出）

> 状态：**草稿（待老板排期）**
> 范围：本日讨论产生的全部待开发需求（Netdata 监控接入 + 告警闭环 + 集群外主机 + 项目级审批）
> 关联：`NETDATA-MONITORING-REQUIREMENTS.md`、`NETDATA-ALARM-MAPPING.md`、`PROJECT-LEVEL-APPROVAL-REQUIREMENT.md`、`REQUIREMENTS-BACKLOG.md`
> 规模：S=小（半天内）/ M=中（1~3 天）/ L=大（3 天+）

---

## 总览（按模块）

| 模块 | 任务数 | 说明 |
|---|---|---|
| **A. Netdata 监控接入** | 9 | 接入即自动下发 + 按集群展示（P1~P3） |
| **B. 集群外主机（ECS）+ Docker 接入** | 5 | 另立「主机监控」 |
| **C. Netdata 告警 ↔ AIOps 闭环** | 6 | webhook 入站 + 映射 + `resource_alert` 迁移 |
| **D. 主机/Docker 诊断 + 修复能力** | 4 | 闭环后两环（主机语义） |
| **E. 项目级审批（审核机制补项目维度）** | 3 | 对称通知路由 |

---

## A. Netdata 监控接入（核心）

| # | 任务 | 落点 | 规模 | 备注 |
|---|---|---|---|---|
| A1 | `clusters` 表加字段 + alembic 迁移 | `models/cluster.py` + `alembic/versions/` | S | `monitoring_enabled`/`monitoring_status`/`monitoring_url`/`monitoring_ns`/`parent_storage_size`/`monitoring_retention_days`/`stream_key_encrypted` |
| A2 | executor 新增 RPC `SetupMonitoring` / `TeardownMonitoring` | `k8s-executor/proto/executor.proto` + `internal/grpc/server.go` + pb 重新生成 | M | `dynamic.Interface` + server-side apply |
| A3 | Netdata manifest 模板（多文档，参数化） | executor 内嵌模板 / `k8s/templates/netdata.yaml` | M | Namespace/SA/ClusterRole/Binding/ConfigMap/PVC/Deployment(parent)/Service/DaemonSet(child) |
| A4 | `monitoring_service.py` | `api-server/app/services/monitoring_service.py`（新） | M | mirror `rbac_service`；生成+加密 stream key；回写状态 |
| A5 | executor_client 加方法 | `api-server/app/clients/executor_client.py` | S | `setup_monitoring` / `teardown_monitoring` |
| A6 | 接入流程挂载 | `api-server/app/api/clusters.py` | S | create 异步触发 + health-check 自动补 + `setup-monitoring` 端点 + delete 卸载 |
| A7 | 反代路由（parent UI） | `api-server/app/api/monitoring.py`（新，或并入 clusters） | M | apiserver Service Proxy → `netdata-parent:19999`（复用 `prometheus/proxy.go`） |
| A8 | 前端监控入口 + iframe + 状态 | `frontend/src/pages/ClusterDetail.tsx` + `services/api.ts` | M | `monitoring_enabled` 显示按钮 |
| A9 | 存量集群补装 | 随 A6 | S | health-check 成功后自动补 |

**分期**：P1 = A1~A6,A8（核心闭环）｜ P2 = A9 + 升级/卸载 ｜ P3 = DB 实例 Netdata 总览（可选）

---

## B. 集群外主机（ECS）+ Docker 接入

| # | 任务 | 落点 | 规模 | 备注 |
|---|---|---|---|---|
| B1 | `hosts` 表 + 迁移 | `models/host.py`（新）+ alembic | S | 名称/地址/环境标签/上报 parent/状态/agent 版本 |
| B2 | 主机接入 API（登记/编辑/删除/状态） | `api-server/app/api/hosts.py`（新） | M | 不走 kubeconfig |
| B3 | 主机 agent 部署 | 手动脚本（起步）/ SSH 自动 / 标准镜像 | M~L | **起步用手动脚本**；SSH 需引入主机凭据管理 |
| B4 | 外部主机 parent 方案 | 专用 parent（推荐）或复用某集群 parent | S~M | 视网络可达性 |
| B5 | 前端「主机监控」入口 + 展示 | `frontend/src/pages/`（新） | M | 与「集群监控」并列；主机构→Docker 容器下钻 |

**分期**：P4

---

## C. Netdata 告警 ↔ AIOps 闭环

| # | 任务 | 落点 | 规模 | 备注 |
|---|---|---|---|---|
| C1 | 入站接口 `POST /api/v1/webhooks/netdata` | `api-server/app/api/webhooks.py`（复用） | M | token 鉴权 + 解析 payload |
| C2 | 字段映射表 + 转换 | 配置/DB + `services/netdata_alert_service.py`（新） | M | `NETDATA-ALARM-MAPPING.md` 落地；未知→`NetdataUnclassified` 兜底 |
| C3 | Netdata health 裁剪清单 + 配置 | Netdata ConfigMap（`health.d`） | M | 只启用映射表内的，其余关闭 |
| C4 | Netdata webhook 通知配置 | Netdata `health_alarm_notify.conf`（随 manifest） | S | 指向平台入站 + token |
| C5 | 状态同步 + 去重/冷却 + 轮询兜底 | C1 服务 | M | CLEAR→resolved；复用去重/维护窗口；`/api/v1/alarms` 对账 |
| C6 | `resource_alert` 迁移 + 旧数据处置 | `services/resource_alert_service.py` + 前端「告警策略」 | M | 4 规则 `fault_type` 不变；旧规则界面/数据取舍 |

**分期**：P4

---

## D. 主机/Docker 诊断 + 修复能力（闭环后两环）

| # | 任务 | 落点 | 规模 | 备注 |
|---|---|---|---|---|
| D1 | 主机诊断工具（只读命令） | `services/tool_registry.py` + executor | L | 需 SSH 到主机能力 |
| D2 | Docker 容器诊断 | 同上 | M | docker inspect / logs 等 |
| D3 | 主机/Docker 修复执行器 | executor + 决策引擎对接 | L | 重启容器 / 清磁盘等 |
| D4 | 接入决策引擎/审批/工单 | `decision_engine` + `approval_service` | M | 复用现有流 |

**分期**：P5（**主要工作量**）

---

## E. 项目级审批（审核机制补项目维度）

| # | 任务 | 落点 | 规模 | 备注 |
|---|---|---|---|---|
| E1 | `projects` 加 `approval_flow_id` + 迁移 | `models/project.py` + alembic | S | 对称 `notify_config_id` |
| E2 | `_load_default_flow` 按 `(cluster,ns)→项目→flow` | `services/approval_service.py` | S | 无则回退默认 |
| E3 | 前端项目编辑加「审批流程」下拉 | `frontend/src/pages/`（项目页） | S | 对称「通知配置」下拉 |

**规模**：**小（最快可做，建议先上）**——独立于 Netdata，改动最小、见效快。

---

## 依赖关系

```
A（Netdata 接入）  ← 基础：没有 parent 就没有展示，也没有 Netdata 告警
   ├─ B（主机接入） 依赖 A（复用 parent/展示链路）
   └─ C（告警闭环） 依赖 A（parent 已部署）
        └─ D（主机诊断/修复） 依赖 B + C
E（项目级审批）  独立，无依赖，可先做
```

## 建议排期

| 优先级 | 内容 | 理由 |
|---|---|---|
| **1** | **E 项目级审批** | 独立、改动最小、可快速见效 |
| **2** | **A Netdata 接入（P1）** | 核心，其余都依赖它 |
| **3** | **C 告警闭环 + B 主机接入（P4）** | 吃到 Netdata 告警价值 |
| **4** | **D 主机/Docker 诊断+修复（P5）** | 工作量大，闭环后两环 |

## 待老板拍板

1. **先做哪个**？（建议 E 或 A-P1）
2. **B3 主机 agent 装法**：手动脚本 / SSH 自动 / 镜像？
3. **B4 parent**：专用 / 复用？
4. **C6 `resource_alert` 旧规则数据/前端**：怎么处置（下线 / 只读留存）？
5. **D 是否做**（工作量最大）？分阶段还是一次性？
