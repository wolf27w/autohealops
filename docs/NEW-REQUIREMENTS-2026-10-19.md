# 新增需求（老板 2026-10-19 11:14）

> **状态：⏸ 已登记，暂不开发**（老板明确「把这两个需求加入到需求开发里，暂时不开发」）
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
- **数据**：
  - Prometheus 路径直接扩 PromQL：`container_memory_working_set_bytes` vs `kube_pod_container_resource_limits`、`kube_pod_container_status_*`、`container_fs_usage_bytes`、`container_spec_*`；
  - 自采集路径：`pod_metrics` 扩容器维度（新增 `container_metrics` 表或加 `container` 列 + 采集器按容器抓取，需迁移）；
  - `METRIC_QUERIES` 补容器状态/限额类指标。
- **权限**：沿用 `check_cluster_permission`（已有）。

### 验收

- 任一集群可从总览下钻到某 Pod 的某容器，看到该容器 CPU/内存/网络/重启/OOM/限流/状态；
- 容器级趋势与 Prometheus 直查对账一致；
- 无 Prometheus 的集群（自采集链路）也能看到容器级 CPU/内存；
- 页面刷新不闪（与 N12 同批验收）。

**工时**：~3–4d（前端 2d + 自采集容器维度 1–2d + 指标补充 0.5d）。

---

## 开发前待确认（登记时不必回答，排期时一起定）

| # | 问题 | 影响 |
|---|---|---|
| 1 | N12 刷新频率目标：3s / 5s / 15s？ | 决定轮询 vs SSE 推送占比、后端压力 |
| 2 | N13 容器指标是否统一走 Prometheus（有则不必扩自采集），还是必须支持无 Prometheus 集群？ | 决定是否要做 `container_metrics` 迁移 + 采集器改造（+1–2d） |
| 3 | 容器级 Top 是否要出现在大屏首页（还是只在集群内视图）？ | 决定首页信息密度与布局 |

## 建议排期

- 两条**同一批做**：N12 先把渲染层改好，N13 的容器下钻直接建在「不重建」的渲染骨架上，避免返工。
- 优先级：N12（观感/实时，1.5–2d）→ N13（容器级，3–4d）。
