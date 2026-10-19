# 新增需求（老板 2026-10-19 11:14）

> **状态：✅ 已开发完成（2026-10-19 12:33–13:00）**（老板 11:14 登记，12:33 开工）
> 编号延续 `NEW-REQUIREMENTS-2026-10-15.md` 的 N1–N11 → 本批 **N12 / N13**。
> 现状描述均按 2026-10-19 代码核实（HEAD `eb196b8`）。

---

## N12 🐞 监控刷新不能闪 —— 要「自动、实时、无闪烁」

**老板原话**：在仪表盘里查看集群监控，每次更新都要闪一下，这个不要，要自动不需要闪一下，监控要实时的。

### 现状核实

- 监控 UI 已从仪表盘迁到大屏 `/screen`（`frontend/public/monitor-bigscreen.html`，commit `21cd196`「监控归大屏」）；仪表盘只剩「集群资源水位 Top」表（`frontend/src/pages/Dashboard.tsx`）。
- 大屏刷新：`boot()` 里 `setInterval(refreshView, 15000)` → `renderOverview()` / `openCluster()` → `renderService()`。
- **闪烁根因（代码级）**：
  1. `clearCharts()`（`monitor-bigscreen.html:275`）对**所有** ECharts 实例 `dispose()`；
  2. `renderOverview()`（:294-297）每次 `clearCharts()` + `$('#app').innerHTML = ...` **整块重建**；
  3. `renderCluster()`（:446-448）同样 `clearCharts()` + 整块重建；
  4. `renderService()`（:496-577）整块替换 `#cbody`/`#svcbody`，连带 namespace 下拉、工作负载类型页签、服务下拉一起重建（用户当前选择会被重置）。
  - 结论：**每次刷新 = 销毁 DOM + 销毁图表 + 重新 init** → 必然闪；不是数据更新慢，是渲染方式问题。
- 仪表盘主数据 5s 轮询已用 `placeholderData: keepPreviousData`（不空白），但数值/进度条本身跳变，属次要观感问题。

### 需求

1. 刷新时**只更新数据，不重建视图**：无白屏、无闪烁、无控件重置。
2. 监控**近实时**（当前 15s 太慢，老板要求实时）。

### 方案要点

| 项 | 做法 |
|---|---|
| 骨架/数据分离 | 首次渲染骨架；后续按元素 key 只改 `textContent`/属性/表格行，禁止整块 `innerHTML` 覆盖 |
| 图表实例池 | ECharts 只 `init` 一次；刷新走 `setOption(opt, { notMerge:false, lazyUpdate:true, replaceMerge:['series'] })`；**禁止 dispose** |
| 表格增量 | 行 key = `namespace/pod/container`；按 key 增删改行，不整表替换 |
| 交互态保护 | 刷新不重建 `select`/页签，保留用户当前命名空间/服务/容器选择 |
| 推送优先 | 复用 `/api/v1/stats/dashboard/stream`（SSE，缓存代次变化即推）+ 监控数据 3–5s 轻量轮询；`document.hidden` 时暂停（已有） |
| 动效 | 数值变化用 CSS transition；图表保持 `animation:false`；不做整块入场动画 |

### 验收

- 大屏连续观察 5 分钟：KPI、表格、图表更新时**无白屏/闪烁/控件重置**；
- 趋势图时间轴平滑推进，ECharts 实例数恒定（无反复 init/dispose）；
- 同一页面 CPU 占用不升高；刷新间隔 ≤5s；后台标签不刷新。

**工时**：~1.5–2d（大屏 JS 渲染层重构 + 回归）。

**刷新频率（老板 2026-10-19 定）**：**3–5s**。落地口径：告警/状态类走 SSE 即时推送；监控数值 5s 轮询兜底，指标突变/图表推进按 3s 取（取数走 Redis 短缓存，避免给 Prometheus 加压）。

### ✅ 实现（2026-10-19）

- **增量渲染 `patchHTML()`**：模板字符串解析后与现有 DOM 逐节点 diff，只改差异（文本/属性/子节点增删）；节点复用 → 不闪、不丢焦点、不重置下拉框。
  - 图表容器（`.chart`）跳过子节点 diff（canvas 由 ECharts 管理）；`select`/`input` 保留当前值。
- **图表实例池 `chartPool`**：同 id 只 `init` 一次，刷新 `setOption(opt,{notMerge:false,lazyUpdate:true,replaceMerge:['series']})`；`clearCharts()` 不再 dispose。
- **刷新节奏**：总览 5s、集群/容器监控 3s、趋势 range 30s（缓存，避免 range 查询打爆 Prometheus）；`_refreshing` 防重入；后台标签暂停。
- 大屏 iframe 版本 `?v=5 → v=6`（强制刷新缓存）。
- 说明：大屏当前走**轮询**（总览 5s / 监控 3s）；SSE 即时推送仅口径预留给告警类变更，本次未接（如需秒级可后续补，复用 `/stats/dashboard/stream`）。

**验证**：headless Chrome + `?selftest=1|cluster` 自检（连续刷新后比较节点引用）：
`dom_reused=true chart_dom_reused=true cbody_reused=true charts=1(or 4)`，canvas 保留；`ci_check.sh` 全绿；`npm run build` 通过。

---

## N13 🏗️ 监控内容要到「容器级别」

**老板原话**：针对监控的内容现在还没有到容器级别，我要监控内容到容器级别。

### 现状核实

- **后端已具备容器级能力**：
  - `prometheus_service.METRIC_QUERIES` 已有 `container_cpu_top` / `container_mem_top` / `container_net_rx` / `container_net_tx` / `container_cpu` / `container_mem`（`api-server/app/services/prometheus_service.py:56-80`）；
  - 接口 `/api/v1/monitoring/clusters/{id}/metrics/{metric}` 与 `/range` 已支持 `namespace` / `pod` / `container` 过滤；`/clusters/{id}/containers` 可列容器名。
- **已有容器级展示**：大屏「服务详情」`renderServiceBody`（:536-561）有容器表格（CPU/内存/重启/OOM/CPU 限流/磁盘读写/网络错误）+ 3 张按容器多线趋势图。
- **缺口**：
  1. 总览页 / 集群列表只在集群或工作负载级，**没有容器 Top**；
  2. **缺 Pod → 容器下钻**（看不到某 Pod 每个容器的实时指标与趋势）；
  3. 容器表格缺 requests/limits 对比、容器状态（Waiting/Terminated reason）、磁盘用量、GPU；
  4. 平台**自采集** `pod_metrics` 只有 Pod 粒度 → 无 Prometheus 的集群到不了容器；
  5. 集群详情「监控」抽屉是 Pod 级（`ClusterDetail.tsx` `PodMetricsDrawer`），无容器拆分/选择。

### 需求

监控对象层级完整打通 **集群 → 节点 → 工作负载 → Pod → 容器**，所有监控视图可下钻到容器；容器级指标覆盖 CPU/内存/网络/磁盘/重启/OOM/限流/状态，并支持与 requests/limits 对比。

### 方案要点

- **UI**：
  - 总览加「容器 Top」卡片（CPU/内存/网络/重启，Top N，可点进集群）；
  - 服务详情容器表格补 limits/requests、容器状态、重启原因；
  - Pod 详情加容器维度（每容器一行 + 点击看单容器趋势），与其他视图统一的容器选择器。
- **数据口径（老板 2026-10-19 定）：容器指标统一走 Prometheus**。
  - Prometheus 路径直接扩 PromQL：`container_memory_working_set_bytes` vs `kube_pod_container_resource_limits`、`kube_pod_container_status_*`、`container_fs_usage_bytes`、`container_spec_*`；
  - **不再扩自采集 `pod_metrics` 的容器维度**（原 1–2d 采集器改造取消）；无 Prometheus 数据源的集群，容器级监控降级为「未配置监控数据源」提示；
  - `METRIC_QUERIES` 补容器状态/限额类指标。
- **权限**：沿用 `check_cluster_permission`（已有）。

### 验收

- 任一集群可从总览下钻到某 Pod 的某容器，看到该容器 CPU/内存/网络/重启/OOM/限流/状态；
- 容器级趋势与 Prometheus 直查对账一致；
- 无 Prometheus 的集群（自采集链路）**不要求**容器级指标，界面提示「未配置监控数据源」；
- 页面刷新不闪（与 N12 同批验收）。

**工时**：~2–2.5d（前端 2d + 指标补充 0.5d；自采集改造已按老板口径取消）。

### ✅ 实现（2026-10-19）

- **总览新增「容器 CPU / 内存 Top 10」**（跨集群）：遍历各集群调 `container_cpu_top` / `container_mem_top`，合并排序取前 10；30s 缓存（`CTOP`）。
- **服务详情新增「Pod × 容器明细」表**：逐 Pod 逐容器列 状态 / CPU / 内存 / CPU 限额 / 内存限额 / 重启 / 上次终止原因。
  - 数据全部走 Prometheus：`container_cpu_usage_seconds_total`、`container_memory_working_set_bytes`、`kube_pod_container_status_restarts_total`、`kube_pod_container_resource_limits`、`kube_pod_container_status_waiting_reason`、`kube_pod_container_status_last_terminated_reason`。
  - 状态判定：Waiting 原因优先，其次 Last terminated reason，否则用 Pod status；重启>0 或异常状态行标红。
- 原「容器实例」聚合表保留（跨 Pod 按容器名汇总），与新表互补。
- **业务容器 / Sidecar 分开**（老板 2026-10-19 反馈）：`filebeat`、`istio-proxy`、`logtail`、`fluent-bit`、`node-exporter` 等识别为 Sidecar，表格加「类型」列并分组排序（业务容器在前、Sidecar 单独分组行），避免与业务容器混在一起看。

**验证（真实数据，2026-10-19）**：总览容器 Top 出实际集群/命名空间/容器（如 `0.46 core`、`9265.6 MiB`）；Pod×容器明细出现 `Running` / `Last:Error`、`1.00 core / 200.0MiB` 限额、重启数。

---

## 已定口径（老板 2026-10-19）

| # | 问题 | 结论 |
|---|---|---|
| 1 | N12 刷新频率目标 | ✅ **3s / 5s**：告警/状态走 SSE 推送，监控数值 5s 轮询、3s 快速取，取数带短缓存 |
| 2 | N13 容器指标数据源 | ✅ **统一走 Prometheus**；不扩自采集 `pod_metrics`；无 Prometheus 的集群容器级降级提示 |
| 3 | 容器 Top 是否上大屏首页 | ⚪ 未明确 → **默认上首页**（CPU/内存/网络/重启 Top N 卡片，可点进集群）；如需只看集群内视图，排期时一句话改 |

## 建议排期

- 两条**同一批做**：N12 先把渲染层改好，N13 的容器下钻直接建在「不重建」的渲染骨架上，避免返工。
- 优先级：N12（观感/实时，1.5–2d）→ N13（容器级，2–2.5d），合计 ~3.5–4.5d。

---

## 附：当前其他未开发需求（2026-10-19 全文核实，代码为准）

### 可开发（无外部依赖）

| # | 需求 | 现状 / 缺口 | 工时 |
|---|---|---|---|
| **A2** | Agent 托管更新：平台升级时批量滚动升级各集群 agent（版本/暂停/回退） | 无实现；仅 `AGENT_IMAGE` 常量 | ~1d |
| **M1** | 平台部署在集群内 → 自动接入本集群 | api-server 无 in-cluster 启动 upsert；"本集群"标记未做（`clusters.source` 字段已有） | ~1.5–2d |
| **D1** | 凭据到期**主动通知**（30/7/1 天预警站内/IM） | 已有 `cred_expires_at` 解析/存储/列表红标，**无通知任务** | ~0.3d |
| **值班 UI** | 值班排班 / 换班 / 日历 | 后端 `api/on_duty.py`（含 `/calendar`、`/overrides`）+ 前端 api 封装已有，**无页面/菜单**（原 `OnDuty.tsx` 已删） | ~1d |
| **A4** | M2「自动下发 agent」开关的实际下发动作 | 开关字段/批量接入已有，下发未做 | 并入 A2 |
| **N12/N13** | 监控无闪烁实时 / 容器级监控 | 见正文 | 1.5–2d + 2–2.5d |

### 部署前置（非纯代码）

| # | 事项 | 说明 |
|---|---|---|
| A6.1 | agent 镜像构建/推送 | `linux/amd64` 构建 `apiops-agent` → 推目标集群可拉仓库，填 `AGENT_IMAGE` |
| A6.2 | agent 网关 9095 对外可达 | 跨集群需 NodePort/LB 或四层转发；当前仅本地 127.0.0.1 |
| B3 | 平台自身生产部署 | M10/M11 已备，未上生产 |

### 待老板拍板（非开发）

- B1「诊断失败」是否单独记录（现映射 pending，未转人工）
- B2 准入风险口径 —— **2026-10-19 核实 `k8s-executor/internal/` 已无 `admission` 模块，服务本体不存在，建议直接销项**
- B3 专项 E：砍 `SelfHealPolicy` 死代码（破坏性删除）

### 已核实完成（原 ROADMAP 误列为待办的，已更正）

C1 KPI 口径（`231913a`）｜M2 多 context 批量接入｜M4 分区维护机制（表清单含 `pod_metrics`）｜告警协作（认领/备注/工单）｜审批增强（批量/加签/转办）｜知识库导入导出｜报表导出+定时推送｜AI 反馈闭环｜LDAP↔本地切换
